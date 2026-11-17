#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sources.json に監視対象を1行で追加／削除する小さなヘルパー。

例:
    python3 add_source.py x tkzwgrs "滝沢ガレソ"
    python3 add_source.py youtube UCxxxxxxxx "チャンネル名"
    python3 add_source.py note someuser "noteのアカウント"
    python3 add_source.py news https://example.com/rss "サイト名"
    python3 add_source.py google_news "site:news.example.com" "exampleニュース"

    python3 add_source.py --list
    python3 add_source.py --remove x_tkzwgrs
    python3 add_source.py ... --disabled   # 追加するが取得はしない
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCES_PATH = os.path.join(BASE_DIR, "sources.json")


def load(path: str = SOURCES_PATH) -> dict:
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    if isinstance(cfg, list):
        cfg = {"settings": {}, "sources": cfg}
    cfg.setdefault("settings", {})
    cfg.setdefault("sources", [])
    return cfg


def save(cfg: dict, path: str = SOURCES_PATH) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def slug(value: str) -> str:
    s = (value or "").strip()
    s = re.sub(r"^@", "", s)
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", s)
    return s.strip("_").lower() or "source"


def unique_id(cfg: dict, base: str) -> str:
    existing = {s.get("id") for s in cfg["sources"]}
    if base not in existing:
        return base
    n = 2
    while f"{base}_{n}" in existing:
        n += 1
    return f"{base}_{n}"


def find(cfg: dict, needle: str):
    needle = (needle or "").strip()
    for src in cfg["sources"]:
        if needle in (src.get("id"), src.get("handle"), src.get("username"), src.get("feed")):
            return src
    return None


def build(args) -> dict:
    kind = args.type
    name = args.name
    if kind == "x":
        handle = args.target.strip().lstrip("@")
        return {"id": f"x_{slug(handle)}", "type": "x", "name": name, "handle": handle}
    if kind == "youtube":
        channel = args.target.strip()
        m = re.search(r"channel_id=([A-Za-z0-9_-]+)", channel)
        if m:
            channel = m.group(1)
        return {
            "id": f"yt_{slug(channel)[:24]}",
            "type": "youtube",
            "name": name,
            "channel_id": channel,
        }
    if kind == "note":
        user = args.target.strip().strip("/")
        user = user.split("/")[-1]
        return {"id": f"note_{slug(user)}", "type": "note", "name": name, "username": user}
    if kind == "google_news":
        return {
            "id": f"gnews_{slug(args.target)[:30]}",
            "type": "google_news",
            "name": name,
            "query": args.target.strip(),
        }
    if kind == "news":
        url = args.target.strip()
        if not url.startswith(("http://", "https://")):
            raise SystemExit("news にはフィードのURL (http/https) を渡してください。")
        host = urllib.parse.urlsplit(url).hostname or "news"
        return {"id": f"news_{slug(host)}", "type": "news", "name": name, "feed": url}
    raise SystemExit(f"未知のtypeです: {kind}")


def guess_name(args) -> str:
    if args.name:
        return args.name
    target = args.target.strip()
    if args.type == "x":
        return "@" + target.lstrip("@")
    if args.type == "news":
        return urllib.parse.urlsplit(target).hostname or target
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="監視ボードにソースを追加/削除")
    parser.add_argument("type", nargs="?", choices=["x", "youtube", "note", "news", "google_news"],
                        help="追加するソースの種類")
    parser.add_argument("target", nargs="?", help="Xの@ハンドル / チャンネルID / noteユーザー名 / RSS URL / 検索クエリ")
    parser.add_argument("name", nargs="?", help="ボードに表示する名前")
    parser.add_argument("--name", dest="name_opt", help="表示名（位置引数の代わりに）")
    parser.add_argument("--id", dest="id_opt", help="ソースIDを明示する")
    parser.add_argument("--disabled", action="store_true", help="追加するが enabled=false にする")
    parser.add_argument("--list", action="store_true", help="現在のソース一覧")
    parser.add_argument("--config", help="編集する設定ファイル（既定: sources.json / 例: sources-ipo.json）")
    parser.add_argument("--remove", metavar="ID", help="ソースを削除")
    args = parser.parse_args(argv)

    config_path = args.config or SOURCES_PATH
    if not os.path.isabs(config_path):
        config_path = os.path.join(BASE_DIR, config_path)
    if not os.path.exists(config_path):
        raise SystemExit(f"設定ファイルがありません: {config_path}")
    cfg = load(config_path)

    if args.list:
        print(f"{'ID':<24}{'種別':<13}{'状態':<6}名前")
        for src in cfg["sources"]:
            state = "有効" if src.get("enabled", True) else "停止"
            print(f"{src.get('id',''):<24}{src.get('type',''):<13}{state:<6}{src.get('name','')}")
        print(f"\n計 {len(cfg['sources'])} ソース / {config_path}")
        return 0

    if args.remove:
        src = find(cfg, args.remove)
        if not src:
            print(f"見つかりません: {args.remove}", file=sys.stderr)
            return 2
        cfg["sources"] = [s for s in cfg["sources"] if s is not src]
        save(cfg, config_path)
        print(f"削除しました: {src.get('id')} ({src.get('name')})")
        return 0

    if not args.type or not args.target:
        parser.print_help()
        return 2

    args.name = args.name_opt or args.name
    src = build(args)
    src["name"] = guess_name(args)
    src["id"] = args.id_opt or unique_id(cfg, src["id"])
    if args.disabled:
        src["enabled"] = False

    # 同じハンドル/URLの重複を防ぐ
    for existing in cfg["sources"]:
        same = (
            (src["type"] == "x" and existing.get("handle", "").lower() == src.get("handle", "").lower())
            or (src["type"] == "youtube" and existing.get("channel_id") == src.get("channel_id"))
            or (src["type"] == "note" and existing.get("username") == src.get("username"))
            or (src.get("feed") and existing.get("feed") == src.get("feed"))
        )
        if same:
            print(f"すでに登録済みです: {existing.get('id')} ({existing.get('name')})")
            return 1

    cfg["sources"].append(src)
    save(cfg, config_path)
    print(f"追加しました: {src['id']} [{src['type']}] {src['name']}")
    print("ボードに反映するには: python3 update.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
