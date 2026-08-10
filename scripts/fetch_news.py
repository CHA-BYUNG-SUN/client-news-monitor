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
7. 고객명이 너무 짧거나 흔한 단어(예: '온', '메이저')라서 오탐이 많이 나는 회사는
   config/company_overrides.json 에 등록해 다음처럼 강화할 수 있음:
   - search_queries: 기본 정리된 이름 대신 더 구체적인 별칭 여러 개로 검색/매칭
   - context_keywords: 이 키워드 중 하나가 함께 있어야만 채택 (업종 관련성 확인)
   - exclude_keywords: 이 키워드 중 하나라도 있으면 무조건 제외 (엉뚱한 동음이의어 기사 배제, 회사별)
8. config/global_exclude_keywords.json 에 등록된 키워드가 제목/본문에 하나라도 있으면
   고객사와 상관없이 전체 공통으로 기사를 제외함 (예: 주가/증시 등 증권사 리포트성 노이즈 기사)
9. data/news.json 으로 결과 저장 (GitHub Pages 정적 사이트에서 fetch 하여 사용)

환경변수
- NAVER_CLIENT_ID, NAVER_CLIENT_SECRET : 네이버 개발자센터에서 발급받은 값 (필수)
- NEWS_LOOKBACK_DAYS : 며칠 이내 기사만 남길지 (기본 7일)
- NEWS_DISPLAY_PER_QUERY : 고객사별 1회 호출 시 가져올 기사 수 (기본 100, 최대 100)
- NEWS_REQUEST_DELAY : 고객사별 API 호출 간 대기 시간(초, 기본 0.2) - 고객사 수가 많을 때 조정
- ANTHROPIC_API_KEY : Anthropic Claude API 키 (선택). 설정하면 고객사별 "최근 N일 AI 요약"을
  함께 생성해 data/news.json 의 company_summaries 필드에 저장한다. 설정하지 않으면 이 단계는
  건너뛰고(company_summaries 필드 자체가 생성되지 않음) 기존처럼 기사 목록만 저장한다.
- SUMMARY_LOOKBACK_DAYS : AI 요약에 포함할 기사 기간(일 단위, 기본 5일)
- SUMMARY_MAX_ARTICLES : 고객사 1건 요약 생성 시 참고할 최대 기사 수(기본 5건)
- SUMMARY_REQUEST_DELAY : 고객사별 Claude API 호출 간 대기 시간(초, 기본 0.3)
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
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-haiku-4-5"

KST = timezone(timedelta(hours=9))


