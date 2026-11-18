#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SmartNews 認定代理店ボード — Excel の「2026年認定企業一覧」「2025年認定企業一覧」から
sources-smartnews-partners.json を作る。

使い方:
    python3 sync_smartnews_partners.py                       # 既定の Excel から書き出す
    python3 sync_smartnews_partners.py --xlsx path/to.xlsx   # Excel を指定
    python3 sync_smartnews_partners.py --check               # 各社のクエリを実際に引いて件数を確認

Excel は標準ライブラリ（zipfile + xml）だけで読むので、追加インストールは不要です。
出力は `sources-smartnews-partners.json`（update.py が自動で拾い、smartnews-partners.html を作ります）。

対象:
  * 2026年に認定された企業と、2025年に認定されていた企業（2026年はランク外）を合わせた
    ユニーク企業。社名非公表（「（社名非公表）」）の行は除外する。
  * 「株式会社WEB GRAM / WEBGRAM」のような表記ゆれは `ALIASES` で同一企業に統合し、
    2026年の表記を採用する。

クエリの作り方:
  * 会社名から「株式会社」などの法人格を外した名前を Google ニュースで検索し、
    その名前を含む見出しだけを残す（`must_match`）。同名他社や一般語のノイズを落とすため。
  * 「無限」「ブリーチ」「ヴァンクリーフ」のように一般語・有名な別物と衝突する名前は、
    `OVERRIDES` でクエリを「株式会社〈名前〉」に絞ったり、広告業界の語（`AD_CONTEXT`）を
    含む見出しだけに絞ったりする。
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
import zipfile
from concurrent.futures import ThreadPoolExecutor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_XLSX = os.path.join(BASE_DIR, "..", "downloads", "smartnews-partners-2025-2026.xlsx")
DEFAULT_OUT = os.path.join(BASE_DIR, "sources-smartnews-partners.json")

SHEET_2026 = "2026年認定企業一覧"
SHEET_2025 = "2025年認定企業一覧"
COMPANY_COLUMN = "企業名"
RANK_COLUMN = "ランク"

# ランクの並び（上のランクから表示する）
RANK_ORDER = {"Diamond": 0, "Platinum": 1, "Gold": 2, "Silver": 3, "Bronze": 4}

# ------------------------------------------------------------------ xlsx (stdlib)
NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _col_index(ref: str) -> int:
    """A1 -> 0, B7 -> 1"""
    m = re.match(r"([A-Z]+)", ref or "A")
    if not m:
        return 0
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def read_sheet(path: str, sheet_name: str) -> list:
    """シートを list[list[str]] で返す（共有文字列・インライン文字列に対応）。"""
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{NS_MAIN}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{NS_MAIN}t")))

        sheet_path = None
        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_map = {r.get("Id"): r.get("Target") for r in rels.findall(f"{NS_PKG_REL}Relationship")}
        for sheet in workbook.iter(f"{NS_MAIN}sheet"):
            if (sheet.get("name") or "") == sheet_name:
                target = (rel_map.get(sheet.get(f"{NS_REL}id"), "") or "").lstrip("/")
                sheet_path = target if target.startswith("xl/") else os.path.join("xl", target)
                break
        if not sheet_path or sheet_path not in names:
            raise SystemExit(f"シートが見つかりません: {sheet_name}")

        rows = []
        root = ET.fromstring(zf.read(sheet_path))
        for row in root.iter(f"{NS_MAIN}row"):
            cells = {}
            for c in row.findall(f"{NS_MAIN}c"):
                idx = _col_index(c.get("r"))
                t = c.get("t")
                if t == "s":
                    v = c.find(f"{NS_MAIN}v")
                    value = shared[int(v.text)] if v is not None and v.text else ""
                elif t == "inlineStr":
                    value = "".join(x.text or "" for x in c.iter(f"{NS_MAIN}t"))
                else:
                    v = c.find(f"{NS_MAIN}v")
                    value = v.text if v is not None else ""
                cells[idx] = (value or "").strip()
            if cells:
                rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
        return rows


# ---------------------------------------------------------------- company names
LEGAL_RE = re.compile(
    r"^(株式会社|有限会社|合同会社|合資会社|一般社団法人|一般財団法人|公益社団法人|公益財団法人)\s*"
)
PAREN_TAIL_RE = re.compile(r"\s*[（(][^（()）]*[)）]\s*$")


