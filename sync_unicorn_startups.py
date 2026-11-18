#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ユニコーン監視ボード — Excel の「評価額あり案件」から、企業価値10億ドル以上
（円換算 1,500億円以上）のスタートアップを抜き出して sources-unicorns.json を作る。

使い方:
    python3 sync_unicorn_startups.py                        # 既定の Excel から sources-unicorns.json を書き出す
    python3 sync_unicorn_startups.py --xlsx path/to.xlsx    # Excel を指定
    python3 sync_unicorn_startups.py --check                # 各社のクエリを実際に引いて件数と見出しを確認
    python3 sync_unicorn_startups.py --list                 # 抽出した社名と評価額だけ表示
    python3 sync_unicorn_startups.py --json                 # 結果を JSON 1行で出力（自動連携用）
    python3 sync_unicorn_startups.py --no-write             # ファイルを書かずに確認

出力した `sources-unicorns.json` は update.py が自動で拾い、unicorns.html を作ります。
依存は標準ライブラリ＋openpyxl のみ。openpyxl が無い環境では標準ライブラリ（zipfile + xml）で読みます。

抽出の決まり:
  * `評価額あり案件` シートの `円換算（億円・参考）` が 1,500 以上（≒10億ドル）の行を対象にする。
  * 同じ企業の複数ラウンドは1社にまとめる（評価額は最大のもの、ラウンドは一覧で保持）。
  * 企業名が未定・仮称のもの（「（Junyang LinのAIラボ）」など）は除外。
  * 買収額しかない行（既存上場企業のM&A。Bumble / Long Lake など）は除外。
    Walmart に買収された Vibe のように「スタートアップの評価額」が載っている行は残す。
  * `Town` `Enter` `Glow` `Modal` `Harvey` `Column` `Vibe` のような一般語・短い社名は、
    クエリに文脈語を足し（`query`）、見出しにも文脈語を要求する（`context_match`）ことで
    「町」「enterキー」「グロウ（化粧品）」のような無関係な記事を落とす。
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
DEFAULT_XLSX = os.path.join(BASE_DIR, "..", "downloads", "startup-funding-valuations.xlsx")
DEFAULT_OUT = os.path.join(BASE_DIR, "sources-unicorns.json")

SHEET_NAME = "評価額あり案件"
MIN_OKU = 1500          # 10億ドル ≒ 1,500億円
ID_PREFIX = "uc"
BOARD_TITLE = "10億ドル以上ユニコーン ニュース監視"
BOARD_NAV_LABEL = "🦄 10億ドル以上"

COL_NAME = "企業・プロジェクト名"
COL_SECTOR = "分野"
COL_ROUND = "ラウンド・種別"
COL_VALUATION = "評価額・時価総額（原文）"
COL_VALUATION_WEB = "評価額（Web補完）"
COL_OKU = "円換算（億円・参考）"
COL_AMOUNT = "調達額"
COL_DATE = "日付"
COL_URL = "投稿URL"

# スタートアップのニュース見出しにだけ出る語。一般語の社名の絞り込みに使う。
STARTUP_CTX = [
    "startup", "start-up", "unicorn", "valuation", "valued at", "funding",
    "raises", "raised", "raising", "Series A", "Series B", "Series C", "Series D",
    "Series E", "Series F", "Series G", "seed round", "backs", "backed by",
    "IPO", "acquires", "acquisition", "investors", "venture",
    "資金調達", "調達", "評価額", "企業価値", "ユニコーン", "上場", "買収", "出資", "億円", "億ドル",
]

