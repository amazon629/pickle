#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ピュアフラット登壇企業ボード — Excel の「登壇企業一覧」から sources-ec-companies.json を作る。

使い方:
    python3 sync_pureflat_companies.py                       # 既定の Excel から sources-ec-companies.json を書き出す
    python3 sync_pureflat_companies.py --xlsx path/to.xlsx   # Excel を指定
    python3 sync_pureflat_companies.py --check               # 書き出し後に各社のクエリを実際に引いて件数を確認

Excel は標準ライブラリ（zipfile + xml）だけで読むので、追加インストールは不要です。
出力は `sources-ec-companies.json`（update.py が自動で拾い、ec-companies.html を作ります）。

クエリの作り方:
  * 会社名から「株式会社」などの法人格と末尾の（…）を外した名前を Google ニュースで検索し、
    その名前を含む見出しだけを残す（`must_match`）。同名他社や一般語のノイズを落とすため。
  * 「これから」「そばに」「グリニッジ」のように一般語でも使われる名前は、`OVERRIDES` で
    クエリを言い換えたり、EC 業界の語（`context_match`）を含む見出しだけに絞ったりする。
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
DEFAULT_XLSX = os.path.join(BASE_DIR, "..", "downloads", "pureflat-events-speakers.xlsx")
DEFAULT_OUT = os.path.join(BASE_DIR, "sources-ec-companies.json")

SHEET_NAME = "登壇企業一覧"
COMPANY_COLUMN = "登壇企業名"

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
    """「株式会社ピュアフラット」→「ピュアフラット」「グリニッジ株式会社」→「グリニッジ」"""
    s = PAREN_TAIL_RE.sub("", (name or "").strip())
    s = LEGAL_RE.sub("", s)
    s = re.sub(r"\s*(株式会社|有限会社|合同会社)$", "", s)
    return s.strip()


# 一般語の社名で「業界の記事」とみなす語。同名の別物（グリニッジ天文台、
# フュージョンエネルギー、プロバスケのスタークス…）を落とすための語彙。
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
    # 一般語・短い名前。EC 文脈の語を足して同名の別物を落とす
    "そばに": {"query": '"株式会社そばに"', "match": ["株式会社そばに", "そばにの", "そばにが"]},
    "これから": {"query": '"株式会社これから"', "match": ["株式会社これから", "これから、"]},
    "NE": {"query": '"NE株式会社"', "match": ["NE株式会社", "NE Korea", "441A"]},
    "しるし": {"query": '"しるし" EC', "context": True},
    "キャスター": {"query": '"キャスター" EC', "context": True},
    "アルド": {"query": '"アルド" EC', "context": True},
    "フルバランス": {"query": '"フルバランス" EC', "context": True},
    "ロックウェーブ": {"query": '"ロックウェーブ" EC', "context": True},
    "グリニッジ": {"query": '"グリニッジ" EC', "context": True},
    "スタークス": {"query": '"スタークス" EC', "context": True},
    "フュージョン": {"query": '"フュージョン" EC', "context": True},
    "COIM": {"query": '"COIM" EC', "context": True},
    "E-Grant": {"query": '"E-Grant" EC', "context": True},
    "GOAT": {"query": '"GOAT" EC', "context": True},
    "blends": {"query": '"blends" EC', "context": True},
    # GIVIN は旧 blends（社名変更）。Google ニュース上はまだ「blends」表記が大半なので、
    # ここでは新社名そのものの記事だけを拾う（他社 Givin' Back の記事を入れない）
    "GIVIN": {"query": '"株式会社GIVIN"', "match": ["株式会社GIVIN", "GIVIN（"]},
    "リスマ": {"query": '"リスマ" EC', "context": True},
    "W2": {"query": '"W2" EC', "context": True},
    "JAPAN SOLUTIONS": {"query": '"JAPAN SOLUTIONS"', "context": True},
    "メルカート": {"context": True},
    "ユーコンスカイ": {"context": True},
    "エンパタウン": {"context": True},
    "ウブン": {"context": True},
    "はぴロジ": {"context": True},
    "ネイビーグループ": {"context": True},
    "マテリアルデジタル": {"context": True},
    "ライスカレープラス": {"context": True},
    # 表記ゆれ（英字表記・略称）を別名として足す
    "天喜ジャパン": {"match": ["天喜ジャパン", "TENKI-JAPAN"]},
    "GMOメイクショップ": {"match": ["GMOメイクショップ", "メイクショップ"]},
    "Gastroduce Japan": {"match": ["Gastroduce"]},
    "ファンコミュニケーションズ": {"match": ["ファンコミュニケーションズ", "ファンコミ"]},
    "ECXグループ": {"match": ["ECXグループ", "ecx"]},
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


def build_source(row: dict, index: int) -> dict:
    name = row["name"]
    core = core_name(name)
    override = OVERRIDES.get(core, {})
    source = {
        "id": f"gn_{index:02d}",
        "type": "google_news",
        "name": name,
        "query": override.get("query") or f'"{core}"',
        "must_match": override.get("match") or [core],
        "limit": 5,
        "enabled": True,
    }
    if override.get("context"):
        source["context_match"] = EC_CONTEXT
    return source


def load_companies(xlsx: str) -> list:
    rows = read_sheet(xlsx, SHEET_NAME)
    if not rows:
        raise SystemExit("シートが空です")
    header = rows[0]
    if COMPANY_COLUMN not in header:
        raise SystemExit(f"「{COMPANY_COLUMN}」列が見つかりません: {header}")
    ci = header.index(COMPANY_COLUMN)
    si = header.index("登壇回数（部）") if "登壇回数（部）" in header else None
    ei = header.index("参加イベント数") if "参加イベント数" in header else None

    def num(values, i):
        if i is None or i >= len(values):
            return 0
        try:
            return int(float(values[i]))
        except (TypeError, ValueError):
            return 0

    out, seen = [], set()
    for r in rows[1:]:
        name = (r[ci] if ci < len(r) else "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "sessions": num(r, si), "events": num(r, ei)})
    # 登壇回数の多い順（同数はシート順）
    out.sort(key=lambda x: -x["sessions"])
    return out


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
            "title": "ピュアフラット登壇企業 ニュース監視",
            "nav_label": f"EC企業{len(sources)}社",
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
    ap = argparse.ArgumentParser(description="登壇企業のニュース監視ボード設定を作る")
    ap.add_argument("--xlsx", default=DEFAULT_XLSX, help="登壇企業一覧を含む Excel")
    ap.add_argument("--out", default=DEFAULT_OUT, help="出力する sources-*.json")
    ap.add_argument("--check", action="store_true", help="各社のクエリを実際に引いて件数を表示")
    ap.add_argument("--workers", type=int, default=4, help="--check の並列数")
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
            if res["kept"] == 0:
                empty.append(src["name"])
        if empty:
            print(f"\n記事0件の会社 {len(empty)}社: " + "、".join(empty))
        print(f"（確認にかかった時間 {time.time() - t0:.1f}秒）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
