#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026年の記事が少ない/無い企業について、補完ソース候補を一括で洗い出す。

  1) PR TIMES のキーワード検索で社名が一致する企業ページ（company_id）を探し、
     企業RSSの2026年リリース有無と、リリース本文の「会社HP」から公式ドメインを取る。
  2) 公式ドメインが分かったら、そのサイトの「お知らせ／ニュース」ページを探して
     日付＋タイトルが取れる URL を検証する。

  python3 discover_supplements.py targets.json out.json [--workers 4] [--limit N] [--offset N]
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
import time
import unicodedata
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import webnews as W

CORP_RE = re.compile(r"(株式会社|有限会社|合同会社|合資会社|合名会社|\(株\)|（株）|㈱|\(有\)|（有）)")
HOST_BLOCK = W.BLOCK_HOSTS

INDUSTRY = {
    "ec-mailorder": ("化粧品", "コスメ", "美容", "スキンケア", "健康食品", "サプリ", "通販", "通信販売",
                     "美容液", "洗顔", "育毛", "発毛", "ダイエット", "化粧水", "エイジング", "食品", "肌"),
    "ec-support": ("EC", "ネットショップ", "通販", "マーケティング", "支援", "物流", "決済", "カート",
                   "代理店", "コンサル", "CRM", "広告", "SaaS", "システム", "アプリ"),
    "ec-brands": ("EC", "通販", "ブランド", "ショップ", "商品", "販売", "新商品", "D2C", "オンライン",
                  "コスメ", "化粧品", "食品", "アパレル", "雑貨", "家電"),
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    for w in ("株式会社", "有限会社", "合同会社", "(株)", "(有)", "㈱", "㈲"):
        s = s.replace(w, "")
    s = re.sub(r"[\s・,\.\u3000'\"「」『』]", "", s)
    return s


def name_match(our: str, theirs: str) -> bool:
    a, b = norm(our), norm(theirs)
    if not a or not b:
        return False
    if a == b:
        return True
    return len(a) >= 4 and len(b) >= 4 and (a in b or b in a)


ART = re.compile(r"<article.*?</article>", re.S)


def prtimes_search(word: str):
    u = "https://prtimes.jp/main/action.php?run=html&page=searchkey&search_word=" + urllib.parse.quote(word)
    st, final, h = W.http_get(u, timeout=12, retries=0)
    if st != 200:
        return []
    out = []
    for b in ART.findall(h):
        m = re.search(r'href="(/main/html/rd/p/(\d+)\.(\d+)\.html)"', b)
        t = re.search(r"<h3[^>]*>(.*?)</h3>", b, re.S)
        d = re.search(r"<time>(.*?)</time>", b, re.S)
        c = re.search(r'searchrlp/company_id/(\d+)"[^>]*>(.*?)</a>', b, re.S)
        out.append({
            "rel": m.group(1) if m else "",
            "cid": str(int(m.group(3))) if m else "",
            "title": html_lib.unescape(re.sub("<[^>]+>", "", t.group(1))).strip() if t else "",
            "date": d.group(1).strip() if d else "",
            "company": html_lib.unescape(re.sub("<[^>]+>", "", c.group(2))).strip() if c else "",
        })
    return out


def prtimes_feed(cid: str):
    st, final, raw = W.http_get(f"https://prtimes.jp/companyrdf.php?company_id={cid}", timeout=12, retries=0)
    if st != 200:
        return []
    items = []
    for m in re.finditer(r"<item[^>]*>(.*?)</item>", raw, re.S):
        blk = m.group(1)
        t = re.search(r"<title>(.*?)</title>", blk, re.S)
        d = re.search(r"<dc:date>(.*?)</dc:date>", blk)
        l = re.search(r"<link>(.*?)</link>", blk)
        items.append({"title": html_lib.unescape(re.sub("<[^>]+>", "", t.group(1))).strip() if t else "",
                      "date": d.group(1).strip() if d else "", "link": l.group(1).strip() if l else ""})
    items.sort(key=lambda i: i["date"], reverse=True)
    return items


def company_hp_from_release(rel: str):
    """リリース本文の「会社HP：https://…」や会社情報リンクから公式ドメインを推定。"""
    st, final, h = W.http_get("https://prtimes.jp" + rel, timeout=12, retries=0)
    if st != 200:
        return ""
    m = re.search(r"(?:会社HP|会社サイト|コーポレートサイト|企業サイト|ホームページ)\s*[:：]?\s*<a[^>]*href=\"(https?://[^\"]+)\"", h, re.I)
    if m:
        return m.group(1)
    m = re.search(r"(?:会社HP|会社サイト|コーポレートサイト|企業サイト)\s*[:：]\s*(https?://[^\s<]+)", re.sub(r"<[^>]+>", " ", h))
    if m:
        return m.group(1)
    return ""


def news_page_for(home_url: str, name: str, board: str):
    st, final, home = W.http_get(home_url, timeout=12, retries=0)
    if st != 200:
        return None
    title = W.strip_tags((re.search(r"<title[^>]*>(.*?)</title>", home, re.S | re.I) or [None, ""])[1] if re.search(r"<title", home, re.I) else "")
    best = None
    for p in W.find_news_links(home, final, limit=5):
        if re.search(r"/detail/|/news/\d|information/\d{4}", p):
            continue
        st2, final2, h2 = W.http_get(p, timeout=12, retries=0)
        if st2 != 200:
            continue
        ents = W.parse_entries(h2, final2)
        if len(ents) < 2:
            continue
        n2026 = sum(1 for e in ents if e["date"].startswith("2026"))
        latest = max(e["date"] for e in ents)
        cand = {"news_url": final2, "n": len(ents), "n2026": n2026, "latest": latest,
                "entries": ents[:6], "brand": title[:60]}
        if best is None or (n2026, latest, len(ents)) > (best["n2026"], best["latest"], best["n"]):
            best = cand
    return best


def discover(t: dict) -> dict:
    name = t["name"]
    word = CORP_RE.sub("", unicodedata.normalize("NFKC", name)).strip() or name
    word = re.sub(r"(ホールディングス|グループ)$", "", word).strip() or word
    res = prtimes_search(word)
    hits = [r for r in res if name_match(name, r["company"])]
    by_cid = {}
    for r in hits:
        by_cid.setdefault(r["cid"], []).append(r)

    out = {"id": t["id"], "board": t["board"], "name": name, "word": word,
           "prtimes": [], "site": None, "verified": False}

    best = None
    for cid, rels in list(by_cid.items())[:3]:
        items = prtimes_feed(cid)
        y2026 = [i for i in items if i["date"].startswith("2026")]
        cand = {"cid": cid, "company": rels[0]["company"], "n": len(items), "y2026": len(y2026),
                "latest": items[0]["date"][:10] if items else "", "top": [i["title"][:70] for i in items[:3]]}
        if y2026:
            # 2026年のリリースがある → 業種語 or 会社HP一致で同一性確認
            keys = INDUSTRY.get(t["board"], ())
            kw_hit = any(any(k.lower() in i["title"].lower() for k in keys) for i in y2026)
            hp = ""
            for r in rels[:3]:
                hp = company_hp_from_release(r["rel"])
                if hp:
                    break
            cand["hp"] = hp
            cand["kw_hit"] = kw_hit
            cand["same_name_only"] = not (kw_hit or hp)
            if not cand["same_name_only"]:
                best = cand
                break
        out["prtimes"].append(cand)
    if best is None and out["prtimes"]:
        best = out["prtimes"][0]
    out["prtimes_best"] = best

    # 公式サイト: PR TIMESの会社HP → そのニュース欄、無ければ検索エンジン
    site = None
    if best and best.get("hp") and not best.get("same_name_only"):
        site = news_page_for(best["hp"], name, t["board"])
        if site:
            site["via"] = "prtimes_hp"
    if not site:
        import discover_sites as D
        cands = D.search_results(name + " 公式サイト") or D.search_results(name + " 公式")
        for u in cands[:4]:
            st, final, home = W.http_get(u, timeout=10, retries=0)
            if st != 200:
                continue
            nt = norm(W.strip_tags((re.search(r"<title[^>]*>(.*?)</title>", home, re.S | re.I) or [None, ""])[1] if re.search(r"<title", home, re.I) else ""))
            if norm(name) and norm(name) not in nt:
                continue
            site = news_page_for(u, name, t["board"])
            if site:
                site["via"] = "search"
                break
    out["site"] = site
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("targets")
    ap.add_argument("out")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--no-site", action="store_true", help="PR TIMES だけで止める（検索エンジンを使わない）")
    args = ap.parse_args(argv)

    targets = json.load(open(args.targets, encoding="utf-8"))
    if args.offset:
        targets = targets[args.offset:]
    if args.limit:
        targets = targets[: args.limit]

    out = []
    def work(t):
        try:
            r = discover(t)
        except Exception as exc:  # noqa: BLE001
            r = {"id": t["id"], "board": t["board"], "name": t["name"], "err": str(exc)[:120]}
        b = r.get("prtimes_best") or {}
        s = r.get("site") or {}
        print(f"{t['name']}: PRT cid={b.get('cid','')} 2026={b.get('y2026',0)} kw={b.get('kw_hit')} "
              f"hp={b.get('hp','')[:30]} | site={(s.get('news_url') or '')[:50]} 2026={s.get('n2026',0)}", flush=True)
        return r

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for r in ex.map(work, targets):
            out.append(r)
    json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    pr = sum(1 for r in out if ((r.get("prtimes_best") or {}).get("y2026", 0) > 0 and not (r.get("prtimes_best") or {}).get("same_name_only")))
    st = sum(1 for r in out if (r.get("site") or {}).get("n2026", 0) > 0)
    print(f"\n{len(out)}社: PR TIMESに2026年あり {pr}社 / 公式サイトに2026年あり {st}社")


if __name__ == "__main__":
    main()
