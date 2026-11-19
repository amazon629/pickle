#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EC・支援企業34社ボード — 社名リストから sources-ec-support.json を作る。

使い方:
    python3 sync_ec_support.py                 # 34社ぶんの sources-ec-support.json を書き出す
    python3 sync_ec_support.py --check         # 書き出したあと各社のクエリを実際に引いて件数を確認
    python3 sync_ec_support.py --check --only これから,12,NE   # 一部の会社だけ確認（まず小さく試す）

出力 `sources-ec-support.json` は update.py が自動で拾い、`ec-support.html`（タイトル
「EC・支援企業34社」）と全ページ共通ナビのピルを作ります。

クエリの作り方（既存の sync_pureflat_companies.py と同じ設計）:
  * 会社名から法人格を外した名前を Google ニュースで検索し、その名前を見出しに含む記事だけ残す
    （`must_match`）。同名他社・一般語のノイズを落とすため。
  * 「これから」「そばに」「ジグザグ」「12」「オリナス」のように一般語・短すぎる名前は、
    `OVERRIDES` でクエリを言い換えたり、EC 業界の語（`context_match`）を含む見出しだけに絞る。
  * 会社リストは下の `COMPANIES`。差し替えるときはここだけ直す（元の並び順を保つ）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from news_supplements import apply_supplements

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(BASE_DIR, "sources-ec-support.json")

# ------------------------------------------------------------------ 対象34社（送られたリストのまま）
COMPANIES = [
    "株式会社WUUZY",
    "AnyMind Japan株式会社",
    "株式会社メルカート",
    "株式会社bloomstrategia",
    "株式会社ファン・マーケティング",
    "株式会社PLAN-B",
    "株式会社これから",
    "株式会社コムニコ",
    "株式会社Vooster",
    "GMOメイクショップ株式会社",
    "株式会社JAPAN SOLUTIONS",
    "株式会社S-CORE",
    "株式会社そばに",
    "株式会社ネイビーグループ",
    "株式会社マクロジ",
    "株式会社shizai",
    "株式会社インターファクトリー",
    "eBay Japan合同会社",
    "株式会社SUPER STUDIO",
    "株式会社visumo",
    "オリナス株式会社",
    "株式会社ホットリンク",
    "株式会社ファンコミュニケーションズ",
    "トゥルーコンサルティング株式会社",
    "GMOペパボ株式会社",
    "EC-Consulting Japan株式会社",
    "株式会社12",
    "株式会社ジグザグ",
    "株式会社Proteinum",
    "NE株式会社",
    "株式会社エフカフェ",
    "株式会社LTV-X",
    "株式会社ロジレスEC",
    "株式会社シナジックスマーケティング",
]

LEGAL_RE = re.compile(
    r"^(株式会社|有限会社|合同会社|合資会社|一般社団法人|一般財団法人|公益社団法人|公益財団法人)\s*"
)
PAREN_TAIL_RE = re.compile(r"\s*[（(][^（()）]*[)）]\s*$")


def core_name(name: str) -> str:
    """「株式会社ピュアフラット」→「ピュアフラット」「グリニッジ株式会社」→「グリニッジ」"""
    s = PAREN_TAIL_RE.sub("", (name or "").strip())
    s = LEGAL_RE.sub("", s)
    s = re.sub(r"\s*(株式会社|有限会社|合同会社)$", "", s)
    return s.strip()


# 一般語の社名で「業界の記事」とみなす語。同名の別物を落とすための語彙。
EC_CONTEXT = [
    "EC", "コマース", "通販", "ネットショップ", "ネット通販", "ショッピング",
    "楽天", "Amazon", "アマゾン", "ヤフー", "Yahoo", "ショップ", "出店", "越境", "D2C",
    "商品", "販売", "物流", "倉庫", "在庫", "受注", "決済", "カスタマー", "CRM",
    "セミナー", "ウェビナー", "登壇", "株式会社", "子会社", "買収", "提携", "決算", "上場",
    "資金調達", "リリース", "導入", "支援", "構築", "運用", "SaaS", "マーケティング",
    "広告", "代表", "社長", "事例", "サービス", "事業",
]

