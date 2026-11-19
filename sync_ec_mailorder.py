#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通販・健康・コスメ54社ボード — 社名リストから sources-ec-mailorder.json を作る。

使い方:
    python3 sync_ec_mailorder.py                 # 54社ぶんの sources-ec-mailorder.json を書き出す
    python3 sync_ec_mailorder.py --check         # 書き出したあと各社のクエリを実際に引いて件数を確認
    python3 sync_ec_mailorder.py --check --only ルアン,フローラ,イング   # 一部の会社だけ確認（まず小さく試す）

出力 `sources-ec-mailorder.json` は update.py が自動で拾い、`ec-mailorder.html`
（タイトル「通販・健康・コスメ54社」）と全ページ共通ナビのピルを作ります。

クエリの作り方（既存の sync_ec_support.py / sync_ec_brands.py と同じ設計）:
  * 会社名から法人格を外した名前を Google ニュースで検索し、その名前を見出しに含む記事だけ残す
    （`must_match`）。同名他社・一般語のノイズを落とすため。
  * 「ランクアップ」「フローラ」「ルアン」「イング」「Right Here」「エポラ」「ナチュラルガーデン」
    のように一般語・人名・音楽・地名と混ざる名前は、`OVERRIDES` で
    クエリの言い換え（`query`）・業界語での絞り込み（`context_match`）・除外語（`exclude_match`）を入れる。
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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(BASE_DIR, "sources-ec-mailorder.json")

# --------------------------------------------------------------- 対象54社（送られたリストのまま）
COMPANIES = [
    "キューサイ",
    "エバーライフ",
    "銀座ステファニー化粧品",
    "ハーブ健康本舗",
    "ファーマフーズ",
    "ランクアップ",
    "フォーマルクライン",
    "日本薬師堂",
    "ダイドードリンコ",
    "ウェルヴィーナス",
    "オークローンマーケティング",
    "ファンファレ",
    "富山常備薬",
    "ルアン",
    "ECスタジオ",
    "愛しとーと",
    "クロコス",
    "フローラ",
    "エモテント",
    "はぐくみプラス",
    "JIMOS",
    "フロムココロ",
    "ニコリオ",
    "リタマインド・ジャパン",
    "世田谷自然食品",
    "RIZAP",
    "新日本製薬",
    "アルマード",
    "グロリアス製薬",
    "ジャパネットたかた",
    "野草酵素",
    "ヴァーナル",
    "日本自然発酵",
    "ベルーナ",
    "ナチュラルガーデン",
    "ユーグレナ",
    "SBIアラプロモ",
    "八幡物産",
    "サントリーウエルネス",
    "ホソカワミクロン化粧品",
    "東京ディベロ",
    "奈良大和生薬",
    "エポラ",
    "ディーエムジェイグループ",
    "ビタブリッドジャパン",
    "フレージュ",
    "チュラコス",
    "メディプラス",
    "ベースフード",
    "長寿乃里",
    "イング",
    "アルトルイズム",
    "ECホールディングス",
    "Right Here",
]

LEGAL_RE = re.compile(
    r"^(株式会社|有限会社|合同会社|合資会社|合名会社|一般社団法人|一般財団法人|公益社団法人|公益財団法人)\s*"
)
PAREN_TAIL_RE = re.compile(r"\s*[（(][^（()）]*[)）]\s*$")
LEGAL_TAIL_RE = re.compile(r"\s*(株式会社|有限会社|合同会社|合資会社|合名会社)$")


def core_name(name: str) -> str:
    """「株式会社ピュアフラット」→「ピュアフラット」「グリニッジ株式会社」→「グリニッジ」"""
    s = PAREN_TAIL_RE.sub("", (name or "").strip())
    s = LEGAL_RE.sub("", s)
    s = LEGAL_TAIL_RE.sub("", s)
    return s.strip()


# 通販・健康食品・コスメ業界の記事とみなす語。一般語の社名で同名の別物を落とすための語彙。
MAILORDER_CONTEXT = [
    "通販", "通信販売", "ネット通販", "ネットショップ", "EC", "D2C", "ダイレクトマーケティング",
    "化粧品", "コスメ", "スキンケア", "美容", "ヘアケア", "エイジングケア", "基礎化粧品",
    "健康食品", "サプリ", "サプリメント", "青汁", "酵素", "ダイエット", "栄養",
    "石けん", "洗顔", "化粧水", "美容液", "クリーム",
    "商品", "新商品", "発売", "販売", "ブランド", "店舗", "利用者", "顧客",
    "株式会社", "決算", "売上", "資金調達", "上場", "買収", "提携", "取材", "社長", "代表",
    "プレスリリース", "リリース", "発表", "サービス", "広告", "テレビ", "CM", "定期購入",
]