# 社名ごとの調整。
#   query    … Google ニュースに投げる文字列（既定は "<社名>"）
#   match    … 見出しにこれを含む記事だけ残す（別名・表記ゆれを足せる。既定は社名）
#   context  … リストを渡すと、見出しがこのどれかを含む記事だけ残す（一般語の社名用）
#   feeds    … 取得する版（既定は en + ja。片方だけにしたいとき ["en"] / ["ja"]）
OVERRIDES = {
    # ---- 一般語・短い英単語。文脈語で絞る
    "Town": {
        "query": '"Town" (startup OR unicorn OR "AI assistant" OR "AI agent" OR Andreessen)',
        "context": ["unicorn", "valuation", "raises", "raised", "AI assistant",
                    "AI agent", "personal assistant", "a16z", "Andreessen", "startup Town"],
        "exclude": ["town hall", "town council", "mayor", "old town", "downtown",
                    "township", "town of ", "in town", "around town", "small-town",
                    "small town", "town centre", "town center", "cape town", "town's",
                    "ghost town", "boom town"],
    },
    "Enter": {
        "query": '"Enter" ("legal AI" OR legaltech OR juridico OR jurídico OR "Founders Fund" OR litigation)',
        "context": ["legal", "jurídic", "juridic", "law firm", "lawyers", "lawsuit", "litigation",
                    "startup", "unicorn", "valuation", "funding", "raises", "Series",
                    "法律", "訴訟", "弁護士"],
        "exclude": ["enter the", "enter a ", "enter into", "enter now", "enter to win",
                    "click to enter", "entering", "to enter", "and enter", "enter talks",
                    "enter settlement", "enter a new"],
    },
    "Glow": {
        "query": '"Glow" (startup OR funding OR valuation OR cybersecurity OR security OR raises)',
        "context": ["startup", "funding", "valuation", "raises", "raised", "cybersecurity",
                    "security", "endpoint", "Series", "資金調達", "評価額"],
        "exclude": ["Dolce Glow", "glow up", "makeup", "skincare", "cosmetic"],
    },
    "Modal": {
        "query": '"Modal" (startup OR funding OR valuation OR serverless OR "AI infrastructure" OR raises)',
        "context": ["startup", "funding", "valuation", "raises", "raised", "serverless",
                    "AI", "GPU", "Series", "資金調達", "評価額"],
    },
    "Harvey": {
        "query": '"Harvey" (startup OR funding OR valuation OR "legal AI" OR "law firm" OR raises)',
        "context": ["startup", "funding", "valuation", "raises", "raised", "legal",
                    "law firm", "lawyers", "AI", "Series", "資金調達", "評価額", "法律"],
    },
    "Column": {
        "query": '"Column" (bank OR fintech OR "Column N.A." OR "Column Bank" OR lending)',
        "context": ["bank", "fintech", "chartered", "lending", "deposits", "payments",
                    "startup", "valuation", "raises", "Series", "資金調達", "評価額", "銀行"],
        "exclude": ["guest column", "opinion column", "column:", "this column",
                    "superintendent", "education funding", "op-ed", "launches column",
                    "column by", "in his column"],
    },
    "Nominal": {
        "query": '"Nominal" (startup OR funding OR valuation OR manufacturing OR aerospace OR raises)',
        "context": ["startup", "funding", "valuation", "raises", "raised", "manufacturing",
                    "aerospace", "space", "hardware", "engineering", "Series",
                    "資金調達", "評価額"],
        "exclude": ["nominal rate", "nominal GDP", "nominal wage", "nominal yield",
                    "nominal value", "nominal growth", "Fed raises"],
    },
    "Corma": {
        "query": '"Corma" (startup OR funding OR valuation OR cybersecurity OR raises)',
        "context": ["startup", "funding", "valuation", "raises", "raised", "security",
                    "cyber", "Series", "資金調達", "評価額"],
    },
    "Whop": {
        "query": '"Whop" (startup OR funding OR valuation OR raises OR creators)',
        "context": ["startup", "funding", "valuation", "raises", "raised", "creator",
                    "marketplace", "Series", "資金調達", "評価額"],
    },
    "Wabi": {
        "query": '"Wabi" (startup OR funding OR valuation OR AI OR raises)',
        "context": ["startup", "funding", "valuation", "raises", "raised", "AI",
                    "app", "Series", "資金調達", "評価額"],
    },
    "Vibe": {
        "query": '"Vibe" (Walmart OR advertising OR "ad tech" OR adtech OR "connected TV" OR adtech)',
        "match": ["vibe.co", "Vibe"],
        "context": ["advertis", "ad tech", "adtech", "CTV", "connected TV", "retail media",
                    "streaming", "acquisition", "acquires", "買収"],
        "exclude": ["vibe coding", "vibe-coding", "vibe coder", "good vibes", "summer vibe",
                    "winter vibe", "cozy vibe", "minimalist"],
    },
    "Blacksmith": {
        "query": '"Blacksmith" (startup OR funding OR valuation OR "dev tools" OR raises)',
        "context": ["startup", "funding", "valuation", "raises", "raised", "developer",
                    "dev tool", "code", "Series", "資金調達", "評価額"],
    },
    "Instinct": {
        "query": '"Instinct" (startup OR funding OR valuation OR "AI agent" OR raises)',
        "context": ["startup", "funding", "valuation", "raises", "raised", "AI",
                    "agent", "Series", "資金調達", "評価額"],
    },
    "Temporal": {
        "query": '"Temporal" (startup OR funding OR valuation OR workflow OR raises)',
        "context": ["startup", "funding", "valuation", "raises", "raised", "workflow",
                    "developer", "Series", "資金調達", "評価額"],
    },
    "Quince": {
        "query": '"Quince" (startup OR funding OR valuation OR apparel OR retailer OR raises)',
        "context": ["startup", "funding", "valuation", "raises", "raised", "apparel",
                    "retail", "fashion", "e-commerce", "Series", "資金調達", "評価額"],
        "exclude": ["Quince Street", "Quinceanera", "pulmonary", "drug endpoints"],
    },
    "Emergent": {
        "query": '"Emergent" (startup OR funding OR valuation OR AI OR raises)',
        "context": ["startup", "funding", "valuation", "raises", "raised", "AI",
                    "app", "Series", "資金調達", "評価額"],
        "exclude": ["Emergent Films", "video production", "film production"],
    },
    "Arda": {
        "query": '"Arda" (manufacturing OR "Arda Industries" OR robotics OR automation OR startup)',
        "context": ["manufacturing", "factory", "factories", "robot", "automation",
                    "Arda Industries", "startup", "raises", "raised", "Series", "stealth",
                    "資金調達", "評価額"],
        "exclude": ["Arda Güler", "Arda Guler", "Real Madrid", "football", "soccer",
                    "Tolkien", "Middle-earth"],
    },
    "Cathedral": {
        "query": '"Cathedral" (startup OR funding OR valuation OR cybersecurity OR military OR raises)',
        "context": ["startup", "valuation", "cyber", "military", "defense",
                    "stealth", "Series", "資金調達", "評価額"],
        "exclude": ["diocese", "church", "archbishop", "cathedral city", "worship",
                    "Cathedral Arts", "St Paul", "St. Paul", "Big Sleep",
                    "Saint Sophia", "Unesco", "UNESCO", "Wells Cathedral", "organ"],
    },
    "Hadrian": {
        "query": '"Hadrian" (startup OR funding OR valuation OR factory OR defense OR raises)',
        "context": ["startup", "funding", "valuation", "raises", "raised", "foundry",
                    "factory", "defense", "manufactur", "submarine", "Series",
                    "資金調達", "評価額"],
    },
    "Neros": {
        "query": '"Neros" (startup OR funding OR valuation OR defense OR drone OR raises)',
        "context": STARTUP_CTX,
    },
    "Crusoe": {
        "query": '"Crusoe" (startup OR funding OR valuation OR "data center" OR AI OR raises)',
        "context": STARTUP_CTX,
    },
    "Zipline": {
        "query": '"Zipline" (startup OR funding OR valuation OR drone OR delivery OR raises)',
        "context": STARTUP_CTX,
    },
    "Science Corporation": {
        "query": '"Science Corporation" (startup OR funding OR valuation OR implant OR raises)',
        "context": STARTUP_CTX,
    },
    "Parallel Web Systems": {"context": STARTUP_CTX},
    "General Intuition": {"context": STARTUP_CTX},
    "Stark Future": {"context": STARTUP_CTX},
    "Reliable Robotics": {"context": STARTUP_CTX},
    "Agile Robots": {"context": STARTUP_CTX},
    "Quantum Systems": {
        "query": '"Quantum Systems" (drone OR defense OR defence OR aerospace OR raises)',
        "context": STARTUP_CTX,
        "exclude": ["quantum computing", "quantum physics", "qubit", "NSF", "quantum computer"],
    },
    "Eka Robotics": {"context": STARTUP_CTX},
    "Blank Street Coffee": {"context": STARTUP_CTX},
    "Micro1": {"context": STARTUP_CTX},
    "Castelion": {"context": STARTUP_CTX},
    "Warhound": {"context": STARTUP_CTX},
    "Legora": {"context": STARTUP_CTX},
    "Chai Discovery": {"context": STARTUP_CTX},
    "Neko Health": {"context": STARTUP_CTX},
    "Tenstorrent": {"context": STARTUP_CTX},
    "Tractian": {"context": STARTUP_CTX},
    "Helsing": {"context": STARTUP_CTX},
    "Kalshi": {"context": STARTUP_CTX},
    "SHEIN": {
        "query": '"SHEIN" (funding OR valuation OR IPO OR revenue OR regulator OR tariff OR lawsuit OR listing)',
        "context": ["funding", "valuation", "IPO", "investors", "revenue", "profit", "sales",
                    "tariff", "regulator", "lawsuit", "ban", "listing", "stock", "fast fashion",
                    "group", "集團", "估值", "融资", "上市", "規制", "上場", "売上"],
        "exclude": ["coupon", "promo code", "discount code", "outfit", "haul", "dupe"],
    },
    # ---- 表記ゆれ・別名
    "Fireworks AI": {
        "query": '"Fireworks AI" OR "Fireworks" AI',
        "match": ["Fireworks AI", "Fireworks"],
        "context": STARTUP_CTX,
        "exclude": ["fireworks display", "fireworks show", "fireworks night", "ipo fireworks",
                    "new year fireworks", "diwali", "花火"],
    },
    "ElevenLabs": {"match": ["ElevenLabs", "Eleven Labs"], "context": STARTUP_CTX},
    "Medici Brands": {"match": ["Medici Brands", "David Protein"], "context": STARTUP_CTX},
    "Skyroot": {"match": ["Skyroot", "スカイルート"], "context": STARTUP_CTX},
    "SpaceX": {"match": ["SpaceX", "Space X", "スペースX", "スペースエックス"]},
    "Norm Ai": {"match": ["Norm Ai", "Norm AI"]},
}

