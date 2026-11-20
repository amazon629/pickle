#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IPO新規承認銘柄ニュースボード — archive から sources-ipo-news.json を作る。

東京IPO（@tokyoipo_sns）の「新規承認」投稿（archive-ipo-approved.json）から社名を抜き出し、
1社＝1ソース（Googleニュース検索）として sources-ipo-news.json を生成する。
update.py はこのファイルを自動で拾い、ipo-news.html（タイトル settings.title）と
全ページ共通ナビのピルを作る。

    python3 sync_ipo_approved_news.py              # archive を読んで sources-ipo-news.json を更新
    python3 sync_ipo_approved_news.py --check      # 各社のクエリを実際に引いて件数と見出しを確認
    python3 sync_ipo_approved_news.py --check --explain GO   # 絞り込みで落ちた見出しも表示（ノイズ調整用）
    python3 sync_ipo_approved_news.py --dry-run    # 書き出さずに差分だけ表示

新しい新規承認が出たときの流れ（AIを起動しない）:
  update.py が ipo-approved ボードの archive に新しい「新規承認」投稿を追記
    → 次の update.py の実行時にこのスクリプトが呼ばれ、archive に居るのに未登録の社名だけを追記
    → その回から ipo-news.html に新しい銘柄のカードが並ぶ

ノイズ設計（2026-11-20 に18社で検証したルール）:
  * 社名をそのまま Google ニュースで引き、見出しに社名を含む記事だけ残す（must_match）。
    NFKC＋小文字化で全角/半角の揺れ（ＬＴＶ－Ｘ / ｅｓｔｉｅ）を吸収する。
  * 一般語・短語・他分野と衝突する社名（GO / NOTA / ネイス / エブリー / ティアフォー …）は
    ``PROFILES`` でクエリを言い換え、業種語（context_match）や除外語（exclude_match）を足す。
  * 18社に無い新しい社名は ``auto_profile()`` が自動でクエリを作る。短い名前・英字だけの名前は
    業種語で絞り、``auto_note`` を付けて「要調整」と分かるようにする。
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
ARCHIVE_PATH = os.path.join(BASE_DIR, "archive-ipo-approved.json")
OUT_PATH = os.path.join(BASE_DIR, "sources-ipo-news.json")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

SETTINGS = {
    "title": "IPO新規承認銘柄 ニュース監視",
    "nav_label": "📰 IPO新規ニュース",
    "items_per_source": 5,
    "timeout_seconds": 20,
    "retries": 2,
    "fetch_workers": 6,
    "tolerate_errors": True,
    "translate": True,
    "translate_max_per_run": 20,
    "user_agent": UA,
}

# どの社にも共通で落とす「株価ページ・掲示板」の定型見出し。
# これらは毎日同じタイトルで並び、5枠を埋めて本来のニュースを隠すため除外する。
ROUTINE_NOISE = [
    "株価・株式情報",
    "株価・株主優待",
    "株式情報",
    "掲示板",
    "売買内訳",
    "決算情報",
    "リアルタイム相場",
    "値動きの背景",
    "適時開示情報",
    "株価時系列",
    "信用残時系列",
    "株つぶやき",
    "配当情報",
    "株価チャート",
    "株主優待",
    "板気配",
    "Research Memo",
    "インパクト銘柄",
]

# 自動追加した社名に付ける「業種・株式まわりの語」。見出しにこのどれかが入っていないと落とす。
BUSINESS_TERMS = [
    "株式会社", "(株)", "株価", "上場", "IPO", "新規上場", "決算", "社長", "株主",
    "事業", "資金調達", "提携", "買収", "子会社", "リリース", "サービス", "導入",
    "提供", "会見", "売上", "成長", "会社",
]

