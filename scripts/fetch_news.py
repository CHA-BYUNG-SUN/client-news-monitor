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
   - match_names: 기사 제목/요약에 이 이름 중 하나가 있어야 채택 (없으면 search_queries 사용)
   - strict_name: true 면 이름 앞뒤가 단어 경계일 때만 인정 (짧은 이름 오탐 방지)
   - context_window: 숫자(글자 수). 이름이 요약에만 있을 때 context_keywords 가 그 이름 근처에 있어야 인정
8. config/global_exclude_keywords.json 에 등록된 키워드가 제목/본문에 하나라도 있으면
   고객사와 상관없이 전체 공통으로 기사를 제외함 (예: 주가/증시 등 증권사 리포트성 노이즈 기사)
9. (2026-11-23) 매칭 품질 자동 판정(scripts/match_quality.py) - 예외 설정이 없는 회사도 자동 적용
   - 모든 회사: 이름 앞뒤 단어 경계(조사/법인표기/그룹 등만 허용) -> 다른 단어 속 글자로 잡힌 기사 제외
   - 위험 이름(한글 2자 이하, 영문 3자 이하): 법인표기/주체표현/이름 근처 업종 키워드 중 하나 필요
   - 이번 수집분에서 이름이 대부분 일반 단어/다른 실체(법무법인 등)로 쓰인 회사는 '일반명사 모드'로 자동 전환
   - 근거가 약한 기사는 버리지 않고 news.json 의 uncertain_articles(보류함)에 따로 저장 (화면/AI 요약에는 미사용)
   - 보류함 기사는 ANTHROPIC_API_KEY 가 있으면 Claude Haiku 에게 "같은 회사 기사인가"를 물어
     true 면 일반 기사로 올리고(judged_by="ai"), false 면 이유와 함께 보류함에 남긴다.
     (AI_JUDGE_MAX 기본 100건/회, AI_JUDGE_BATCH 기본 10건/호출, 오류 시 그대로 보류)
   - 끄기: 환경변수 MATCH_QUALITY=off (AI 판정만 끄기: AI_JUDGE=off)
10. (2026-11-24) 놓친 기사 줄이기 1단계
   - 누적 보관(store/archive.json, ARCHIVE_DAYS 기본 180일): 매일 채택 기사를 쌓고, 화면은 사업 사건 BIZ_DAYS(90일) / 일반 NEWS_LOOKBACK_DAYS(20일)
     (20일보다 오래된 기사는 제목/요약에 사업 단어가 있을 때만 보관 -> 사업 사건으로 판정되면 90일까지 보임)
   - 페이지 넘김: 첫 페이지 100건이 전부 기준 시각 안이면 start=101, 201 ... 901 까지(최대 NEWS_MAX_PAGES=10)
     기준 시각 = 처음엔 NEWS_LOOKBACK_DAYS 전, 이후엔 마지막 수집 시각 - 2일(store/fetch_state.json)
   - 사건 분류(scripts/event_classify.py, Haiku): 같은 회사/역할/사건 종류/사건 날짜/근거 인용, 새 회사-기사 쌍만(캐시)
     사업 사건은 위로, 곁가지/주가는 접어서, 같은 사건은 묶어서(scripts/news_view.py)
   - 증시어(global_exclude_keywords)는 분류가 켜져 있으면 삭제 대신 신호(stock=true)로만 씀. 분류가 꺼지면 예전처럼 제외
   - 제외 사유별 건수 + 표본(스니펫 이름 없음/나열형)을 store/filter_log.json 에 저장 (화면 영향 없음)
   - 끄기: NEWS_STORE=off (보관/분류/90일 전부 끄고 예전 방식), EVENT_CLASSIFY=off (분류만), NEWS_PAGING=off (페이지 넘김만)
   - DART 공시(scripts/dart_source.py, config/dart_corp_map.json): 공급계약/신규시설투자/유형자산 취득/타법인주식 취득을 사업 소식으로,
     화면에 "DART" 스티커. 같은 사건 뉴스와 "같은 소식"으로 묶음. 끄기 DART_SOURCE=off, 키 DART_API_KEY 없으면 건너뜀
11. data/news.json 으로 결과 저장 (GitHub Pages 정적 사이트에서 fetch 하여 사용)

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
import hashlib
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from urllib import request as urlrequest
from urllib import error as urlerror

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import match_quality as mq  # noqa: E402
import news_store as ns  # noqa: E402
import news_view as nv  # noqa: E402
import event_classify as ec  # noqa: E402
import dart_source as ds  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
DATA_DIR = os.path.join(BASE_DIR, "data")
STORE_DIR = os.path.join(BASE_DIR, "store")

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


def company_title_hit(article, code, company_name):
    """이 기사의 '제목'에 해당 회사 자신의 이름(또는 매칭된 SUB고객명)이 직접 등장하는지 확인.

    2026-08-25 추가: AI 요약(generate_company_summaries)에서 어떤 기사를 상위 N건으로
    골라 Claude에게 넘길지 정할 때 쓰는 신호다. "회사명이 본문 한 구석에 다른 회사의
    협력사/고객사로 잠깐 언급된 기사"와 "이 회사 자신이 제목의 주인공인 기사"를 구분하려는
    목적으로, 후자를 우선한다(둘 다 화면의 "전체 기사 보기" 목록에는 그대로 다 나온다 -
    이 함수는 AI 요약 재료를 고를 때만 쓰인다).

    2026-09-07 수정: company_links 매칭 기준을 이름(name)에서 고유 코드(code)로 변경.
    companies.json에는 이름은 완전히 같은데 실제로는 서로 다른 회사인 경우가 5쌍
    있는데(예: 제닉스(주) - 코드 981241(TSC2·김혁)과 코드 955240(TSC3·이인규)), 이름으로
    매칭하면 두 회사의 matched_sub_names가 뒤섞여 엉뚱한 회사 판단에 영향을 줄 수 있었다."""
    title = article.get("title", "")
    if not title:
        return False
    display = display_company_name(company_name)
    if display and display in title:
        return True
    for link in article.get("company_links", []):
        if link.get("code") == code:
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