# 表記ゆれのまとめ（小さいほうの名前 → まとめ先）
ALIASES = {
    "Fireworks": "Fireworks AI",
    "Elevenlabs": "ElevenLabs",
}

# 除外する名前（仮称・固有名なし）
PLACEHOLDER_RE = re.compile(r"^[（(].*[)）]$|のAIラボ|未定|仮称|不明")
# 評価額ではなく「買収額」しか載っていない行（スタートアップの評価額ではない）
ACQUISITION_ONLY_RE = re.compile(r"^\s*買収額")


# ------------------------------------------------------------------ xlsx 読み込み
def read_rows_openpyxl(path: str, sheet: str) -> list:
    import openpyxl  # 遅延 import（無い環境では stdlib にフォールバック）

    wb = openpyxl.load_workbook(path, data_only=True)
    if sheet not in wb.sheetnames:
        raise SystemExit(f"シートが見つかりません: {sheet}")
    ws = wb[sheet]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    header = [str(c).strip() if c is not None else "" for c in rows[0]]
    return [dict(zip(header, r)) for r in rows[1:]]


def read_rows_stdlib(path: str, sheet: str) -> list:
    """openpyxl が無いとき用（speaker_boards.py と同じ zipfile + xml の読み方）。"""
    import speaker_boards as sb

    rows = sb.read_sheet(path, sheet)
    if not rows:
        return []
    header = rows[0]
    return [dict(zip(header, r)) for r in rows[1:]]


