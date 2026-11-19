#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
既存ボードの「2026年の記事が取れていない社」へ足す補完ソースを、検証つきで一括探索する。

discover_supplements.py の考え方（PR TIMES 企業RSS＋公式サイトHTML）をそのまま使い、
同名他社を落とすための確認を足したもの。結果は社ごとに JSON キャッシュするので、
時間切れで止まっても続きから再開できる。

  python3 expand_supplements.py targets.json cache_dir [--workers 3] [--max-seconds 240]

判定（採用条件）
  PR TIMES: 社名一致の企業ページがあり、その2026年リリースがある。
            さらに「会社HP のドメインが検索結果の公式候補と同じ」または
            「リリース見出しに業種語が入る」または「企業ページ名が完全一致」で同一性を確認。
  公式サイト: トップの title / 本文に社名（法人格の有無を問わず）が入り、
            ニュース欄の記事に2026年が1件以上ある。
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import webnews as W
import discover_sites as DS

CORP_RE = re.compile(r"(株式会社|有限会社|合同会社|合資会社|合名会社|\(株\)|（株）|㈱|\(有\)|（有）)")

# ボードごとの業種語（PR TIMES リリースの同一性チェックに使う）
INDUSTRY = {
    "smartnews-partners": ("広告", "マーケティング", "代理店", "デジタル", "メディア", "配信", "PR",
                           "プロモーション", "キャンペーン", "SNS", "動画", "クリエイティブ", "販促",
                           "SmartNews", "スマートニュース", "記事", "コンテンツ"),
    "caster-companies": ("EC", "通販", "ネットショップ", "マーケティング", "広告", "D2C", "CRM",
                         "カート", "物流", "決済", "SaaS", "支援", "コンサル", "アパレル", "ブランド"),
    "ec-companies": ("EC", "通販", "ネットショップ", "マーケティング", "広告", "D2C", "CRM",
                     "カート", "物流", "決済", "SaaS", "支援", "コンサル", "ショッピング", "販売"),
    "nrtv-companies": ("EC", "通販", "ネットショップ", "マーケティング", "広告", "D2C", "CRM",
                       "カート", "物流", "決済", "SaaS", "支援", "コンサル", "動画", "配信"),
    "shopify-partners": ("Shopify", "ショピファイ", "EC", "通販", "ネットショップ", "D2C",
                         "カート", "マーケティング", "支援", "構築", "代理店"),
}