# ------------------------------------------------------------------ 18社の個別ルール（2026-11-20 検証済み）
#   query         … Google ニュースに投げる検索語
#   must_match    … 見出しに含まれていなければ落とす（表記ゆれを列挙）
#   context_match … 一般語と衝突する社名だけ。見出しにこのどれかが必要
#   exclude_match … 別の意味・別会社の語を含む見出しを落とす
PROFILES = {
    "FOLIOホールディングス": {
        "query": '"FOLIOホールディングス" OR ("FOLIO" ホールディングス)',
        "must_match": ["FOLIO"],
        "note": "FOLIO単体でもSBI関連の発表が拾える。ポートフォリオは単語境界で入らない",
    },
    "スマートニュース": {
        # SmartNews 自身が配信する一般ニュース（見出し末尾が「- SmartNews」）をクエリ段階で除外
        "query": '"スマートニュース" -"SmartNews"',
        "must_match": ["スマートニュース", "SmartNews"],
        "note": "ニュースアプリ由来の一般記事ノイズは -\"SmartNews\" で除外",
    },
    "NOTA": {
        "display": "NOTA（NOT A HOTEL）",
        "query": '"NOTA" 株式会社 OR "NOT A HOTEL"',
        "must_match": ["NOTA", "NOT A HOTEL"],
        "note": "社名変更前のNOT A HOTEL名義の記事も拾う",
    },
    "TOKIUM": {
        "query": '"TOKIUM"',
        "must_match": ["TOKIUM"],
    },
    "RECEPTIONIST": {
        "query": '"RECEPTIONIST"',
        "must_match": ["RECEPTIONIST"],
        "context_match": [
            "株式会社", "受付", "上場", "IPO", "新規上場", "決算", "株価", "株主",
            "導入", "リリース", "システム", "SaaS", "AI", "業務", "会見",
        ],
        "note": "英単語のreceptionist（求人・海外記事）を業種語で落とす",
    },
    "estie": {
        "query": '"estie"',
        "must_match": ["estie"],
        "note": "全角ｅｓｔｉｅはNFKCで一致。bestie等は単語境界で入らない",
    },
    "LTV-X": {
        "query": '"LTV-X" OR "ＬＴＶ－Ｘ"',
        "must_match": ["LTV-X", "LTV‐X", "LTV−X", "LTV—X"],
        "note": "ハイフン違いを列挙。LTV単体はマーケ用語と衝突するので使わない",
    },
    "Allganize Holdings": {
        "query": '"Allganize" OR "オルガナイズ"',
        "must_match": ["Allganize", "オルガナイズ"],
    },
    "MabGenesis": {
        "query": '"MabGenesis"',
        "must_match": ["MabGenesis"],
    },
    "トグルホールディングス": {
        "query": '"トグルホールディングス" OR "トグルHD" OR "トグルホームズ"',
        "must_match": ["トグル"],
        "context_match": [
            "不動産", "賃貸", "開発", "上場", "IPO", "新規上場", "決算", "株価",
            "株主", "株式会社", "マンション", "住宅", "物件", "会見",
        ],
        "note": "「トグル（toggle）」の技術記事と分けるため不動産・株式の語を要求",
    },
    "エブリー": {
        "query": '"エブリー" 株式会社 OR "エブリー" デリッシュキッチン OR "エブリー" 株価',
        "must_match": ["エブリー"],
        "context_match": [
            "株式会社", "(株)", "上場", "IPO", "新規上場", "決算", "株価", "株主",
            "デリッシュ", "レシピ", "料理", "食品", "アプリ", "メディア", "社長",
            "事業", "会見",
        ],
        "exclude_match": ["ダイセーエブリー", "エブリイ"],
        "note": "物流のダイセーエブリー二十四・スーパーのエブリイを除外",
    },
    "ティアフォー": {
        "query": '"ティアフォー" 自動運転 OR "ティアフォー" 株式会社 OR "ティアフォー" Autoware',
        "must_match": ["ティアフォー"],
        "context_match": [
            "自動運転", "Autoware", "自動車", "車両", "モビリティ", "ソフトウェア",
            "株式会社", "上場", "IPO", "新規上場", "決算", "株価", "調達", "提携",
            "実証", "ロボット", "バス", "配送", "運行", "会見",
        ],
        "exclude_match": [
            "テニス", "ATP", "コボリ", "アルカラス", "ジョコビッチ", "ツアー",
            "準決勝", "ファイナルズ", "トーナメント",
        ],
        "note": "テニスのフランシス・ティアフォーを除外（最大のノイズ源）",
    },
    "アイ・グリッド・ソリューションズ": {
        "query": '"アイ・グリッド" OR "アイグリッド"',
        "must_match": ["アイ・グリッド", "アイグリッド"],
    },
    "ビーエイブル": {
        "query": '"ビーエイブル"',
        "must_match": ["ビーエイブル", "ビー・エイブル"],
    },
    "チャットプラス": {
        "query": '"チャットプラス"',
        "must_match": ["チャットプラス"],
    },
    "ネイス": {
        "query": '"ネイス" 株式会社 OR "ネイス" 体操 OR "ネイス" 株価',
        "must_match": ["ネイス"],
        "context_match": [
            "体操", "教室", "株式会社", "(株)", "上場", "IPO", "新規上場", "決算",
            "株価", "株主", "社長", "事業", "スイミング", "習い事", "会見",
        ],
        "exclude_match": ["シクロ", "トロフェー", "レース"],
        "note": "自転車選手のネイス（シクロワイアード等）を除外",
    },
    "LiNKX": {
        "query": '"LiNKX"',
        "must_match": ["LiNKX"],
        "note": "「リンクス」は一般語すぎるので使わない（英字表記のみ）",
    },
    "GO": {
        "display": "GO（タクシーアプリ）",
        "query": '"GO株式会社" OR "GOタクシー" OR "タクシーアプリ「GO」" -"ポケモンGO"',
        "must_match": ["GO"],
        "exclude_match": [
            "ポケモンGO", "Pokémon GO", "ポケモン GO", "Go To トラベル",
            "GO TO キャンペーン", "GO TO トラベル", "GoToトラベル",
        ],
        "note": "2文字の一般語。クエリでGO株式会社/GOタクシーに限定し、ポケモンGO・GoToを除外",
    },
}

