#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
(2026-11-24) DART 전자공시를 '사업 소식' 출처로 더하기 (형 결정: 화면에 "DART" 스티커)

- 대상: config/dart_corp_map.json 의 고객(그룹코드 -> DART 고유번호, 상장 고객만)
- 공시 종류(보고서 이름으로 판단, Haiku 안 씀):
    단일판매/공급계약체결 -> 수주/공급계약, 신규시설투자 / 유형자산 취득/양수 -> 설비투자/증설/신공장,
    타법인주식및출자증권 취득/양수 -> 투자/인수.  정정 공시([기재정정] 등)는 원본과 같은 사건이라 넣지 않음
- 받는 법(GET 만, 호출 사이 DART_SLEEP 초 쉼):
    처음(또는 마지막 수집이 80일 넘게 지남): 고객 고유번호마다 최근 90일 목록 (약 285회)
    평소: 고유번호 없이 날짜 범위(마지막 수집 - 2일 ~ 오늘) 전체 목록을 쪽 단위로 받아 우리 고객만 거름 (하루 몇 회)
- 실패해도 뉴스 수집은 그대로: 오류는 경고만 남기고, 다음 실행 때 같은 범위를 다시 받음
- 끄기: DART_SOURCE=off, 키(DART_API_KEY)가 없으면 자동으로 건너뜀
"""

import json
import re
import sys
import time
from datetime import datetime, timedelta
from urllib import parse as urlparse
from urllib import request as urlrequest

LIST_URL = "https://opendart.fss.or.kr/api/list.json"
VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="

TYPE_RULES = [
    (re.compile(r"단일판매.?공급계약"), "order"),
    (re.compile(r"신규시설투자"), "capex"),
    (re.compile(r"유형자산(취득|양수)"), "capex"),
    (re.compile(r"타법인주식및출자증권(취득|양수)"), "invest"),
]
_PREFIX = re.compile(r"^\s*(\[[^\]]*\]\s*)+")


def norm_name(nm):
    n = _PREFIX.sub("", nm or "")
    n = re.split(r"\s{3,}", n.strip())[0]
    n = re.sub(r"\((자율공시|종속회사의주요경영사항|안내공시)\)", "", n)
    return re.sub(r"[\sㆍ·/,]", "", n)


def is_correction(nm):
    return bool(re.search(r"정정\]", nm or "")) or (nm or "").strip().startswith("[정정")


def event_type(report_nm):
    """보고서 이름 -> 사건 종류 코드(event_classify.EVENT_TYPES), 대상 아니면 None"""
    if is_correction(report_nm):
        return None
    n = norm_name(report_nm)
    for rx, code in TYPE_RULES:
        if rx.search(n):
            return code
    return None


def readable_title(corp_name, report_nm):
    n = _PREFIX.sub("", report_nm or "").strip()
    n = re.split(r"\s{3,}", n)[0]
    n = n.replace("ㆍ", "/").replace("·", "/")
    n = re.sub(r"\s+", " ", n)
    return f"{corp_name} {n}".strip()


def load_map(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {}
    m = d.get("map", d) if isinstance(d, dict) else {}
    return {str(k): str(v).zfill(8) for k, v in m.items() if not str(k).startswith("_") and v}


def _get(params, timeout=30):
    url = LIST_URL + "?" + urlparse.urlencode(params)
    with urlrequest.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_disclosures(api_key, corp_codes, state, now, days=90, overlap_days=2, sleep=0.35, max_calls=400, get=None):
    """반환: (공시 목록, 통계, 새 state). 오류가 나도 예외를 올리지 않음.
    공시 목록 = OpenDART list 항목 중 corp_codes 에 있는 것."""
    get = get or _get
    sleep_fn = time.sleep if sleep else (lambda s: None)
    stats = {"mode": "", "calls": 0, "errors": 0, "items": 0, "ok": False}
    want = set(corp_codes)
    end = now.strftime("%Y%m%d")
    last = (state or {}).get("last_fetch")
    try:
        last_dt = datetime.fromisoformat(last) if last else None
    except Exception:
        last_dt = None
    out = []
    if last_dt is None or (now - last_dt).days > 80:
        stats["mode"] = "corp"
        bgn = (now - timedelta(days=days)).strftime("%Y%m%d")
        for cc in sorted(want):
            page = 1
            while stats["calls"] < max_calls:
                try:
                    d = get({"crtfc_key": api_key, "corp_code": cc, "bgn_de": bgn, "end_de": end,
                             "page_no": page, "page_count": 100})
                except Exception as e:
                    print(f"    [경고] DART 조회 실패: {type(e).__name__}", file=sys.stderr)
                    d = {"status": "ERR"}
                stats["calls"] += 1
                sleep_fn(sleep)
                st = d.get("status")
                if st not in ("000", "013"):
                    stats["errors"] += 1
                if st != "000":
                    break
                out += d.get("list", [])
                if page >= int(d.get("total_page", 1) or 1):
                    break
                page += 1
            if stats["calls"] >= max_calls:
                stats["errors"] += 1
                break
    else:
        stats["mode"] = "range"
        bgn = (last_dt - timedelta(days=overlap_days)).strftime("%Y%m%d")
        page = 1
        while stats["calls"] < max_calls:
            try:
                d = get({"crtfc_key": api_key, "bgn_de": bgn, "end_de": end, "page_no": page, "page_count": 100})
            except Exception as e:
                print(f"    [경고] DART 조회 실패: {type(e).__name__}", file=sys.stderr)
                d = {"status": "ERR"}
            stats["calls"] += 1
            sleep_fn(sleep)
            st = d.get("status")
            if st not in ("000", "013"):
                stats["errors"] += 1
            if st != "000":
                break
            out += [x for x in d.get("list", []) if x.get("corp_code") in want]
            if page >= int(d.get("total_page", 1) or 1):
                break
            page += 1
        if stats["calls"] >= max_calls:
            stats["errors"] += 1
    stats["items"] = len(out)
    stats["ok"] = stats["errors"] == 0
    new_state = dict(state or {})
    if stats["ok"]:  # 다 받았을 때만 시각을 넘김(실패하면 다음 실행 때 같은 범위를 다시)
        new_state["last_fetch"] = now.isoformat()
    return out, stats, new_state


def to_articles(disclosures, code_by_corp, companies_by_code, kst):
    """공시 -> 보관소에 넣을 기사 + 분류 결과(캐시에 그대로 넣음). 반환: (articles, results{pair_key: result})"""
    arts, results, seen = [], {}, set()
    for d in disclosures:
        typ = event_type(d.get("report_nm", ""))
        if not typ:
            continue
        rno = d.get("rcept_no")
        if not rno or rno in seen:
            continue
        seen.add(rno)
        codes = [c for c in code_by_corp.get(d.get("corp_code"), []) if c in companies_by_code]
        if not codes:
            continue
        try:
            day = datetime.strptime(d["rcept_dt"], "%Y%m%d").replace(hour=9, tzinfo=kst)
        except Exception:
            continue
        url = VIEW_URL + rno
        title = readable_title(d.get("corp_name", ""), d.get("report_nm", ""))
        arts.append({
            "company_links": [{"code": c, "name": companies_by_code[c]["name"], "matched_sub_names": []} for c in codes],
            "title": title,
            "description": "전자공시(DART) 원문에서 계약 상대, 금액, 기간을 확인하세요." if typ == "order"
            else "전자공시(DART) 원문에서 투자 대상, 금액, 일정을 확인하세요.",
            "link": url, "originallink": url, "press": "DART 전자공시", "domain": "dart.fss.or.kr", "major": False,
            "pubDate_iso": day.isoformat(), "pubDate_display": day.strftime("%Y-%m-%d") + " 공시",
            "tag_key": None, "tag_label": "일반", "tag_color": "#7f8c8d", "source": "DART",
        })
        for c in codes:
            results[f"{c}|{url}"] = {"same": "y", "role": "lead", "type": typ, "date": day.strftime("%Y-%m-%d"),
                                     "quote": "", "quote_ok": True, "m": "dart"}
    return arts, results
