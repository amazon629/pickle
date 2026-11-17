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

Xのアカウントをまとめて追加する（貼り付けテキストから）:
    python3 add_source.py --bulk --config sources-ai.json --dry-run <<'EOF'
    鈴木 @ai_300
    おかぴ🦒⚡️｜AIニュース解説 @okapi_fukugyo
    EOF

    pbpaste | python3 add_source.py --bulk --config sources-ai.json --update
    python3 add_source.py --bulk --config sources-new.json --title "新ボード" --text "名前 @handle ..."
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCES_PATH = os.path.join(BASE_DIR, "sources.json")

# --bulk で設定ファイルを作るときの初期 settings
BULK_DEFAULT_SETTINGS = {
    "items_per_source": 15,
    "translate": True,
    "translate_max_per_run": 120,
    "nitter_base": "https://nitter.cf",
    "nitter_mirrors": [
        "https://nitter.cf",
        "https://nitter.privacyredirect.com",
        "https://xcancel.com",
    ],
}

# x.com の URL のうちアカウント名でないパス
X_RESERVED_PATHS = {
    "i", "home", "search", "explore", "intent", "share", "hashtag", "settings",
    "login", "logout", "signup", "messages", "notifications", "compose", "tos",
    "privacy", "about", "download", "jobs", "help", "account", "following",
    "followers", "lists", "bookmarks", "communities", "premium", "verified",
    "topics", "news", "places", "moments", "rules", "en", "ja",
}

# 「https://x.com/handle」「@handle」のどちらでも拾う（メールの foo@bar は拾わない）
X_REF_RE = re.compile(
    r"https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,30})"
    r"|(?<![A-Za-z0-9_.])@([A-Za-z0-9_]{1,30})"
)

TRAIL_SEP = ",、;；:：|｜/\\-–—・…\t "
LEAD_SEP = ",、;；:：|｜/\\\t "
OPENERS = "(（[［{【<«\"'“”‘’"


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


def resolve_config(path: str | None) -> str:
    config_path = path or SOURCES_PATH
    if not os.path.isabs(config_path):
        config_path = os.path.join(BASE_DIR, config_path)
    return config_path


def board_hint(config_path: str) -> str:
    """sources-ai.json -> 'ai'（新規作成時の title / nav_label の既定に使う）"""
    stem = os.path.splitext(os.path.basename(config_path))[0]
    if stem.startswith("sources-"):
        return stem[len("sources-"):]
    if stem == "sources":
        return "main"
    return stem or "board"


def new_config(config_path: str, args) -> dict:
    hint = board_hint(config_path)
    title = args.title or ("監視ボード" if hint == "main" else f"{hint} 監視ボード")
    nav = args.nav_label or (hint.upper() if hint.isascii() else hint)
    settings = {"title": title, "nav_label": nav}
    settings.update(BULK_DEFAULT_SETTINGS)
    return {"settings": settings, "sources": []}


def _clean_name(chunk: str) -> str:
    """@handle の直前のテキストから表示名を取り出す。"""
    chunk = chunk.replace("\r\n", "\n").replace("\r", "\n")
    for line in reversed(chunk.split("\n")):
        cand = line.strip()
        if not cand:
            continue
        cand = cand.rstrip(TRAIL_SEP)
        # 「名前 (@handle)」の開きかっこを落とす
        while cand and cand[-1] in OPENERS:
            cand = cand[:-1].rstrip(TRAIL_SEP)
        cand = cand.lstrip(LEAD_SEP)
        if cand:
            return re.sub(r"[ \t]{2,}", " ", cand).strip()
    return ""


def parse_bulk_text(text: str):
    """貼り付けテキストから X の (handle, name) を取り出す。

    戻り値: (entries, dups, problems)
      entries  ... [{"handle": ..., "name": ...}]（貼り付け内の重複は除去済み）
      dups     ... 貼り付け内で同じハンドルが繰り返されたもの
      problems ... 読み取れなかった箇所の説明
    """
    entries: list[dict] = []
    dups: list[str] = []
    problems: list[str] = []

    matches = list(X_REF_RE.finditer(text))
    if not matches:
        return entries, dups, ["@ハンドル も x.com の URL も見つかりませんでした"]

    cursor = 0
    seen: set[str] = set()
    for m in matches:
        url_handle, at_handle = m.group(1), m.group(2)
        handle = url_handle or at_handle
        chunk = text[cursor:m.start()]
        cursor = m.end()

        if url_handle and handle.lower() in X_RESERVED_PATHS:
            problems.append(f"アカウントのURLではありません: {m.group(0)}")
            continue
        if len(handle) > 15:
            problems.append(f"ハンドルが15文字を超えています: {handle}")
            continue
        key = handle.lower()
        if key in seen:
            dups.append(handle)
            continue
        seen.add(key)
        entries.append({"handle": handle, "name": _clean_name(chunk) or f"@{handle}"})

    return entries, dups, problems