# 社名（法人格を外した名前）ごとの調整。
#   query    … Google ニュースに投げる文字列（既定は "<名前>"）
#   match    … 見出しにこれを含む記事だけを残す（別名・表記ゆれを足せる。既定は [名前]）
#   context  … True なら EC_CONTEXT のどれかを含む見出しだけに絞る
OVERRIDES = {
    # 短い・一般語・同名他社が多い名前
    "WUUZY": {"match": ["WUUZY"]},
    "AnyMind Japan": {"query": '"AnyMind"', "match": ["AnyMind"]},
    "メルカート": {"context": True, "exclude": ["メルカートEX"]},
    # 「ファンマーケティング」は一般名詞なので、法人名の形でだけ拾う
    "ファン・マーケティング": {
        "query": '"株式会社ファン・マーケティング"',
        "match": ["株式会社ファン・マーケティング", "ファン・マーケティング株式会社"],
    },
    "PLAN-B": {"match": ["PLAN-B"]},
    "これから": {"query": '"株式会社これから"', "match": ["株式会社これから", "これから、"]},
    "コムニコ": {"match": ["コムニコ", "Comnico"]},
    "JAPAN SOLUTIONS": {
        "query": '"JAPAN SOLUTIONS"',
        "context": True,
        "exclude": ["Wonder Japan Solutions"],
    },
    "S-CORE": {
        "query": '"株式会社S-CORE"',
        "match": ["株式会社S-CORE", "S-CORE"],
        "exclude": ["Eclipse", "資生堂", "全粧協", "スマレジ"],
    },
    "そばに": {"query": '"株式会社そばに"', "match": ["株式会社そばに", "そばにの", "そばにが"]},
    "ネイビーグループ": {"context": True},
    "shizai": {"match": ["shizai", "シザイ"]},
    "eBay Japan": {
        "query": '"eBay Japan"',
        "match": ["eBay Japan", "イーベイ・ジャパン", "イーベイジャパン"],
    },
    "SUPER STUDIO": {"match": ["SUPER STUDIO"]},
    "オリナス": {"query": '"オリナス株式会社"', "match": ["オリナス株式会社"]},
    "GMOペパボ": {"match": ["GMOペパボ", "ペパボ"]},
    "トゥルーコンサルティング": {"match": ["トゥルーコンサルティング", "True Consulting"]},
    # 株式会社12（トゥエルブ）は「12薬局」「12区」など別法人の記事が多いので除外する
    "12": {
        "query": '"株式会社12"',
        "match": ["株式会社12", "株式会社12、", "株式会社12が", "株式会社12の", "株式会社12は", "株式会社12を", "株式会社12（", "株式会社トゥエルブ"],
        "exclude": ["12薬局", "12区", "十二社", "株式会社12/"],
    },
    "ジグザグ": {"query": '"ジグザグ" EC', "context": True},
    "NE": {"query": '"NE株式会社"', "match": ["NE株式会社", "NE Korea", "441A"]},
    "LTV-X": {"match": ["LTV-X", "LTVX"]},
    "ロジレスEC": {"query": '"ロジレス"', "match": ["ロジレス"]},
    "シナジックスマーケティング": {"match": ["シナジックスマーケティング", "Synergy Marketing"]},
}


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


def match_any(text: str, terms: list) -> bool:
    """タイトルが terms のどれかを含むか（全角半角・大小の揺れを吸収、英字は単語境界）。"""
    t = normalize(text)
    for term in terms or []:
        n = normalize(term).strip()
        if not n:
            continue
        if re.fullmatch(r"[\x00-\x7f]+", n):
            if re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", t):
                return True
        elif n in t:
            return True
    return False


