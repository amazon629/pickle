#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第1回 広告界隈。（2026年11月26日｜アジュール竹芝）参加102社のニュース監視ボード設定を作る。

`adkaiwai-companies.json`（会場参加企業102社＋ご協賛8社のリスト）を読み、
`sources-adkaiwai.json` を書き出す。update.py がこれを拾って `adkaiwai.html` を
毎時（GitHub Actions）自動更新する。AIは介在しない。

使い方:
    python3 sync_adkaiwai_companies.py                 # 既定のリストから sources-adkaiwai.json を書き出す
    python3 sync_adkaiwai_companies.py --check         # 各社のクエリを実際に引いて件数を確認
    python3 sync_adkaiwai_companies.py --list          # 生成結果（クエリ・絞り込み・補完）を一覧表示

設計:
  * 既存ボード（sources-*.json）に同じ社名の検証済み設定（クエリ・must_match・
    PR TIMES企業RSS・公式サイトのお知らせ）があれば流用する（OVERRIDES が優先）。
  * 短い社名・一般名詞（D株式会社・クレア・リズム・マテリアル等）は OVERRIDES で
    法人格付きのクエリや must_match に絞り、同名他社・無関係記事を落とす。
  * 区分（登壇・協賛41社 / 一般参加61社 / ゴールド・シルバー協賛8社）は
    各 source の groups に入れ、update.py が絞り込みチップとして描画する。
  * 補完（PR TIMES 企業RSS・公式サイトのニュース）は news-supplements.json の
    全ボードぶんを社名で引き当てて合流させる（このボード専用の定義は "adkaiwai"）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import speaker_boards as sb

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LIST_PATH = os.path.join(BASE_DIR, "adkaiwai-companies.json")
OUT_PATH = os.path.join(BASE_DIR, "sources-adkaiwai.json")
SUPPLEMENTS_PATH = os.path.join(BASE_DIR, "news-supplements.json")

GROUPS = [
    {"id": "speaker", "label": "登壇・協賛 41社"},
    {"id": "attendee", "label": "一般参加 61社"},
    {"id": "sponsor", "label": "🏅 協賛 8社"},
]

# 既存設定の流用元として優先するボード（前に書いたものほど優先）
PREFER_BOARDS = [
    "ec-brands", "ec-companies", "ecnopro-companies", "caster-companies",
    "nrtv-companies", "ec-support", "ec-mailorder", "smartnews-partners",
    "shopify-partners", "sns-agencies", "livecommerce-4companies", "ad-marketing",
]

