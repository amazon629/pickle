#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SNS・広告代理店ボード — 監視したい代理店・SNSマーケ企業の一覧から
sources-sns-agencies.json を作る（1社＝1ソースの Google ニュース検索）。

使い方:
    python3 sync_sns_agencies.py             # sources-sns-agencies.json を書き出す
    python3 sync_sns_agencies.py --check     # 各社のクエリを実際に引いて件数も確認
    python3 sync_sns_agencies.py --list      # 登録されている社名だけ表示

対象企業はこのファイルの `COMPANIES` に並べています（Excel は不要）。
出力は `sources-sns-agencies.json`（update.py が自動で拾い、sns-agencies.html を作ります）。

クエリの作り方:
  * 会社名から「株式会社」などの法人格を外した名前を Google ニュースで検索し、
    その名前を含む見出しだけを残す（`must_match`）。同名他社や一般語のノイズを落とすため。
  * 「ベクトル」「ナハト」「オプト」「UUUM」「DYM」「CATS」のように一般語・短い英字・
    同名の別物と衝突する名前は、`OVERRIDES` でクエリを「株式会社〈社名〉」に絞ったり、
    広告・マーケ業界の語（`AD_CONTEXT`）を含む見出しだけに絞ったりする。
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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(BASE_DIR, "sources-sns-agencies.json")

BOARD_TITLE = "SNS・広告代理店 ニュース監視"
BOARD_NAV_LABEL = "SNS・広告代理店"

# --------------------------------------------------------------- 監視する企業
# 名前は表示名（カードの見出し）。並び順がそのままカードの並び順になります。
COMPANIES = [
    "株式会社Yaaha",
    "株式会社電通デジタル",
    "株式会社セプテーニ",
    "株式会社ナハト",
    "株式会社ブリーチ",
    "株式会社Hakuhodo DY ONE",
    "博報堂",
    "株式会社デジタルホールディングス",
    "株式会社CyberZ",
    "AnyMind Group株式会社",
    "株式会社CARTA ZERO",
    "UUUM",
    "toridori",
    "株式会社サイバー・バズ",
    "バレットグループ株式会社",
    "ワンメディア株式会社",
    "株式会社CyberACE",
    "株式会社TORIHADA",
    "株式会社Natee",
    "株式会社ベクトル",
    "TikTok for Business",
    "株式会社BitStar",
    "X Mile株式会社",
    "ソウルドアウト株式会社",
    "株式会社アドウェイズ",
    "株式会社DYM",
    "株式会社メディアハウスホールディングス",
    "株式会社インフィニティエージェント",
    "トランスコスモス株式会社",
    "studio15株式会社",
    "株式会社ピアラ",
    "株式会社オプト",
    "株式会社ワンスター",
    "株式会社Orchestra Holdings",
    "株式会社KASHIKA",
    "CATS株式会社",
    "株式会社電通ジャパン・インターナショナルブランズ",
    "株式会社デジタリフト",
]

# ---------------------------------------------------------------- company names
LEGAL_RE = re.compile(
    r"^(株式会社|有限会社|合同会社|合資会社|一般社団法人|一般財団法人|公益社団法人|公益財団法人)\s*"
)
PAREN_TAIL_RE = re.compile(r"\s*[（(][^（()）]*[)）]\s*$")


def core_name(name: str) -> str:
    """「株式会社オプト」→「オプト」「AnyMind Group株式会社」→「AnyMind Group」"""
    s = PAREN_TAIL_RE.sub("", (name or "").strip())
    s = LEGAL_RE.sub("", s)
    s = re.sub(r"\s*(株式会社|有限会社|合同会社)$", "", s)
    return s.strip()


# 広告・マーケティング業界の語。一般語の社名で「業界の記事」だけを残すための語彙。
AD_CONTEXT = [
    "広告", "マーケティング", "代理店", "デジタル", "メディア", "配信", "ソリューション",
    "プロモーション", "ブランド", "キャンペーン", "クリエイティブ", "販促", "インフルエンサー",
    "SNS", "動画", "PR", "プレスリリース", "株式会社", "社長", "代表", "事業", "サービス",
    "導入", "支援", "提携", "買収", "子会社", "決算", "上場", "資金調達", "調査",
    "セミナー", "レポート", "インタビュー", "EC", "アプリ", "YouTube", "TikTok",
]