def build_source(name: str, index: int) -> dict:
    core = core_name(name)
    override = OVERRIDES.get(core, {})
    source = {
        "id": f"ecs_{index:02d}",
        "type": "google_news",
        "name": name,
        "query": override.get("query") or f'"{core}"',
        "must_match": override.get("match") or [core],
        "limit": 5,
        "enabled": True,
    }
    if override.get("context"):
        source["context_match"] = EC_CONTEXT
    if override.get("exclude"):
        source["exclude_match"] = override["exclude"]
    return source


# ------------------------------------------------------------------- checking
def check_source(src: dict, settings: dict) -> dict:
    """1社ぶんクエリを引いて、must_match / context_match で残る件数を数える（--check 用）。"""
    url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(src["query"])
        + "&hl=ja&gl=JP&ceid=JP:ja"
    )
    ua = settings.get("user_agent") or "Mozilla/5.0 (compatible; MonitorBoard/1.0)"
    last = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "identity"})
            with urllib.request.urlopen(req, timeout=20) as res:
                raw = res.read()
            root = ET.fromstring(
                re.sub(rb"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", b"&amp;", raw)
            )
            titles = [it.findtext("title") or "" for it in root.findall(".//item")]
            kept = [
                t
                for t in titles
                if match_any(t, src["must_match"])
                and (not src.get("context_match") or match_any(t, src["context_match"]))
                and not (src.get("exclude_match") and match_any(t, src["exclude_match"]))
            ]
            return {"total": len(titles), "kept": len(kept), "sample": kept[:3], "error": None}
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 + attempt)
    return {"total": 0, "kept": 0, "sample": [], "error": str(last)[:80]}


# --------------------------------------------------------------------- output
def make_document(sources: list) -> dict:
    return {
        "settings": {
            "title": "EC・支援企業34社 ニュース監視",
            "nav_label": f"EC・支援{len(sources)}社",
            "items_per_source": 5,
            "timeout_seconds": 15,
            "retries": 2,
            "fetch_workers": 5,
            "tolerate_errors": True,
            "translate": True,
            "translate_max_per_run": 20,
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
        },
        "sources": sources,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="EC・支援企業34社のニュース監視ボード設定を作る")
    ap.add_argument("--out", default=DEFAULT_OUT, help="出力する sources-*.json")
    ap.add_argument("--check", action="store_true", help="各社のクエリを実際に引いて件数を表示")
    ap.add_argument("--only", help="この文字列を含む会社だけ確認（カンマ区切り）")
    ap.add_argument("--workers", type=int, default=4, help="--check の並列数")
    args = ap.parse_args(argv)

    sources = [build_source(n, i + 1) for i, n in enumerate(COMPANIES)]
    sources = apply_supplements("ec-support", sources)
    doc = make_document(sources)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"{len(sources)}社を書き出しました: {os.path.relpath(args.out, BASE_DIR)}")

    if args.check:
        targets = sources
        if args.only:
            keys = [k.strip() for k in args.only.split(",") if k.strip()]
            targets = [s for s in sources if any(k in s["name"] for k in keys)]
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            results = list(pool.map(lambda s: check_source(s, doc["settings"]), targets))
        print("")
        print(f"{'件数':>4}  {'元記事':>5}  会社 / クエリ")
        empty = []
        for src, res in zip(targets, results):
            note = f"(エラー: {res['error']})" if res["error"] else ""
            print(f"{res['kept']:>4}  {res['total']:>5}  {src['name'][:34]:<34} {src['query']} {note}")
            for s in res["sample"][:2]:
                print(f"        · {s[:90]}")
            if res["kept"] == 0:
                empty.append(src["name"])
        if empty:
            print(f"\n記事0件の会社 {len(empty)}社: " + "、".join(empty))
        print(f"（確認にかかった時間 {time.time() - t0:.1f}秒）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
