#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
(2026-11-24) 보관소(store/archive.json) + 사건 분류 캐시 -> 화면용 기사 목록 (data/news.json 의 articles)

- 회사별로 판단: 같은 기사라도 A사에는 사업 사건(biz), B사에는 곁가지(fold)일 수 있음
- 표시 기간: biz 90일, gen/fold 20일 (환경변수 BIZ_DAYS / NEWS_LOOKBACK_DAYS)
- other(다른 회사 확실) 는 화면에서 빼고 hidden 목록으로 돌려줌
- 같은 사건 묶기: 회사마다 같은 단계/같은 종류/게시일 3일 안/제목 비슷 -> ev.g(묶음 번호), ev.n(묶음 크기), ev.lead(대표)
- 기존 화면/허브 대시보드가 읽는 필드(companies, company_links, team, cell, reps, title...)는 그대로 둠
"""

from datetime import datetime, timedelta

import event_classify as ec


def build_view(archive, cache, companies_by_code, now, biz_days=90, gen_days=20, ai_active=True):
    biz_cut = (now - timedelta(days=biz_days)).isoformat()
    gen_cut = (now - timedelta(days=gen_days)).isoformat()
    out, hidden = [], []
    stats = {"biz": 0, "gen": 0, "fold": 0, "other": 0, "unclassified": 0, "old_drop": 0}
    for key, a in archive.get("articles", {}).items():
        pub = a.get("pubDate_iso") or ""
        links = []
        for l in a.get("links", []):
            comp = companies_by_code.get(l.get("code"))
            if not comp:
                continue  # 고객 목록에서 빠진 회사
            res = cache.get(f"{l['code']}|{key}")
            if res is None:
                stats["unclassified"] += 1
            tier = ec.tier_of(res, bool(a.get("stock")), ai_active)
            if tier == "biz":
                ok = pub >= biz_cut
            elif tier == "other":
                if pub >= gen_cut:
                    hidden.append({"code": l["code"], "name": comp["name"], "title": a.get("title", ""),
                                   "link": a.get("link", ""), "originallink": a.get("originallink", ""),
                                   "pubDate_iso": pub, "reason": "ai_other",
                                   "quote": (res or {}).get("quote", "")})
                stats["other"] += 1
                continue
            else:
                ok = pub >= gen_cut
            if not ok:
                stats["old_drop"] += 1
                continue
            stats[tier] += 1
            ext = sorted(set(r for r in comp.get("ext_rep", []) if r))
            link = {"code": l["code"], "name": comp["name"],
                    "matched_sub_names": list(l.get("matched_sub_names", [])),
                    "team": list(comp.get("team", [])), "cell": list(comp.get("cell", [])), "reps": ext,
                    "ev": ec.ev_payload(res, tier)}
            if l.get("judged_by"):
                link["judged_by"] = l["judged_by"]
            links.append(link)
        if not links:
            continue
        art = {k: a[k] for k in ec_fields() if k in a}
        art["companies"] = [l["name"] for l in links]
        art["company_links"] = links
        for f in ("team", "cell", "reps", "matched_sub_names"):
            vals = []
            for l in links:
                for v in l[f]:
                    if v not in vals:
                        vals.append(v)
            art[f] = vals
        art["tier"] = min((l["ev"]["tier"] for l in links), key=lambda t: ec.TIER_RANK[t])
        out.append(art)

    _group(out)
    out.sort(key=lambda x: x.get("pubDate_iso", ""), reverse=True)
    out.sort(key=lambda x: ec.TIER_RANK[x["tier"]])
    return out, hidden, stats


def ec_fields():
    return ("title", "description", "link", "originallink", "press", "domain", "major",
            "pubDate_iso", "pubDate_display", "tag_key", "tag_label", "tag_color", "stock", "judged_by", "ai_reason", "source")


def _group(articles):
    by_code = {}
    for i, a in enumerate(articles):
        for l in a["company_links"]:
            by_code.setdefault(l["code"], []).append((i, l))
    for code, rows in by_code.items():
        by_tier = {}
        for i, l in rows:
            by_tier.setdefault(l["ev"]["tier"], []).append((i, l))
        n = 0
        for tier, trs in by_tier.items():
            items = [{"idx": i, "title": articles[i].get("title", ""),
                      "date": (articles[i].get("pubDate_iso") or "")[:10],
                      "type": l["ev"].get("type"), "major": articles[i].get("major"),
                      "dart": articles[i].get("source") == "DART"} for i, l in trs]
            lmap = {i: l for i, l in trs}
            for g in ec.group_events(items):
                n += 1
                for j, idx in enumerate(g):
                    ev = lmap[idx]["ev"]
                    ev["g"] = n
                    ev["n"] = len(g)
                    if j == 0:
                        ev["lead"] = True


def summary_material(view_articles, code):
    """AI 요약 재료: 이 회사의 biz -> gen 순(fold 제외), 묶음 대표만"""
    rows = []
    for a in view_articles:
        for l in a["company_links"]:
            if l["code"] == code and l["ev"]["tier"] in ("biz", "gen") and l["ev"].get("lead", True):
                rows.append((ec.TIER_RANK[l["ev"]["tier"]], a))
    rows.sort(key=lambda r: r[1].get("pubDate_iso", ""), reverse=True)
    rows.sort(key=lambda r: r[0])
    return [a for _, a in rows]
