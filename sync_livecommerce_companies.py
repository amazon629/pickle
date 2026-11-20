#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ライブコマース・TikTok Shop 4社ボード — sources-livecommerce-4companies.json を作る。

対象4社（2026-11-20 時点で公式サイト・所在地・事業内容を確認済み）:
  1. 株式会社Sホールディングス   … 東京都品川区（銀座オフィスあり）／代表 燕咏靖／ライブコマース・ブランド事業
  2. 株式会社Cellest            … 大阪市中央区本町／代表 佐々木宏志／WABE・トキバナ
  3. Gastroduce Japan株式会社    … 福岡市博多区／代表 若松友貴／食品EC・TikTok Shop支援
  4. 株式会社ＣＲＥＯＫ          … 東京都江東区毛利／代表 桜井吉男／TikTok Shop支援・CREOK LAB

「株式会社Sホールディングス」は SBSホールディングス（物流）・RSホールディングスなどの
同名他社がニュースに混ざりやすいため、除外語とブランド名（燕チャンネル・ABITOKYO 等）で
本物の記事だけを残す。CREOK は半角/全角の表記ゆれを両方検索する。

ソースは3種を併用する（PR TIMES・公式サイトの定義は news-supplements.json 側）:
  GoogleニュースRSS … 各社クエリ
  PR TIMES企業RSS   … company_id 90341 / 102719 / 56558 / 169316
  公式サイト        … ニュースRSS（Sホールディングス・Cellest・Gastroduce Japan）。
                      CREOK は公式サイトにニュース／お知らせページが無いため2ソース。

使い方:
    python3 sync_livecommerce_companies.py            # sources-livecommerce-4companies.json を書き出す
    python3 sync_livecommerce_companies.py --check    # 書き出した各社を実際に取得して件数を確認
"""

from __future__ import annotations

import json
import os
import sys

from news_supplements import apply_supplements

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SUPPLEMENT_KEY = "livecommerce-4companies"
OUT_PATH = os.path.join(BASE_DIR, "sources-livecommerce-4companies.json")

COMPANIES = [
    {
        "id": "lc4_sholdings",
        "name": "株式会社Sホールディングス",
        "query": '"Sホールディングス" OR "S-ホールディングス"',
        "must_match": [
            "Sホールディングス",
            "S-ホールディングス",
            # 社名が無い自社リリース（燕チャンネル・ABITOKYO 等のブランド名で拾う）
            "燕チャンネル",
            "ABITOKYO",
            "アビトーキョー",
            "S-LIVE JAPAN",
            "1899mall",
        ],
        "exclude_match": [
            # 同名他社（SBSホールディングス=物流、RS/GS/NSホールディングス等）とソフトバンク関連
            "SBS",
            "RSホールディングス",
            "GSホールディングス",
            "NSホールディングス",
            "ソフトバンク",
            "SBホールディングス",
            "エス・ホールディングス",
            "エスホールディングス",
        ],
        "official": "https://www.s-hldgs.co.jp/",
        "note": "本社 東京都品川区上大崎3-5-11／銀座オフィス 中央区銀座2-3-6、代表 燕咏靖。"
                "ライブコマース（燕チャンネル）・ブランド（ABITOKYO 等）事業。",
    },
    {
        "id": "lc4_cellest",
        "name": "株式会社Cellest",
        "query": '"Cellest"',
        "must_match": ["Cellest"],
        "exclude_match": [],
        "official": "https://cellest.co.jp/",
        "note": "本社 大阪市中央区本町3-1-10、代表 佐々木宏志。ライブコマースECモール「WABE」・"
                "事務所「トキバナ」運営。",
    },
    {
        "id": "lc4_gastroduce",
        "name": "Gastroduce Japan株式会社",
        "query": '"Gastroduce Japan" OR "GastroduceJapan" OR "ガストロデュース"',
        "must_match": ["Gastroduce Japan", "GastroduceJapan", "ガストロデュース"],
        "exclude_match": [],
        "official": "https://gastroduce-japan.co.jp/",
        "note": "本社 福岡市博多区下川端町10-9、代表 若松友貴。食品EC支援・TikTok Shop公式パートナー"
                "（TSP/TAP/CAP）。",
    },
    {
        "id": "lc4_creok",
        "name": "株式会社ＣＲＥＯＫ",
        "query": '"CREOK" OR "ＣＲＥＯＫ"',
        "must_match": ["CREOK"],
        "exclude_match": [],
        "official": "https://creok.co.jp/",
        "note": "本社 東京都江東区毛利2-9-20、代表 桜井吉男。TikTok Shop支援（TSP/TAP/MCN・CAP）・"
                "CREOK LAB。公式サイトにニュース／お知らせページが無いため、発表はPR TIMESが一次情報。",
    },
]


def build() -> dict:
    settings = {
        "title": "ライブコマース・TikTok Shop 4社 ニュース監視ボード",
        "nav_label": "🏢 ライブコマース4社",
        "items_per_source": 25,
        "timeout_seconds": 20,
        "retries": 2,
        "fetch_workers": 5,
        "tolerate_errors": True,
        "translate": True,
        "translate_max_per_run": 20,
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
    }
    sources = []
    for company in COMPANIES:
        src = {
            "id": company["id"],
            "type": "google_news",
            "name": company["name"],
            "query": company["query"],
            "must_match": company["must_match"],
            "limit": settings["items_per_source"],
            "enabled": True,
            "note": company["note"],
        }
        if company["exclude_match"]:
            src["exclude_match"] = company["exclude_match"]
        sources.append(src)
    # PR TIMES企業RSS・公式サイトニュースRSSは news-supplements.json から社名で引く
    sources = apply_supplements(SUPPLEMENT_KEY, sources)
    return {"settings": settings, "sources": sources}


def check(sources: list) -> int:
    """各社を実際に取得して、件数・2026年記事数・ソース別内訳を出す（失敗は1で返す）。"""
    import update  # update.py の取得ロジックをそのまま使う

    settings = build()["settings"]
    bad = 0
    for src in sources:
        result = update.fetch_source(src, settings)
        items = result.get("items") or []
        years = [int((i.get("published") or "0000")[:4] or 0) for i in items]
        by_origin = {}
        for item in items:
            key = item.get("origin") or "google_news"
            by_origin[key] = by_origin.get(key, 0) + 1
        print(
            f"{src['name']}: {result['status']} / {len(items)}件"
            f"（2026年 {sum(1 for y in years if y >= 2026)}件 / "
            f"Google {by_origin.get('google_news', 0)}・PR TIMES {by_origin.get('prtimes', 0)}・"
            f"公式 {by_origin.get('site', 0)}）"
        )
        if result["status"] not in ("ok", "partial"):
            bad += 1
            print(f"  error: {result.get('error')}")
        elif result.get("error"):
            print(f"  一部取得失敗: {result['error']}")
    return 1 if bad else 0


def main(argv: list) -> int:
    board = build()
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(board, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"書き出しました: {os.path.basename(OUT_PATH)}（{len(board['sources'])}社）")
    if "--check" in argv:
        return check(board["sources"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