def run_update(config_path: str) -> int:
    """追加したボードだけ update.py で更新する。"""
    print("\nupdate.py を実行します …")
    return subprocess.call(
        [sys.executable, os.path.join(BASE_DIR, "update.py"), "--config", config_path],
        cwd=BASE_DIR,
    )


def bulk_add(args, config_path: str) -> int:
    text = args.text if args.text is not None else sys.stdin.read()
    if not text.strip():
        print("入力が空です（--text か標準入力でテキストを渡してください）。", file=sys.stderr)
        return 2

    entries, dups, problems = parse_bulk_text(text)
    if not entries:
        print("Xのアカウントを読み取れませんでした（1行1件で「表示名 @handle」の形にしてください）。",
              file=sys.stderr)
        for problem in problems:
            print(f"  ! {problem}", file=sys.stderr)
        return 2

    existed = os.path.exists(config_path)
    cfg = load(config_path) if existed else new_config(config_path, args)

    registered = {
        (s.get("handle") or "").lower()
        for s in cfg["sources"]
        if s.get("type") == "x"
    }

    to_add, skipped = [], []
    for entry in entries:
        if entry["handle"].lower() in registered:
            src = next(
                (s for s in cfg["sources"]
                 if (s.get("handle") or "").lower() == entry["handle"].lower()),
                {},
            )
            skipped.append((entry, src))
            continue
        src = {
            "id": unique_id(cfg, f"x_{slug(entry['handle'])}"),
            "type": "x",
            "name": entry["name"],
            "handle": entry["handle"],
            "enabled": False if args.disabled else True,
        }
        cfg["sources"].append(src)
        registered.add(entry["handle"].lower())
        to_add.append(src)

    target = os.path.basename(config_path)
    print(f"{'[dry-run] ' if args.dry_run else ''}{target}: "
          f"{len(entries)}件を読み取りました（追加 {len(to_add)} / 登録済み {len(skipped)}"
          f"{f' / 貼り付け内の重複 {len(dups)}' if dups else ''}）")
    if not existed:
        print(f"{'[dry-run] ' if args.dry_run else ''}設定ファイルを新規作成します: {config_path}")
        print(f"  settings.title = {cfg['settings'].get('title')!r} / "
              f"nav_label = {cfg['settings'].get('nav_label')!r}")

    for src in to_add:
        print(f"  + {src['id']} (@{src['handle']}) {src['name']}")
    for entry, src in skipped:
        print(f"  - 登録済み: @{entry['handle']}"
              + (f" ({src.get('id')} / {src.get('name')})" if src else ""))
    for handle in dups:
        print(f"  - 貼り付け内の重複: @{handle}")
    for problem in problems:
        print(f"  ! {problem}", file=sys.stderr)

    if args.dry_run:
        print("\n[dry-run] ファイルは書き換えていません。")
        return 0

    save(cfg, config_path)
    print(f"\n計 {len(cfg['sources'])} ソース / {config_path}")

    if args.update:
        return run_update(config_path)
    if to_add:
        print("ボードに反映するには: python3 update.py")
    return 0


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
    parser.add_argument("--bulk", action="store_true",
                        help="貼り付けテキストからXアカウントをまとめて追加（--text 省略時は標準入力）")
    parser.add_argument("--text", help="--bulk で読む文字列（例: \"鈴木 @ai_300 おかぴ @okapi_fukugyo\"）")
    parser.add_argument("--dry-run", action="store_true", help="書き込まずに追加/スキップの結果だけ表示")
    parser.add_argument("--update", action="store_true", help="追加後に update.py を実行する")
    parser.add_argument("--title", help="設定ファイルを新規作成するときの settings.title")
    parser.add_argument("--nav-label", dest="nav_label",
                        help="設定ファイルを新規作成するときの settings.nav_label")
    args = parser.parse_args(argv)

    config_path = resolve_config(args.config)

    if args.bulk or args.text is not None:
        return bulk_add(args, config_path)

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

    if args.dry_run:
        print(f"[dry-run] 追加します: {src['id']} [{src['type']}] {src['name']}")
        return 0

    cfg["sources"].append(src)
    save(cfg, config_path)
    print(f"追加しました: {src['id']} [{src['type']}] {src['name']}")
    if args.update:
        return run_update(config_path)
    print("ボードに反映するには: python3 update.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