# 社名（法人格を外した名前）ごとの調整。
#   query    … Google ニュースに投げる文字列（既定は "<名前>"）
#   match    … 見出しにこれを含む記事だけを残す（別名・表記ゆれを足せる。既定は [名前]）
#   context  … True なら MAILORDER_CONTEXT のどれかを含む見出しだけに絞る
#   exclude  … 見出しにこれを含む記事は落とす
OVERRIDES = {
    # 「ランクアップ」はスポーツの昇格・ランキング記事が大半。通販化粧品（マナラ）の記事だけ残す
    "ランクアップ": {
        "query": '"マナラ" OR "ランクアップ" 通販',
        "match": ["ランクアップ", "マナラ", "MANARA"],
        "context": True,
        "exclude": ["トリニータ", "ホーリーホック", "Jリーグ", "J1", "J2", "J3", "昇格", "降格",
                    "チケット", "ゲーム", "パワプロ", "選手"],
    },
    # ルアン（株）＝スーパーミリオンヘアー。人名・ラオスのホテル・ゲームのキャラが大半
    "ルアン": {
        "query": '"ルアン" ヘアケア OR "スーパーミリオンヘアー"',
        "match": ["ルアン", "スーパーミリオンヘアー", "SMH"],
        "exclude": ["ラオス", "ルアン サイ", "ルアンパバーン", "ルアンプラバン", "ノルキエ",
                    "ボタ", "崩壊スターレイル", "メルセデス", "AMG"],
    },
    # 「フローラ」は花・園芸・ジルスチュアートのライン名が大半。HB-101 の会社だけ残す
    "フローラ": {
        "query": '"株式会社フローラ" OR "HB-101"',
        "match": ["フローラ", "HB-101", "エイチビー"],
        "exclude": ["フローラノーティス", "フローラル", "腸内フローラ", "ミライフローラ",
                    "フローラ・ハウス", "フローラ春"],
    },
    # 「イング」は「ビューイング」など複合語に埋もれる。法人名と独自商品名で拾う
    "イング": {
        "query": '"株式会社イング" 鹿児島 OR 化粧品 OR 健康食品',
        "match": ["株式会社イング", "長寿の里", "シリカナノコロイド", "美彩生"],
        "exclude": ["ビューイング", "ブリューイング", "ミーティング", "イングコーポレーション",
                    "スマイルサーベイ", "smile survey", "Salesforce", "アンケート", "人活診断",
                    "ドリームニュース", "イーストサイド"],
    },
    # 「Right Here」は曲名・アルバム名が大半。化粧品（WrinkFade）の会社だけ残す
    "Right Here": {
        "query": '"Right Here" 化粧品 OR WrinkFade',
        "match": ["Right Here", "Ｒｉｇｈｔ Ｈｅｒｅ", "ライトヒア", "WrinkFade"],
        "exclude": ["KANA-BOON", "SEVENTEEN", "羊文学", "武道館", "ツアー", "楽曲", "アルバム",
                    "MV", "ライブ", "配信リリース", "オリコン"],
    },
    # 「エポラ」は出血熱・選挙の記事が混ざる（エポラ＝ユーグレナグループの化粧品会社）
    "エポラ": {
        "query": '"エポラ" 化粧品 OR ヘマチン',
        "match": ["エポラ"],
        "exclude": ["出血熱", "エボラ", "コンゴ", "感染", "選挙", "市議", "議員", "エポラ祭り"],
    },
    # 「ナチュラルガーデン」は庭園・園芸記事が大半（白酵の通販会社）
    "ナチュラルガーデン": {
        "query": '"ナチュラルガーデン" 白酵 OR 化粧品 OR 通信販売',
        "match": ["ナチュラルガーデン", "白酵"],
        "exclude": ["滝川", "オープンガーデン", "庭園", "ガーデニング", "軽井沢", "江部乙", "花壇",
                    "多年草", "植えっぱなし", "園芸", "造園", "雑草"],
    },
    # 商品名でしか出てこない会社
    "銀座ステファニー化粧品": {
        "query": '"銀座ステファニー"',
        "match": ["銀座ステファニー", "ステファニー化粧品"],
    },
    "オークローンマーケティング": {
        "query": '"オークローンマーケティング"',
        "match": ["オークローン", "オークローンマーケティング"],
    },
    "JIMOS": {"query": '"JIMOS" OR "マキアレイベル"', "match": ["JIMOS", "マキアレイベル"]},
    "リタマインド・ジャパン": {"query": '"リタマインド"', "match": ["リタマインド"]},
    "SBIアラプロモ": {"query": '"アラプロモ"', "match": ["アラプロモ"]},
    "ジャパネットたかた": {
        "query": '"ジャパネットたかた" OR "ジャパネットホールディングス"',
        "match": ["ジャパネット"],
    },
    "ディーエムジェイグループ": {
        "query": '"ディーエムジェイ" OR "DMJ"',
        "match": ["ディーエムジェイ", "DMJ"],
        "exclude": ["ジャズ", "ハママツ", "ヤマハ", "楽器"],
    },
    "ベースフード": {
        "query": '"ベースフード" OR "BASE FOOD"',
        "match": ["ベースフード", "BASE FOOD"],
    },
    # 社名が競走馬名・スタジアム名・イベント名に使われているもの
    "ヴァーナル": {
        "exclude": ["競馬", "アクアヴァーナル", "エリザベス女王杯", "松山弘平", "騎手", "枠", "馬券"],
    },
    "ベルーナ": {
        "exclude": ["ベルーナドーム", "西武ドーム", "球場", "ライブ", "EXILE", "コンサート", "試合", "野球"],
    },
    # 商品名が一般名詞化していて、他社の商品レビューが混ざる
    "野草酵素": {
        "exclude": ["マイベスト", "ビオトピア", "野草酵素de美人", "酵素ドリンクの作り方"],
    },
    "ECホールディングス": {"query": '"ECホールディングス"', "match": ["ECホールディングス"]},
    "ECスタジオ": {
        "query": '"ECスタジオ"',
        "match": ["ECスタジオ"],
        "exclude": ["Profoto", "ストロボ", "撮影", "フォトスタジオ", "カメラ"],
    },
    # 関連会社・商品名でも検索して拾う
    "長寿乃里": {"match": ["長寿乃里", "然 よかせっけん", "SHIKARI", "つかってみんしゃい"]},
    "ホソカワミクロン化粧品": {"query": '"ホソカワミクロン"', "match": ["ホソカワミクロン"]},
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
    override = OVERRIDES.get(core, {}) or OVERRIDES.get(name, {})
    source = {
        "id": f"ecm_{index:02d}",
        "type": "google_news",
        "name": name,
        "query": override.get("query") or f'"{core}"',
        "must_match": override.get("match") or [core],
        "limit": 5,
        "enabled": True,
    }
    if override.get("context"):
        source["context_match"] = MAILORDER_CONTEXT
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
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "identity"})
            with urllib.request.urlopen(req, timeout=25) as res:
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
            time.sleep(2.0 + attempt * 2)
    return {"total": 0, "kept": 0, "sample": [], "error": str(last)[:80]}


