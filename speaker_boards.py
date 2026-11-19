#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
登壇企業ボードの共通部品 — Excel「登壇企業一覧」から sources-*.json を作るところまで。

`sync_caster_companies.py` と `sync_nrtv_companies.py` が、それぞれのボード設定
（Excel のパス・列名・出力先・社名ごとのノイズ対策）を渡して使います。
Excel は標準ライブラリ（zipfile + xml）だけで読むので、追加インストールは不要です。

クエリの作り方:
  * 会社名から「株式会社」などの法人格と末尾の（…）を外した名前を Google ニュースで検索し、
    その名前を含む見出しだけを残す（`must_match`）。同名他社や一般語のノイズを落とすため。
  * 「これから」「そばに」「キャスター」のように一般語でも使われる名前は、`overrides` で
    クエリを言い換えたり、EC・マーケティング業界の語（`context_match`）を含む見出しだけに
    絞ったりする。
  * 同じ主催の中で「株式会社Picaro」と「株式会社Picaro.AI」のような表記ゆれは1社にまとめる
    （`aliases` に「小さいほうの名前 → まとめ先の名前」を書く）。
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

from news_supplements import apply_supplements
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor

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
PAREN_TAIL_RE = re.compile(r"\s*[（(]([^（()）]*)[)）]\s*$")
TRAILING_DOT_RE = re.compile(r"[.。\s]+$")


def split_alias(name: str) -> tuple:
    """「株式会社WUUZY（ECのプロ）」→ ("株式会社WUUZY", "ECのプロ")"""
    s = (name or "").strip()
    m = PAREN_TAIL_RE.search(s)
    if not m:
        return s, ""
    return s[: m.start()].strip(), m.group(1).strip()


def core_name(name: str) -> str:
    """「株式会社ピュアフラット」→「ピュアフラット」「グリニッジ株式会社」→「グリニッジ」"""
    s = (name or "").strip()
    s = PAREN_TAIL_RE.sub("", s)
    s = LEGAL_RE.sub("", s)
    s = re.sub(r"\s*(株式会社|有限会社|合同会社)$", "", s)
    s = TRAILING_DOT_RE.sub("", s)
    return s.strip()


def name_key(name: str) -> str:
    """重複判定用のキー（全角半角・大小・中黒や空白の揺れを吸収）。"""
    s = unicodedata.normalize("NFKC", core_name(name)).lower()
    return re.sub(r"[\s・.＿_\-&／/（）()「」'\"、,]", "", s)


def screen_aliases(alias: str) -> list:
    """かっこ書きの別名のうち、検索語として足しても安全なものだけ返す。

    「A8.net」「U-KOMI」「eBay Japan合同会社」のような英字主体の別名は表記ゆれなので足す。
    「ECのプロ」「マイクロアドグループ」のような一般語・親会社名は、足すとノイズになるので足さない
    （必要なものは overrides の match に明示する）。
    """
    a = (alias or "").strip()
    if len(a) < 3 or re.search(r"[ぁ-んァ-ヶー一-龥]", a):
        return []
    if not re.search(r"[A-Za-z]", a):
        return []
    return [a]


# ---------------------------------------------- 一般語社名のための文脈語（EC・マーケ）
# 広めの語彙（「株式会社」「支援」なども含む）。普通の社名で、業界の記事だけに絞りたいときに使う。
EC_CONTEXT = [
    "EC", "コマーズ", "通販", "ネットショップ", "ネット通販", "ショッピング",
    "楽天", "Amazon", "アマゾン", "ヤフー", "Yahoo", "ショップ", "出店", "越境", "D2C",
    "商品", "販売", "物流", "倉庫", "在庫", "受注", "決済", "カスタマー", "CRM",
    "セミナー", "ウェビナー", "登壇", "株式会社", "子会社", "買収", "提携", "決算", "上場",
    "資金調達", "リリース", "導入", "支援", "構築", "運用", "SaaS", "マーケティング",
    "広告", "代表", "社長", "事例", "サービス", "事業", "SNS", "ブランド", "メーカー",
    "メディア", "統合", "ツール", "システム", "業務", "顧客", "売上", "販促", "プロモーション",
]

# 狭めの語彙。一般語の社名（「オンサイト」「ジグザグ」「サポーターズ」など）で、
# 同名の別物を落とすときに使う。「株式会社」「支援」「代表」のような、どの記事にも出る語は入れない。
EC_STRICT = [
    "EC", "コマーズ", "通販", "ネットショップ", "ネット通販", "ショッピング", "楽天",
    "Amazon", "アマゾン", "ヤフー", "Yahoo", "Qoo10", "ショップ", "出店", "越境", "D2C",
    "決済", "物流", "倉庫", "在庫", "受注", "CRM", "SaaS", "マーケティング", "広告", "SNS",
    "カスタマー", "ブランド", "購買", "小売", "リテール", "店舗", "商品", "販売", "顧客",
    "ネット販売", "オンライン", "メーカー", "フルフィルメント", "受託",
    # その会社のセキュリティ事故は載せたい（同名他社ではまず起きない語）
    "不正アクセス", "サイバー", "情報漏えい", "個人情報",
]


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


