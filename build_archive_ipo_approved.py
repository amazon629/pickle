#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""archive-ipo-approved.json を作る（東京IPO @tokyoipo_sns の「新規承認」過去投稿）。

nitter の RSS は最新18〜20件しか返さないので、HTML タイムラインの
「もっと見る」カーソル（?cursor=…）を辿って RSS をページ送りし、
2026年の「新規承認」投稿を可能な限り遡って集める。

    python3 build_archive_ipo_approved.py            # 取得して archive を更新
    python3 build_archive_ipo_approved.py --probe     # どこまで遡れるかだけ表示
    python3 build_archive_ipo_approved.py --max-pages 40

既存の archive は消さずにマージする（同じ投稿は link で1件に）。
update.py 側は sources-ipo-approved.json の "archive" を読んで毎時の取得結果と合流する。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.request
from datetime import datetime, timezone

import update

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ARCHIVE_PATH = os.path.join(BASE_DIR, "archive-ipo-approved.json")
HANDLE = "tokyoipo_sns"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
NITTER_HOSTS = ["https://nitter.cf", "https://nitter.privacyredirect.com", "https://xcancel.com"]
KEYWORDS = ["新規承認"]
MIN_YEAR = 2026

CURSOR_RE = re.compile(r"cursor=([^\"&\s]+)")


def http_text(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def http_bytes(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def next_cursor(host: str, cursor: str = "") -> str:
    url = f"{host}/{HANDLE}" + (f"?cursor={cursor}" if cursor else "")
    html = http_text(url)
    m = CURSOR_RE.search(html)
    return m.group(1) if m else ""


def rss_entries(host: str, cursor: str = ""):
    url = f"{host}/{HANDLE}/rss" + (f"?cursor={cursor}" if cursor else "")
    raw = http_bytes(url)
    return update.parse_feed(raw)


def entry_text(e: dict) -> str:
    title = update.strip_html(e.get("title", "") or "")
    body = update.strip_html(e.get("summary", "") or "")
    return update.clean_x_text(body or title)


def entry_time(e: dict):
    return e.get("published")


def collect(host: str, max_pages: int = 60, sleep: float = 1.2, probe: bool = False):
    """カーソルを辿って全ページの entries を集める。"""
    cursor = ""
    seen_links = set()
    pages = []
    for page_no in range(1, max_pages + 1):
        try:
            entries = rss_entries(host, cursor)
        except Exception as exc:  # noqa: BLE001
            print(f"  page {page_no}: RSS 取得失敗 {exc}")
            break
        if not entries:
            print(f"  page {page_no}: 空")
            break
        pub = [entry_time(e) for e in entries if entry_time(e)]
        oldest = min(pub) if pub else None
        newest = max(pub) if pub else None
        new = 0
        for e in entries:
            link = update.rewrite_x_link((e.get("link") or "").strip())
            if link and link not in seen_links:
                seen_links.add(link)
                new += 1
        pages.append({"page": page_no, "count": len(entries), "new": new, "oldest": oldest, "newest": newest})
        print(f"  page {page_no}: {len(entries)}件 新規{new}  {newest} 〜 {oldest}")
        if new == 0:
            print("  同じ内容が続くので終了")
            break
        if probe:
            try:
                cursor = next_cursor(host, cursor)
            except Exception as exc:  # noqa: BLE001
                print(f"  カーソル取得失敗 {exc}")
                break
        else:
            try:
                cursor = next_cursor(host, cursor)
            except Exception as exc:  # noqa: BLE001
                print(f"  カーソル取得失敗 {exc}")
                break
        if not cursor:
            print("  次のカーソルなし（ここが遡れる限界）")
            break
        time.sleep(sleep)
    return pages


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="遡れる範囲を表示するだけ")
    ap.add_argument("--rebuild", action="store_true", help="既存 archive を引き継がず作り直す")
    ap.add_argument("--max-pages", type=int, default=60)
    ap.add_argument("--host", default=NITTER_HOSTS[0])
    args = ap.parse_args(argv)

    print(f"取得元: {args.host}")
    pages = collect(args.host, max_pages=args.max_pages, probe=args.probe)
    if args.probe:
        return 0

    # 本取得: archive をマージ
    try:
        if args.rebuild:
            raise FileNotFoundError
        with open(ARCHIVE_PATH, encoding="utf-8") as fh:
            archive = json.load(fh)
    except FileNotFoundError:
        archive = {"settings": {"handle": HANDLE, "min_year": MIN_YEAR, "keywords": KEYWORDS}, "items": []}

    # update.py と同じ判定（年 >= min_year / 本文の冒頭が「新規承認」）
    src = {
        "id": "x_tokyoipo_approved",
        "type": "x",
        "name": "東京IPO（新規承認）",
        "handle": HANDLE,
        "must_match": KEYWORDS,
        "must_start": KEYWORDS,
        "limit": 500,
    }
    by_link = {} if args.rebuild else {it["link"]: it for it in archive.get("items", []) if it.get("link")}
    if args.rebuild:
        archive["items"] = []

    cursor = ""
    added = 0
    scanned = 0
    for page_no in range(1, args.max_pages + 1):
        try:
            entries = rss_entries(args.host, cursor)
        except Exception as exc:  # noqa: BLE001
            print(f"  page {page_no}: RSS 取得失敗 {exc}")
            break
        if not entries:
            break
        items = update.build_items(src, entries)
        scanned += len(items)
        fresh = 0
        for it in items:
            pub = it.get("published") or ""
            if len(pub) < 4 or not pub[:4].isdigit() or int(pub[:4]) < MIN_YEAR:
                continue
            if not update.match_any_term(it.get("title", ""), KEYWORDS):
                continue
            if not update.starts_with_term(it.get("title", ""), KEYWORDS):
                continue
            link = it["link"]
            if link in by_link:
                continue
            by_link[link] = it
            added += 1
            fresh += 1
        pub_list = [it.get("published") for it in items if it.get("published")]
        oldest = min(pub_list) if pub_list else None
        print(f"  page {page_no}: 取得{len(items)}件 / 新規承認+{fresh}  (最古 {oldest})")
        # 2026年より前まで来たら、そこから先は不要
        if oldest and int(oldest[:4]) < MIN_YEAR:
            print("  2025年以前に到達したので終了")
            break
        try:
            cursor = next_cursor(args.host, cursor)
        except Exception as exc:  # noqa: BLE001
            print(f"  カーソル取得失敗 {exc}")
            break
        if not cursor:
            print("  次のカーソルなし（ここが遡れる限界）")
            break
        time.sleep(1.2)

    items = sorted(by_link.values(), key=lambda it: it.get("published") or "", reverse=True)
    archive["items"] = items
    archive.setdefault("settings", {})
    archive["settings"].update(
        {
            "handle": HANDLE,
            "min_year": MIN_YEAR,
            "keywords": KEYWORDS,
            "item_count": len(items),
            "oldest": items[-1]["published"] if items else None,
            "newest": items[0]["published"] if items else None,
            "built_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        }
    )
    with open(ARCHIVE_PATH, "w", encoding="utf-8") as fh:
        json.dump(archive, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"archive: {len(items)}件（今回の新規 +{added} / 走査 {scanned}件） -> {ARCHIVE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