def core_name(name: str) -> str:
    """「株式会社オプト」→「オプト」「Supership株式会社」→「Supership」"""
    s = PAREN_TAIL_RE.sub("", (name or "").strip())
    s = LEGAL_RE.sub("", s)
    s = re.sub(r"\s*(株式会社|有限会社|合同会社)$", "", s)
    return s.strip()


def dedupe_key(name: str) -> str:
    """表記ゆれ（全角半角・空白・中黒・アポストロフィ）を吸収した重複判定用のキー。"""
    s = unicodedata.normalize("NFKC", core_name(name)).lower()
    s = re.sub(r"[\s・･\.\,，'’`´]", "", s)
    return s


# 2025年表記 → 2026年表記（概要シート「データの注意点」9 の表記ゆれ）
ALIASES = {
    "株式会社WEB GRAM": "株式会社WEBGRAM",
    "株式会社無限": "株式会社 無限",
    "株式会社CyberZ": "株式会社 CyberZ",
    "株式会社Lits": "株式会社LIT’S",
    "株式会社保険市場": "株式会社 保険市場",
}

# 広告・マーケティング業界の語。一般語の社名で「業界の記事」だけを残すための語彙。
# （ヴァンクリーフ宝石、ブリーチ（脱色・漫画）、自閉症スペクトラム… を落とす）
AD_CONTEXT = [
    "広告", "マーケティング", "代理店", "デジタル", "メディア", "配信", "ソリューション",
    "プロモーション", "ブランド", "キャンペーン", "クリエイティブ", "販促", "インフルエンサー",
    "SNS", "動画", "PR", "プレスリリース", "株式会社", "社長", "代表", "事業", "サービス",
    "導入", "支援", "提携", "買収", "子会社", "決算", "上場", "資金調達", "調査",
    "セミナー", "レポート", "インタビュー", "EC", "アプリ",
]

