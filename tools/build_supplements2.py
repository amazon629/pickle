#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cache94/ の探索結果から news-supplements.json を作る（既存3ボードはそのまま残す）。

・同名他社・別法人と判断したものは EXCLUDE に理由つきで明示（再実行しても同じ結果）。
・html_news は update.py の parse_html_news で実際に取得し、2026年の記事が1件以上あるものだけ残す。
・extra_feeds は PR TIMES の企業RSSを実際に取得し、2026年のリリースが1件以上あるものだけ残す。

  python3 build_supplements2.py [cache_dir]
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("u", os.path.join(BASE, "update.py"))
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)

NEW_BOARDS = ["smartnews-partners", "caster-companies", "ec-companies", "nrtv-companies", "shopify-partners"]

# 同名他社・別法人・統合済みのため除外（探索結果に出たが採用しない）
EXCLUDE = {
    ("smartnews-partners", "snp_27"): "PR TIMES の同名企業が「Ｌｉｂｅｒ＆Ｘ株式会社」で別法人",
    ("smartnews-partners", "snp_70"): "公式サイト he-lios.co.jp は株式会社ヘリオス（WEB制作）。同名の株式会社HELIOS（広告）とは別法人。PR TIMES も同名の別社（地理空間データ）",
    ("smartnews-partners", "snp_89"): "laughgroup.jp は株式会社ラフダイニング、PR TIMES はラフ株式会社（いずれも株式会社ラフではない）",
    ("smartnews-partners", "snp_23"): "adkms.jp は ADKマーケティング・ソリューションズ（ADKデジタル・コミュニケーションズは2024年に統合）",
    ("smartnews-partners", "snp_64"): "d2c.co.jp は親会社 株式会社D2C。D2C R の自社サイト d2cr.com は取得不可",
    ("caster-companies", "cc_52"): "onsight-japan.com は化学品メーカー。EC支援のオンサイト株式会社は on-sight.biz（ニュース欄なし）",
    ("caster-companies", "cc_47"): "nextdeal.co.jp は別法人",
    ("caster-companies", "cc_51"): "actus.co.jp はアクタス（インテリア）。税理士法人アクタスとは別",
    ("ec-companies", "gn_51"): "tagsapi.com は株式会社Mofflyの別サービス提供元",
    ("ec-companies", "gn_03"): "ec-vision.co.jp/news は WordPress の固定ページを拾うだけで記事にならない（update.py の抽出で0件）",
}


def check_feed(url: str):
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
    try:
        raw, st, final = u.http_get(url, 15, UA, 1)
    except Exception as exc:  # noqa: BLE001
        return url, 0, str(exc)[:50]
    if st != 200:
        return url, 0, f"HTTP {st}"
    items = u.parse_feed(raw)
    n = sum(1 for it in items if str(it.get("published", ""))[:4] == "2026")
    return url, n, ""


def check_html(url: str):
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
    try:
        raw, status, final = u.http_get(url, 20, UA, 1)
        ents = u.parse_html_news(raw.decode("utf-8", "replace"), final)
    except Exception as exc:  # noqa: BLE001
        return url, 0, 0, str(exc)[:50]
    n2026 = sum(1 for e in ents if e.get("published") and e["published"].year == 2026)
    return url, len(ents), n2026, ""


def main(argv=None):
    cache = (argv[0] if argv else os.path.join(BASE, "tools", "cache94"))
    out = json.load(open(os.path.join(BASE, "news-supplements.json"), encoding="utf-8"))
    rows = []
    for f in sorted(os.listdir(cache)):
        if not f.endswith(".json"):
            continue
        d = json.load(open(os.path.join(cache, f), encoding="utf-8"))
        if d["board"] not in NEW_BOARDS:
            continue
        rows.append(d)

    # 1) 追加候補を集める
    feeds, sites, skipped = [], [], []
    for d in rows:
        key = (d["board"], d["id"])
        if key in EXCLUDE:
            skipped.append((d["board"], d["id"], d["name"], EXCLUDE[key]))
            continue
        p = d.get("prtimes_ok") or {}
        if p and p.get("cid") and p.get("y2026", 0) > 0:
            feeds.append((d["board"], d["id"], d["name"], p["cid"], p["y2026"]))
        s = d.get("site") or {}
        n = s.get("news") or {}
        if n.get("news_url") and s.get("home_has_name") and s.get("ok_full") and not s.get("reject"):
            sites.append((d["board"], d["id"], d["name"], n["news_url"], n["n2026"]))

    # 2) 実取得で最終確認
    with ThreadPoolExecutor(max_workers=6) as pool:
        feed_chk = list(pool.map(lambda x: check_feed(f"https://prtimes.jp/companyrdf.php?company_id={x[3]}"), feeds))
        site_chk = list(pool.map(lambda x: check_html(x[3]), sites))

    added = {}
    for (board, sid, name, cid, n0), (url, n, err) in zip(feeds, feed_chk):
        if n < 1:
            print(f"  SKIP feed {name} ({board}) {url} 2026={n} {err}")
            continue
        added.setdefault(board, {}).setdefault(name, {})["extra_feeds"] = [
            {"url": url, "origin": "prtimes", "label": "PR TIMES"}]
        print(f"  ok  feed {name} ({board}) cid={cid} 2026={n}")
    for (board, sid, name, url0, n0), (url, n, n2026, err) in zip(sites, site_chk):
        if n2026 < 1:
            print(f"  SKIP site {name} ({board}) {url0} 2026={n2026} {err}")
            continue
        added.setdefault(board, {}).setdefault(name, {})["html_news"] = [
            {"url": url0, "origin": "site"}]
        print(f"  ok  site {name} ({board}) {url0} n={n} 2026={n2026}")

    for board, sid, name, why in skipped:
        print(f"  x   {name} ({board}) — {why}")

    for board, items in added.items():
        out.setdefault(board, {}).update(items)
        out[board] = dict(sorted(out[board].items()))

    path = os.path.join(BASE, "news-supplements.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print("\nboards:", {b: len(v) for b, v in sorted(out.items())})
    print("追加:", {b: len(v) for b, v in sorted(added.items())})
    return 0


if __name__ == "__main__":
    sys.exit(main())