# 社名ごとの調整。
#   query        … Google ニュースに投げる文字列
#   match        … 見出しにこれを含む記事だけを残す（must_match）
#   context      … "strict" / True / [語...]。広告・EC業界の語を含む見出しだけに絞る
#   exclude      … この語を含む見出しは落とす（同名の別物）
#   extra_queries… 同じカードに足す追加クエリ（合算表示）
#   html_news / extra_feeds … 公式サイトのお知らせ / PR TIMES 企業RSS
#   keep_ctx     … True なら流用元の context_match をそのまま使う（既定は破棄）
OVERRIDES = {
    # ---- ご登壇・ご協賛 41社 ----
    "株式会社DMM.com": {"query": '"DMM"', "match": ["DMM"]},
    "株式会社GreenSpoon": {"query": '"Greenspoon" OR "GreenSpoon"', "match": ["Greenspoon", "GreenSpoon"]},
    "株式会社Icra": {"query": '"Icra" 広告 OR "Icra" マーケティング', "match": ["Icra"],
                     "html_news": [{"url": "https://icra.marketing/news", "origin": "site"}]},
    "株式会社一絲・知足（kojitsu）": {
        "query": '"株式会社一絲" OR "知足" 事業戦略',
        "match": ["株式会社一絲", "一絲（", "一絲、", "知足スクール", "知足（", "知足が", "知足、", "koujitsu"],
        "extra_queries": ['"koujitsu"', '"事業戦略スクール" 知足'],
    },
    "株式会社Oz link": {"query": '"Oz link"', "match": ["Oz link", "Ozlink", "オズリンク"]},
    "株式会社Yaaha": {"query": '"Yaaha"', "match": ["Yaaha"]},
    "株式会社アルク": {"query": '"株式会社アルク"', "match": ["株式会社アルク"]},
    "オルクス株式会社": {"query": '"オルクス"', "match": ["オルクス株式会社", "株式会社オルクス", "オルクス、", "オルクスが"]},
    "株式会社ディー・エヌ・エー": {"query": '"DeNA"', "match": ["DeNA", "ディー・エヌ・エー"]},
    "株式会社メリル": {"query": '"株式会社メリル"', "match": ["株式会社メリル", "メリル、"]},
    "渋谷文化村通り皮膚科": {"query": '"渋谷文化村通り皮膚科"', "match": ["渋谷文化村通り皮膚科", "文化村通り皮膚科"]},
    "思考実験／正直サミット": {
        "query": '"正直サミット" OR "思考実験" 広告',
        "match": ["正直サミット", "思考実験/正直サミット", "思考実験／正直サミット"],
        "extra_queries": ['"思考実験" マーケティング'],
    },
    "株式会社HIGHLINK": {"query": '"High Link"', "match": ["High Link", "HighLink", "ハイリンク"],
                    "extra_feeds": [{"url": "https://prtimes.jp/companyrdf.php?company_id=61060",
                                     "origin": "prtimes", "label": "PR TIMES"}]},
    "株式会社JAPAN SOLUTIONS": {"query": '"JAPAN SOLUTIONS"', "match": ["JAPAN SOLUTIONS", "JAPANSOLUTIONS"]},
    "株式会社S&K": {"query": '"S&K"', "match": ["S&K", "S＆K"], "context": "strict",
                    "exclude": ["ボクシング", "選手名鑑", "アートコレクション", "先島", "坂田", "熊本発"]},
    "アルパカ／リテリア": {
        "query": '"リテリア"', "match": ["リテリア", "株式会社ALPACA", "ALPACA株式会社"],
        "extra_queries": ['"株式会社ALPACA"'],
    },
    "株式会社オービックビジネスコンサルタント": {"query": '"オービックビジネスコンサルタント"',
                                        "match": ["オービックビジネスコンサルタント", "オービックビジネス"]},
    "ピアラ": {"query": '"ピアラ"', "match": ["ピアラ"]},
    "レリック社会保険労務士法人": {"query": '"レリック社会保険労務士"', "match": ["レリック社会保険労務士"]},
    "株式会社出前館": {"query": '"出前館"', "match": ["出前館"]},
    "東京スポーツ新聞社": {"query": '"東京スポーツ新聞社"', "match": ["東京スポーツ新聞社", "東スポ新聞社"]},

    # ---- ご参加 61社 ----
    "株式会社AdOps": {"query": '"AdOps"', "match": ["AdOps株式会社", "株式会社AdOps", "AdOps、", "AdOpsが", "AdOpsの", "【AdOps】"],
                   "extra_feeds": [{"url": "https://prtimes.jp/companyrdf.php?company_id=74020",
                                    "origin": "prtimes", "label": "PR TIMES"}],
                   "html_news": [{"url": "https://adops.co.jp/news/", "origin": "site"}]},
    "BRANDPORT株式会社": {"query": '"BRANDPORT"', "match": ["BRANDPORT", "ブランドポート"]},
    "CommerceX M&A株式会社": {"query": '"CommerceX"', "match": ["CommerceX"]},
    "株式会社DYM": {"query": '"株式会社DYM"', "match": ["株式会社DYM", "DYM、", "DYMが", "DYMの"]},
    "株式会社FRED": {"query": '"株式会社FRED"', "match": ["株式会社FRED", "FRED、", "FREDが", "FREDの", "株式会社fred"]},
    "identify株式会社": {"query": '"identify株式会社"', "match": ["identify株式会社", "株式会社identify", "identify、", "identifyが"]},
    "株式会社All Ads": {"query": '"All Ads"', "match": ["All Ads", "オールアドズ"]},
    "合同会社BRBK": {"query": '"BRBK"', "match": ["BRBK"]},
    "D株式会社": {"query": '"D株式会社"', "match": ["D株式会社"]},
    "株式会社Grand Bleu&Co.": {"query": '"Grand Bleu" OR "GrandBleu"', "match": ["Grand Bleu", "GrandBleu"]},
    "IGNITE株式会社": {"query": '"IGNITE株式会社"', "match": ["IGNITE株式会社", "株式会社IGNITE", "IGNITE、", "IGNITEが"],
                   "extra_feeds": [{"url": "https://prtimes.jp/companyrdf.php?company_id=91557",
                                    "origin": "prtimes", "label": "PR TIMES"}]},
    "株式会社NKKソリューションズ": {"query": '"NKKソリューションズ"', "match": ["NKKソリューションズ"],
                          "extra_feeds": [{"url": "https://prtimes.jp/companyrdf.php?company_id=122322",
                                           "origin": "prtimes", "label": "PR TIMES"}]},
    "SHIRORU株式会社": {"query": '"SHIRORU"', "match": ["SHIRORU", "シロル"]},
    "株式会社SOCIAL GREEN TECH": {"query": '"SOCIAL GREEN TECH"', "match": ["SOCIAL GREEN TECH", "ソーシャルグリーンテック"],
                                 "extra_feeds": [{"url": "https://prtimes.jp/companyrdf.php?company_id=159724",
                                                  "origin": "prtimes", "label": "PR TIMES"}]},
    "株式会社アドコミ": {"query": '"アドコミ"', "match": ["アドコミ"]},
    "マルルー株式会社": {"query": '"マルルー"', "match": ["マルルー"]},
    "株式会社ヨノラボ": {"query": '"ヨノラボ"', "match": ["ヨノラボ"],
                  "extra_feeds": [{"url": "https://prtimes.jp/companyrdf.php?company_id=178115",
                                   "origin": "prtimes", "label": "PR TIMES"}]},
    "リズム株式会社": {"query": '"リズム株式会社"', "match": ["リズム株式会社", "株式会社リズム"]},
    "上海洛塔信息技術有限公司": {"query": '"洛塔" OR "上海洛塔"', "match": ["洛塔"]},
    "楽天グループ株式会社": {"query": '"楽天グループ"', "match": ["楽天グループ"]},
    "株式会社ログラフ": {"query": '"ログラフ"', "match": ["ログラフ"]},
    "株式会社VEXL": {"query": '"VEXL"', "match": ["VEXL"]},
    "株式会社SOLXIA": {"query": '"SOLXIA"', "match": ["SOLXIA"],
                    "html_news": [{"url": "https://solxia.co.jp/news/", "origin": "site"}]},
    "アミノセルス製薬株式会社": {"query": '"アミノセルス"', "match": ["アミノセルス"],
                      "extra_feeds": [{"url": "https://prtimes.jp/companyrdf.php?company_id=57198",
                                       "origin": "prtimes", "label": "PR TIMES"}],
                      "html_news": [{"url": "https://aminocells.com/shop/information_categories/news", "origin": "site"}]},
    "アスレプロダクツ株式会社": {"query": '"アスレプロダクツ"', "match": ["アスレプロダクツ"]},
    "株式会社鈴木ハーブ研究所": {"query": '"鈴木ハーブ研究所"', "match": ["鈴木ハーブ研究所"]},
    "株式会社MOG": {"query": '"株式会社MOG"', "match": ["株式会社MOG", "MOG株式会社", "MOG、", "MOGが"]},
    "Oxora": {"query": '"Oxora"', "match": ["Oxora", "オクソラ"]},
    "株式会社SATOI": {"query": '"SATOI"', "match": ["SATOI", "サトイ"]},
    "Taboola Japan株式会社": {"query": '"Taboola"', "match": ["Taboola", "タブーラ"]},
    "株式会社クレア": {"query": '"株式会社クレア"', "match": ["株式会社クレア", "クレア株式会社", "クレア、"]},
    "株式会社ハハハラボ": {"query": '"ハハハラボ"', "match": ["ハハハラボ"]},
    "ファンネルアド株式会社": {"query": '"ファンネルアド"', "match": ["ファンネルアド"]},
    "株式会社マテリアル": {"query": '"株式会社マテリアル"', "match": ["株式会社マテリアル", "マテリアル株式会社", "マテリアル、"]},
    "株式会社ユニメディア（通販通信）": {"query": '"ユニメディア"', "match": ["ユニメディア", "通販通信"],
                              "extra_queries": ['"通販通信"']},
    "一般社団法人JAPANライブコマース協会": {"query": '"JAPANライブコマース協会"', "match": ["JAPANライブコマース協会"]},
    "株式会社日本美心": {"query": '"日本美心"', "match": ["日本美心"]},
    "株式会社NuMeRo": {"query": '"NuMeRo"', "match": ["NuMeRo", "ヌメロ"]},
    "株式会社ソルビア": {"query": '"ソルビア"', "match": ["ソルビア"]},
    "株式会社ヨミテ": {"query": '"ヨミテ"', "match": ["ヨミテ"],
                 "extra_feeds": [{"url": "https://prtimes.jp/companyrdf.php?company_id=174226",
                                  "origin": "prtimes", "label": "PR TIMES"}]},
    "ユーナメディアパートナー株式会社": {"query": '"ユーナメディア" OR "POPPIXS"', "match": ["ユーナメディア", "POPPIXS"],
                          "extra_feeds": [{"url": "https://prtimes.jp/companyrdf.php?company_id=187393",
                                           "origin": "prtimes", "label": "PR TIMES"}]},
    "Comexposium Japan株式会社": {"query": '"Comexposium"', "match": ["Comexposium"]},
    "株式会社Oh my teeth": {"query": '"Oh my teeth"', "match": ["Oh my teeth", "オーマイティース"]},
    "レバレッジーズM&Aアドバイザリー株式会社": {"query": '"レバレジーズM&A" OR "レバレッジーズM&A"',
                                        "match": ["レバレジーズM&Aアドバイザリー", "レバレッジーズM&A", "レバレジーズM&A"],
                                        "extra_feeds": [{"url": "https://prtimes.jp/companyrdf.php?company_id=10591",
                                                         "origin": "prtimes", "label": "PR TIMES"}]},
}


