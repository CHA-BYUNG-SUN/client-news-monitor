#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
고객사 뉴스 모니터링 - 뉴스 수집 스크립트

기능
1. 네이버 뉴스 검색 API로 config/companies.json 에 등록된 고객사(고객명 기준)별 최신 기사 수집
2. 기사 원문 링크 도메인으로 '메이저 언론사' 여부 판정 (config/major_media.json)
3. 제목/본문 키워드로 우선순위 태그 부여: 투자 > 수주 > 협업 > 자동화 (config/priority_keywords.json)
4. 중복 기사 제거 (동일 링크 + 제목 유사도 기반 유사 기사)
5. SUB고객명은 검색에 쓰지 않고, 기사 본문/제목에 등장할 때만 참고용으로 표시(matched_sub_names)
6. 팀구분/셀코드/외근영업/내근영업 정보를 기사에 함께 저장해 웹사이트 필터(팀/셀/개인/고객명)에 활용
7. data/news.json 으로 결과 저장 (GitHub Pages 정적 사이트에서 fetch 하여 사용)

환경변수
- NAVER_CLIENT_ID, NAVER_CLIENT_SECRET : 네이버 개발자센터에서 발급받은 값 (필수)
- NEWS_LOOKBACK_DAYS : 며칠 이내 기사만 남길지 (기본 7일)
- NEWS_DISPLAY_PER_QUERY : 고객사별 1회 호출 시 가져올 기사 수 (기본 100, 최대 100)
- NEWS_REQUEST_DELAY : 고객사별 API 호출 간 대기 시간(초, 기본 0.2) - 고객사 수가 많을 때 조정
"""

import os
import sys
import json
import re
import html
import time
import difflib
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from urllib import request as urlrequest
from urllib import error as urlerror

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
DATA_DIR = os.path.join(BASE_DIR, "data")

NAVER_API_URL = "https://openapi.naver.com/v1/search/news.json"

KST = timezone(timedelta(hours=9))


def load_json(filename):
    path = os.path.join(CONFIG_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def clean_text(raw):
    """네이버 API 응답의 <b>, &quot; 등 HTML 태그/엔티티 제거"""
    if not raw:
        return ""
    text = re.sub(r"</?b>", "", raw)
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def normalize_title(title):
    """중복 판정을 위한 제목 정규화: 공백/특수문자 제거, 소문자화"""
    t = re.sub(r"[^0-9a-zA-Z가-힣]", "", title)
    return t.lower()


def get_domain(url):
    try:
        netloc = urlparse(url).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""


def resolve_media(domain, media_config):
    domains = media_config.get("domains", {})
    default = media_config.get("default", {"name": "기타 언론사", "major": False})
    if not domain:
        return default["name"], default["major"]
    if domain in domains:
        info = domains[domain]
        return info["name"], info.get("major", False)
    # 서브도메인 등 부분 일치 (예: news.chosun.com -> chosun.com)
    for key, info in domains.items():
        if domain == key or domain.endswith("." + key):
            return info["name"], info.get("major", False)
    return default["name"], default["major"]


def resolve_tag(title, description, keyword_config):
    text = f"{title} {description}"
    order = keyword_config.get("priority_order", [])
    tags = keyword_config.get("tags", {})
    for tag_key in order:
        tag_info = tags.get(tag_key, {})
        for kw in tag_info.get("keywords", []):
            if kw.lower() in text.lower():
                return tag_key, tag_info.get("label", tag_key), tag_info.get("color", "#7f8c8d")
    default = keyword_config.get("default_tag", {"label": "일반", "color": "#7f8c8d"})
    return None, default["label"], default["color"]


LIST_SEPARATORS = [",", "·", "、", "ㆍ", "/"]


def is_relevant_article(title, description, query):
    """검색어(고객명)가 기사에서 실질적으로 다뤄지는 기사인지 확인.

    네이버 뉴스 검색은 본문 어딘가에 검색어가 '포함'되기만 해도 결과로 반환하기 때문에,
    여러 회사가 함께 언급되는 산업 동향/컨퍼런스 요약 기사에서 고객명이 스쳐 지나가듯
    한 번 언급된 경우까지 딸려 온다. 이런 '나열형 언급'을 걸러내기 위한 필터.

    1) 제목에 검색어가 있으면 확실한 관련 기사로 간주.
    2) 본문에만 있으면, 검색어 주변(전후 20자)에 쉼표/가운뎃점 등 나열 구분자가
       2개 이상 있는 경우 '여러 회사 중 하나로 언급'된 것으로 보고 제외한다.
    """
    if not query:
        return True
    q = query.strip().lower()
    if not q:
        return True

    title_l = (title or "").lower()
    if q in title_l:
        return True

    desc = description or ""
    desc_l = desc.lower()
    idx = desc_l.find(q)
    if idx == -1:
        # 네이버 검색 자체가 query로 걸러주므로 이 경우는 드물지만, 못 찾으면 통과시킨다
        return True

    start = max(0, idx - 20)
    end = min(len(desc), idx + len(q) + 20)
    window = desc[start:end]
    separator_count = sum(window.count(sep) for sep in LIST_SEPARATORS)
    if separator_count >= 2:
        return False
    return True


def find_matched_sub_names(title, description, sub_names):
    """그룹 내 SUB고객명이 기사 제목/본문에 등장하면 원래 표기(raw)를 반환.
    검색 자체는 고객명(메인)으로만 수행하고, SUB고객명은 표시용으로만 사용합니다."""
    text = f"{title} {description}".lower()
    matched = []
    for sub in sub_names or []:
        clean = (sub.get("clean") or "").strip().lower()
        if clean and clean in text:
            matched.append(sub.get("raw"))
    return matched


def parse_pubdate(pubdate_str):
    try:
        dt = parsedate_to_datetime(pubdate_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(KST)
    except Exception:
        return None


def fetch_company_news(company_query, client_id, client_secret, display=100, retries=3):
    params = f"query={urlrequest.quote(company_query)}&display={display}&sort=date"
    url = f"{NAVER_API_URL}?{params}"
    req = urlrequest.Request(url)
    req.add_header("X-Naver-Client-Id", client_id)
    req.add_header("X-Naver-Client-Secret", client_secret)

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            with urlrequest.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body).get("items", [])
        except urlerror.HTTPError as e:
            last_err = e
            print(f"  [경고] '{company_query}' API 호출 실패 (시도 {attempt}/{retries}): HTTP {e.code}", file=sys.stderr)
            time.sleep(1.5 * attempt)
        except Exception as e:
            last_err = e
            print(f"  [경고] '{company_query}' API 호출 실패 (시도 {attempt}/{retries}): {e}", file=sys.stderr)
            time.sleep(1.5 * attempt)
    print(f"  [오류] '{company_query}' 뉴스 수집 실패: {last_err}", file=sys.stderr)
    return []


def is_duplicate(article, seen_articles, title_similarity_threshold=0.82):
    """동일 링크 또는 유사 제목(같은 날짜 기준) 기사인지 확인"""
    for existing in seen_articles:
        if article["originallink"] and article["originallink"] == existing["originallink"]:
            return existing
        if article["link"] == existing["link"]:
            return existing
        # 같은 날 기사끼리만 제목 유사도 비교 (기사 재판/전재 대응)
        if article.get("pubDate_iso", "")[:10] == existing.get("pubDate_iso", "")[:10]:
            ratio = difflib.SequenceMatcher(
                None, article["_norm_title"], existing["_norm_title"]
            ).ratio()
            if ratio >= title_similarity_threshold:
                return existing
    return None


def main():
    client_id = os.environ.get("NAVER_CLIENT_ID")
    client_secret = os.environ.get("NAVER_CLIENT_SECRET")
    if not client_id or not client_secret:
        print(
            "[오류] NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수가 설정되어 있지 않습니다.\n"
            "네이버 개발자센터(https://developers.naver.com)에서 애플리케이션을 등록해 "
            "Client ID/Secret을 발급받고 환경변수 또는 GitHub Secrets로 등록하세요.",
            file=sys.stderr,
        )
        sys.exit(1)

    lookback_days = int(os.environ.get("NEWS_LOOKBACK_DAYS", "7"))
    display = min(int(os.environ.get("NEWS_DISPLAY_PER_QUERY", "100")), 100)
    request_delay = float(os.environ.get("NEWS_REQUEST_DELAY", "0.2"))

    companies_cfg = load_json("companies.json")["companies"]
    media_cfg = load_json("major_media.json")
    keyword_cfg = load_json("priority_keywords.json")
    priority_order = keyword_cfg.get("priority_order", [])

    cutoff = datetime.now(KST) - timedelta(days=lookback_days)

    all_articles = []
    seen_articles = []  # 전역 중복 체크용 (링크/유사 제목)

    total = len(companies_cfg)
    for i, company in enumerate(companies_cfg, start=1):
        name = company["name"]
        query = company.get("query", name)
        sub_names = company.get("sub_names", [])
        team = company.get("team", [])
        cell = company.get("cell", [])
        ext_rep = company.get("ext_rep", [])
        int_rep = company.get("int_rep", [])
        reps = sorted(set([r for r in (ext_rep + int_rep) if r]))

        print(f"[{i}/{total}] 수집: {name} ('{query}')")
        items = fetch_company_news(query, client_id, client_secret, display=display)

        kept, skipped_old, skipped_dup, skipped_irrelevant = 0, 0, 0, 0
        for item in items:
            pub_dt = parse_pubdate(item.get("pubDate", ""))
            if pub_dt is None:
                continue
            if pub_dt < cutoff:
                skipped_old += 1
                continue

            title = clean_text(item.get("title", ""))
            description = clean_text(item.get("description", ""))

            if not is_relevant_article(title, description, query):
                skipped_irrelevant += 1
                continue

            link = item.get("link", "")
            originallink = item.get("originallink", "") or link
            domain = get_domain(originallink) or get_domain(link)
            press_name, is_major = resolve_media(domain, media_cfg)
            tag_key, tag_label, tag_color = resolve_tag(title, description, keyword_cfg)
            matched_sub_names = find_matched_sub_names(title, description, sub_names)

            article = {
                "company": name,
                "matched_sub_names": matched_sub_names,
                "team": team,
                "cell": cell,
                "reps": reps,
                "title": title,
                "description": description,
                "link": link,
                "originallink": originallink,
                "press": press_name,
                "domain": domain,
                "major": is_major,
                "pubDate_iso": pub_dt.isoformat(),
                "pubDate_display": pub_dt.strftime("%Y-%m-%d %H:%M"),
                "tag_key": tag_key,
                "tag_label": tag_label,
                "tag_color": tag_color,
                "_norm_title": normalize_title(title),
            }

            dup = is_duplicate(article, seen_articles)
            if dup:
                skipped_dup += 1
                # 기존 기사가 비메이저이고 새 기사가 메이저 언론사면 메이저 기사로 교체
                if is_major and not dup["major"]:
                    seen_articles.remove(dup)
                    all_articles.remove(dup)
                    seen_articles.append(article)
                    all_articles.append(article)
                continue

            seen_articles.append(article)
            all_articles.append(article)
            kept += 1

        print(f"       수집 {len(items)} / 채택 {kept} / 중복제외 {skipped_dup} / 기간외제외 {skipped_old} / 나열형제외 {skipped_irrelevant}")
        time.sleep(request_delay)  # API 호출 간 짧은 대기

    # 우선순위 정렬: 먼저 최신순으로 정렬한 뒤, 태그 우선순위(투자>수주>협업>자동화>일반)로 안정 정렬
    # (동일 우선순위 그룹 내에서는 최신 기사가 위로 오도록 stable sort 활용)
    all_articles.sort(key=lambda a: a["pubDate_iso"], reverse=True)
    all_articles.sort(key=lambda a: (
        priority_order.index(a["tag_key"]) if a["tag_key"] in priority_order else len(priority_order)
    ))

    # 내부 필드 제거
    for a in all_articles:
        a.pop("_norm_title", None)

    output = {
        "generated_at": datetime.now(KST).isoformat(),
        "lookback_days": lookback_days,
        "companies": [c["name"] for c in companies_cfg],
        "total_articles": len(all_articles),
        "articles": all_articles,
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = os.path.join(DATA_DIR, "news.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n완료: 총 {len(all_articles)}건 -> {out_path}")


if __name__ == "__main__":
    main()
