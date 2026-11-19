#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""検証済みの補完ソースから pickle-repo/news-supplements.json を作る（最後にHTMLを実取得して確認）。"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("u", f"{REPO}/update.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# (board, 社名, cid) — 社名一致＋業種語/会社HPで同一性を確認できたもの、または手動確認済み
PRT = [
    ("ec-support", "株式会社エフカフェ", 21177),
    ("ec-support", "オリナス株式会社", 86631),
    ("ec-support", "トゥルーコンサルティング株式会社", 56054),
    ("ec-mailorder", "エバーライフ", 170809),
    ("ec-mailorder", "フォーマルクライン", 69527),
    ("ec-mailorder", "クロコス", 88698),
    ("ec-mailorder", "ウェルヴィーナス", 128435),
    ("ec-brands", "DINETTE株式会社", 26615),
    ("ec-brands", "株式会社Magnifi", 85691),
    ("ec-brands", "株式会社mtc.", 152214),
    ("ec-brands", "株式会社JAPANSOLUTIONS", 163031),
    ("ec-brands", "FAVO株式会社", 175747),
    ("ec-brands", "HelloVenus", 96848),
    ("ec-brands", "アンカー", 16775),
    ("ec-brands", "サヴァリ株式会社", 33158),
    ("ec-brands", "ヨミテ株式会社", 174226),
    ("ec-brands", "レバレジーズM&Aアドバイザリー株式会社", 10591),
    ("ec-brands", "株式会社SHINY", 192317),
    ("ec-brands", "株式会社セレス", 21504),
    ("ec-brands", "株式会社GrandBleu&Co.", 171363),
    ("ec-brands", "株式会社SLONE", 178728),
    ("ec-brands", "株式会社通販の虎 マーケティング", 142246),
    ("ec-brands", "アカマイテクノロジーズ", 31697),
    ("ec-brands", "株式会社HighLink", 61060),
    ("ec-brands", "株式会社出前館", 29254),
    ("ec-brands", "株式会社fred", 192776),
    ("ec-brands", "元株式会社テレシー", 76049),
    ("ec-brands", "株式会社コンボイスイッチ", 137986),
    ("ec-brands", "株式会社京福堂", 98890),
    ("ec-brands", "福島鰹株式会社", 158290),
    ("ec-brands", "株式会社湖池屋", 3039),
]

# (board, 社名) — 公式サイトのニュース欄を実取得して確認済みのもの。URL は sup_all.json から引く
SITE = [
    ("ec-mailorder", "日本薬師堂"),
    ("ec-mailorder", "ウェルヴィーナス"),
    ("ec-mailorder", "ヴァーナル"),
    ("ec-mailorder", "東京ディベロ"),
    ("ec-mailorder", "奈良大和生薬"),
    ("ec-mailorder", "エバーライフ"),
    ("ec-mailorder", "ハーブ健康本舗"),
    ("ec-mailorder", "ランクアップ"),
    ("ec-mailorder", "フォーマルクライン"),
    ("ec-mailorder", "富山常備薬"),
    ("ec-mailorder", "クロコス"),
    ("ec-mailorder", "フローラ"),
    ("ec-mailorder", "フロムココロ"),
    ("ec-mailorder", "世田谷自然食品"),
    ("ec-mailorder", "日本自然発酵"),
    ("ec-mailorder", "八幡物産"),
    ("ec-mailorder", "野草酵素"),
    ("ec-mailorder", "ECホールディングス"),
    ("ec-mailorder", "ファンファレ"),
    ("ec-mailorder", "ディーエムジェイグループ"),
    ("ec-brands", "DINETTE株式会社"),
    ("ec-brands", "ウェペック株式会社"),
    ("ec-brands", "株式会社JAPANSOLUTIONS"),
    ("ec-brands", "株式会社和漢"),
    ("ec-brands", "WHRP株式会社"),
    ("ec-brands", "ecxia株式会社"),
    ("ec-brands", "アドコミ株式会社"),
    ("ec-brands", "サヴァリ株式会社"),
    ("ec-brands", "ネクストレベルホールディングス株式会社"),
    ("ec-brands", "和心本舗株式会社"),
    ("ec-brands", "株式会社丸辰"),
    ("ec-brands", "電通九州"),
    ("ec-brands", "bfj株式会社"),
    ("ec-brands", "株式会社クリアビジョン"),
    ("ec-brands", "株式会社ダイレクトマーケティングゼロ"),
    ("ec-brands", "Oz link"),
    ("ec-brands", "株式会社THE RICH"),
    ("ec-brands", "株式会社オプト"),
    ("ec-brands", "株式会社BULK"),
    ("ec-brands", "株式会社リテリア"),
    ("ec-brands", "株式会社夢中"),
    ("ec-brands", "株式会社ブランドパトローラー"),
    ("ec-brands", "Olks"),
    ("ec-brands", "株式会社Icra"),
    ("ec-brands", "株式会社オムニバス"),
    ("ec-brands", "zealandbifu株式会社"),
    ("ec-brands", "株式会社2-GEAR"),
    ("ec-brands", "株式会社Collelis"),
    ("ec-brands", "株式会社Weeare"),
    ("ec-brands", "株式会社my's"),
    ("ec-brands", "株式会社アトラクト"),
    ("ec-brands", "株式会社サイトスコープ"),
    ("ec-brands", "株式会社サマー・スノウ"),
    ("ec-brands", "株式会社ダイヤモンドファンタジー"),
    ("ec-brands", "株式会社メデタ"),
    ("ec-brands", "株式会社京福堂"),
    ("ec-brands", "Toniez株式会社"),
    ("ec-brands", "アスニカ株式会社"),
    ("ec-brands", "株式会社Grow"),
    ("ec-brands", "株式会社LLL"),
    ("ec-brands", "Flip Nine株式会社"),
    ("ec-brands", "株式会社Vieon"),
    ("ec-brands", "株式会社食選館"),
    ("ec-brands", "株式会社IMAGICA IRIS"),
    ("ec-brands", "株式会社アーツ"),
    ("ec-brands", "株式会社オレコン"),
    ("ec-brands", "株式会社湖池屋"),
    ("ec-brands", "株式会社黒船物流"),
]