def load_rows(path: str, sheet: str) -> list:
    try:
        return read_rows_openpyxl(path, sheet)
    except ImportError:
        return read_rows_stdlib(path, sheet)


# ------------------------------------------------------------------ 名前の道具
LEGAL_RE = re.compile(r"^(株式会社|有限会社|合同会社)\s*")
PAREN_TAIL_RE = re.compile(r"\s*[（(]([^（()）]*)[)）]\s*$")


def split_alias(name: str) -> tuple:
    """「Skyroot（スカイルート）」→ ("Skyroot", "スカイルート")"""
    s = (name or "").strip()
    m = PAREN_TAIL_RE.search(s)
    if not m:
        return s, ""
    return s[: m.start()].strip(), m.group(1).strip()


def core_name(name: str) -> str:
    s = PAREN_TAIL_RE.sub("", (name or "").strip())
    s = LEGAL_RE.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def name_key(name: str) -> str:
    s = unicodedata.normalize("NFKC", core_name(name)).lower()
    return re.sub(r"[\s・.＿_\-&／/（）()「」'\"、,]", "", s)


def fmt_oku(oku: float) -> str:
    """1500 → 1,500億円 / 2760000 → 276兆円"""
    try:
        v = float(oku)
    except (TypeError, ValueError):
        return ""
    if v >= 10000:
        s = f"{v / 10000:.2f}".rstrip("0").rstrip(".")
        return f"{s}兆円"
    return f"{v:,.0f}億円"


