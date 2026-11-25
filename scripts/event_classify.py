#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
(2026-11-24) 고객 기사 사건 분류 (Claude Haiku) + 같은 사건 묶기

- 회사-기사 한 쌍마다 한 번만 묻고 결과는 store/classify_cache.json 에 링크 기준으로 저장(다음 날 다시 묻지 않음)
- Haiku 에게 묻는 것: 같은 회사인지(y/n/?), 역할(주도/참여/곁가지/불명), 사건 종류, 사건 날짜, 근거 인용(기사 문구 그대로)
- 화면 단계(tier):
    biz  = 같은 회사 + 주도/참여 + 사업 사건(수주/설비투자/신제품/자동화) + 근거 인용이 실제 제목/요약에 있음 -> 90일 표시
    gen  = 일반(애매/보류 포함, 지금과 같은 대우) -> 20일 표시
    fold = 곁가지 언급 또는 실적/주가 -> 20일, 접어서 표시
    (Haiku 가 "다른 회사"라고 하면: 11-24 실측에서 대부분 틀려서 숨기지 않고 fold 로 접음. EVENT_HIDE_OTHER=on 이면 숨김)
- 기본은 Message Batches(반값, 비동기): 오늘 못 받은 결과는 다음 실행 때 받음 (EVENT_USE_BATCH=off 면 바로 호출)
- 실패해도 실행은 멈추지 않음: 답이 없거나 형식이 틀리면 그 기사는 규칙(지금 방식)대로 gen
- 끄기: EVENT_CLASSIFY=off (분류 전체), 모델 바꾸기: EVENT_MODEL
표준 라이브러리만 사용 (저장소의 다른 Claude 호출과 같은 urllib 방식)
"""

import json
import os
import re
import sys
import difflib
from urllib import request as urlrequest

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-4-5"

EVENT_TYPES = {
    "order": "수주/공급계약",
    "capex": "설비투자/증설/신공장",
    "product": "신제품/개발",
    "auto": "자동화/라인",
    "people": "인사/조직",
    "stock": "실적/주가",
    "etc": "기타",
    "invest": "투자/인수",  # DART 타법인주식 취득 공시용(Haiku 에게는 묻지 않음)
}
ROLES = {"lead": "주도", "part": "참여", "side": "곁가지", "unk": "불명"}
BIZ_TYPES = ("order", "capex", "product", "auto", "invest")
TIER_RANK = {"biz": 0, "gen": 1, "fold": 2, "other": 3}

# 20일 밖 기사는 사업 사건일 때만 화면에 나오므로, 이 단어가 제목/요약에 있을 때만 Haiku 에게 묻는다(비용 절약).
BIZ_HINT_RE = re.compile(
    r"수주|공급계약|공급 계약|납품|계약 체결|계약을 체결|단일판매|수출계약|낙찰|발주|"
    r"공장|증설|착공|준공|신설|생산라인|라인|양산|설비|투자|생산능력|캐파|CAPA|거점|가동|"
    r"신제품|출시|개발|선보|공개|론칭|런칭|국산화|"
    r"자동화|로봇|스마트팩토리|스마트 팩토리|무인화|AMR|AGV|협동로봇", re.IGNORECASE)


def biz_hint(title, description):
    return bool(BIZ_HINT_RE.search(f"{title} {description}"))


def pair_key(code, article):
    return f"{code}|{article.get('originallink') or article.get('link') or article.get('title', '')}"


def _norm(s):
    return re.sub(r"[\s'\"‘’“”.,…]", "", (s or "")).lower()


def quote_found(quote, text):
    """근거 인용이 제목/요약에 실제로 있는지. 따옴표/띄어쓰기/쉼표 차이와 '앞부분, 뒷부분' 이어 붙이기는 허용
    (인용 글자 2개씩 묶음의 80% 이상이 본문에 있으면 인정). 지어낸 문구는 거름."""
    q, t = _norm(quote), _norm(text)
    if len(q) < 4:
        return False
    if q in t:
        return True
    grams = [q[i:i + 2] for i in range(len(q) - 1)]
    return sum(1 for g in grams if g in t) / len(grams) >= 0.8


def build_prompt(items):
    """items: [(id, dict(company, industry, title, summary, date, stock))]"""
    lines = [json.dumps(dict(id=i, **x), ensure_ascii=False) for i, x in items]
    return (
        "FA 기계부품 영업 담당자가 거래처 방문 전에 볼 뉴스를 분류합니다. 각 줄은 거래처명(company, 사내 등록명이라 "
        "사업장명이 붙거나 영문/한글 표기가 기사와 다를 수 있음), 기사에서 쓰는 이름(aliases), 업종, "
        "그 이름으로 검색된 기사 제목/요약, 게시일입니다. 제목과 요약에 적힌 내용만 근거로 판정하세요(추측 금지).\n"
        "same: 기사 속 그 이름이 이 거래처(같은 회사나 그 사업장)면 \"y\"(잠깐 언급돼도 y), "
        "이름만 같은 전혀 다른 회사/단어/인물이 확실할 때만 \"n\", 판단 근거가 부족하면 \"?\".\n"
        "role: 거래처가 사건의 주인공이면 \"lead\", 공동 당사자(계약 상대, 공동 개발 등)면 \"part\", "
        "다른 회사 기사에 고객사/협력사/관련주로 잠깐 언급되면 \"side\", 알 수 없으면 \"unk\".\n"
        "type: order(수주/공급계약/납품), capex(설비투자/증설/신공장/착공/양산 시작), product(신제품/기술개발/출시), "
        "auto(자동화/로봇/생산라인 구축), people(인사/조직/채용/수상/기부), stock(실적/주가/증권 리포트/신용등급/수급), etc(그 밖).\n"
        "date, quote 는 type 이 order/capex/product/auto 일 때만 채우고, 그 밖에는 null 과 \"\" 로 두세요.\n"
        "date: 사건이 일어난(또는 일어날) 날짜를 기사에서 알 수 있으면 YYYY-MM-DD, 모르면 null. 게시일을 그대로 쓰지 마세요.\n"
        "quote: 판정 근거가 되는 제목/요약 속 문구를 글자 그대로 25자 이내로 복사(바꿔 쓰지 말 것). 근거가 없으면 \"\".\n"
        "반드시 JSON 배열만 출력하세요. 형식: "
        '[{"id":0,"same":"y","role":"lead","type":"order","date":null,"quote":"..."}]\n\n'
        + "\n".join(lines)
    )


def parse_response(text, valid_ids):
    """Haiku 응답 -> {id: result}. 형식이 틀린 항목은 버림(=규칙 기본값)."""
    if not text:
        return {}
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    a, b = t.find("["), t.rfind("]")
    if a < 0 or b <= a:
        return {}
    try:
        data = json.loads(t[a:b + 1])
    except Exception:
        return {}
    if not isinstance(data, list):
        return {}
    out = {}
    for d in data:
        if not isinstance(d, dict):
            continue
        i = d.get("id")
        if isinstance(i, bool) or not isinstance(i, int) or i not in valid_ids:
            continue
        same = d.get("same")
        if same is True:
            same = "y"
        elif same is False:
            same = "n"
        if same not in ("y", "n", "?"):
            continue
        role = d.get("role") if d.get("role") in ROLES else "unk"
        typ = d.get("type") if d.get("type") in EVENT_TYPES else "etc"
        date = d.get("date")
        if not (isinstance(date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)):
            date = None
        quote = d.get("quote") if isinstance(d.get("quote"), str) else ""
        out[i] = {"same": same, "role": role, "type": typ, "date": date, "quote": quote[:60]}
    return out


def call_claude(prompt, api_key, model=DEFAULT_MODEL, max_tokens=2000, timeout=60):
    """Claude 호출 1회(재시도 없음). (text, usage) 또는 (None, None)."""
    body = json.dumps({
        "model": model,
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
        text = "".join(p.get("text", "") for p in data.get("content", []) if p.get("type") == "text").strip()
        return (text or None), data.get("usage") or {}
    except Exception as e:
        print(f"    [경고] 사건 분류 호출 실패: {e}", file=sys.stderr)
        return None, None


def classify_pairs(pairs, api_key, cache, model=DEFAULT_MODEL, batch_size=15, max_new=1500, call=None, today=""):
    """pairs: [(key, item_dict)] (아직 캐시에 없는 것만, 우선순위 순). 결과는 cache[key] 에 저장.
    반환: 통계 dict. 어떤 오류도 예외로 올리지 않음."""
    call = call or (lambda p: call_claude(p, api_key, model=model, max_tokens=110 * batch_size + 200))
    stats = {"asked": 0, "answered": 0, "failed": 0, "skipped_cap": 0, "calls": 0,
             "input_tokens": 0, "output_tokens": 0}
    todo = pairs[:max_new]
    stats["skipped_cap"] = max(0, len(pairs) - len(todo))
    for start in range(0, len(todo), batch_size):
        chunk = todo[start:start + batch_size]
        ids = list(range(len(chunk)))
        stats["asked"] += len(chunk)
        stats["calls"] += 1
        try:
            text, usage = call(build_prompt([(i, chunk[i][1]) for i in ids]))
            if usage:
                stats["input_tokens"] += int(usage.get("input_tokens", 0) or 0)
                stats["output_tokens"] += int(usage.get("output_tokens", 0) or 0)
            res = parse_response(text, set(ids))
        except Exception as e:
            print(f"    [경고] 사건 분류 처리 오류: {e}", file=sys.stderr)
            res = {}
        for i in ids:
            if i in res:
                key, item = chunk[i]
                r = res[i]
                r["quote_ok"] = quote_found(r["quote"], item.get("title", "") + " " + item.get("summary", ""))
                r["m"] = model
                r["t"] = today
                cache[key] = r
                stats["answered"] += 1
            else:
                stats["failed"] += 1
    return stats


# ---------------- Message Batches (50% 할인, 비동기) ----------------
BATCH_URL = "https://api.anthropic.com/v1/messages/batches"


def _http(method, url, api_key, body=None, timeout=60, raw=False):
    req = urlrequest.Request(url, data=json.dumps(body).encode("utf-8") if body is not None else None, method=method)
    req.add_header("x-api-key", api_key)
    req.add_header("anthropic-version", "2023-06-01")
    req.add_header("content-type", "application/json")
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        data = resp.read().decode("utf-8")
    return data if raw else json.loads(data)


def classify_pairs_batched(pairs, api_key, cache, pending, model=DEFAULT_MODEL, batch_size=15, max_new=1500,
                           today="", wait_sec=1800, poll_sec=30, http=None, sleep=None, clock=None):
    """Message Batches 로 분류. pending(dict, store/classify_pending.json 내용)에 아직 안 끝난 묶음을 남겨
    다음 실행 때 결과를 받는다(오늘 다 못 받아도 손해 없음). 실패하면 그 쌍은 캐시에 없으니 다음 날 다시 질의.
    반환: 통계 dict. 어떤 오류도 예외로 올리지 않음."""
    import time as _t
    http = http or (lambda m, u, b=None, raw=False: _http(m, u, api_key, b, raw=raw))
    sleep = sleep or _t.sleep
    clock = clock or _t.time
    stats = {"asked": 0, "answered": 0, "failed": 0, "skipped_cap": 0, "calls": 0, "batches_submitted": 0,
             "batches_collected": 0, "batches_waiting": 0, "input_tokens": 0, "output_tokens": 0, "mode": "batch"}
    pending.setdefault("batches", [])
    waiting_keys = {k for b in pending["batches"] for ks in b["chunks"].values() for k in ks}
    todo = [p for p in pairs if p[0] not in waiting_keys]
    stats["skipped_cap"] = max(0, len(todo) - max_new)
    todo = todo[:max_new]

    # 1) 새 묶음 제출
    if todo:
        reqs, chunks, items = [], {}, {}
        for n, start in enumerate(range(0, len(todo), batch_size)):
            chunk = todo[start:start + batch_size]
            cid = f"c{n}"
            chunks[cid] = [k for k, _ in chunk]
            for k, it in chunk:
                items[k] = {"title": it.get("title", ""), "summary": it.get("summary", "")}
            reqs.append({"custom_id": cid, "params": {
                "model": model, "max_tokens": 110 * len(chunk) + 200,
                "messages": [{"role": "user", "content": build_prompt([(i, chunk[i][1]) for i in range(len(chunk))])}]}})
        try:
            r = http("POST", BATCH_URL, {"requests": reqs})
            pending["batches"].append({"id": r["id"], "submitted": today, "model": model, "chunks": chunks, "items": items})
            stats["batches_submitted"] += 1
            stats["asked"] += len(todo)
            stats["calls"] += len(reqs)
        except Exception as e:
            print(f"    [경고] 사건 분류 묶음 제출 실패(다음 실행 때 다시): {e}", file=sys.stderr)

    # 2) 끝난 묶음 결과 받기 (wait_sec 까지 기다림, 못 받은 것은 pending 에 남김)
    deadline = clock() + wait_sec
    while pending["batches"]:
        still = []
        for b in pending["batches"]:
            try:
                info = http("GET", f"{BATCH_URL}/{b['id']}")
            except Exception as e:
                print(f"    [경고] 묶음 상태 확인 실패: {e}", file=sys.stderr)
                still.append(b)
                continue
            if info.get("processing_status") != "ended":
                still.append(b)
                continue
            try:
                lines = http("GET", info.get("results_url") or f"{BATCH_URL}/{b['id']}/results", raw=True)
            except Exception as e:
                print(f"    [경고] 묶음 결과 받기 실패: {e}", file=sys.stderr)
                still.append(b)
                continue
            stats["batches_collected"] += 1
            for line in (lines or "").splitlines():
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                keys = b["chunks"].get(row.get("custom_id"), [])
                res = {}
                if (row.get("result") or {}).get("type") == "succeeded":
                    msg = row["result"]["message"]
                    u = msg.get("usage") or {}
                    stats["input_tokens"] += int(u.get("input_tokens", 0) or 0)
                    stats["output_tokens"] += int(u.get("output_tokens", 0) or 0)
                    text = "".join(p.get("text", "") for p in msg.get("content", []) if p.get("type") == "text")
                    res = parse_response(text, set(range(len(keys))))
                for i, k in enumerate(keys):
                    if i in res:
                        r = res[i]
                        it = b["items"].get(k, {})
                        r["quote_ok"] = quote_found(r["quote"], it.get("title", "") + " " + it.get("summary", ""))
                        r["m"] = b.get("model", model)
                        r["t"] = today
                        cache[k] = r
                        stats["answered"] += 1
                    else:
                        stats["failed"] += 1
        pending["batches"] = still
        if not still or clock() >= deadline:
            break
        sleep(poll_sec)
    stats["batches_waiting"] = len(pending["batches"])
    # 하루 넘게 안 끝난 묶음(만료)은 버리고 다음 실행 때 다시 질의
    pending["batches"] = [b for b in pending["batches"] if not b.get("submitted") or not today or b["submitted"] >= _days_before(today, 2)]
    return stats


def _days_before(day, n):
    from datetime import date, timedelta
    try:
        return (date.fromisoformat(day) - timedelta(days=n)).isoformat()
    except Exception:
        return ""


def tier_of(result, stock_flag=False, ai_active=True):
    """분류 결과 -> 화면 단계. result 없으면 규칙(지금 방식): 증시어는 접기, 나머지 일반."""
    if not result:
        return "fold" if (stock_flag and ai_active) else "gen"
    same = result.get("same")
    if same == "n":
        # 11-24 실측: Haiku 의 "다른 회사" 판정이 사내 등록명/곁가지 언급과 자주 헷갈림 -> 숨기지 않고 접기.
        # 이름 오탐은 앞 단계(단어 경계/보류함)가 이미 걸러 냄. 숨기려면 EVENT_HIDE_OTHER=on
        return "other" if os.environ.get("EVENT_HIDE_OTHER", "off").lower() == "on" else "fold"
    if same != "y":
        return "gen"  # 애매하면 보류 = 지금처럼 일반 기사(20일)
    role, typ = result.get("role"), result.get("type")
    if typ in BIZ_TYPES and role in ("lead", "part") and result.get("quote_ok"):
        return "biz"
    if role == "side" or typ == "stock":
        return "fold"
    return "gen"


def ev_payload(result, tier):
    """company_links 에 붙일 짧은 사건 정보 (화면용)"""
    ev = {"tier": tier}
    if result:
        ev["role"] = ROLES.get(result.get("role"), "불명")
        ev["type"] = EVENT_TYPES.get(result.get("type"), "기타")
        if result.get("date"):
            ev["date"] = result["date"]
        if result.get("quote") and result.get("quote_ok"):
            ev["quote"] = result["quote"]
        if result.get("same") == "?":
            ev["hold"] = True
        if result.get("same") == "n":
            ev["maybe_other"] = True
    return ev


# ---------------- 같은 사건 묶기 ----------------
_NUM_RE = re.compile(r"\d[\d,.]*\s*(?:억|조|만|%|대|톤|명|MW|GW|㎡|평)")


def _bigrams(s):
    s = re.sub(r"[^0-9a-zA-Z가-힣]", "", s or "").lower()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _sim(a, b):
    A, B = _bigrams(a), _bigrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def same_event(x, y, days=3):
    """x, y: dict(title, date(YYYY-MM-DD), type). 같은 회사 안에서 같은 사건인지."""
    if x.get("type") and y.get("type") and x["type"] != y["type"]:
        return False
    try:
        from datetime import date
        dx, dy = date.fromisoformat(x["date"][:10]), date.fromisoformat(y["date"][:10])
    except Exception:
        return False
    if bool(x.get("dart")) != bool(y.get("dart")):
        # DART 공시와 뉴스: 같은 종류이고 뉴스가 공시 1일 전 ~ 5일 뒤면 같은 소식(제목은 서로 달라서 안 봄)
        if not (x.get("type") and y.get("type")):
            return False
        dd, dn = (dx, dy) if x.get("dart") else (dy, dx)
        return -1 <= (dn - dd).days <= 5
    if x.get("dart") and y.get("dart"):
        return False  # 공시끼리는 서로 다른 계약으로 봄
    if abs((dx - dy).days) > days:
        return False
    s = _sim(x["title"], y["title"])
    if s >= 0.42:
        return True
    nx, ny = set(_NUM_RE.findall(x["title"])), set(_NUM_RE.findall(y["title"]))
    if nx & ny and s >= 0.22:
        return True
    return difflib.SequenceMatcher(None, _norm(x["title"]), _norm(y["title"])).ratio() >= 0.6


def group_events(items, days=3):
    """items: [dict(idx, title, date, type, major)] (한 회사분). 반환: [[idx...], ...] 대표(첫 원소) 먼저.
    대표 = 메이저 언론사 우선, 그다음 가장 이른 기사."""
    order = sorted(items, key=lambda x: x["date"])
    groups = []
    for it in order:
        for g in groups:
            if any(same_event(it, m, days) for m in g):
                g.append(it)
                break
        else:
            groups.append([it])
    out = []
    for g in groups:
        g.sort(key=lambda x: (1 if x.get("dart") else 0, 0 if x.get("major") else 1, x["date"]))  # 대표는 뉴스 우선
        out.append([x["idx"] for x in g])
    return out