def load_json(filename):
    path = os.path.join(CONFIG_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_overrides():
    """고객사별 검색 별칭/업종 키워드 예외 설정을 불러온다.
    파일이 없으면 빈 딕셔너리를 반환한다 (필수 아님)."""
    path = os.path.join(CONFIG_DIR, "company_overrides.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data.pop("_comment", None)
    return data


def load_global_exclude_keywords():
    """모든 고객사에 공통으로 적용되는 제외 키워드를 불러온다.
    (예: 증시/주가 관련 증권사 리포트성 노이즈 기사 필터링)
    파일이 없으면 빈 리스트를 반환한다 (필수 아님)."""
    path = os.path.join(CONFIG_DIR, "global_exclude_keywords.json")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("keywords", [])


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
    """검색어(고객명/별칭)가 기사에서 실질적으로 다뤄지는 기사인지 확인.

    네이버 뉴스 검색은 기사 원문 전체를 기준으로 검색어를 찾아 결과에 포함시키기 때문에,
    API가 돌려주는 제목/요약(description) 스니펫에는 정작 검색어가 전혀 등장하지 않는
    경우가 많다 (원문 뒷부분에만 있거나, 아예 다른 회사 위주 기사에 살짝 언급된 경우 등).
    이런 경우 화면에는 '고객명' 뱃지만 붙고 실제 내용은 전혀 다른 회사 얘기로 보이게 된다.

    1) 제목/본문 어디에도 검색어가 글자 그대로 없으면 무관한 기사로 보고 제외한다.
    2) 제목에 검색어가 있으면 확실한 관련 기사로 간주한다.
    3) 본문에만 있으면, 검색어 주변(전후 20자)에 쉼표/가운뎃점 등 나열 구분자가
       2개 이상 있는 경우 '여러 회사가 나열된 기사 중 하나로 언급'된 것으로 보고 제외한다.
    """
    if not query:
        return True
    q = query.strip().lower()
    if not q:
        return True

    title = title or ""
    desc = description or ""
    title_l = title.lower()
    desc_l = desc.lower()

    if q not in title_l and q not in desc_l:
        # 요약문 어디에도 고객명이 없다 -> 다른 회사 위주 기사일 가능성이 높으므로 제외
        return False

    if q in title_l:
        return True

    idx = desc_l.find(q)
    start = max(0, idx - 20)
    end = min(len(desc), idx + len(q) + 20)
    window = desc[start:end]
    separator_count = sum(window.count(sep) for sep in LIST_SEPARATORS)
    if separator_count >= 2:
        return False
    return True


def contains_any_keyword(text, keywords):
    """keywords 중 하나라도 text에 포함되어 있으면 True.
    keywords가 없으면(설정 안 함) 항상 False를 반환한다 (검사 자체를 생략)."""
    if not keywords:
        return False
    text_l = (text or "").lower()
    return any(kw.lower() in text_l for kw in keywords)


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


def call_anthropic_summary(prompt, api_key, retries=3):
    """Claude Haiku에게 요약 문장 생성을 요청한다. 실패하면 None을 반환한다
    (이 고객사는 이번 회차에 요약 없이 건너뛰고, 기존 기사 목록은 그대로 저장된다)."""
    body = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": 300,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urlrequest.Request(ANTHROPIC_API_URL, data=body, method="POST")
    req.add_header("x-api-key", api_key)
    req.add_header("anthropic-version", "2023-06-01")
    req.add_header("content-type", "application/json")

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            with urlrequest.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                parts = data.get("content", [])
                text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
                text = text.strip()
                return text or None
        except urlerror.HTTPError as e:
            last_err = e
            # 429(요청 과다) 등은 조금 더 여유있게 기다린 뒤 재시도
            wait = 3.0 * attempt if e.code == 429 else 1.5 * attempt
            print(f"    [경고] Claude 요약 호출 실패 (시도 {attempt}/{retries}): HTTP {e.code}", file=sys.stderr)
            time.sleep(wait)
        except Exception as e:
            last_err = e
            print(f"    [경고] Claude 요약 호출 실패 (시도 {attempt}/{retries}): {e}", file=sys.stderr)
            time.sleep(1.5 * attempt)
    print(f"    [오류] Claude 요약 실패, 이 고객사는 이번 회차에 요약 없이 건너뜀: {last_err}", file=sys.stderr)
    return None


def build_summary_prompt(company_name, articles):
    lines = []
    for a in articles:
        lines.append(
            f"- [{a['tag_label']}] {a['title']} ({a['press']}, {a['pubDate_display']}): {a['description']}"
        )
    articles_text = "\n".join(lines)
    return (
        f"다음은 '{company_name}'과 관련된 것으로 자동 수집된 최근 기사 목록입니다. "
        "다만 이 기사들은 고객명을 키워드로 검색해서 모은 것이라, 이름이 우연히 겹쳤을 뿐 실제로는 "
        f"이 고객사와 무관하거나 다른 회사/산업 위주로 다뤄진 기사가 섞여 있을 수 있습니다. "
        f"먼저 이 기사들 중 '{company_name}'의 실제 사업 활동(수주, 투자, 협업, 설비/자동화 도입, 신규 계약 등)과 "
        "직접 관련된 기사만 골라내고, 이름만 겹쳤거나 다른 회사·산업이 중심인 기사는 무시하세요. "
        "그렇게 골라낸 기사만으로 한국어 2~3문장의 짧은 요약을 작성해 주세요. "
        "영업사원이 이 고객사를 방문하기 전에 빠르게 훑어볼 수 있는 요약이어야 합니다. "
        "직접 관련된 기사가 하나도 없다면 다른 말 없이 '최근 이 고객사와 직접 관련된 뚜렷한 소식은 없습니다.'라고만 답하세요. "
        "불필요한 서론이나 인사말 없이 바로 핵심 내용만 문장으로 작성하고, "
        "문장 앞에 번호나 불릿, 따옴표를 붙이지 마세요.\n\n"
        f"{articles_text}"
    )


def generate_company_summaries(all_articles, api_key, lookback_days=5, max_articles=5, request_delay=0.3):
    """최근 lookback_days일 이내 기사가 있는 고객사만 골라 Claude Haiku로 2~3문장 요약을 생성한다.
    all_articles는 이미 '우선순위 태그 -> 최신순'으로 정렬돼 있으므로, 고객사별로 그 순서 그대로
    상위 max_articles건만 골라 요약 재료로 쓴다.
    반환값은 {고객사명: {"summary": str, "based_on": int, "generated_at": iso}} 형태이며,
    요약 생성에 실패한 고객사는 이 딕셔너리에 포함되지 않는다(웹사이트에서는 "새 소식 없음"과
    구분하기 위해, 이 함수를 호출한 것 자체는 output에 company_summaries 키를 남겨 구분한다)."""
    cutoff = datetime.now(KST) - timedelta(days=lookback_days)
    by_company = {}
    for a in all_articles:
        try:
            pub_dt = datetime.fromisoformat(a["pubDate_iso"])
        except Exception:
            continue
        if pub_dt < cutoff:
            continue
        by_company.setdefault(a["company"], []).append(a)

    summaries = {}
    total = len(by_company)
    print(f"\nAI 요약 생성 대상: {total}개 고객사 (최근 {lookback_days}일 이내 기사 보유)")
    for i, (company_name, articles) in enumerate(by_company.items(), start=1):
        top_articles = articles[:max_articles]
        prompt = build_summary_prompt(company_name, top_articles)
        print(f"  [{i}/{total}] 요약 생성: {company_name} ({len(top_articles)}건 기준)")
        summary_text = call_anthropic_summary(prompt, api_key)
        if summary_text:
            summaries[company_name] = {
                "summary": summary_text,
                "based_on": len(top_articles),
                "generated_at": datetime.now(KST).isoformat(),
            }
        time.sleep(request_delay)
    print(f"AI 요약 생성 완료: {len(summaries)}/{total}개 고객사 성공")
    return summaries


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

    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")
    summary_lookback_days = int(os.environ.get("SUMMARY_LOOKBACK_DAYS", "5"))
    summary_max_articles = int(os.environ.get("SUMMARY_MAX_ARTICLES", "5"))
    summary_request_delay = float(os.environ.get("SUMMARY_REQUEST_DELAY", "0.3"))

    companies_cfg = load_json("companies.json")["companies"]
    media_cfg = load_json("major_media.json")
    keyword_cfg = load_json("priority_keywords.json")
    overrides_cfg = load_overrides()
    global_exclude_keywords = load_global_exclude_keywords()
    priority_order = keyword_cfg.get("priority_order", [])

    cutoff = datetime.now(KST) - timedelta(days=lookback_days)

    all_articles = []
    seen_articles = []  # 전역 중복 체크용 (링크/유사 제목)

    total = len(companies_cfg)
    for i, company in enumerate(companies_cfg, start=1):
        name = company["name"]
        query = company.get("query", name)
        code = company.get("code", "")
        sub_names = company.get("sub_names", [])
        team = company.get("team", [])
        cell = company.get("cell", [])
        ext_rep = company.get("ext_rep", [])
        # 영업담당(reps) 표시/필터는 외근영업만 사용한다.
        # 내근영업까지 합치면 같은 고객의 기사가 영업명 필터상 여러 이름으로 흩어져 보이는 문제가 있어 제외한다.
        reps = sorted(set([r for r in ext_rep if r]))

        override = overrides_cfg.get(code)
        if override and override.get("search_queries"):
            search_queries = override["search_queries"]
        else:
            search_queries = [query]
        context_keywords = override.get("context_keywords") if override else None
        exclude_keywords = override.get("exclude_keywords") if override else None

        print(f"[{i}/{total}] 수집: {name} ({' / '.join(search_queries)})")

        items_by_key = {}
        for sq in search_queries:
            for item in fetch_company_news(sq, client_id, client_secret, display=display):
                key = item.get("link") or item.get("originallink") or item.get("title")
                if key and key not in items_by_key:
                    items_by_key[key] = item
            time.sleep(request_delay)
        items = list(items_by_key.values())

        kept, skipped_old, skipped_dup, skipped_irrelevant, skipped_stock = 0, 0, 0, 0, 0
        for item in items:
            pub_dt = parse_pubdate(item.get("pubDate", ""))
            if pub_dt is None:
                continue
            if pub_dt < cutoff:
                skipped_old += 1
                continue

            title = clean_text(item.get("title", ""))
            description = clean_text(item.get("description", ""))
            combined_text = f"{title} {description}"

            if not any(is_relevant_article(title, description, sq) for sq in search_queries):
                skipped_irrelevant += 1
                continue

            if context_keywords and not contains_any_keyword(combined_text, context_keywords):
                skipped_irrelevant += 1
                continue

            if exclude_keywords and contains_any_keyword(combined_text, exclude_keywords):
                skipped_irrelevant += 1
                continue

            if contains_any_keyword(combined_text, global_exclude_keywords):
                skipped_stock += 1
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

        print(f"       수집 {len(items)} / 채택 {kept} / 중복제외 {skipped_dup} / 기간외제외 {skipped_old} / 나열형제외 {skipped_irrelevant} / 증시노이즈제외 {skipped_stock}")

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

    if anthropic_api_key:
        company_summaries = generate_company_summaries(
            all_articles,
            anthropic_api_key,
            lookback_days=summary_lookback_days,
            max_articles=summary_max_articles,
            request_delay=summary_request_delay,
        )
        output["summary_lookback_days"] = summary_lookback_days
        output["company_summaries"] = company_summaries
    else:
        print(
            "\n[안내] ANTHROPIC_API_KEY가 설정되어 있지 않아 고객사별 AI 요약은 생성하지 않습니다. "
            "(웹사이트는 이 경우 자동으로 기존 기사 목록 방식으로 표시합니다)",
            file=sys.stderr,
        )

    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = os.path.join(DATA_DIR, "news.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n완료: 총 {len(all_articles)}건 -> {out_path}")


if __name__ == "__main__":
    main()