def to_number(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = re.sub(r"[^0-9.]", "", str(value))
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0


def norm_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


def match_any(text: str, terms: list) -> bool:
    """NFKC + 小文字化。英字だけの語は単語境界で判定する（update.py と同じ規則）。"""
    t = norm_text(text)
    for term in terms or []:
        n = norm_text(term).strip()
        if not n:
            continue
        if re.fullmatch(r"[\x00-\x7f]+", n):
            if re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", t):
                return True
        elif n in t:
            return True
    return False


# ------------------------------------------------------------------ 抽出
def extract_companies(rows: list) -> tuple:
    """Excel の行から (企業リスト, 除外リスト) を作る。"""
    groups: dict = {}
    excluded = []

    for r in rows:
        name = str(r.get(COL_NAME) or "").strip()
        if not name:
            continue
        oku = to_number(r.get(COL_OKU))
        if oku < MIN_OKU:
            continue

        valuation = str(r.get(COL_VALUATION) or "").strip()
        round_ = str(r.get(COL_ROUND) or "").strip()
        sector = str(r.get(COL_SECTOR) or "").strip()

        if PLACEHOLDER_RE.search(name):
            excluded.append({"name": name, "oku": oku, "reason": "仮称・社名未定"})
            continue
        if ACQUISITION_ONLY_RE.search(valuation):
            excluded.append({"name": name, "oku": oku,
                             "reason": "買収額のみ（スタートアップの評価額ではない）"})
            continue

        root = name_key(name)
        if root in {name_key(k) for k in ALIASES}:   # ALIASES は小さいほう→大きいほう
            mapped = [v for k, v in ALIASES.items() if name_key(k) == root]
            if mapped:
                root = name_key(mapped[0])

        g = groups.setdefault(root, {
            "key": root, "names": [], "oku": 0.0, "rounds": [], "sectors": [],
            "valuations": [], "urls": [], "first": len(groups),
        })
        if name not in g["names"]:
            g["names"].append(name)
        g["oku"] = max(g["oku"], oku)
        for field, value in (("rounds", round_), ("sectors", sector),
                             ("valuations", valuation),
                             ("urls", str(r.get(COL_URL) or "").strip())):
            if value and value not in g[field]:
                g[field].append(value)

    companies = []
    for g in groups.values():
        # 表示名は「評価額が最大の行の表記」。まとめた別名はかっこに残す
        canonical = g["names"][0]
        canonical_core = core_name(canonical)
        # 「Fireworks AI」にまとめた「Fireworks」のように、名前の一部でしかない別名は並べない
        alias_extra = [
            n for n in g["names"][1:]
            if core_name(n).lower() not in canonical_core.lower()
            and canonical_core.lower() not in core_name(n).lower()
        ]
        display = canonical
        if alias_extra:
            extra = "、".join(dict.fromkeys(split_alias(n)[0] for n in alias_extra))
            if split_alias(canonical)[1]:
                display = f"{canonical}／{extra}"
            else:
                display = f"{canonical}（{extra}）"
        companies.append({
            "company": core_name(canonical),
            "display": display,
            "oku": g["oku"],
            "rounds": g["rounds"],
            "sectors": g["sectors"],
            "valuations": g["valuations"],
            "urls": g["urls"],
            "names": g["names"],
            "first": g["first"],
        })

    companies.sort(key=lambda c: (-c["oku"], c["first"]))
    return companies, excluded


# ------------------------------------------------------------------ ソース生成
def feed_urls(query: str, langs: list) -> list:
    urls = []
    for code in langs:
        params = ("en-US", "US", "US:en") if code == "en" else ("ja", "JP", "JP:ja")
        urls.append(
            "https://news.google.com/rss/search?q=" + urllib.parse.quote(query)
            + f"&hl={params[0]}&gl={params[1]}&ceid={params[2]}"
        )
    return urls


def build_source(c: dict, index: int) -> dict:
    core = c["company"]
    override = OVERRIDES.get(core) or {}

    match = override.get("match")
    if not match:
        match = [core]
        alias = split_alias(c["names"][0])[1]
        if alias and alias.lower() not in (core or "").lower():
            match.append(alias)

    query = override.get("query") or f'"{core}"'
    langs = override.get("feeds") or ["en", "ja"]

    src = {
        "id": f"{ID_PREFIX}_{index:02d}",
        "type": "google_news",
        "company": core,
        "name": f"{c['display']}｜{fmt_oku(c['oku'])}",
        "query": query,
        "must_match": match,
        "limit": 5,
        "enabled": True,
        "feeds": feed_urls(query, langs),
    }
    if override.get("context"):
        src["context_match"] = override["context"]
    if override.get("exclude"):
        src["exclude_match"] = override["exclude"]
    return src


def settings_doc() -> dict:
    return {
        "title": BOARD_TITLE,
        "nav_label": BOARD_NAV_LABEL,
        "items_per_source": 5,
        "timeout_seconds": 15,
        "retries": 2,
        "fetch_workers": 6,
        "tolerate_errors": True,
        "translate": True,
        "translate_max_per_run": 20,
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
    }


# ------------------------------------------------------------------ 確認用
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def fetch_titles(url: str, timeout: int = 20) -> list:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        raw = res.read()
    root = ET.fromstring(
        re.sub(rb"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", b"&amp;", raw)
    )
    out = []
    for it in root.findall(".//item"):
        title = it.findtext("title") or ""
        site = it.findtext("source") or ""
        if site:
            title = re.sub(r"\s*[-|–—]\s*" + re.escape(site) + r"\s*$", "", title).strip()
        out.append(title)
    return out


def check_source(src: dict, show: int = 4) -> dict:
    total, kept, errors = 0, [], []
    for url in src.get("feeds") or [src.get("feed")]:
        if not url:
            continue
        for attempt in range(2):
            try:
                titles = fetch_titles(url)
                total += len(titles)
                for t in titles:
                    if match_any(t, src["must_match"]) and (
                        not src.get("context_match") or match_any(t, src["context_match"])
                    ) and not match_any(t, src.get("exclude_match") or []):
                        kept.append(t)
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 1:
                    errors.append(str(exc)[:60])
                else:
                    time.sleep(1.0)
    # 同じ見出しを1本に
    dedup = list(dict.fromkeys(kept))
    return {"total": total, "kept": len(dedup), "sample": dedup[:show], "errors": errors}


# ------------------------------------------------------------------ 出力
def load_previous(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        return {s.get("company") or s.get("name"): s for s in doc.get("sources", [])}
    except Exception:
        return {}


def write_document(path: str, sources: list) -> None:
    doc = {"settings": settings_doc(), "sources": sources}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def run(argv=None) -> int:
    ap = argparse.ArgumentParser(description="10億ドル以上スタートアップの監視設定を作る")
    ap.add_argument("--xlsx", default=DEFAULT_XLSX, help="評価額つき案件を含む Excel")
    ap.add_argument("--out", default=DEFAULT_OUT, help="出力する sources-*.json")
    ap.add_argument("--check", action="store_true", help="各社のクエリを実際に引いて件数と見出しを表示")
    ap.add_argument("--workers", type=int, default=6, help="--check の並列数")
    ap.add_argument("--list", action="store_true", help="抽出した社名と評価額だけ表示")
    ap.add_argument("--only", help="--check をこの社名だけに絞る（カンマ区切りの部分一致）")
    ap.add_argument("--json", action="store_true", help="結果を JSON 1行で出力（自動連携用）")
    ap.add_argument("--no-write", action="store_true", help="sources-*.json を書かない")
    args = ap.parse_args(argv)

    if not os.path.exists(args.xlsx):
        msg = f"Excel がありません: {args.xlsx}"
        print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False) if args.json else msg,
              file=sys.stderr)
        return 2

    rows = load_rows(args.xlsx, SHEET_NAME)
    companies, excluded = extract_companies(rows)
    sources = [build_source(c, i + 1) for i, c in enumerate(companies)]

    previous = load_previous(args.out)
    new_companies = [c["company"] for c in companies if c["company"] not in previous]
    removed = [k for k in previous if k not in {c["company"] for c in companies}]

    result = {
        "ok": True,
        "excel": os.path.abspath(args.xlsx),
        "out": os.path.abspath(args.out),
        "rows": len(rows),
        "companies": len(companies),
        "new_companies": new_companies,
        "removed_companies": removed,
        "excluded": excluded,
        "written": False,
    }

    if not args.no_write:
        write_document(args.out, sources)
        result["written"] = True

    if args.check:
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            results = list(pool.map(check_source, sources))
        pairs = list(zip(sources, results))
        if args.only:
            wanted = [w.strip().lower() for w in args.only.split(",") if w.strip()]
            pairs = [(s, r) for s, r in pairs
                     if any(w in s["name"].lower() for w in wanted)]
        lines = []
        for src, res in pairs:
            err = f"（エラー: {res['errors'][0]}）" if res["errors"] else ""
            lines.append(f"{res['kept']:>3}/{res['total']:>4}  {src['name']}")
            for s in res["sample"]:
                lines.append(f"        - {s[:96]}")
            if err:
                lines.append(f"        {err}")
        empty = [s["name"] for s, r in pairs if r["kept"] == 0]
        tail = [f"（確認 {time.time() - t0:.1f}秒）"]
        if empty:
            tail.append(f"記事0件 {len(empty)}社: " + "、".join(empty))
        if args.json:
            result["check"] = [
                {"name": s["name"], "total": r["total"], "kept": r["kept"], "sample": r["sample"]}
                for s, r in zip(sources, results)
            ]
            result["check_lines"] = lines + tail
        else:
            print("\n".join(lines + [""] + tail))

    if args.list and not args.json:
        print(f"{'評価額':>10}  {'分野':<16} 社名")
        for c in companies:
            print(f"{fmt_oku(c['oku']):>10}  {c['sectors'][0][:16]:<16} {c['display']}")
        print(f"\n{len(companies)}社 / 除外 {len(excluded)}件")
        for e in excluded:
            print(f"  除外: {e['name']}（{fmt_oku(e['oku'])}）— {e['reason']}")

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"{len(companies)}社を書き出しました: {os.path.basename(args.out)}")
        if new_companies:
            print("新規: " + "、".join(new_companies))
        if removed:
            print("対象外になった: " + "、".join(removed))
        if excluded:
            print(f"除外 {len(excluded)}件: " + "、".join(e["name"] for e in excluded))
    return 0


if __name__ == "__main__":
    sys.exit(run())