# 公式サイトの本文がその業種かどうかを見る語（同名他社の除外用・弱い条件）
SITE_INDUSTRY = {
    "shopify-partners": ("shopify", "ec", "通販", "ネットショップ", "d2c", "カート", "マーケティング",
                         "ネット販売", "オンラインショップ", "amazon", "楽天", "支援"),
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    for w in ("株式会社", "有限会社", "合同会社", "(株)", "(有)", "㈱", "㈲"):
        s = s.replace(w, "")
    s = re.sub(r"[\s・,\.\u3000'\"「」『』&＆]", "", s)
    return s


def name_match(our: str, theirs: str) -> bool:
    a, b = norm(our), norm(theirs)
    if not a or not b:
        return False
    if a == b:
        return True
    return len(a) >= 4 and len(b) >= 4 and (a in b or b in a)


def full_name_match(our: str, text: str) -> bool:
    """法人格込みの社名がそのまま入っているか（同名他社を落とす強い条件）。

    「株式会社◯◯」「◯◯株式会社」の前後どちらの書き方でも認める。
    """
    a = unicodedata.normalize("NFKC", our or "").lower()
    a = re.sub(r"[\s\u3000]", "", a)
    t = unicodedata.normalize("NFKC", text or "").lower()
    t = re.sub(r"[\s\u3000]", "", t)
    if not a:
        return False
    if a in t:
        return True
    core = CORP_RE.sub("", a)
    if not core:
        return False
    legal = re.search(CORP_RE, a)
    word = legal.group(1) if legal else ""
    for w in ("株式会社", "有限会社", "合同会社"):
        if w + core in t or core + w in t:
            return True
    return bool(word) and word in t and core in t


ART = re.compile(r"<article.*?</article>", re.S)


def prtimes_search(word: str) -> list:
    u = "https://prtimes.jp/main/action.php?run=html&page=searchkey&search_word=" + urllib.parse.quote(word)
    st, final, h = W.http_get(u, timeout=15, retries=1)
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


def prtimes_feed(cid: str) -> list:
    st, final, raw = W.http_get(f"https://prtimes.jp/companyrdf.php?company_id={cid}", timeout=15, retries=1)
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


def prtimes_company_page(cid: str) -> dict:
    """企業ページから 会社名・会社HP を取る。"""
    st, final, h = W.http_get(f"https://prtimes.jp/main/html/searchrlp/company_id/{cid}", timeout=15, retries=0)
    if st != 200:
        st, final, h = W.http_get(f"https://prtimes.jp/main/html/company_id/{cid}", timeout=15, retries=0)
    if st != 200:
        return {}
    title = W.strip_tags((re.search(r"<title[^>]*>(.*?)</title>", h, re.S | re.I) or [None, ""])[1]) \
        if re.search(r"<title", h, re.I) else ""
    txt = W.strip_tags(h)
    hp = ""
    m = re.search(r'(?:会社HP|会社サイト|コーポレートサイト|企業サイト|ホームページ|URL)\s*[:：]?\s*'
                  r'<a[^>]*href="(https?://[^"]+)"', h, re.I)
    if m:
        hp = html_lib.unescape(m.group(1))
    else:
        m = re.search(r"(?:会社HP|会社サイト|コーポレートサイト|企業サイト|ホームページ)\s*[:：]\s*"
                      r"(https?://[^\s<\"']+)", txt, re.I)
        if m:
            hp = m.group(1)
    return {"title": title, "text": txt[:4000], "hp": hp}


def host(url: str) -> str:
    h = urllib.parse.urlparse(url or "").netloc.lower()
    return h[4:] if h.startswith("www.") else h


# 企業情報ディレクトリ・求人・まとめ・ニュース配信サイト（公式サイトではないもの）
# 社名で検索すると上位に出てくるので、ホスト単位で落とす。
DIRECTORY_HOSTS = (
    "compalyze", "baseconnect", "wantedly", "wantedly", "en-gage", "herp.careers",
    "openwork", "vorkers", "jobtalk", "rikunabi", "doda.jp", "type.jp", "en-japan",
    "mynavi", "offers.jp", "iroot", "ipros", "boxil", "itreview", "saasdirect",
    "productzine", "prtimes", "value-press", "dreamnews", "atpress", "nikkei.com",
    "ecnomikata", "netshop.impress", "itmedia", "markezine", "shopping.yahoo",
    "rakuten.co.jp", "amazon.", "indeed", "tenshoku", "kyujin", "reworker",
    "linkedin", "facebook", "instagram", "twitter.com", "x.com", "youtube",
    "note.com", "qiita", "zenn.dev", "speakerdeck", "docs.google",
    "houjin.jp", "houjin-bangou", "nayose", "kanri",
)


def is_directory(u: str) -> bool:
    h = host(u)
    return any(b in h for b in DIRECTORY_HOSTS)


def same_site(a: str, b: str) -> bool:
    ha, hb = host(a), host(b)
    if not ha or not hb:
        return False
    return ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha)