# archive に新しい社名が出たとき、個別ルールを書くまでの暫定処理で「一般語」とみなす語
GENERIC_WORDS = {
    "GO", "NOTA", "ONE", "ALL", "NEW", "UP", "AI", "IT", "NEXT", "BASE", "LINK",
    "ネイス", "エブリー", "トグル", "ハロー", "サン", "ライフ", "ワン", "ミライ",
}


# ---------------------------------------------------------------- helpers
def nfkc(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "")


def match_any_term(text: str, terms) -> bool:
    """update.py の match_any_term と同じ判定（NFKC＋小文字・英字は単語境界）。"""
    t = nfkc(text).lower()
    for term in terms or []:
        n = nfkc(term).lower().strip()
        if not n:
            continue
        if re.fullmatch(r"[\x00-\x7f]+", n):
            if re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", t):
                return True
        elif n in t:
            return True
    return False


def clean_company(candidate: str) -> str:
    """「💹FOLIOホールディングス」のような行から社名だけ抜く。"""
    s = (candidate or "").strip()
    s = re.sub(r"^[^0-9A-Za-z\u3040-\u30ff\u4e00-\u9fff]+", "", s)
    s = re.sub(r"[^0-9A-Za-z\u3040-\u30ff\u4e00-\u9fff]+$", "", s)
    return s.strip()


def company_from_item(item: dict) -> str:
    title = item.get("title") or ""
    lines = [ln.strip() for ln in title.split("\n") if ln.strip()]
    for i, line in enumerate(lines):
        if "新規承認" in line and i + 1 < len(lines):
            name = clean_company(lines[i + 1])
            if 1 < len(name) <= 40:
                return name
    return ""


