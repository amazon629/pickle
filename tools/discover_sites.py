#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""検索エンジン（Yahoo! JAPAN / Bing）で社名から公式サイト候補 URL を引く。

discover_supplements.py から `search_results(name + " 公式サイト")` の形で使う。
標準ライブラリのみ。結果は「上位の実 URL」をそのまま返す（順位順・重複除去）。
"""
from __future__ import annotations

import base64
import html as html_lib
import re
import time
import urllib.parse

import webnews as W

UA = W.UA


def _yahoo(query: str, timeout: int = 12) -> list:
    u = "https://search.yahoo.co.jp/search?p=" + urllib.parse.quote(query)
    st, final, h = W.http_get(u, timeout=timeout, retries=1)
    if st != 200:
        return []
    out, seen = [], set()
    for m in re.finditer(r'<a\b[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', h, re.S | re.I):
        url = html_lib.unescape(m.group(1))
        host = urllib.parse.urlparse(url).netloc
        if not host or any(b in host for b in W.BLOCK_HOSTS):
            continue
        if host.endswith("yahoo.co.jp") or "yahoo.co.jp" in host:
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= 12:
            break
    return out


def _bing(query: str, timeout: int = 12) -> list:
    u = "https://www.bing.com/search?q=" + urllib.parse.quote(query)
    st, final, h = W.http_get(u, timeout=timeout, retries=1)
    if st != 200:
        return []
    out, seen = [], set()
    for m in re.finditer(r'<h2><a\b[^>]*href="([^"]+)"', h, re.I):
        url = html_lib.unescape(m.group(1))
        if url.startswith("/ck/a"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            raw = (q.get("u") or [""])[0]
            if raw.startswith("a1"):
                try:
                    url = base64.urlsafe_b64decode(raw[2:] + "==").decode("utf-8", "ignore")
                except Exception:  # noqa: BLE001
                    continue
        host = urllib.parse.urlparse(url).netloc
        if not host or any(b in host for b in W.BLOCK_HOSTS) or "bing.com" in host:
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= 12:
            break
    return out


def search_results(query: str) -> list:
    for fn in (_yahoo, _bing):
        try:
            res = fn(query)
        except Exception:  # noqa: BLE001
            res = []
        if res:
            return res
        time.sleep(0.5)
    return []


if __name__ == "__main__":
    import sys
    for q in sys.argv[1:] or ["エバーライフ 公式サイト"]:
        print(q)
        for u in search_results(q):
            print("  ", u)