# 社名（法人格を外した名前）ごとの調整。
#   query    … Google ニュースに投げる文字列（既定は "\"<名前>\""）
#   match    … 見出しにこれを含む記事だけを残す（別名・表記ゆれを足せる。既定は [名前]）
#   context  … True なら AD_CONTEXT のどれかを含む見出しだけに絞る
OVERRIDES = {
    # --- 一般語・有名な別物と衝突する名前：法人格つきで検索して業界語で絞る
    "オプト": {"query": '"株式会社オプト"', "context": True},
    "ブリーチ": {"query": '"株式会社ブリーチ"', "context": True},
    "無限": {"query": '"株式会社無限"', "match": ["株式会社無限", "株式会社 無限"], "context": True},
    "NAKAI": {"query": '"株式会社NAKAI"', "context": True},
    "Full.": {"query": '"株式会社Full."', "match": ["株式会社Full.", "フルドット"], "context": True},
    "ディスカバリー": {
        "query": '"株式会社ディスカバリー"',
        # 株式会社ディスカバリー・ホームズ / ディスカバリー・ネクスト など別会社を落とす
        "match": [
            "株式会社ディスカバリーの",
            "株式会社ディスカバリー、",
            "ディスカバリーが",
            "ディスカバリーは",
        ],
        "context": True,
    },
    "エレガント": {"query": '"有限会社エレガント"', "context": True},
    "未踏": {"query": '"合同会社未踏"', "context": True},
    "はこ": {"query": '"株式会社はこ"', "context": True},
    "スイミー": {"query": '"スイミー株式会社"', "context": True},
    "ヴァンクリーフ": {"query": '"株式会社ヴァンクリーフ"', "context": True},
    "スペクトラム": {"query": '"株式会社スペクトラム"', "context": True},
    "ラフ": {"query": '"株式会社ラフ"', "context": True},
    "キュービック": {"query": '"株式会社キュービック"', "context": True},
    "セレス": {"query": '"株式会社セレス"', "context": True},
    "アップデイト": {"query": '"株式会社アップデイト"', "context": True},
    "ワンスター": {"query": '"株式会社ワンスター"', "context": True},
    "LIBER": {"query": '"株式会社LIBER"', "context": True},
    "prisma": {"query": '"株式会社prisma"', "context": True},
    "milkyway": {"query": '"株式会社milkyway"', "context": True},
    "メディックス": {
        # バレーボールの「埼玉上尾メディックス」を落とす
        "match": ["株式会社メディックス", "メディックスが", "メディックスは"],
        "context": True,
    },
    "shiroiro": {"query": '"株式会社shiroiro"', "context": True},
    "フォースリー": {"query": '"株式会社フォースリー"', "context": True},
    "マーケティングワン": {
        "query": '"株式会社マーケティングワン"',
        "match": ["株式会社マーケティングワン"],
        "context": True,
    },
    "メンバーズ": {"query": '"株式会社メンバーズ"', "context": True},
    "ラフ": {"query": '"株式会社ラフ"', "match": ["株式会社ラフ", "株式会社 ラフ"]},
    "TeN": {"query": '"株式会社TeN"', "context": True},
    "デジタルアイデンティティ": {
        "query": '"株式会社デジタルアイデンティティ"',
        "match": ["株式会社デジタルアイデンティティ", "株式会社 デジタルアイデンティティ", "デジタルアイデンティティ株式会社"],
        "context": True,
    },
    "キュービック": {
        "query": '"株式会社キュービック"',
        "match": ["株式会社キュービック", "株式会社 キュービック"],
        "context": True,
    },
    "Kento": {"query": '"株式会社Kento"', "context": True},
    "カカクコム": {"match": ["カカクコム"]},
    "保険市場": {
        "query": '"株式会社保険市場"',
        "match": ["株式会社保険市場", "株式会社 保険市場", "保険市場ドットコム"],
    },
    "ヘリオス": {"context": True},
    "HELIOS": {"query": '"株式会社HELIOS"', "context": True},
    "Hide&Seek": {
        "query": '"株式会社Hide&Seek"',
        "match": ["株式会社Hide&Seek"],
        "context": True,
    },
    # Forbes JAPAN の「リンクタイズ株式会社」と人材系の同名社を落とす
    "タイズ": {"query": '"タイズ株式会社" -リンク -転職', "match": ["タイズ株式会社"], "context": True},
    "バローズ": {"query": '"株式会社バローズ"', "match": ["株式会社バローズ"], "context": True},
    "アドスタイル": {
        "query": '"株式会社アドスタイル"',
        "match": ["株式会社アドスタイル"],
        "context": True,
    },
    "BHK": {"query": '"株式会社BHK"', "context": True},
    "N8": {"query": '"株式会社N8"', "match": ["株式会社N8", "N8"]},
    # ポケモンの「メタモン」、ゲームの「Slayers」、音楽の「SWEEET」等を落とす
    "Metamon": {"match": ["株式会社Metamon"]},
    "Slayers": {"match": ["株式会社Slayers"]},
    "Sweeet": {"match": ["株式会社Sweeet", "株式会社SWEEET"]},
    "SMD": {"query": '"株式会社SMD"', "match": ["株式会社SMD", "SMD"]},
    "GivingBack": {"query": '"株式会社GivingBack"', "match": ["株式会社GivingBack"]},
    "Best Relation": {"query": '"株式会社Best Relation"', "context": True},
    # --- 表記ゆれ（英字表記・略称）を別名として足す
    "サイバーエージェント": {"match": ["サイバーエージェント", "CyberAgent"]},
    "電通デジタル": {"match": ["電通デジタル"]},
    "Hakuhodo DY ONE": {"match": ["Hakuhodo DY ONE", "博報堂DYワン"]},
    "DIGITAL HEARTS CROSS Singapore Pte. Ltd.": {
        "query": '"DIGITAL HEARTS"',
        "match": ["DIGITAL HEARTS"],
    },
    "LIT’S": {
        "query": '"株式会社LIT’S"',
        "match": ["株式会社LIT’S", "株式会社LIT'S", "LIT’S株式会社"],
    },
    "WEBGRAM": {"match": ["WEBGRAM", "WEB GRAM"]},
    "boxXXX": {"match": ["boxXXX", "box-x"]},
    "ADKデジタル・コミュニケーションズ": {
        "match": ["ADKデジタル・コミュニケーションズ", "ADKデジタルコミュニケーションズ"],
    },
    "カタリナマーケティングジャパン": {"match": ["カタリナマーケティング"]},
    "I&S BBDO": {"query": '"I&S BBDO"', "match": ["I&S BBDO", "I＆S BBDO"]},
    "オライオン・トレーディング・ジャパン": {
        "query": '"オライオントレーディングジャパン" OR "オライオン・トレーディング・ジャパン"',
        "match": ["オライオン"],
    },
    "D2C R": {"match": ["D2C R", "D2CR"]},
    "データ・ワン": {"match": ["データ・ワン", "データワン"]},
    "みらいきれい": {"match": ["みらいきれい"]},
    "ソウルドアウト": {"match": ["ソウルドアウト", "Sold Out"]},
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


def build_source(company: dict, index: int) -> dict:
    name = company["name"]
    core = core_name(name)
    override = OVERRIDES.get(core) or OVERRIDES.get(name) or {}
    source = {
        "id": f"snp_{index:02d}",
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


def load_companies(xlsx: str) -> list:
    """2026年 + 2025年の認定企業を、重複を統合して1社1件にする。"""
    companies = []  # {"name", "rank", "year"}
    by_key = {}

    for sheet, year in ((SHEET_2026, 2026), (SHEET_2025, 2025)):
        rows = read_sheet(xlsx, sheet)
        if not rows:
            raise SystemExit(f"シートが空です: {sheet}")
        header = rows[0]
        if COMPANY_COLUMN not in header or RANK_COLUMN not in header:
            raise SystemExit(f"「{COMPANY_COLUMN}」「{RANK_COLUMN}」列が見つかりません: {sheet} / {header}")
        ni, ri = header.index(COMPANY_COLUMN), header.index(RANK_COLUMN)

        for r in rows[1:]:
            raw = (r[ni] if ni < len(r) else "").strip()
            if not raw or "非公表" in raw:
                continue  # 「（社名非公表）」の行は対象外
            # 「…株式会社（連絡は関連会社まで）」のような長い注記入りの表記は注記を落とす
            name = PAREN_TAIL_RE.sub("", ALIASES.get(raw, raw)).strip()
            key = dedupe_key(name)
            if key in by_key:
                by_key[key]["years"].add(year)
                # 2026年の表記・ランクを優先する
                if year == 2026:
                    by_key[key]["name"] = name
                    by_key[key]["rank"] = (r[ri] if ri < len(r) else "").strip()
                continue
            entry = {
                "name": name,
                "rank": (r[ri] if ri < len(r) else "").strip(),
                "years": {year},
            }
            by_key[key] = entry
            companies.append(entry)

    # 2026年のランク順（同ランクはシート順）。2025年のみの企業は末尾に回す。
    def sort_key(item):
        idx, company = item
        if 2026 in company["years"]:
            return (0, RANK_ORDER.get(company["rank"], 9), idx)
        return (1, RANK_ORDER.get(company["rank"], 9), idx)

    ordered = [c for _, c in sorted(enumerate(companies), key=sort_key)]
    return ordered


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
            "title": "SmartNews 認定代理店 ニュース監視",
            "nav_label": "SmartNews代理店",
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
        "sources": sorted(sources, key=lambda s: s["id"]),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SmartNews 認定代理店のニュース監視ボード設定を作る")
    ap.add_argument("--xlsx", default=DEFAULT_XLSX, help="認定企業一覧を含む Excel")
    ap.add_argument("--out", default=DEFAULT_OUT, help="出力する sources-*.json")
    ap.add_argument("--check", action="store_true", help="各社のクエリを実際に引いて件数を表示")
    ap.add_argument("--workers", type=int, default=3, help="--check の並列数（増やすと 429 になりやすい）")
    ap.add_argument("--show-samples", action="store_true", help="--check で残った見出しの例も出す")
    args = ap.parse_args(argv)

    if not os.path.exists(args.xlsx):
        print(f"Excel がありません: {args.xlsx}", file=sys.stderr)
        return 2

    companies = load_companies(args.xlsx)
    sources = [build_source(c, i + 1) for i, c in enumerate(companies)]
    doc = make_document(sources)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"{len(sources)}社を書き出しました: {os.path.relpath(args.out, BASE_DIR)}")
    print(
        f"  （2026年 {sum(1 for c in companies if 2026 in c['years'])}社 / "
        f"2025年のみ {sum(1 for c in companies if 2026 not in c['years'])}社）"
    )

    if args.check:
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            results = list(pool.map(lambda s: check_source(s, doc["settings"]), sources))
        print("")
        print(f"{'件数':>4}  {'元記事':>5}  会社 / クエリ")
        empty = []
        for src, res in zip(sources, results):
            note = f"(エラー: {res['error']})" if res["error"] else ""
            print(f"{res['kept']:>4}  {res['total']:>5}  {src['name'][:34]:<34} {src['query']} {note}")
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