def news_page_from_home(home_url: str, our_name: str, board: str) -> dict:
    st, final, home = W.http_get(home_url, timeout=15, retries=1)
    if st != 200:
        return {"err": f"home {st}"}
    title = W.strip_tags((re.search(r"<title[^>]*>(.*?)</title>", home, re.S | re.I) or [None, ""])[1]) \
        if re.search(r"<title", home, re.I) else ""
    home_txt = W.strip_tags(home)
    ok_title = norm(our_name) and norm(our_name) in norm(title)
    # 法人格込み（株式会社◯◯ / ◯◯株式会社）で社名が書かれているか。
    # 検索で引っかかる「企業ディレクトリ」「まとめサイト」のページはここで落とす。
    ok_full = full_name_match(our_name, title) or full_name_match(our_name, home_txt[:6000])
    best = None
    links = W.find_news_links(home, final, limit=6)
    # サイト内の「ニュース」っぽい固定パスも候補に足す
    base = urllib.parse.urlparse(final)
    for p in ("/news/", "/news", "/topics/", "/information/", "/press/", "/info/"):
        u = f"{base.scheme}://{base.netloc}{p}"
        if u not in links:
            links.append(u)
    for p in links:
        p = p.split("#")[0]
        if re.search(r"/detail/|/news/\d|information/\d{4}|/entry/\d", p):
            continue
        # ニュース欄らしいパスだけを認める（#news だけの疑似アンカーや、まとめサイトの /fit 等を落とす）
        ppath = urllib.parse.urlparse(p).path.lower()
        if not re.search(r"(news|topics|information|press|release|whatsnew|what-s-new|info|blog|"
                         r"oshirase|announce|topics)", ppath):
            continue
        if not same_site(p, final):
            continue
        st2, final2, h2 = W.http_get(p, timeout=15, retries=0)
        if st2 != 200:
            continue
        ents = W.parse_entries(h2, final2)
        if len(ents) < 3:
            continue
        # 記事リンクも同じサイト内のものだけ数える（外部埋め込みの除去）
        same = [e for e in ents if same_site(e.get("link", ""), final2)]
        if len(same) >= 3:
            ents = same
        n2026 = sum(1 for e in ents if e["date"].startswith("2026"))
        if n2026 < 1:
            continue
        latest = max(e["date"] for e in ents)
        cand = {"news_url": final2, "n": len(ents), "n2026": n2026, "latest": latest,
                "top": [e["title"][:80] for e in ents[:5]], "dates": [e["date"] for e in ents[:5]],
                "page_title": W.strip_tags((re.search(r"<title[^>]*>(.*?)</title>", h2, re.S | re.I) or [None, ""])[1])[:90]}
        if best is None or (n2026, latest, len(ents)) > (best["n2026"], best["latest"], best["n"]):
            best = cand
    out = {"home": final, "title": title[:120], "ok_title": bool(ok_title), "ok_full": bool(ok_full),
           "home_has_name": bool(ok_title or ok_full), "news": best}
    if board in SITE_INDUSTRY:
        t = norm(home_txt[:8000])
        out["industry_hit"] = any(norm(k) in t for k in SITE_INDUSTRY[board])
    return out