# ------------------------------------------------------------------- board load
def load_companies(xlsx: str, board: dict) -> list:
    """「登壇企業一覧」を読み、表記ゆれをまとめた会社リストを返す。"""
    rows = read_sheet(xlsx, board["sheet"])
    if not rows:
        raise SystemExit("シートが空です")
    header = rows[0]
    col = board["name_column"]
    if col not in header:
        raise SystemExit(f"「{col}」列が見つかりません: {header}")
    ci = header.index(col)
    si = header.index(board["sessions_column"]) if board["sessions_column"] in header else None
    ei = header.index(board["events_column"]) if board["events_column"] in header else None

    def num(values, i):
        if i is None or i >= len(values):
            return 0
        try:
            return int(float(values[i]))
        except (TypeError, ValueError):
            return 0

    aliases = {name_key(k): name_key(v) for k, v in (board.get("aliases") or {}).items()}

    def root_key(name: str) -> str:
        k = name_key(name)
        seen = set()
        while k in aliases and k not in seen:
            seen.add(k)
            k = aliases[k]
        return k

    groups: dict = {}
    for pos, r in enumerate(rows[1:]):
        name = (r[ci] if ci < len(r) else "").strip()
        if not name:
            continue
        key = root_key(name)
        g = groups.setdefault(key, {"names": [], "sessions": 0, "events": 0, "first": pos, "counts": {}})
        if name not in g["names"]:
            g["names"].append(name)
        g["sessions"] += num(r, si)
        g["events"] += num(r, ei)
        g["counts"][name] = max(g["counts"].get(name, 0), num(r, si))

    out = []
    for key, g in groups.items():
        # 表示名は登壇回数の多い表記（同数ならシートで先に出た表記）
        names = sorted(g["names"], key=lambda n: (-g["counts"].get(n, 0), g["names"].index(n)))
        main = names[0]
        main_core, main_alias = split_alias(main)
        variants = [core_name(n) for n in names]
        # まとめた別表記は表示名のかっこに残す（例: 株式会社Picaro（Picaro.AI））
        extra_aliases = [split_alias(n)[1] for n in names[1:] if split_alias(n)[1]]
        extra_aliases += [core_name(n) for n in names[1:]]
        if len(names) > 1 and not main_alias:
            label = f"{main}（{'、'.join(dict.fromkeys(extra_aliases))}）"
        else:
            label = main
        out.append(
            {
                "name": label,
                "core": core_name(main),
                "sessions": g["sessions"],
                "events": g["events"],
                "first": g["first"],
                "variants": list(dict.fromkeys(variants)),
            }
        )
    # 登壇回数の多い順（同数はシート順）
    out.sort(key=lambda x: (-x["sessions"], x["first"]))
    return out


def build_source(company: dict, index: int, board: dict) -> dict:
    core = company["core"]
    override = (board.get("overrides") or {}).get(core, {})
    match = override.get("match")
    if not match:
        match = list(dict.fromkeys(company["variants"] + screen_aliases(split_alias(company["name"])[1])))
    source = {
        "id": f"{board['id_prefix']}_{index:02d}",
        "type": "google_news",
        "name": company["name"],
        "query": override.get("query") or f'"{core}"',
        "must_match": match,
        "limit": 5,
        "enabled": True,
    }
    if override.get("context"):
        ctx = override["context"]
        if ctx == "strict":
            source["context_match"] = EC_STRICT
        elif isinstance(ctx, list):
            source["context_match"] = ctx
        else:
            source["context_match"] = EC_CONTEXT
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
            titles = []
            for it in root.findall(".//item"):
                title = it.findtext("title") or ""
                site = it.findtext("source") or ""
                # update.py と同じく「 - サイト名」を落としてから見出しで判定する
                if site:
                    title = re.sub(r"\s*[-|–—]\s*" + re.escape(site) + r"\s*$", "", title).strip()
                titles.append(title)
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
def make_document(sources: list, board: dict) -> dict:
    return {
        "settings": {
            "title": board["title"],
            "nav_label": board["nav_label"],
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


def run(board: dict, argv=None) -> int:
    ap = argparse.ArgumentParser(description=f"{board['title']} の設定を作る")
    ap.add_argument("--xlsx", default=board["xlsx"], help="登壇企業一覧を含む Excel")
    ap.add_argument("--out", default=board["out"], help="出力する sources-*.json")
    ap.add_argument("--check", action="store_true", help="各社のクエリを実際に引いて件数を表示")
    ap.add_argument("--workers", type=int, default=6, help="--check の並列数")
    args = ap.parse_args(argv)

    if not os.path.exists(args.xlsx):
        print(f"Excel がありません: {args.xlsx}", file=sys.stderr)
        return 2

    companies = load_companies(args.xlsx, board)
    sources = [build_source(c, i + 1, board) for i, c in enumerate(companies)]
    # news-supplements.json の補完（PR TIMES 企業RSS・公式サイトのお知らせ）を足す。
    # 再同期しても補完定義が消えないように、書き出す直前に毎回適用する。
    sources = apply_supplements(board.get("supplement_key") or board["out"].split("sources-")[-1].replace(".json", ""), sources)
    doc = make_document(sources, board)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"{len(sources)}社を書き出しました: {os.path.basename(args.out)}")

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