# ------------------------------------------------------------------ helpers
def name_key(name: str) -> str:
    return sb.name_key(name)


def existing_index() -> dict:
    """既存ボードの sources を社名キーで引けるようにする（優先ボード順）。"""
    index = {}
    order = {b: i for i, b in enumerate(PREFER_BOARDS)}
    paths = []
    for board in PREFER_BOARDS:
        p = os.path.join(BASE_DIR, f"sources-{board}.json")
        if os.path.exists(p):
            paths.append((order[board], p))
    for _, path in sorted(paths):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        for src in data.get("sources", []):
            key = name_key(src.get("name") or "")
            if not key or key in index:
                continue
            index[key] = src
    return index


def supplement_index() -> dict:
    """news-supplements.json の全ボードぶんを社名キーでまとめる（同キーは先勝ち）。"""
    try:
        with open(SUPPLEMENTS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    index = {}
    for board, entries in data.items():
        for name, sup in (entries or {}).items():
            key = name_key(name)
            bucket = index.setdefault(key, {})
            for field in ("extra_feeds", "site_feeds", "html_news"):
                for item in sup.get(field) or []:
                    bucket.setdefault(field, [])
                    if item not in bucket[field]:
                        bucket[field].append(item)
    return index


# ------------------------------------------------------------------- build
def build_sources(companies: list, existing: dict, supplements: dict) -> list:
    sources = []
    for i, c in enumerate(companies, start=1):
        name = c["name"]
        core = sb.core_name(name)
        ov = OVERRIDES.get(name, {})
        inherited = existing.get(name_key(name)) or {}

        query = ov.get("query") or inherited.get("query") or f'"{core}"'
        match = ov.get("match") or inherited.get("must_match") or ([core] if core else [name])

        src = {
            "id": f"ak_{i:03d}",
            "type": "google_news",
            "name": name,
            "query": query,
            "must_match": list(dict.fromkeys(match)),
            "limit": 5,
            "enabled": True,
            "groups": (["speaker"] if c["role"] == "speaker" else ["attendee"])
                      + (["sponsor"] if c.get("sponsor") else []),
        }
        if c.get("sponsor"):
            src["rank"] = c["sponsor"]

        # 広告・EC・マーケティング業界の語で絞る（同名他社・一般語対策）
        context = ov.get("context")
        if context is None and ov.get("keep_ctx"):
            context = inherited.get("context_match")
        if context:
            if context == "strict":
                src["context_match"] = sb.EC_STRICT
            elif isinstance(context, list):
                src["context_match"] = context
            else:
                src["context_match"] = sb.EC_CONTEXT

        exclude = ov.get("exclude") or inherited.get("exclude_match")
        if exclude:
            src["exclude_match"] = list(exclude)

        extra_queries = ov.get("extra_queries")
        if extra_queries:
            src["extra_queries"] = list(extra_queries)

        # 補完（PR TIMES 企業RSS・公式サイトのお知らせ）: 明示 > 既存ボードの流用 > 全ボード共通
        for field in ("extra_feeds", "site_feeds", "html_news"):
            items = ov.get(field) or inherited.get(field) or (supplements.get(name_key(name)) or {}).get(field)
            if items:
                src[field] = items

        sources.append(src)
    return sources


def make_document(sources: list) -> dict:
    return {
        "settings": {
            "title": "第1回 広告界隈。 参加102社 ニュース監視",
            "nav_label": "📣 広告界隈",
            "items_per_source": 5,
            "timeout_seconds": 15,
            "retries": 2,
            "fetch_workers": 8,
            "tolerate_errors": True,
            "translate": True,
            "translate_max_per_run": 20,
            "groups": GROUPS,
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
        },
        "sources": sources,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="広告界隈 102社ボードの sources-adkaiwai.json を作る")
    ap.add_argument("--list", action="store_true", help="生成結果を一覧表示")
    ap.add_argument("--check", action="store_true", help="各社のクエリを実際に引いて件数を表示")
    ap.add_argument("--workers", type=int, default=8, help="--check の並列数")
    args = ap.parse_args(argv)

    with open(LIST_PATH, encoding="utf-8") as fh:
        companies = json.load(fh)["companies"]

    existing = existing_index()
    supplements = supplement_index()
    sources = build_sources(companies, existing, supplements)
    doc = make_document(sources)

    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"{len(sources)}社を書き出しました: {os.path.basename(OUT_PATH)}")

    if args.list:
        for s in sources:
            sup = []
            if s.get("extra_feeds"):
                sup.append("PRT")
            if s.get("html_news"):
                sup.append("公式")
            if s.get("site_feeds"):
                sup.append("公式RSS")
            print(
                f"{s['id']} {','.join(s['groups']):<14} {s['name'][:30]:<30} "
                f"q={s['query']} mm={s['must_match']}"
                + (f" ctx" if s.get("context_match") else "")
                + (f" ex={s['exclude_match']}" if s.get("exclude_match") else "")
                + (f" +q{len(s['extra_queries'])}" if s.get("extra_queries") else "")
                + (f" [{'/'.join(sup)}]" if sup else "")
            )

    if args.check:
        import time
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            results = list(pool.map(lambda s: sb.check_source(s, doc["settings"]), sources))
        print("")
        print(f"{'件数':>4}  {'元記事':>5}  会社 / クエリ")
        empty = []
        for src, res in zip(sources, results):
            note = f"(エラー: {res['error']})" if res["error"] else ""
            print(f"{res['kept']:>4}  {res['total']:>5}  {src['name'][:34]:<34} {src['query']} {note}")
            if res["kept"] == 0:
                empty.append(src["name"])
        if empty:
            print(f"\n記事0件の会社 {len(empty)}社: " + "、".join(empty))
        print(f"（確認にかかった時間 {time.time() - t0:.1f}秒）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