# 2026-09-29 추가: 짧은 고객명(열성/창성/노바/일진 등)이 다른 단어 속 글자로 잡히는 문제 대응.
# strict_name 이 켜진 회사는 이름 앞뒤가 "단어 경계"일 때만 이름이 나온 것으로 본다.
#   - 앞: 한글/영문/숫자가 바로 붙어 있으면 안 됨 (예: 발열성, 하나금융티아이 -> 제외)
#   - 뒤: 영문/숫자가 붙으면 안 되고, 한글이 붙으면 조사일 때만 허용
#         (예: 테스는/창성의 -> 인정, 테스트/동도금/넥사다이내믹스 -> 제외)
_JOSA = ("으로", "에서", "에게", "까지", "부터", "은", "는", "이", "가", "을", "를", "의",
         "에", "와", "과", "도", "로", "만", "측")
_HANGUL = re.compile(r"[가-힣]")
_WORDCH = re.compile(r"[가-힣A-Za-z0-9]")


def find_name(text_l, q, strict=False):
    """text_l(소문자) 안에서 q가 처음 '제대로' 등장하는 위치. 없으면 -1."""
    start = 0
    while True:
        idx = text_l.find(q, start)
        if idx < 0 or not strict:
            return idx
        prev = text_l[idx - 1] if idx > 0 else ""
        rest = text_l[idx + len(q):]
        nxt = rest[:1]
        ok_prev = not (prev and _WORDCH.match(prev))
        if not nxt or not _WORDCH.match(nxt):
            ok_next = True
        elif _HANGUL.match(nxt):
            ok_next = rest.startswith(_JOSA)
        else:
            ok_next = False
        if ok_prev and ok_next:
            return idx
        start = idx + 1


def context_near_name(title, description, names, strict, keywords, window):
    """2026-09-29 추가(context_window 옵션). 이름이 제목에 있으면 제목+요약 어디든 키워드가
    있으면 통과. 이름이 요약에만 있으면, 이름 앞뒤 window 글자 안에 키워드가 있어야 통과.
    (예: 요약 끝에 '열성 팬'이 스치듯 나오고 앞쪽에 '공장'이 있는 기사 -> 제외)"""
    t_l, d_l = (title or "").lower(), (description or "").lower()
    kws = [k.lower() for k in keywords]
    for nm in names:
        q = nm.strip().lower()
        if q and find_name(t_l, q, strict) >= 0:
            return any(k in t_l or k in d_l for k in kws)
    for nm in names:
        q = nm.strip().lower()
        start = 0
        while q:
            idx = find_name(d_l[start:], q, strict)
            if idx < 0:
                break
            idx += start
            seg = d_l[max(0, idx - window): idx + len(q) + window]
            if any(k in seg for k in kws):
                return True
            start = idx + 1
    return False


def relevance_reason(title, description, query, strict=False):
    """is_relevant_article 와 같은 판정을 하되 이유를 돌려준다: 'ok' / 'no_name'(제목/요약에 이름 없음) / 'list'(나열형)"""
    if not query or not query.strip():
        return "ok"
    q = query.strip().lower()
    title_l = (title or "").lower()
    desc = description or ""
    desc_l = desc.lower()
    t_idx = find_name(title_l, q, strict)
    idx = find_name(desc_l, q, strict)
    if t_idx < 0 and idx < 0:
        return "no_name"
    if t_idx >= 0:
        return "ok"
    window = desc[max(0, idx - 20):min(len(desc), idx + len(q) + 20)]
    if sum(window.count(sep) for sep in LIST_SEPARATORS) >= 2:
        return "list"
    return "ok"


def is_relevant_article(title, description, query, strict=False):
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

    t_idx = find_name(title_l, q, strict)
    idx = find_name(desc_l, q, strict)
    if t_idx < 0 and idx < 0:
        # 요약문 어디에도 고객명이 없다 -> 다른 회사 위주 기사일 가능성이 높으므로 제외
        return False

    if t_idx >= 0:
        return True

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


def fetch_company_news(company_query, client_id, client_secret, display=100, retries=3, start=1):
    params = f"query={urlrequest.quote(company_query)}&display={display}&start={start}&sort=date"
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


class DupIndex:
    """is_duplicate 와 같은 판정(같은 링크 또는 같은 날짜+제목 유사도)을 링크/날짜 색인으로 빠르게.
    (2026-11-24 수집 기간이 길어져 기사 수가 늘어도 느려지지 않게)"""

    def __init__(self, threshold=0.82):
        self.threshold = threshold
        self.by_link = {}
        self.by_date = {}

    def find(self, article):
        ol = article.get("originallink")
        if ol and ol in self.by_link:
            return self.by_link[ol]
        if article.get("link") in self.by_link:
            return self.by_link[article["link"]]
        for ex in self.by_date.get(article.get("pubDate_iso", "")[:10], []):
            if difflib.SequenceMatcher(None, article["_norm_title"], ex["_norm_title"]).ratio() >= self.threshold:
                return ex
        return None

    def add(self, article):
        for k in (article.get("originallink"), article.get("link")):
            if k:
                self.by_link.setdefault(k, article)
        self.by_date.setdefault(article.get("pubDate_iso", "")[:10], []).append(article)

    def remove(self, article):
        for k in (article.get("originallink"), article.get("link")):
            if k and self.by_link.get(k) is article:
                del self.by_link[k]
        lst = self.by_date.get(article.get("pubDate_iso", "")[:10], [])
        if article in lst:
            lst.remove(article)


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


AI_JUDGE_MODEL = ANTHROPIC_MODEL  # 요약과 같은 모델(Haiku)


