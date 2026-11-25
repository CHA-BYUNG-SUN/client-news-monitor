#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
(2026-11-24) 첫 1회: 과거 data/news.json 기록으로 store/archive.json 채우기 (네이버 호출 0회)

과거 기사를 '지금 필터'(이름/나열형, 예외 설정, 매칭 품질)로 다시 판정해서 통과한 회사-기사 연결만 넣는다.
20일보다 오래된 기사는 사업 단어가 있을 때만 넣는다(fetch_news.py 와 같은 규칙).

사용:
  python scripts/backfill_archive.py --git [--every-days 5]        # 저장소 git 기록에서 (actions/checkout fetch-depth: 0 필요)
  python scripts/backfill_archive.py --files 과거1.json 과거2.json ...  # 내려받아 둔 파일에서
  옵션: --archive-days 180 --lookback-days 20 --out store/archive.json --dry (저장 안 하고 숫자만)
이미 있는 archive.json 이 있으면 합친다(기존 기사 유지).
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_news as fn  # noqa: E402
import match_quality as mq  # noqa: E402
import news_store as ns  # noqa: E402
import event_classify as ec  # noqa: E402


def git_snapshots(every_days, since):
    out = subprocess.run(["git", "log", "--format=%H %cI", "--", "data/news.json"],
                         cwd=fn.BASE_DIR, capture_output=True, text=True, check=True).stdout.split("\n")
    picked, last_day = [], None
    for line in out:  # 최신 -> 과거
        if not line.strip():
            continue
        sha, when = line.split(" ", 1)
        day = datetime.fromisoformat(when.strip()).date()
        if day < since.date():
            break
        if last_day is None or (last_day - day).days >= every_days:
            picked.append((sha, day))
            last_day = day
    for sha, day in picked:
        raw = subprocess.run(["git", "show", f"{sha}:data/news.json"], cwd=fn.BASE_DIR,
                             capture_output=True, check=True).stdout
        yield str(day), json.loads(raw.decode("utf-8"))


def file_snapshots(paths):
    for p in paths:
        with open(p, encoding="utf-8") as f:
            yield os.path.basename(p), json.load(f)


def passes(company, override, longer, gex, title, desc):
    """fetch_news.py 와 같은 순서의 판정. 반환: (통과?, 증시어?)"""
    o = override or {}
    sq = o.get("search_queries") or [company.get("query", company["name"])]
    mn = o.get("match_names") or sq
    strict = bool(o.get("strict_name"))
    if not any(fn.is_relevant_article(title, desc, m, strict) for m in mn):
        return False, False
    comb = f"{title} {desc}"
    ck, ek = o.get("context_keywords"), o.get("exclude_keywords")
    if ck and not fn.contains_any_keyword(comb, ck):
        return False, False
    if ck and o.get("context_window") and not fn.context_near_name(title, desc, mn, strict, ck, int(o["context_window"])):
        return False, False
    if ek and fn.contains_any_keyword(comb, ek):
        return False, False
    v, _ = mq.judge(title, desc, mn, company, override, longer_names=longer, common=(o.get("name_risk") == "common"))
    if v != "ok":
        return False, False
    return True, (not ck) and fn.contains_any_keyword(comb, gex)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--git", action="store_true")
    ap.add_argument("--files", nargs="*")
    ap.add_argument("--every-days", type=int, default=5)
    ap.add_argument("--archive-days", type=int, default=int(os.environ.get("ARCHIVE_DAYS", "180")))
    ap.add_argument("--lookback-days", type=int, default=int(os.environ.get("NEWS_LOOKBACK_DAYS", "20")))
    ap.add_argument("--out", default=os.path.join(fn.STORE_DIR, "archive.json"))
    ap.add_argument("--now")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    now = datetime.fromisoformat(a.now) if a.now else datetime.now(fn.KST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=fn.KST)
    keep_cut = (now - timedelta(days=a.archive_days)).isoformat()
    gen_cut = (now - timedelta(days=a.lookback_days)).isoformat()

    cfg = fn.load_json("companies.json")["companies"]
    ov = fn.load_overrides()
    gex = fn.load_global_exclude_keywords()
    longer = mq.build_longer_names(cfg, ov)
    by_code = {c["code"]: c for c in cfg if c.get("code")}
    codes_by_name = {}
    for c in cfg:
        codes_by_name.setdefault(c["name"], []).append(c.get("code"))

    archive = ns.load_json_file(a.out, {"version": 1, "articles": {}})
    before = len(archive.get("articles", {}))
    seen_pairs = set()
    stats = {"snapshots": 0, "pairs": 0, "passed": 0, "too_old": 0, "old_no_biz": 0, "rejected": 0}
    snaps = git_snapshots(a.every_days, now - timedelta(days=a.archive_days)) if a.git else file_snapshots(a.files or [])
    for label, data in snaps:
        stats["snapshots"] += 1
        batch = []
        for art in data.get("articles", []):
            pub = art.get("pubDate_iso") or ""
            if not pub:
                continue
            if "company_links" in art:
                codes = [l.get("code") for l in art["company_links"] if l.get("code")] or \
                        [c for l in art["company_links"] for c in codes_by_name.get(l.get("name"), [])]
            else:
                names = art.get("companies") or ([art["company"]] if art.get("company") else [])
                codes = [c for n in names for c in codes_by_name.get(n, [])]
            title, desc = art.get("title", ""), art.get("description", "")
            links = []
            stock = False
            for code in dict.fromkeys(codes):
                comp = by_code.get(code)
                k = (code, art.get("originallink") or art.get("link"))
                if not comp or k in seen_pairs:
                    continue
                seen_pairs.add(k)
                stats["pairs"] += 1
                if pub < keep_cut:
                    stats["too_old"] += 1
                    continue
                if pub < gen_cut and not ec.biz_hint(title, desc):
                    stats["old_no_biz"] += 1
                    continue
                ok, st = passes(comp, ov.get(code), longer, gex, title, desc)
                if not ok:
                    stats["rejected"] += 1
                    continue
                stats["passed"] += 1
                stock = stock or st
                links.append({"code": code, "name": comp["name"],
                              "matched_sub_names": fn.find_matched_sub_names(title, desc, comp.get("sub_names", []))})
            if links:
                b = {f: art[f] for f in ns.ARCH_FIELDS if f in art}
                b["company_links"] = links
                b.pop("judged_by", None)
                if stock:
                    b["stock"] = True
                batch.append(b)
        ns.merge_into_archive(archive, batch, f"backfill:{label}")
        print(f"  {label}: 기사 {len(data.get('articles', []))} -> 보관 {len(archive['articles'])}")
    ns.prune_archive(archive, now, a.archive_days)
    print(f"백필: {json.dumps(stats, ensure_ascii=False)} / 보관 {before} -> {len(archive['articles'])}")
    if not a.dry:
        ns.save_lines(a.out, archive)
        print("저장:", a.out)


if __name__ == "__main__":
    main()