# 社名（法人格を外した名前）ごとの調整。
#   query    … Google ニュースに投げる文字列（既定は "\"<名前>\""）
#   match    … 見出しにこれを含む記事だけを残す（別名・表記ゆれを足せる。既定は [名前]）
#   context  … True なら AD_CONTEXT のどれかを含む見出しだけに絞る
OVERRIDES = {
    # --- 一般語・短い英字と衝突する名前
    "ベクトル": {"query": '"株式会社ベクトル"', "context": True},
    # 美容の「ブリーチ」と区別する
    "ブリーチ": {"query": '"株式会社ブリーチ"', "match": ["株式会社ブリーチ"], "context": True},
    "ナハト": {"query": '"株式会社ナハト"', "context": True},
    "オプト": {"query": '"株式会社オプト"', "context": True},
    "UUUM": {"query": '"UUUM"', "match": ["UUUM", "ウーム"], "context": True},
    "DYM": {"query": '"株式会社DYM"', "match": ["株式会社DYM", "DYM"], "context": True},
    "CATS": {"query": '"CATS株式会社"', "match": ["CATS株式会社"], "context": True},
    "studio15": {"query": '"studio15"', "match": ["studio15", "スタジオ15"]},
    "ワンスター": {"query": '"株式会社ワンスター"', "context": True},
    "ピアラ": {"match": ["ピアラ"]},
    "toridori": {"match": ["toridori", "トリドリ"]},
    "KASHIKA": {"query": '"株式会社KASHIKA"', "match": ["KASHIKA"]},
    "Natee": {"query": '"株式会社Natee"', "match": ["Natee"]},
    "X Mile": {"query": '"X Mile"', "match": ["X Mile", "XMILE", "クロスミル"]},
    "Yaaha": {"query": '"Yaaha"', "match": ["Yaaha"]},
    "TORIHADA": {"query": '"TORIHADA"', "match": ["TORIHADA", "トリハダ"]},
    "CyberACE": {"query": '"CyberACE"', "match": ["CyberACE", "サイバーエース"]},
    "BitStar": {"query": '"BitStar"', "match": ["BitStar", "ビットスター"]},
    "バレットグループ": {"match": ["バレットグループ", "バレット"]},
    "インフィニティエージェント": {"match": ["インフィニティエージェント", "Infinity Agent"]},
    "メディアハウスホールディングス": {"match": ["メディアハウスホールディングス", "メディアハウスHD"]},
    "デジタルホールディングス": {"match": ["デジタルホールディングス", "デジタルHD"]},
    "サイバー・バズ": {"match": ["サイバー・バズ", "サイバーバズ"]},
    "電通ジャパン・インターナショナルブランズ": {
        "match": ["電通ジャパン・インターナショナルブランズ", "電通JIB"],
    },
    "Orchestra Holdings": {
        "query": '"Orchestra Holdings" OR "オーケストラ・ホールディングス"',
        "match": ["Orchestra Holdings", "オーケストラ・ホールディングス", "オーケストラホールディングス"],
    },
    "TikTok for Business": {"query": '"TikTok for Business"', "match": ["TikTok for Business"]},
    "博報堂": {"query": '"博報堂"', "match": ["博報堂"]},
    # --- 表記ゆれ（英字表記・略称）を別名として足す
    "電通デジタル": {"match": ["電通デジタル"]},
    "Hakuhodo DY ONE": {"match": ["Hakuhodo DY ONE", "博報堂DYワン"]},
    "サイバーエージェント": {"match": ["サイバーエージェント", "CyberAgent"]},
    "CyberZ": {"match": ["CyberZ"]},
    "AnyMind Group": {"match": ["AnyMind"]},
    "CARTA ZERO": {"match": ["CARTA ZERO"]},
    "ソウルドアウト": {"match": ["ソウルドアウト", "Sold Out"]},
    "アドウェイズ": {"match": ["アドウェイズ"]},
    "トランスコスモス": {"match": ["トランスコスモス"]},
    "デジタリフト": {"match": ["デジタリフト"]},
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
    override = {}
    for key in (core, unicodedata.normalize("NFKC", core), name, unicodedata.normalize("NFKC", name)):
        if OVERRIDES.get(key):
            override = OVERRIDES[key]
            break
    source = {
        "id": f"sns_{index:02d}",
        "type": "google_news",
        "name": name,
        "query": override.get("query") or f'"{core}"',
        "must_match": override.get("match") or [core],
        "limit": 5,
        "enabled": True,
    }
    if override.get("context"):
        source["context_match"] = AD_CONTEXT
    return source


def build_sources() -> list:
    seen, out = set(), []
    for name in COMPANIES:
        key = normalize(core_name(name))
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return [build_source(name, i + 1) for i, name in enumerate(out)]


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
            "title": BOARD_TITLE,
            "nav_label": BOARD_NAV_LABEL,
            "items_per_source": 5,
            "timeout_seconds": 15,
            "retries": 2,
            "fetch_workers": 4,
            "tolerate_errors": True,
            "translate": True,
            "translate_max_per_run": 20,
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
        },
        "sources": sorted(sources, key=lambda s: s["id"]),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SNS・広告代理店のニュース監視ボード設定を作る")
    ap.add_argument("--out", default=DEFAULT_OUT, help="出力する sources-*.json")
    ap.add_argument("--check", action="store_true", help="各社のクエリを実際に引いて件数を表示")
    ap.add_argument("--workers", type=int, default=3, help="--check の並列数（増やすと 429 になりやすい）")
    ap.add_argument("--show-samples", action="store_true", help="--check で残った見出しの例も出す")
    ap.add_argument("--list", action="store_true", help="登録する社名だけ表示して終了")
    args = ap.parse_args(argv)

    sources = build_sources()
    if args.list:
        for s in sources:
            print(f"{s['id']}  {s['name']:<34} {s['query']}")
        print(f"\n{len(sources)}社")
        return 0

    doc = make_document(sources)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"{len(sources)}社を書き出しました: {os.path.relpath(args.out, BASE_DIR)}")

    if args.check:
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            results = list(pool.map(lambda s: check_source(s, doc["settings"]), sources))
        print("")
        print(f"{'件数':>4}  {'元記事':>5}  会社 / クエリ")
        empty = []
        for src, res in zip(sources, results):
            note = f"(エラー: {res['error']})" if res["error"] else ""
            print(f"{res['kept']:>4}  {res['total']:>5}  {src['name'][:30]:<30} {src['query']} {note}")
            if args.show_samples and res["sample"]:
                for t in res["sample"]:
                    print(f"          - {t[:90]}")
            if res["kept"] == 0:
                empty.append(src["name"])
        if empty:
            print(f"\n記事0件の会社 {len(empty)}社: " + "、".join(empty))
        print(f"（確認にかかった時間 {time.time() - t0:.1f}秒）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
