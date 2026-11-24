#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全ボードのナビ（上部のピル）だけを書き直す。

sources-*.json を足したとき、update.py はそのボード自身と
「一緒に走ったボード」しか作り直さない。取得をやり直さずに
既存ページのナビだけ新しくしたいときに使う。

    python3 refresh_nav.py            # 全HTMLのナビを更新（差分があれば書き換え）
    python3 refresh_nav.py --dry-run  # 変更されるページを表示するだけ
"""

from __future__ import annotations

import argparse
import os
import re
import sys

import update


def build_labels(boards: list) -> dict:
    labels = {}
    static_labels = {e["name"]: e.get("label") or e["name"] for e in update.SAFE_BOARDS}
    for b in boards:
        if b.get("static"):
            labels[b["name"]] = static_labels.get(b["name"], b["name"])
            continue
        try:
            labels[b["name"]] = update.board_label(b, update.load_sources(b["config"]).get("settings", {}))
        except Exception:  # noqa: BLE001
            labels[b["name"]] = b["name"]
    return labels


NAV_RE = re.compile(r"<nav class=\"nav\">.*?</nav>", re.S)

# ナビは「1行横スクロール」に統一する（ピルが33枚を超え、折り返すと
# スマホで4行も占めてしまう）。旧テンプレートの wrap 版だけを置き換える。
NAV_CSS_OLD = ".nav { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 7px; }"
NAV_CSS_NEW = (
    ".nav { display: flex; flex-wrap: nowrap; gap: 6px; margin-top: 7px;"
    " overflow-x: auto; scrollbar-width: none; -webkit-overflow-scrolling: touch; }"
    "\n  .nav::-webkit-scrollbar { display: none; }"
)
NAV_A_OLD = (
    ".nav a {\n    font-size: 11.5px; line-height: 1.5; padding: 2px 10px; border-radius: 999px;"
)
NAV_A_NEW = (
    ".nav a {\n    flex: 0 0 auto; white-space: nowrap;"
    "\n    font-size: 11.5px; line-height: 1.5; padding: 2px 10px; border-radius: 999px;"
)


def normalize_nav_css(text: str) -> str:
    """古い wrap 型の .nav CSS を、1行横スクロール型に書き換える。"""
    text = text.replace(NAV_CSS_OLD, NAV_CSS_NEW)
    text = text.replace(NAV_A_OLD, NAV_A_NEW)
    return text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="全ボードのナビだけを更新する")
    ap.add_argument("--dry-run", action="store_true", help="書き換えずに差分のあるページを表示")
    args = ap.parse_args(argv)

    boards = update.discover_boards()
    labels = build_labels(boards)
    changed, missing = [], []
    for b in boards:
        path = b["index"]
        if not os.path.exists(path):
            missing.append(os.path.basename(path))
            continue
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        nav = update.render_nav(b["name"], boards, labels).strip()
        if not nav:
            continue
        if not NAV_RE.search(text):
            print(f"ナビが見つかりません: {os.path.basename(path)}", file=sys.stderr)
            continue
        new_text = NAV_RE.sub(lambda _m: nav, text, count=1)
        new_text = normalize_nav_css(new_text)
        if new_text == text:
            continue
        changed.append(os.path.basename(path))
        if not args.dry_run:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new_text)

    verb = "更新予定" if args.dry_run else "更新"
    print(f"{verb}: {len(changed)}ページ" + ("（" + ", ".join(changed) + "）" if changed else ""))
    if missing:
        print("HTMLが無いためスキップ: " + ", ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
