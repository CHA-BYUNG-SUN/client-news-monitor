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
- SUMMARY_LOOKBACK_DAYS : AI 요약에 포함할 기사 기간(일 단위, 기본 20일)
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


def display_company_name(name):
    """AI 요약 프롬프트 등 사람/AI가 읽을 곳에 보여줄 '깨끗한' 회사명을 만든다.

    2026-08-25 추가: companies.json의 고객명은 "현대트랜시스(주)_ERP"처럼 내부 시스템
    표기("_ERP")나 법인 표기("(주)")가 그대로 붙어있는 경우가 많다. 이 원본 문자열을
    AI 요약 프롬프트에 그대로 넣으면, 실제 기사 제목/본문에는 당연히 "_ERP" 같은 표기가
    없기 때문에 AI가 "이 기사가 정말 이 고객사 얘기인가?"를 판단할 때 불필요하게 헷갈려
    할 수 있다. 검색어 정리(clean_query)와 비슷한 방식으로 내부 표기만 걷어내고, 사람이
    실제로 부르는 이름에 가깝게 만들어서 AI에게 넘긴다."""
    if not name:
        return name
    n = re.sub(r"_ERP$", "", name, flags=re.IGNORECASE)
    for token in ["(주)", "㈜", "(유)", "(재)", "(사)", "(합)", "주식회사", "유한회사"]:
        n = n.replace(token, "")
    n = re.sub(r"[\[\]\(\)]", "", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n or name


def company_title_hit(article, company_name):
    """이 기사의 '제목'에 해당 회사 자신의 이름(또는 매칭된 SUB고객명)이 직접 등장하는지 확인.

    2026-08-25 추가: AI 요약(generate_company_summaries)에서 어떤 기사를 상위 N건으로
    골라 Claude에게 넘길지 정할 때 쓰는 신호다. "회사명이 본문 한 구석에 다른 회사의
    협력사/고객사로 잠깐 언급된 기사"와 "이 회사 자신이 제목의 주인공인 기사"를 구분하려는
    목적으로, 후자를 우선한다(둘 다 화면의 "전체 기사 보기" 목록에는 그대로 다 나온다 -
    이 함수는 AI 요약 재료를 고를 때만 쓰인다)."""
    title = article.get("title", "")
    if not title:
        return False
    display = display_company_name(company_name)
    if display and display in title:
        return True
    for link in article.get("company_links", []):
        if link.get("name") == company_name:
            for sub_name in link.get("matched_sub_names", []):
                sub_display = display_company_name(sub_name)
                if sub_display and sub_display in title:
                    return True
            break
    return False


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
    # 2026-08-25 수정: 프롬프트에 넘기는 회사명은 "현대트랜시스(주)_ERP"같은 원본 표기 대신
    # display_company_name()으로 정리한 이름을 쓴다. "_ERP"/"(주)" 같은 내부 시스템 표기가
    # 실제 기사 본문에는 당연히 등장하지 않는데, 원본 표기를 그대로 프롬프트에 넣으면 AI가
    # "제목에 나온 이름이 정말 이 고객사를 가리키는 게 맞나?"를 판단하는 데 불필요한 혼란을
    # 겪을 수 있었다(예: 기사 제목에 분명히 "하이비젼시스템"이라고 나오는데도, 프롬프트에는
    # "(주)하이비젼시스템_ERP"라고 적혀 있어 같은 대상인지 애매하게 느껴져 "관련 기사 없음"으로
    # 잘못 판단하는 사례가 있었다).
    display_name = display_company_name(company_name)
    lines = []
    for a in articles:
        lines.append(
            f"- [{a['tag_label']}] {a['title']} ({a['press']}, {a['pubDate_display']}): {a['description']}"
        )
    articles_text = "\n".join(lines)
    return (
        f"다음은 '{display_name}'과 관련된 것으로 자동 수집된 최근 기사 목록입니다. "
        f"('{display_name}'라는 이름은 고객 관리 시스템에 등록된 회사명에서 '(주)', '_ERP' 같은 내부 표기만 "
        "정리한 이름이며, 실제 기사에는 이 이름 그대로, 혹은 일부만 등장할 수 있습니다.) "
        "다만 이 기사들은 고객명을 키워드로 검색해서 모은 것이라, 이름이 우연히 겹쳤을 뿐 실제로는 "
        f"이 고객사와 무관하거나 다른 회사/산업 위주로 다뤄진 기사가 섞여 있을 수 있습니다. "
        f"먼저 이 기사들 중 '{display_name}'의 실제 사업 활동(수주, 투자, 협업, 설비/자동화 도입, 신규 계약, "
        "공시, 인수합병(M&A), 투자 유치, 실적/매출 발표 등)과 직접 관련된 기사만 골라내세요. "
        f"제목의 주인공이 '{display_name}' 자신인 기사는 직접 관련된 기사로 적극 인정하고, "
        "이름만 겹쳤거나 다른 회사·산업이 중심인 기사(예: 다른 회사 기사에서 협력사/고객사로 잠깐 언급만 된 경우)는 무시하세요. "
        "그렇게 골라낸 기사만으로 한국어 2~3문장의 짧은 요약을 작성해 주세요. "
        "단 1건이라도 직접 관련된 기사가 있다면 그 기사만으로 요약을 작성해야 하며, 나머지 기사가 무관하다는 이유로 "
        "전체를 '소식 없음'으로 처리하면 안 됩니다. "
        "영업사원이 이 고객사를 방문하기 전에 빠르게 훑어볼 수 있는 요약이어야 합니다. "
        "직접 관련된 기사가 정말 하나도 없을 때만, 다른 말 없이 '최근 이 고객사와 직접 관련된 뚜렷한 소식은 없습니다.'라고만 답하세요. "
        "불필요한 서론이나 인사말 없이 바로 핵심 내용만 문장으로 작성하고, "
        "문장 앞에 번호나 불릿, 따옴표를 붙이지 마세요.\n\n"
        f"{articles_text}"
    )


def generate_company_summaries(all_articles, api_key, lookback_days=20, max_articles=5, request_delay=0.3):
    """최근 lookback_days일 이내 기사가 있는 고객사만 골라 Claude Haiku로 2~3문장 요약을 생성한다.
    all_articles는 이미 '우선순위 태그 -> 최신순'으로 정렬돼 있는데, 이 전역 순서만 그대로 잘라서
    상위 max_articles건을 쓰면 문제가 생길 수 있다: 예를 들어 "현대트랜시스" 같은 대기업 고객은
    "디에스엠이 현대트랜시스에 공급한다"처럼 '다른 회사가 주인공이고 이 고객사는 협력사로 잠깐
    언급'된 [투자] 태그 기사가 우선순위상 앞에 와서 상위 5건을 다 차지해버리고, 실제로 이 고객사
    "자신"이 제목의 주인공인 기사(예: "현대트랜시스·두산밥캣·LG엔솔 상생기금 출연...")는 정작
    6번째, 10번째... 뒤로 밀려서 AI 요약 재료에 아예 포함되지 못하는 경우가 있었다(2026-08-25
    발견 - 화면의 "전체 기사 보기"에는 정상적으로 다 나오는데, AI 요약만 "새 소식 없음"으로
    나오는 원인 중 하나).
    그래서 고객사별로 자르기 전에, company_title_hit()로 "이 회사 자신이 제목의 주인공인 기사"를
    앞으로 오도록 재정렬한 뒤 상위 max_articles건을 뽑는다(같은 우선순위 그룹 내에서는 기존
    순서 그대로 유지됨 - 정렬이 stable하기 때문).
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
        # 기사 하나가 여러 회사(같은 브랜드의 여러 사업장)에 동시에 관련된 경우,
        # 관련된 모든 회사의 요약 재료 목록에 포함시킨다.
        for company_name in a.get("companies", []):
            by_company.setdefault(company_name, []).append(a)

    summaries = {}
    total = len(by_company)
    print(f"\nAI 요약 생성 대상: {total}개 고객사 (최근 {lookback_days}일 이내 기사 보유)")
    for i, (company_name, articles) in enumerate(by_company.items(), start=1):
        ranked_articles = sorted(
            articles, key=lambda a: 0 if company_title_hit(a, company_name) else 1
        )
        top_articles = ranked_articles[:max_articles]
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
    summary_lookback_days = int(os.environ.get("SUMMARY_LOOKBACK_DAYS", "20"))
    # 2026-08-25 수정: 기존 5건에서 8건으로 상향. company_title_hit() 재정렬로 "회사 자신이
    # 주인공인 기사"가 앞으로 오게 됐지만, 그런 기사가 여러 건 있는 고객사(예: 보도자료가
    # 여러 언론사에 살짝 다른 제목으로 실린 경우)는 5건보다 조금 더 여유를 줘야 AI가 판단할
    # 재료가 충분해진다. Haiku 비용은 미미하게 늘어나는 수준이라 큰 부담은 없다.
    summary_max_articles = int(os.environ.get("SUMMARY_MAX_ARTICLES", "8"))
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

            # 2026-08-25 수정: 전역 "증시노이즈 제외" 목록(코스피/코스닥/주가 등)은 원래
            # 회사명만 겹치고 내용은 전혀 무관한 증권사 리포트성 잡음을 걸러내기 위한 것이었다.
            # 그런데 company_overrides.json에 context_keywords(업종 관련 키워드)를 설정해둔
            # 회사는, 이미 그 키워드로 한 번 더 관련성을 확인했기 때문에 성격이 다르다 — 예를
            # 들어 "성우하이텍, 상반기 영업익 1196억...코스닥 10위"처럼 정상적인 실적 발표
            # 기사인데 제목에 "코스닥"이 들어갔다는 이유만으로 걸러지는 문제가 있었다.
            # 그래서 context_keywords가 설정된 회사는 전역 증시노이즈 제외를 적용하지 않는다
            # (형님 확인 완료, 2026-08-25).
            if not context_keywords and contains_any_keyword(combined_text, global_exclude_keywords):
                skipped_stock += 1
                continue

            link = item.get("link", "")
            originallink = item.get("originallink", "") or link
            domain = get_domain(originallink) or get_domain(link)
            press_name, is_major = resolve_media(domain, media_cfg)
            tag_key, tag_label, tag_color = resolve_tag(title, description, keyword_cfg)
            matched_sub_names = find_matched_sub_names(title, description, sub_names)

            article = {
                # 2026-08-24 변경: 예전에는 "company"(단일 문자열)였는데, 같은 브랜드의 여러
                # 사업장(예: 삼성전자 생산기술연구소/구미공장/광주공장)이 고객 마스터에 각각
                # 등록돼 있고 검색어가 겹치는 경우, 아래 전역 중복 제거 로직을 통과할 때
                # 나중에 처리되는 사업장이 관련 기사를 하나도 못 가져가는 문제가 있었다.
                # "companies"(배열)로 바꾸고, 중복으로 판정된 기사는 버리지 않고 관련된
                # 모든 회사/팀/셀/담당자를 이 배열에 계속 합쳐서 모든 관련 사업장에 표시되게 한다.
                #
                # 2026-08-25 추가 수정: 위 방식대로 team/cell/reps를 그냥 하나의 배열에 계속
                # 합치기만 하면, "이 기사에 관련된 회사가 누구인지"는 알 수 있어도 "그중 어느
                # 회사가 어느 팀/셀/담당자 소속인지"라는 연결 정보가 사라진다. 예를 들어 배터리
                # 장비업체 6곳을 한꺼번에 언급하는 산업 동향 기사 하나가 (주)나인테크(TSC3·이인규)
                # 와 (주)엠오티(TSC4·김훈)에 동시에 걸리면, team=["TSC3","TSC4"],
                # reps=["이인규","김훈"]처럼 통째로 섞여버려서 "팀 필터로 TSC4를 고르면 관련
                # 없는 이인규까지 영업명 목록에 나오는" 문제, "김훈을 검색하면 김훈과 무관한
                # 나인테크까지 담당 고객 목록에 나오는" 문제가 생겼다. 이를 막기 위해 회사별
                # 연결 정보를 "company_links" 배열에 별도로 보존한다: 각 원소가
                # {name, team, cell, reps, matched_sub_names}로, 어느 회사가 어느 팀/셀/
                # 담당자에 속하는지 끝까지 유지한다. 화면에서 정확한 필터링·검색·고객사별 팝업은
                # 이 company_links를 기준으로 하고, 아래 team/cell/reps/matched_sub_names(전체
                # 배열)는 피드 카드에 보여주는 "요약 정보"용으로만 남겨둔다.
                "companies": [name],
                "company_links": [{
                    "name": name,
                    "matched_sub_names": list(matched_sub_names),
                    "team": list(team),
                    "cell": list(cell),
                    "reps": list(reps),
                }],
                "matched_sub_names": list(matched_sub_names),
                "team": list(team),
                "cell": list(cell),
                "reps": list(reps),
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
                # 같은 기사가 다른 회사(주로 같은 브랜드의 다른 사업장, 또는 여러 고객사가
                # 함께 언급된 산업 동향 기사)에서도 검색된 경우, 그냥 버리지 않고 이 회사를
                # company_links에 별도 항목으로 추가한다(회사별 팀/셀/담당자 연결 유지).
                # 화면에 보여줄 "요약용" team/cell/reps/matched_sub_names 배열에도 합쳐 두지만,
                # 실제 필터링·검색·고객사 팝업은 company_links를 기준으로 동작한다.
                existing_link = None
                for link in dup["company_links"]:
                    if link["name"] == name:
                        existing_link = link
                        break
                if existing_link:
                    for sn in matched_sub_names:
                        if sn not in existing_link["matched_sub_names"]:
                            existing_link["matched_sub_names"].append(sn)
                    for t in team:
                        if t not in existing_link["team"]:
                            existing_link["team"].append(t)
                    for c in cell:
                        if c not in existing_link["cell"]:
                            existing_link["cell"].append(c)
                    for r in reps:
                        if r not in existing_link["reps"]:
                            existing_link["reps"].append(r)
                else:
                    dup["company_links"].append({
                        "name": name,
                        "matched_sub_names": list(matched_sub_names),
                        "team": list(team),
                        "cell": list(cell),
                        "reps": list(reps),
                    })
                if name not in dup["companies"]:
                    dup["companies"].append(name)
                for t in team:
                    if t not in dup["team"]:
                        dup["team"].append(t)
                for c in cell:
                    if c not in dup["cell"]:
                        dup["cell"].append(c)
                for r in reps:
                    if r not in dup["reps"]:
                        dup["reps"].append(r)
                for sn in matched_sub_names:
                    if sn not in dup["matched_sub_names"]:
                        dup["matched_sub_names"].append(sn)
                # 기존 기사가 비메이저이고 새 기사가 메이저 언론사면 본문/링크/언론사 정보만
                # 메이저 기사로 교체하되, 지금까지 합쳐둔 회사/팀/셀/담당자 정보는 유지한다.
                if is_major and not dup["major"]:
                    article["companies"] = dup["companies"]
                    article["company_links"] = dup["company_links"]
                    article["team"] = dup["team"]
                    article["cell"] = dup["cell"]
                    article["reps"] = dup["reps"]
                    article["matched_sub_names"] = dup["matched_sub_names"]
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