def call_anthropic_text(prompt, api_key, max_tokens=800, timeout=30):
    """Claude 호출 1회(재시도 없음). 실패하면 None. 보류함 AI 판정용."""
    body = json.dumps({
        "model": AI_JUDGE_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urlrequest.Request(ANTHROPIC_API_URL, data=body, method="POST")
    req.add_header("x-api-key", api_key)
    req.add_header("anthropic-version", "2023-06-01")
    req.add_header("content-type", "application/json")
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        parts = data.get("content", [])
        return "".join(p.get("text", "") for p in parts if p.get("type") == "text").strip() or None
    except Exception as e:
        print(f"    [경고] 보류함 AI 판정 호출 실패: {e}", file=sys.stderr)
        return None


def build_ai_judge_prompt(batch):
    """batch: [(id, 보류기사)] -> 엄격한 JSON 배열만 답하게 하는 프롬프트"""
    lines = []
    for i, u in batch:
        inds = "/".join(x for x in u.get("industry", []) if x) or "미상"
        lines.append(json.dumps({
            "id": i,
            "company": display_company_name(u.get("name", "")),
            "industry": inds,
            "title": u.get("title", ""),
            "summary": u.get("description", ""),
        }, ensure_ascii=False))
    return (
        "아래 각 줄은 거래처(회사)명과 업종, 그리고 그 회사명으로 검색된 뉴스 제목/요약입니다. "
        "각 기사가 '바로 그 회사(해당 업종의 제조 관련 기업)'에 대한 기사인지 판정하세요. "
        "회사명이 일반 단어(예: 동사/명사)로 쓰였거나, 이름만 같은 다른 회사/로펌/해외 업체/제품/인물이면 false 입니다. "
        "판단 근거가 부족하면 false 로 답하세요.\n"
        "반드시 JSON 배열만 출력하고 다른 말은 쓰지 마세요. 형식: "
        '[{"id": 숫자, "same_company": true 또는 false, "reason": "20자 이내"}]\n\n'
        + "\n".join(lines)
    )


def parse_ai_judge(text, valid_ids):
    """Haiku 응답에서 {id: (bool, reason)} 를 꺼낸다. 형식이 틀리면 빈 dict(=전부 보류 유지)."""
    if not text:
        return {}
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    a, b = t.find("["), t.rfind("]")
    if a < 0 or b <= a:
        return {}
    try:
        data = json.loads(t[a:b + 1])
    except Exception:
        return {}
    out = {}
    if not isinstance(data, list):
        return {}
    for d in data:
        if not isinstance(d, dict):
            continue
        i, same = d.get("id"), d.get("same_company")
        if isinstance(i, bool) or not isinstance(i, int) or i not in valid_ids or not isinstance(same, bool):
            continue
        out[i] = (same, str(d.get("reason", ""))[:20])
    return out


def ai_judge_uncertain(uncertain, api_key, max_articles=100, batch_size=10, call=None):
    """보류함 기사를 Haiku 로 판정. 반환: (승격할 [(기사, 이유)], 통계). 오류는 모두 보류 유지."""
    call = call or call_anthropic_text
    stats = {"asked": 0, "promoted": 0, "kept": 0, "failed": 0, "skipped_cap": 0}
    promoted = []
    targets = uncertain[:max_articles]
    stats["skipped_cap"] = max(0, len(uncertain) - len(targets))
    for start in range(0, len(targets), batch_size):
        chunk = list(enumerate(targets[start:start + batch_size], start=start))
        stats["asked"] += len(chunk)
        try:
            res = parse_ai_judge(call(build_ai_judge_prompt(chunk), api_key), {i for i, _ in chunk})
        except Exception as e:  # 어떤 오류도 실행 전체를 멈추지 않는다
            print(f"    [경고] 보류함 AI 판정 처리 오류: {e}", file=sys.stderr)
            res = {}
        for i, u in chunk:
            if i not in res:
                stats["failed"] += 1
                continue
            same, reason = res[i]
            u["ai_reason"] = reason
            if same:
                promoted.append((u, reason))
                stats["promoted"] += 1
            else:
                stats["kept"] += 1
    return promoted, stats


def build_summary_prompt(company_name, articles, industry=None):
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
    # 2026-11-23 추가: 업종을 함께 알려 주어, 이름만 같은 다른 업종 기사(금 ETF, 로펌, 스포츠 등)를 AI가 더 잘 걸러내게 한다.
    industry_hint = ""
    inds = [i for i in (industry or []) if i and i not in ("미분류", "기타")]
    if inds:
        industry_hint = (f"참고로 '{display_name}'은(는) {'/'.join(inds)} 업종의 제조 관련 거래처입니다. "
                         "이 업종과 무관한 기사(예: 금융시장 전망, 법률사무소, 스포츠, 연예, 일반 명사로 쓰인 경우)는 "
                         "이름이 같아도 무시하세요. ")
    return (
        industry_hint +
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


def generate_company_summaries(all_articles, api_key, lookback_days=20, max_articles=5, request_delay=0.3,
                               industries=None, material=None, prev=None):
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
    반환값은 {고객코드: {"summary": str, "based_on": int, "generated_at": iso}} 형태이며,
    요약 생성에 실패한 고객사는 이 딕셔너리에 포함되지 않는다(웹사이트에서는 "새 소식 없음"과
    구분하기 위해, 이 함수를 호출한 것 자체는 output에 company_summaries 키를 남겨 구분한다).

    2026-09-07 수정: 그룹핑 기준을 회사 "이름"에서 company_links의 "code"(고유 고객번호)로
    변경. companies.json에는 이름은 같지만 실제로는 서로 다른 회사가 5쌍 있어(예: 제닉스(주)
    2곳), 이름으로 그룹핑하면 서로 무관한 두 회사의 기사가 하나의 AI 요약 재료로 섞이는
    문제가 있었다(형님 제보로 확인)."""
    cutoff = datetime.now(KST) - timedelta(days=lookback_days)
    by_company = {}  # code -> articles
    company_names = {}  # code -> companies.json 원본 표기 이름 (프롬프트 생성용)
    # 2026-11-24: material(회사코드 -> 사업 사건 먼저 정렬된 기사 목록)이 오면 그대로 쓴다(곁가지/주가 제외, 같은 사건은 대표만)
    for code, arts in (material or {}).items():
        if arts:
            by_company[code] = arts
            company_names[code] = next((l.get("name") for l in arts[0].get("company_links", []) if l.get("code") == code), code)
    for a in ([] if material is not None else all_articles):
        try:
            pub_dt = datetime.fromisoformat(a["pubDate_iso"])
        except Exception:
            continue
        if pub_dt < cutoff:
            continue
        # 기사 하나가 여러 회사(같은 브랜드의 여러 사업장, 또는 이름은 같지만 코드가 다른
        # 별개 회사)에 동시에 관련된 경우, company_links를 기준으로 관련된 모든 회사 각각의
        # 요약 재료 목록에 포함시킨다.
        for link in a.get("company_links", []):
            code = link.get("code")
            if not code:
                continue
            by_company.setdefault(code, []).append(a)
            company_names.setdefault(code, link.get("name"))

    summaries = {}
    total = len(by_company)
    print(f"\nAI 요약 생성 대상: {total}개 고객사 (최근 {lookback_days}일 이내 기사 보유)")
    for i, (code, articles) in enumerate(by_company.items(), start=1):
        company_name = company_names.get(code, code)
        ranked_articles = articles if material is not None else sorted(
            articles, key=lambda a: 0 if company_title_hit(a, code, company_name) else 1
        )
        top_articles = ranked_articles[:max_articles]
        # 2026-11-24: 재료 기사(링크 목록)가 어제와 같으면 어제 요약을 그대로 씀(같은 요약을 매일 다시 만드는 비용 절약)
        basis = hashlib.sha1("|".join(sorted((a.get("originallink") or a.get("link") or "") for a in top_articles)).encode("utf-8")).hexdigest()[:16]
        old = (prev or {}).get(code)
        if old and old.get("basis") == basis and old.get("summary"):
            summaries[code] = old
            continue
        prompt = build_summary_prompt(company_name, top_articles, (industries or {}).get(code))
        print(f"  [{i}/{total}] 요약 생성: {company_name} (code={code}, {len(top_articles)}건 기준)")
        summary_text = call_anthropic_summary(prompt, api_key)
        if summary_text:
            summaries[code] = {
                "summary": summary_text,
                "based_on": len(top_articles),
                "generated_at": datetime.now(KST).isoformat(),
                "basis": basis,
            }
        time.sleep(request_delay)
    print(f"AI 요약 생성 완료: {len(summaries)}/{total}개 고객사 성공")
    return summaries


def classify_aliases(company, override):
    """사건 분류에 같이 보내는 '기사에서 쓰는 이름' (사내 등록명 SKON -> 기사 표기 SK온 등). 최대 4개"""
    if not company:
        return []
    o = override or {}
    names = list(o.get("match_names") or o.get("search_queries") or [company.get("query") or company.get("name", "")])
    names.append(display_company_name(company.get("name", "")))
    out = []
    for n in names:
        n = (n or "").strip()
        if n and n not in out:
            out.append(n)
    return out[:4]


def env_on(name, default="on"):
    return os.environ.get(name, default).lower() != "off"


def main(fetch_page_fn=None, now=None):
    """fetch_page_fn(query, start, display) 는 테스트/재현용(네이버 대신). now 도 테스트용."""
    client_id = os.environ.get("NAVER_CLIENT_ID")
    client_secret = os.environ.get("NAVER_CLIENT_SECRET")
    if fetch_page_fn is None and (not client_id or not client_secret):
        print(
            "[오류] NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수가 설정되어 있지 않습니다.\n"
            "네이버 개발자센터(https://developers.naver.com)에서 애플리케이션을 등록해 "
            "Client ID/Secret을 발급받고 환경변수 또는 GitHub Secrets로 등록하세요.",
            file=sys.stderr,
        )
        sys.exit(1)
    if fetch_page_fn is None:
        def fetch_page_fn(q, start, disp):
            return fetch_company_news(q, client_id, client_secret, display=disp, start=start)

    lookback_days = int(os.environ.get("NEWS_LOOKBACK_DAYS", "7"))
    display = min(int(os.environ.get("NEWS_DISPLAY_PER_QUERY", "100")), 100)
    request_delay = float(os.environ.get("NEWS_REQUEST_DELAY", "0.2"))

    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")
    summary_lookback_days = int(os.environ.get("SUMMARY_LOOKBACK_DAYS", "20"))
    # 2026-08-25 수정: 기존 5건에서 8건으로 상향 (회사 자신이 주인공인 기사 여러 건 대비)
    summary_max_articles = int(os.environ.get("SUMMARY_MAX_ARTICLES", "8"))
    summary_request_delay = float(os.environ.get("SUMMARY_REQUEST_DELAY", "0.3"))

    # 2026-11-24 놓친 기사 줄이기 1단계 설정
    store_on = env_on("NEWS_STORE")
    paging_on = env_on("NEWS_PAGING")
    classify_on = store_on and env_on("EVENT_CLASSIFY") and bool(anthropic_api_key)
    archive_days = int(os.environ.get("ARCHIVE_DAYS", "180"))
    biz_days = int(os.environ.get("BIZ_DAYS", "90"))
    max_pages = int(os.environ.get("NEWS_MAX_PAGES", "10")) if paging_on else 1
    overlap_days = int(os.environ.get("NEWS_OVERLAP_DAYS", "2"))
    event_model = os.environ.get("EVENT_MODEL", ANTHROPIC_MODEL)

    companies_cfg = load_json("companies.json")["companies"]
    media_cfg = load_json("major_media.json")
    keyword_cfg = load_json("priority_keywords.json")
    overrides_cfg = load_overrides()
    global_exclude_keywords = load_global_exclude_keywords()
    priority_order = keyword_cfg.get("priority_order", [])

    now = now or datetime.now(KST)
    cutoff = now - timedelta(days=lookback_days)
    # 보관을 켜면 20일보다 오래된 기사도 보관 기간까지 받아 두되, 사업 단어가 있을 때만(사업 사건만 90일 표시하므로)
    collect_cutoff = now - timedelta(days=archive_days) if store_on else cutoff

    fetch_state = ns.load_json_file(os.path.join(STORE_DIR, "fetch_state.json"), {}) if store_on else {}
    flog = ns.FilterLog(sample_n=int(os.environ.get("FILTER_SAMPLE_N", "40")), seed=now.strftime("%Y%m%d"))
    page_stats = {"queries": 0, "calls": 0, "multi_page_queries": 0, "capped_queries": []}

    # 2026-11-23 매칭 품질 판정 준비
    match_quality_on = os.environ.get("MATCH_QUALITY", "on").lower() != "off"
    common_ratio = float(os.environ.get("MATCH_COMMON_RATIO", "0.5"))
    longer_names = mq.build_longer_names(companies_cfg, overrides_cfg)
    uncertain_articles = []   # 보류함: 이름은 나오지만 이 회사 기사라는 근거가 약한 것
    match_stats = {"ok": 0, "uncertain": 0, "reject": 0, "common_mode": []}

    all_articles = []
    dup_index = DupIndex()  # 전역 중복 체크용 (링크/유사 제목)
    fetch_cache = {}  # 같은 검색어를 여러 사업장이 쓰면 한 번만 받음

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
        reps = sorted(set([r for r in ext_rep if r]))

        override = overrides_cfg.get(code)
        if override and override.get("search_queries"):
            search_queries = override["search_queries"]
        else:
            search_queries = [query]
        # 2026-09-29 추가: "검색할 말"과 "기사에 꼭 있어야 하는 이름"을 분리.
        match_names = (override.get("match_names") if override else None) or search_queries
        strict_name = bool(override.get("strict_name")) if override else False
        context_window = int(override.get("context_window") or 0) if override else 0
        context_keywords = override.get("context_keywords") if override else None
        exclude_keywords = override.get("exclude_keywords") if override else None

        print(f"[{i}/{total}] 수집: {name} ({' / '.join(search_queries)})")

        items_by_key = {}
        for sq in search_queries:
            if sq in fetch_cache:
                got = fetch_cache[sq]
            else:
                boundary = ns.boundary_for(fetch_state.get(sq), now, lookback_days, overlap_days)

                def _page(start, _sq=sq):
                    if start > 1:
                        time.sleep(request_delay)
                    return fetch_page_fn(_sq, start, display)

                got, info = ns.fetch_paged(_page, lambda it: parse_pubdate(it.get("pubDate", "")), boundary,
                                           display=display, max_pages=max_pages)
                fetch_cache[sq] = got
                page_stats["queries"] += 1
                page_stats["calls"] += info["pages"]
                if info["pages"] > 1:
                    page_stats["multi_page_queries"] += 1
                    print(f"       페이지 넘김 {info['pages']}쪽 (멈춤: {info['stop']})")
                if info["stop"] == "cap" and max_pages > 1:
                    page_stats["capped_queries"].append(sq)
                    print(f"       [안내] {max_pages}쪽까지 받아도 기준 시각에 못 미침(수집 미완료)")
                if got or info["stop"] in ("short", "boundary", "empty"):
                    fetch_state[sq] = {"last_fetch": now.isoformat(), "pages": info["pages"], "stop": info["stop"]}
                time.sleep(request_delay)
            for item in got:
                key = item.get("link") or item.get("originallink") or item.get("title")
                if key and key not in items_by_key:
                    items_by_key[key] = item
        items = list(items_by_key.values())
        flog.add("received", len(items))

        # 2026-11-23: 위험 이름 회사는 이번 수집분에서 이름이 일반 단어/다른 실체로 쓰인 비율을 재서
        # 절반 이상이면 '일반명사 모드'(법인표기 또는 제목+업종어만 인정)로 판정한다.
        common_mode = False
        if match_quality_on and any(mq.is_risky_name(n, override) for n in match_names):
            texts = [(clean_text(it.get("title", "")), clean_text(it.get("description", ""))) for it in items]
            ratio, seen = mq.company_noise_ratio(texts, match_names, company, override, longer_names)
            if seen >= 5 and ratio >= common_ratio:
                common_mode = True
                match_stats["common_mode"].append(code)
                print(f"       [일반명사 모드] 이름이 다른 뜻으로 쓰인 비율 {ratio:.0%} ({seen}건 중)")

        kept, skipped_old, skipped_dup, skipped_irrelevant, skipped_stock = 0, 0, 0, 0, 0
        for item in items:
            pub_dt = parse_pubdate(item.get("pubDate", ""))
            if pub_dt is None:
                continue
            title = clean_text(item.get("title", ""))
            description = clean_text(item.get("description", ""))
            combined_text = f"{title} {description}"

            if pub_dt < collect_cutoff or (pub_dt < cutoff and not ec.biz_hint(title, description)):
                skipped_old += 1
                flog.add("old")
                continue

            reasons = [relevance_reason(title, description, mn, strict_name) for mn in match_names]
            if "ok" not in reasons:
                skipped_irrelevant += 1
                why = "list_type" if "list" in reasons else "no_name"
                flog.add(why)
                if pub_dt >= cutoff:
                    flog.sample(why, {"code": code, "name": name, "title": title, "description": description,
                                      "link": item.get("originallink") or item.get("link", ""),
                                      "pubDate": pub_dt.strftime("%Y-%m-%d"),
                                      "biz_word": ec.biz_hint(title, "")})
                continue

            if context_keywords and not contains_any_keyword(combined_text, context_keywords):
                skipped_irrelevant += 1
                flog.add("ctx")
                continue

            if context_keywords and context_window and not context_near_name(
                    title, description, match_names, strict_name, context_keywords, context_window):
                skipped_irrelevant += 1
                flog.add("ctxwin")
                continue

            if exclude_keywords and contains_any_keyword(combined_text, exclude_keywords):
                skipped_irrelevant += 1
                flog.add("excl")
                continue

            # 2026-11-23 매칭 품질 판정 (단어 경계 / 위험 이름 / 일반명사 모드)
            if match_quality_on:
                verdict, why = mq.judge(title, description, match_names, company, override,
                                        longer_names=longer_names, common=common_mode)
                match_stats[verdict] += 1
                if verdict == "reject":
                    skipped_irrelevant += 1
                    flog.add("mq_reject")
                    continue
                if verdict == "uncertain":
                    skipped_irrelevant += 1
                    flog.add("mq_uncertain")
                    if pub_dt >= cutoff:  # 보류함은 지금처럼 20일 안 기사만
                        uncertain_articles.append({
                            "code": code, "name": name, "reason": why,
                            "industry": list(company.get("industry", []) or []),
                            "matched_sub_names": find_matched_sub_names(title, description, sub_names),
                            "title": title, "description": description,
                            "link": item.get("link", ""),
                            "originallink": item.get("originallink", "") or item.get("link", ""),
                            "pubDate_iso": pub_dt.isoformat(),
                            "pubDate_display": pub_dt.strftime("%Y-%m-%d %H:%M"),
                            "team": list(team), "cell": list(cell), "reps": list(reps),
                        })
                    continue

            # 2026-08-25: context_keywords 가 있는 회사는 전역 증시어 제외를 적용하지 않음(형님 확인 완료).
            # 2026-11-24: 분류가 켜져 있으면 증시어 기사는 지우지 않고 stock=true 신호만 붙임(접힌 칸 후보)
            stock_hit = (not context_keywords) and contains_any_keyword(combined_text, global_exclude_keywords)
            if stock_hit and not classify_on:
                skipped_stock += 1
                flog.add("stock_excluded")
                continue
            if stock_hit:
                flog.add("stock_signal")

            link = item.get("link", "")
            originallink = item.get("originallink", "") or link
            domain = get_domain(originallink) or get_domain(link)
            press_name, is_major = resolve_media(domain, media_cfg)
            tag_key, tag_label, tag_color = resolve_tag(title, description, keyword_cfg)
            matched_sub_names = find_matched_sub_names(title, description, sub_names)

            article = {
                # 회사별 연결 정보(company_links)를 끝까지 유지 (2026-08-24/08-25/09-07 변경 이력은 git 기록 참고)
                "companies": [name],
                "company_links": [{
                    "code": code,
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
            if stock_hit:
                article["stock"] = True

            dup = dup_index.find(article)
            if dup:
                skipped_dup += 1
                flog.add("dup")
                # 같은 기사가 다른 회사에서도 검색된 경우 company_links 에 별도 항목으로 추가(코드 기준, 2026-09-07)
                existing_link = None
                for lk in dup["company_links"]:
                    if lk.get("code") == code:
                        existing_link = lk
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
                        "code": code,
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
                if stock_hit:
                    dup["stock"] = True
                # 기존 기사가 비메이저이고 새 기사가 메이저 언론사면 본문/링크/언론사 정보만 교체
                if is_major and not dup["major"]:
                    for f in ("companies", "company_links", "team", "cell", "reps", "matched_sub_names"):
                        article[f] = dup[f]
                    if dup.get("stock"):
                        article["stock"] = True
                    dup_index.remove(dup)
                    all_articles.remove(dup)
                    dup_index.add(article)
                    all_articles.append(article)
                continue

            dup_index.add(article)
            all_articles.append(article)
            kept += 1
            flog.add("kept")

        print(f"       수집 {len(items)} / 채택 {kept} / 중복제외 {skipped_dup} / 기간외제외 {skipped_old} / 나열형제외 {skipped_irrelevant} / 증시노이즈제외 {skipped_stock}")

    # 2026-11-23 보류함 AI 판정: true 는 일반 기사로 승격(judged_by="ai"), false/실패는 보류함에 남김
    if match_quality_on and uncertain_articles:
        ai_on = os.environ.get("AI_JUDGE", "on").lower() != "off"
        if ai_on and anthropic_api_key:
            uncertain_articles.sort(key=lambda a: a["pubDate_iso"], reverse=True)
            promoted, ai_stats = ai_judge_uncertain(
                uncertain_articles, anthropic_api_key,
                max_articles=int(os.environ.get("AI_JUDGE_MAX", "100")),
                batch_size=int(os.environ.get("AI_JUDGE_BATCH", "10")))
            match_stats["ai_judge"] = ai_stats
            print(f"\n보류함 AI 판정: 질의 {ai_stats['asked']} / 승격 {ai_stats['promoted']} / 보류 유지 {ai_stats['kept']}"
                  f" / 응답 실패 {ai_stats['failed']} / 상한 초과 {ai_stats['skipped_cap']}")
            promoted_ids = set()
            for u, reason in promoted:
                try:
                    promoted_ids.add(id(u))
                    link_info = {"code": u["code"], "name": u["name"],
                                 "matched_sub_names": list(u.get("matched_sub_names", [])),
                                 "team": list(u["team"]), "cell": list(u["cell"]), "reps": list(u["reps"]),
                                 "judged_by": "ai"}
                    domain = get_domain(u["originallink"]) or get_domain(u["link"])
                    press_name, is_major = resolve_media(domain, media_cfg)
                    tag_key, tag_label, tag_color = resolve_tag(u["title"], u["description"], keyword_cfg)
                    art = {
                        "companies": [u["name"]], "company_links": [link_info],
                        "matched_sub_names": list(u.get("matched_sub_names", [])),
                        "team": list(u["team"]), "cell": list(u["cell"]), "reps": list(u["reps"]),
                        "title": u["title"], "description": u["description"],
                        "link": u["link"], "originallink": u["originallink"],
                        "press": press_name, "domain": domain, "major": is_major,
                        "pubDate_iso": u["pubDate_iso"], "pubDate_display": u["pubDate_display"],
                        "tag_key": tag_key, "tag_label": tag_label, "tag_color": tag_color,
                        "judged_by": "ai", "ai_reason": reason,
                        "_norm_title": normalize_title(u["title"]),
                    }
                    dup = dup_index.find(art)
                    if dup:
                        if not any(l.get("code") == u["code"] for l in dup["company_links"]):
                            dup["company_links"].append(link_info)
                        for key, vals in (("companies", [u["name"]]), ("team", u["team"]), ("cell", u["cell"]),
                                          ("reps", u["reps"]), ("matched_sub_names", u.get("matched_sub_names", []))):
                            for v in vals:
                                if v not in dup[key]:
                                    dup[key].append(v)
                    else:
                        dup_index.add(art)
                        all_articles.append(art)
                except Exception as e:  # 한 건 오류로 전체 실행이 멈추지 않게
                    promoted_ids.discard(id(u))
                    print(f"    [경고] 보류함 승격 처리 오류(보류 유지): {e}", file=sys.stderr)
            uncertain_articles = [u for u in uncertain_articles if id(u) not in promoted_ids]
        else:
            print("\n[안내] 보류함 AI 판정 건너뜀 (ANTHROPIC_API_KEY 없음 또는 AI_JUDGE=off)")

    for a in all_articles:
        a.pop("_norm_title", None)

    output = {
        "generated_at": now.isoformat(),
        "lookback_days": lookback_days,
        "companies": [c["name"] for c in companies_cfg],
        # 2026-11-25 툴 개수 의뢰 #14: 기사가 0건인 담당자도 팀/셀/영업명 목록에 나오도록 전체 고객 명단(기사와 같은 외근영업 기준)
        "roster": [{"code": c.get("code", ""), "name": c["name"], "team": list(c.get("team", [])),
                    "cell": list(c.get("cell", [])), "reps": sorted(set(r for r in c.get("ext_rep", []) if r))}
                   for c in companies_cfg],
    }

    if store_on:
        # ---- 2026-11-24 보관소에 합치고, 새 회사-기사 쌍만 사건 분류, 화면 목록 만들기 ----
        arch_path = os.path.join(STORE_DIR, "archive.json")
        cache_path = os.path.join(STORE_DIR, "classify_cache.json")
        archive = ns.load_json_file(arch_path, {"version": 1, "articles": {}})
        cache = ns.load_json_file(cache_path, {}).get("items", {})
        added = ns.merge_into_archive(archive, all_articles, now.isoformat())
        pruned = ns.prune_archive(archive, now, archive_days)
        print(f"\n보관소: 새 기사 {added} / 기간 지나 삭제 {pruned} / 보관 {len(archive['articles'])}")

        # ---- 2026-11-24 DART 공시(사업 소식 출처, 화면에 "DART" 스티커). 실패해도 뉴스 수집은 그대로 ----
        dart_stats = {"on": False}
        dart_key = os.environ.get("DART_API_KEY")
        if env_on("DART_SOURCE") and dart_key:
            try:
                dmap = ds.load_map(os.path.join(CONFIG_DIR, "dart_corp_map.json"))
                code_by_corp = {}
                for g, cc in dmap.items():
                    code_by_corp.setdefault(cc, []).append(g)
                dstate_path = os.path.join(STORE_DIR, "dart_state.json")
                dstate = ns.load_json_file(dstate_path, {})
                ditems, dst, dstate = ds.fetch_disclosures(
                    dart_key, list(code_by_corp), dstate, now, days=biz_days,
                    sleep=float(os.environ.get("DART_SLEEP", "0.35")), max_calls=int(os.environ.get("DART_MAX_CALLS", "400")))
                darts, dres = ds.to_articles(ditems, code_by_corp, {c.get("code"): c for c in companies_cfg}, KST)
                dadded = ns.merge_into_archive(archive, darts, now.isoformat())
                for k, v in dres.items():
                    if k not in cache:
                        v["t"] = now.strftime("%Y-%m-%d")
                        cache[k] = v
                ns.save_json_file(dstate_path, dstate, compact=False)
                dart_stats = {"on": True, "mode": dst["mode"], "calls": dst["calls"], "errors": dst["errors"],
                              "listed": dst["items"], "events": len(darts), "new": dadded}
                print(f"DART: 방식 {dst['mode']} / 호출 {dst['calls']} / 오류 {dst['errors']} / 사업 공시 {len(darts)} / 새로 보관 {dadded}")
            except Exception as e:  # DART 오류는 절대 실행을 멈추지 않음
                dart_stats = {"on": True, "error": type(e).__name__}
                print(f"[경고] DART 단계 건너뜀: {type(e).__name__}", file=sys.stderr)
        else:
            print("[안내] DART 공시 건너뜀 (DART_SOURCE=off 또는 DART_API_KEY 없음)")

        ev_stats = {"on": classify_on}
        if classify_on:
            todo = []
            gen_cut = cutoff.isoformat()
            ind_by_code = {c.get("code"): c.get("industry", []) for c in companies_cfg}
            comp_by_code = {c.get("code"): c for c in companies_cfg}
            for k, a in archive["articles"].items():
                pub = a.get("pubDate_iso", "")
                if pub < gen_cut and not ec.biz_hint(a.get("title", ""), a.get("description", "")):
                    continue  # 20일 밖 + 사업 단어 없음 -> 어차피 화면에 안 나옴
                for l in a.get("links", []):
                    pk = f"{l['code']}|{k}"
                    if pk in cache:
                        continue
                    inds = "/".join(x for x in ind_by_code.get(l["code"], []) if x and x not in ("미분류", "기타")) or "미상"
                    todo.append((pub, pk, {"company": display_company_name(l.get("name", "")),
                                           "aliases": classify_aliases(comp_by_code.get(l["code"]), overrides_cfg.get(l["code"])),
                                           "industry": inds,
                                           "title": a.get("title", ""), "summary": a.get("description", ""),
                                           "date": pub[:10], "stock": bool(a.get("stock"))}))
            todo.sort(key=lambda x: x[0], reverse=True)  # 최신 기사부터(상한에 걸리면 오래된 것은 다음 날)
            pairs = [(pk, it) for _, pk, it in todo]
            batch_size = int(os.environ.get("EVENT_BATCH", "15"))
            if env_on("EVENT_USE_BATCH"):
                # Message Batches(반값). 오늘 못 받은 결과는 store/classify_pending.json 에 남겨 다음 실행 때 받음
                pend_path = os.path.join(STORE_DIR, "classify_pending.json")
                pending = ns.load_json_file(pend_path, {"batches": []})
                st = ec.classify_pairs_batched(pairs, anthropic_api_key, cache, pending, model=event_model,
                                               batch_size=batch_size, max_new=int(os.environ.get("EVENT_MAX", "3000")),
                                               today=now.strftime("%Y-%m-%d"),
                                               wait_sec=60 * int(os.environ.get("EVENT_BATCH_WAIT_MIN", "30")))
                ns.save_json_file(pend_path, pending)
            else:
                st = ec.classify_pairs(pairs, anthropic_api_key, cache, model=event_model, batch_size=batch_size,
                                       max_new=int(os.environ.get("EVENT_MAX", "1500")), today=now.strftime("%Y-%m-%d"))
            ev_stats.update(st)
            print(f"사건 분류: 새 쌍 {len(todo)} / 질의 {st['asked']} / 답 {st['answered']} / 실패 {st['failed']}"
                  f" / 상한 초과 {st['skipped_cap']} / 호출 {st['calls']} / 토큰 입력 {st['input_tokens']} 출력 {st['output_tokens']}")
        ns.prune_cache(cache, archive)

        by_code = {c.get("code"): c for c in companies_cfg if c.get("code")}
        view, hidden, vstats = nv.build_view(archive, cache, by_code, now, biz_days=biz_days,
                                             gen_days=lookback_days, ai_active=classify_on)
        print(f"화면: 기사 {len(view)} / 회사-기사 사업 {vstats['biz']} 일반 {vstats['gen']} 접기 {vstats['fold']}"
              f" / 다른 회사로 숨김 {vstats['other']} / 미분류 {vstats['unclassified']}")
        output["biz_days"] = biz_days
        output["total_articles"] = len(view)
        output["articles"] = view
        output["view_stats"] = vstats
        output["event_classify"] = ev_stats
        output["dart"] = dart_stats
        for h in hidden:
            uncertain_articles.append(h)
        ns.save_lines(arch_path, archive)
        ns.save_lines(cache_path, {"version": 1, "items": cache}, key="items")
        ns.save_json_file(os.path.join(STORE_DIR, "fetch_state.json"), fetch_state, compact=False)
        flog.save(os.path.join(STORE_DIR, "filter_log.json"), now.isoformat(),
                  extra={"paging": page_stats, "match_stats": match_stats, "view_stats": vstats, "event_classify": ev_stats})
        material = {}
        for code in by_code:
            m = nv.summary_material(view, code)
            if m:
                material[code] = m
    else:
        # 예전 방식: 우선순위 태그 -> 최신순
        all_articles.sort(key=lambda a: a["pubDate_iso"], reverse=True)
        all_articles.sort(key=lambda a: (
            priority_order.index(a["tag_key"]) if a["tag_key"] in priority_order else len(priority_order)
        ))
        output["total_articles"] = len(all_articles)
        output["articles"] = all_articles
        material = None

    print(f"페이지 넘김: 검색어 {page_stats['queries']} / 네이버 호출 {page_stats['calls']}"
          f" / 2쪽 이상 {page_stats['multi_page_queries']} / 상한 도달 {len(page_stats['capped_queries'])}")
    print("제외 사유: " + ", ".join(f"{k} {v}" for k, v in flog.counts.items()))

    if match_quality_on:
        uncertain_articles.sort(key=lambda a: a["pubDate_iso"], reverse=True)
        output["uncertain_articles"] = uncertain_articles
        output["match_stats"] = match_stats
        print(f"\n매칭 판정: 채택 {match_stats['ok']} / 보류 {match_stats['uncertain']} / 제외 {match_stats['reject']}"
              f" / 일반명사 모드 {len(match_stats['common_mode'])}곳")

    prev_summaries = None
    if anthropic_api_key and env_on("SUMMARY_REUSE"):
        prev_summaries = (ns.load_json_file(os.path.join(DATA_DIR, "news.json"), {}) or {}).get("company_summaries")
    if anthropic_api_key:
        company_summaries = generate_company_summaries(
            output["articles"],
            anthropic_api_key,
            lookback_days=summary_lookback_days,
            max_articles=summary_max_articles,
            request_delay=summary_request_delay,
            industries={c.get("code"): c.get("industry", []) for c in companies_cfg},
            material=material,
            prev=prev_summaries,
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
        if store_on:  # 2026-11-24: 90일 사업 사건이 더해져도 파일이 커지지 않게 공백 없이 저장(화면/대시보드는 그대로 읽음)
            json.dump(output, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n완료: 총 {output['total_articles']}건 -> {out_path}")


if __name__ == "__main__":
    main()
