#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
(2026-11-24) 기사 누적 보관소 / 페이지 넘김 / 제외 사유 기록 / 화면용 news.json 만들기

store/ (허브는 data/ 만 가져가므로 화면 파일 크기에 영향 없음)
  archive.json        회사-기사 연결을 ARCHIVE_DAYS(기본 180일) 동안 쌓아 둠 (원본 링크 기준 한 건)
  classify_cache.json 사건 분류 결과(회사코드|링크 -> 결과), 보관소에서 빠진 기사는 같이 지움
  fetch_state.json    검색어별 마지막 수집 시각/페이지 수 (다음 날 2일 겹쳐 받기)
  filter_log.json     오늘 제외 사유별 건수 + 표본(스니펫 이름 없음/나열형), 최근 30일 건수 기록. 화면에는 안 씀
"""

import json
import os
import random
from datetime import datetime, timedelta

NAVER_MAX_START = 1000  # 네이버 뉴스 검색 start 최댓값(공식 문서: 1~1000)


# ---------------- 파일 ----------------
def load_json_file(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json_file(path, data, compact=True):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def save_lines(path, data, key="articles"):
    """{..., key: {k: v}} 를 한 항목 한 줄로 저장 (git 기록에서 날마다 바뀐 줄만 보이게, 크기도 작게)"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    head = {k: v for k, v in data.items() if k != key}
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("{")
        for k, v in head.items():
            f.write(json.dumps(k, ensure_ascii=False) + ":" + json.dumps(v, ensure_ascii=False, separators=(",", ":")) + ",\n")
        f.write(json.dumps(key) + ":{")
        items = list(data.get(key, {}).items())
        for i, (k, v) in enumerate(items):
            f.write(("\n" if i == 0 else ",\n") + json.dumps(k, ensure_ascii=False) + ":" +
                    json.dumps(v, ensure_ascii=False, separators=(",", ":")))
        f.write("\n}}\n")
    os.replace(tmp, path)


# ---------------- 페이지 넘김 ----------------
def page_starts(display=100, max_pages=10):
    """start 값 목록. start + display - 1 <= 1000 을 지켜 마지막 페이지가 1000번째 결과에서 끝나게 함
    (display=100 이면 1, 101, ..., 901 = 10페이지)."""
    starts = []
    s = 1
    while len(starts) < max_pages and s + display - 1 <= NAVER_MAX_START:
        starts.append(s)
        s += display
    return starts


def boundary_for(state_entry, now, first_days, overlap_days=2):
    """이 검색어를 어디까지 넘겨 받을지(이보다 오래된 기사가 나오면 멈춤).
    처음: now - first_days. 이후: 마지막 수집 시각 - overlap_days (단 first_days 보다 더 과거로는 안 감)."""
    floor = now - timedelta(days=first_days)
    last = (state_entry or {}).get("last_fetch")
    if not last:
        return floor
    try:
        b = datetime.fromisoformat(last) - timedelta(days=overlap_days)
    except Exception:
        return floor
    return max(b, floor)


def fetch_paged(fetch_page, parse_dt, boundary, display=100, max_pages=10):
    """fetch_page(start) -> items. 최신순 결과를 boundary 보다 오래된 기사가 나올 때까지(또는 끝/상한까지) 받음.
    반환: (items, info) info = {pages, stop: 'short'|'boundary'|'empty'|'cap'}"""
    items = []
    info = {"pages": 0, "stop": "cap"}
    for start in page_starts(display, max_pages):
        page = fetch_page(start) or []
        info["pages"] += 1
        if not page:
            info["stop"] = "empty"
            break
        items.extend(page)
        if len(page) < display:
            info["stop"] = "short"
            break
        dts = [d for d in (parse_dt(it) for it in page) if d is not None]
        if not dts or min(dts) < boundary:
            info["stop"] = "boundary"
            break
    return items, info


# ---------------- 제외 사유 기록 ----------------
class FilterLog:
    REASONS = ("received", "old", "no_name", "list_type", "ctx", "ctxwin", "excl", "mq_reject",
               "mq_uncertain", "dup", "stock_signal", "stock_excluded", "kept")

    def __init__(self, sample_n=40, seed=None):
        self.counts = {r: 0 for r in self.REASONS}
        self.sample_n = sample_n
        self.samples = {"no_name": [], "list_type": []}
        self._seen = {"no_name": 0, "list_type": 0}
        self._rng = random.Random(seed)

    def add(self, reason, n=1):
        self.counts[reason] = self.counts.get(reason, 0) + n

    def sample(self, reason, row):
        """저수지 표본(하루 sample_n 건)"""
        if reason not in self.samples:
            return
        self._seen[reason] += 1
        k = self._seen[reason]
        bucket = self.samples[reason]
        if len(bucket) < self.sample_n:
            bucket.append(row)
        else:
            j = self._rng.randrange(k)
            if j < self.sample_n:
                bucket[j] = row

    def save(self, path, generated_at, extra=None):
        old = load_json_file(path, {})
        hist = [h for h in old.get("history", []) if h.get("date") != generated_at[:10]]
        hist.append({"date": generated_at[:10], "counts": self.counts})
        out = {"generated_at": generated_at, "counts": self.counts, "samples": self.samples,
               "history": hist[-30:]}
        if extra:
            out.update(extra)
        save_json_file(path, out, compact=False)


# ---------------- 누적 보관소 ----------------
ARCH_FIELDS = ("title", "description", "link", "originallink", "press", "domain", "major",
               "pubDate_iso", "pubDate_display", "tag_key", "tag_label", "tag_color", "stock", "judged_by", "ai_reason", "source")


def article_key(a):
    return a.get("originallink") or a.get("link") or a.get("title", "")


def merge_into_archive(archive, articles, now_iso):
    """오늘 채택 기사(company_links 포함)를 보관소에 합침. 반환: 새로 들어온 기사 수"""
    store = archive.setdefault("articles", {})
    added = 0
    for a in articles:
        k = article_key(a)
        if not k:
            continue
        cur = store.get(k)
        links = [{"code": l.get("code"), "name": l.get("name"),
                  "matched_sub_names": list(l.get("matched_sub_names", [])),
                  **({"judged_by": l["judged_by"]} if l.get("judged_by") else {})}
                 for l in a.get("company_links", []) if l.get("code")]
        if cur is None:
            cur = {f: a[f] for f in ARCH_FIELDS if f in a}
            cur["first_seen"] = now_iso
            cur["links"] = links
            store[k] = cur
            added += 1
        else:
            for f in ARCH_FIELDS:  # 메이저 언론 기사로 바뀐 경우 등 최신 정보로
                if f in a and f not in ("pubDate_iso", "pubDate_display"):
                    cur[f] = a[f]
            have = {l["code"]: l for l in cur.get("links", [])}
            for l in links:
                if l["code"] in have:
                    for s in l["matched_sub_names"]:
                        if s not in have[l["code"]]["matched_sub_names"]:
                            have[l["code"]]["matched_sub_names"].append(s)
                else:
                    cur.setdefault("links", []).append(l)
    return added


def prune_archive(archive, now, days):
    cutoff = (now - timedelta(days=days)).isoformat()
    store = archive.get("articles", {})
    drop = [k for k, a in store.items() if (a.get("pubDate_iso") or "") < cutoff]
    for k in drop:
        del store[k]
    return len(drop)


def prune_cache(cache, archive):
    live = set()
    for k, a in archive.get("articles", {}).items():
        for l in a.get("links", []):
            live.add(f"{l['code']}|{k}")
    drop = [k for k in cache if k not in live]
    for k in drop:
        del cache[k]
    return len(drop)