# --------------------------------------------------------------------- output
def make_document(sources: list) -> dict:
    return {
        "settings": {
            "title": f"通販・健康・コスメ{len(sources)}社 ニュース監視",
            "nav_label": f"通販・コスメ{len(sources)}社",
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
    ap = argparse.ArgumentParser(description="通販・健康・コスメ54社のニュース監視ボード設定を作る")
    ap.add_argument("--out", default=DEFAULT_OUT, help="出力する sources-*.json")
    ap.add_argument("--check", action="store_true", help="各社のクエリを実際に引いて件数を表示")
    ap.add_argument("--only", help="この文字列を含む会社だけ確認（カンマ区切り）")
    ap.add_argument("--workers", type=int, default=4, help="--check の並列数")
    args = ap.parse_args(argv)

    sources = [build_source(n, i + 1) for i, n in enumerate(COMPANIES)]
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
            print(f"{res['kept']:>4}  {res['total']:>5}  {src['name'][:26]:<26} {src['query']} {note}")
            for s in res["sample"][:2]:
                print(f"        · {s[:96]}")
            if res["kept"] == 0:
                empty.append(src["name"])
        if empty:
            print(f"\n記事0件の会社 {len(empty)}社: " + "、".join(empty))
        print(f"（確認にかかった時間 {time.time() - t0:.1f}秒）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
