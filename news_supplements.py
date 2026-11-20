#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
news-supplements.json を sources-*.json の各社カードへ足す。

Googleニュース検索だけでは2026年の記事が取れない会社について、
  extra_feeds … PR TIMES 企業RSS（companyrdf.php?company_id=…）
  site_feeds  … 公式サイトの「お知らせ／ニュース」RSS
  html_news   … 公式サイトの「お知らせ／ニュース」ページ
を1カードの中で合算して表示するための補完定義。

update.py 側は 1ソース内の複数取得元をまとめて、重複除去・日付降順で limit 件にする。
ここは「どの会社にどの取得元を足すか」だけを持つ（同期スクリプトから毎回同じ結果になる）。
"""
from __future__ import annotations

import json
import os
import re
import unicodedata

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(BASE_DIR, "news-supplements.json")
_CACHE = {}


def load(path: str = DEFAULT_PATH) -> dict:
    if path not in _CACHE:
        try:
            with open(path, encoding="utf-8") as fh:
                _CACHE[path] = json.load(fh)
        except FileNotFoundError:
            _CACHE[path] = {}
    return _CACHE[path]


def _name_key(name: str) -> str:
    """社名の照合キー（全角半角・大小・空白・中黒の揺れを吸収）。

    Excel を作り直すと「SHOPLINE Japan株式会社」が「SHOPLINEJapan株式会社」になるなど
    表記が変わるため、完全一致でなくキー一致でも補完定義を引き当てる。
    """
    s = unicodedata.normalize("NFKC", name or "").lower()
    s = re.sub(r"^(株式会社|有限会社|合同会社|一般社団法人|一般財団法人)\s*", "", s)
    s = re.sub(r"\s*(株式会社|有限会社|合同会社)$", "", s)
    return re.sub(r"[\s・.＿_\-&／/（）()「」'\"、,]", "", s)


def apply_supplements(board: str, sources: list, path: str = DEFAULT_PATH) -> list:
    """board 名（ec-mailorder 等）の補完定義を sources に反映して返す。

    引くのは **社名優先**（`id` は Excel の並び順で振るので、行が増減すると別会社に当たる）。
    社名は空白・法人格の有無を無視したキーでも照合する。
    """
    data = (load(path).get(board) or {})
    if not data:
        return sources
    by_key = {_name_key(k): v for k, v in data.items()}
    for src in sources:
        sup = (data.get(src.get("name")) or data.get(src.get("id"))
               or by_key.get(_name_key(src.get("name") or "")) or {})
        feeds = sup.get("extra_feeds") or []
        site = sup.get("site_feeds") or []
        html = sup.get("html_news") or []
        if feeds:
            src["extra_feeds"] = feeds
        if site:
            src["site_feeds"] = site
        if html:
            src["html_news"] = html
    return sources
