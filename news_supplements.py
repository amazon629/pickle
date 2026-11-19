#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
news-supplements.json を sources-*.json の各社カードへ足す。

Googleニュース検索だけでは2026年の記事が取れない会社について、
  extra_feeds … PR TIMES 企業RSS（companyrdf.php?company_id=…）
  html_news   … 公式サイトの「お知らせ／ニュース」ページ
を1カードの中で合算して表示するための補完定義。

update.py 側は 1ソース内の複数取得元をまとめて、重複除去・日付降順で limit 件にする。
ここは「どの会社にどの取得元を足すか」だけを持つ（同期スクリプトから毎回同じ結果になる）。
"""
from __future__ import annotations

import json
import os

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


def apply_supplements(board: str, sources: list, path: str = DEFAULT_PATH) -> list:
    """board 名（ec-mailorder 等）の補完定義を sources に反映して返す。

    引くのは **社名優先**（`id` は Excel の並び順で振るので、行が増減すると別会社に当たる）。
    """
    data = (load(path).get(board) or {})
    if not data:
        return sources
    for src in sources:
        sup = data.get(src.get("name")) or data.get(src.get("id")) or {}
        feeds = sup.get("extra_feeds") or []
        html = sup.get("html_news") or []
        if feeds:
            src["extra_feeds"] = feeds
        if html:
            src["html_news"] = html
    return sources