def load_archive() -> list:
    try:
        with open(ARCHIVE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return []
    return data.get("items") or []


def companies_from_archive() -> list:
    """[(社名, 発表日時)] を新しい順で返す（同じ社名は最初の1件だけ）。"""
    seen, out = set(), []
    items = sorted(load_archive(), key=lambda it: it.get("published") or "", reverse=True)
    for item in items:
        name = company_from_item(item)
        if not name:
            continue
        key = nfkc(name).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((name, item.get("published") or ""))
    return out


def profile_for(name: str) -> dict:
    key = nfkc(name).lower()
    for profile_name, profile in PROFILES.items():
        if nfkc(profile_name).lower() == key:
            return dict(profile)
    return auto_profile(name)


def auto_profile(name: str) -> dict:
    """個別ルールの無い（＝将来の）新規承認銘柄の暫定ルール。"""
    base = re.sub(r"\s*(株式会社|合同会社|\(株\)|（株）)$", "", name).strip() or name
    must = [base]
    if base != name:
        must.insert(0, name)
    generic = (
        len(base) <= 3
        or base.upper() in GENERIC_WORDS
        or (re.fullmatch(r"[A-Za-z0-9 .\-&]+", base) and len(base) <= 4)
    )
    profile = {
        "query": f'"{base}"',
        "must_match": must,
        "auto": True,
    }
    if generic:
        profile["query"] = f'"{base}" 株式会社 OR "{base}" 上場 OR "{base}" 株価'
        profile["context_match"] = list(BUSINESS_TERMS)
        profile["note"] = "自動追加（一般語・短語のため株式・事業の語で絞り込み。要調整なら PROFILES に追記）"
    else:
        profile["note"] = "自動追加（archive の新しい新規承認から生成）"
    return profile


def source_id(name: str, order: int) -> str:
    """並び順（古い順の連番）で安定したIDを作る。新しい銘柄は末尾に増えるだけ。"""
    return f"ipon_{order:02d}"


def build_sources() -> list:
    companies = companies_from_archive()  # 新しい順
    order_of = {nfkc(n).lower(): i for i, (n, _p) in enumerate(list(reversed(companies)), start=1)}
    sources = {}
    for name, _published in companies:
        profile = profile_for(name)
        src = {
            "id": source_id(name, order_of[nfkc(name).lower()]),
            "type": "google_news",
            "name": profile.get("display") or name,
            "company": name,
            "query": profile["query"],
            "must_match": profile.get("must_match") or [name],
            "limit": 5,
            "enabled": True,
        }
        for key in ("context_match", "exclude_match"):
            if profile.get(key):
                src[key] = profile[key]
        noise = list(ROUTINE_NOISE)
        for term in src.get("exclude_match") or []:
            if term not in noise:
                noise.append(term)
        src["exclude_match"] = noise
        if profile.get("note"):
            src["note"] = profile["note"]
        if profile.get("auto"):
            src["auto"] = True
        sources[nfkc(name).lower()] = src
    # 表示は新しい承認順（最新の銘柄が上）
    return [sources[nfkc(n).lower()] for n, _p in companies if nfkc(n).lower() in sources]


def load_existing() -> dict:
    try:
        with open(OUT_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def sync(verbose: bool = False) -> list:
    """sources-ipo-news.json を archive に合わせて更新する。新しく足した社名のリストを返す。"""
    sources = build_sources()
    if not sources:
        return []
    existing = load_existing()
    old_by_name = {}
    for s in existing.get("sources", []):
        for key in (s.get("company"), s.get("name")):
            if key:
                old_by_name[nfkc(key).lower()] = s
    added = []
    for src in sources:
        old = old_by_name.get(nfkc(src.get("company") or src["name"]).lower())
        if old:
            # 人が止めたソース（enabled: false）はそのまま尊重する
            if old.get("enabled") is False:
                src["enabled"] = False
            if old.get("limit") and not src.get("auto"):
                pass  # 個別ルール側の limit を優先
            if old.get("note") and not src.get("note"):
                src["note"] = old["note"]
        else:
            added.append(src["name"])
    settings = dict(SETTINGS)
    settings["item_count"] = len(sources)
    settings["auto_added"] = [s["name"] for s in sources if s.get("auto")]
    payload = {"settings": settings, "sources": sources}
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    old_serialized = ""
    if os.path.exists(OUT_PATH):
        with open(OUT_PATH, encoding="utf-8") as fh:
            old_serialized = fh.read()
    if serialized != old_serialized:
        with open(OUT_PATH, "w", encoding="utf-8") as fh:
            fh.write(serialized)
        if verbose:
            action = "更新" if old_serialized else "作成"
            print(f"sources-ipo-news.json を{action}: {len(sources)}社")
    elif verbose:
        print(f"sources-ipo-news.json 変更なし: {len(sources)}社")
    if added and verbose:
        print("新しく監視に追加: " + ", ".join(added))
    return added


# ---------------------------------------------------------------- check（ノイズ検証）
def news_url(src: dict) -> str:
    q = urllib.parse.quote(src.get("query", ""))
    return f"https://news.google.com/rss/search?q={q}&hl=ja&gl=JP&ceid=JP:ja"


def fetch_titles(src: dict, timeout: int = 25) -> list:
    req = urllib.request.Request(news_url(src), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    root = ET.fromstring(raw)
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        src_name = item.findtext("source") or ""
        # update.py の clean_news_title と同じ「 - サイト名」落とし
        for _ in range(2):
            if src_name:
                title = re.sub(r"\s*[-|–—]\s*" + re.escape(src_name) + r"\s*$", "", title).strip()
        out.append({"title": title, "raw": item.findtext("title") or "", "source": src_name,
                    "date": item.findtext("pubDate") or ""})
    return out


def apply_filters(src: dict, entries: list) -> tuple:
    kept, dropped = [], []
    for e in entries:
        title = e["title"]
        if src.get("must_match") and not match_any_term(title, src.get("must_match")):
            dropped.append(("社名なし", e))
            continue
        if src.get("context_match") and not match_any_term(title, src.get("context_match")):
            dropped.append(("業種語なし", e))
            continue
        if src.get("exclude_match") and match_any_term(title, src.get("exclude_match")):
            dropped.append(("除外語", e))
            continue
        kept.append(e)
    return kept, dropped


def check(only: str = "", explain: str = "", limit: int = 5) -> int:
    sources = build_sources()
    if only:
        wanted = {unicodedata.normalize("NFKC", w.strip()).lower() for w in only.split(",") if w.strip()}
        sources = [
            s for s in sources
            if unicodedata.normalize("NFKC", s.get("company") or s["name"]).lower() in wanted
        ]
    explain_key = unicodedata.normalize("NFKC", explain).lower() if explain else ""
    print(f"{'社名':<32}{'取得':>4}{'採用':>4}  メモ")
    total_kept = 0
    for src in sources:
        try:
            entries = fetch_titles(src)
        except Exception as exc:  # noqa: BLE001
            print(f"{src['name'][:30]:<32}{'ERR':>4}   {exc}")
            continue
        kept, dropped = apply_filters(src, entries)
        total_kept += len(kept)
        print(f"{src['name'][:30]:<32}{len(entries):>4}{len(kept):>4}  {src.get('note','')[:40]}")
        show_kept = kept[:limit]
        for e in show_kept:
            print(f"    ○ {e['title'][:88]}")
        if not kept:
            print("    （採用0件。クエリかフィルタを見直す）")
        if explain_key and unicodedata.normalize("NFKC", src.get("company") or src["name"]).lower() == explain_key:
            for reason, e in dropped[:25]:
                print(f"    × [{reason}] {e['title'][:80]}")
        time.sleep(0.4)
    print(f"\n採用合計 {total_kept}件 / {len(sources)}社")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="IPO新規承認銘柄ニュースボードのソース同期")
    ap.add_argument("--check", action="store_true", help="各社のクエリを実際に引いて件数を確認")
    ap.add_argument("--only", default="", help="--check で見る社名（カンマ区切り）")
    ap.add_argument("--explain", default="", help="この社名で落ちた見出しも表示（ノイズ調整用）")
    ap.add_argument("--dry-run", action="store_true", help="書き出さずに件数だけ表示")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    if args.check:
        return check(only=args.only, explain=args.explain)

    companies = companies_from_archive()
    if not companies:
        print("archive-ipo-approved.json に新規承認がありません（ipo-approved ボードが先に必要）", file=sys.stderr)
        return 1
    if args.dry_run:
        for i, (name, published) in enumerate(companies, start=1):
            profile = profile_for(name)
            print(f"{i:>2}. {name:<32} {published[:10]}  auto={bool(profile.get('auto'))}")
        print(f"合計 {len(companies)}社（--dry-run のため書き出しなし）")
        return 0

    added = sync(verbose=True)
    print(f"対象 {len(companies)}社" + (f" / 新規追加 {len(added)}社" if added else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