# 探索結果に無い／別URLが正しいもの
SITE_URL = {
    "日本薬師堂": "https://www.yakushido.com/news/",
    "ハーブ健康本舗": "https://www.herb-kenko.com/news/",
    "ウェルヴィーナス": "https://wellvenus.jp/archives/category/news",
    "野草酵素": "https://seedcoms.net/blogs/news",
    "ECホールディングス": "https://www.ec-h.co.jp/news-release/",
    "フォーマルクライン": "https://shop.formalklein.com/shop/information_categories/news",
    "富山常備薬": "https://toyama-jobiyaku.co.jp/shop/information_categories/newinfo",
    "ディーエムジェイグループ": "https://corp.dmjegao.com/topics/",
    "ファンファレ": "https://fanfare-shop.com/",
}


def check(url: str):
    try:
        raw, status, final = u.http_get(url, 15, UA, 0)
        ents = u.parse_html_news(raw.decode("utf-8", "replace"), final)
    except Exception as exc:  # noqa: BLE001
        return url, 0, 0, str(exc)[:60]
    n2026 = sum(1 for e in ents if e["published"].year == 2026)
    return url, len(ents), n2026, ""


def main():
    rows = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "sup_all.json", encoding="utf-8"))
    by = {(r.get("board"), r.get("name")): r for r in rows}
    ids = {}
    for r in rows:
        ids[(r.get("board"), r.get("name"))] = r.get("id")

    out = {}
    missing = []

    for board, name, cid in PRT:
        sid = ids.get((board, name))
        if not sid:
            missing.append(("prt-id", board, name))
            continue
        out.setdefault(board, {}).setdefault(sid, {})["extra_feeds"] = [
            {"url": f"https://prtimes.jp/companyrdf.php?company_id={cid}", "origin": "prtimes", "label": "PR TIMES"}
        ]

    urls = []
    for board, name in SITE:
        sid = ids.get((board, name))
        url = SITE_URL.get(name)
        if not url:
            r = by.get((board, name)) or {}
            url = (r.get("site") or {}).get("news_url")
        if not sid or not url:
            missing.append(("site", board, name))
            continue
        urls.append((board, name, sid, url))

    with ThreadPoolExecutor(max_workers=6) as pool:
        checks = list(pool.map(lambda x: check(x[3]), urls))
    for (board, name, sid, url), (_, n, n2026, err) in zip(urls, checks):
        if n2026 < 1:
            print(f"  SKIP {name} ({board}) {url} n={n} n2026={n2026} {err}")
            continue
        out.setdefault(board, {}).setdefault(sid, {})["html_news"] = [
            {"url": url, "origin": "site"}
        ]
        print(f"  ok   {name} ({board}) {url} n={n} n2026={n2026}")

    path = f"{REPO}/news-supplements.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print("missing:", missing)
    print("boards:", {b: len(v) for b, v in out.items()})
    print("written", path)


if __name__ == "__main__":
    main()