def discover(t: dict) -> dict:
    board, name = t["board"], t["name"]
    word = CORP_RE.sub("", unicodedata.normalize("NFKC", name)).strip() or name
    word = re.sub(r"(ホールディングス|グループ)$", "", word).strip() or word

    res = prtimes_search(word)
    hits = [r for r in res if name_match(name, r["company"])]
    by_cid = {}
    for r in hits:
        by_cid.setdefault(r["cid"], []).append(r)

    out = {"id": t["id"], "board": board, "name": name, "word": word, "prtimes": [],
           "prtimes_ok": None, "site": None}

    keys = INDUSTRY.get(board, ())
    for cid, rels in list(by_cid.items())[:3]:
        items = prtimes_feed(cid)
        y2026 = [i for i in items if i["date"].startswith("2026")]
        page = prtimes_company_page(cid) if y2026 else {}
        kw_hit = any(any(k.lower() in i["title"].lower() for k in keys) for i in y2026)
        cand = {"cid": cid, "company": rels[0]["company"], "n": len(items), "y2026": len(y2026),
                "latest": items[0]["date"][:10] if items else "",
                "top": [f"{i['date'][:10]} {i['title'][:70]}" for i in items[:4]],
                "page_title": (page.get("title") or "")[:90], "hp": page.get("hp", ""),
                "kw_hit": kw_hit,
                "name_exact": name_match(name, page.get("title", "") or rels[0]["company"]),
                "page_has_name": full_name_match(name, page.get("text", ""))}
        out["prtimes"].append(cand)

    # PR TIMES の採用（2026年にリリースがあり、同一性が確認できるもの）
    for cand in out["prtimes"]:
        if cand["y2026"] < 1:
            continue
        if cand["kw_hit"] or cand["page_has_name"]:
            out["prtimes_ok"] = cand
            break
    if out["prtimes_ok"] is None:
        # 同姓同名の別業種しか無い場合は、訂正のため記録だけ残す
        pass

    # 公式サイト
    site = None
    home_candidates = []
    prt_hp = (out["prtimes_ok"] or {}).get("hp") or ""
    if prt_hp:
        home_candidates.append(("prtimes_hp", prt_hp))
    try:
        search_hits = DS.search_results(name + " 公式サイト") or DS.search_results(name + " 公式")
    except Exception:  # noqa: BLE001
        search_hits = []
    out["search_top"] = [u for u in search_hits[:6]]
    for u in search_hits[:6]:
        if is_directory(u):
            continue
        home_candidates.append(("search", u))

    tried = set()
    for via, u in home_candidates:
        pr = urllib.parse.urlparse(u)
        if via == "search":
            # 検索結果は下層ページ（企業ディレクトリ等）が混ざるので、ホストのトップだけを見る
            if pr.path not in ("", "/"):
                u = f"{pr.scheme}://{pr.netloc}/"
                pr = urllib.parse.urlparse(u)
        h = host(u)
        if not h or h in tried:
            continue
        tried.add(h)
        r = news_page_from_home(u, name, board)
        r["via"] = via
        r["url"] = u
        if r.get("news") and r.get("home_has_name") and r.get("ok_full"):
            if via == "search" and not r.get("ok_title"):
                # 検索由来はトップの title に社名があることを必須にする（まとめページの本文一致を除く）
                r["reject"] = "weak_title"
                site = site or r
                continue
            if board in SITE_INDUSTRY and not r.get("industry_hit"):
                r["reject"] = "industry"
                site = site or r
                continue
            site = r
            break
        if r.get("news") and r.get("home_has_name"):
            r["reject"] = r.get("reject") or "weak_name"
        if site is None:
            site = r
    out["site"] = site
    return out


def done_ids(cache_dir: str) -> set:
    return {f[:-5] for f in os.listdir(cache_dir) if f.endswith(".json")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("targets")
    ap.add_argument("cache")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--max-seconds", type=int, default=240)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="", help="id をカンマ区切りで指定")
    args = ap.parse_args(argv)

    os.makedirs(args.cache, exist_ok=True)
    targets = json.load(open(args.targets, encoding="utf-8"))
    if args.only:
        keys = [k.strip() for k in args.only.split(",") if k.strip()]
        targets = [t for t in targets if t["id"] in keys or any(k in t["name"] for k in keys)]
    done = done_ids(args.cache)
    todo = [t for t in targets if t["id"] not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"残り {len(todo)} / 全 {len(targets)} 社（済 {len(done)}）", flush=True)

    t0 = time.time()
    stop = False

    def work(t):
        nonlocal stop
        if stop:
            return None
        r = discover(t)
        json.dump(r, open(os.path.join(args.cache, t["id"] + ".json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        p = r.get("prtimes_ok") or {}
        s = r.get("site") or {}
        n = s.get("news") or {}
        print(f"{t['id']:10} {t['name'][:26]:<26} PRT={p.get('cid','')}/{p.get('y2026',0)} "
              f"site={(n.get('news_url') or '')[:46]} 2026={n.get('n2026',0)} "
              f"{'REJ:' + s.get('reject','') if s.get('reject') else ''}", flush=True)
        if time.time() - t0 > args.max_seconds:
            stop = True
        return r

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(work, todo))
    print(f"完了 {len(done_ids(args.cache))} 社 / {time.time() - t0:.0f}秒", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
