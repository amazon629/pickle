#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ボードごとの ntfy 新着通知を、1コマンドで付け外し・確認・テストする。

update.py 側は「sources-*.json の settings に ntfy_topic があるボードだけ」を
対象にした汎用の仕組みなので、このスクリプトでどのボードにも同じように付けられる
（付け外しは settings の書き換えだけ。GitHub Actions の次回実行から効く）。

使い方:
    python3 set_ntfy.py --list                                  # どのボードに付いているか一覧
    python3 set_ntfy.py --board adkaiwai --topic kiwi-yuji-8m4k9p2x   # 付ける／トピックを変える
    python3 set_ntfy.py --board adkaiwai --off                   # 外す
    python3 set_ntfy.py --board adkaiwai --test                  # テスト通知を送る（[テスト] 付き）
    python3 set_ntfy.py --board adkaiwai --test --dry-run        # 送る内容だけ表示
    python3 set_ntfy.py --board adkaiwai --test --topic some-topic    # 別トピックへ試し送り

    --board は sources-<name>.json の <name>（例: adkaiwai, ipo, ec-brands）。
    --list は読み取りだけで、何も書き換えない。

補足:
  * 通知は GitHub Actions（GITHUB_ACTIONS=true）からの実行でだけ送られる。
    手元で試すときは update.py --board <name> --dry-run --notify。
  * sync_*.py が settings を作り直すボード（例: adkaiwai は
    sync_adkaiwai_companies.py）は、そちらの make_document() にも ntfy_topic を
    書いておかないと再生成で消える。--list の「生成元」列で気づけるようにしている。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import update as U

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# settings を作り直すスクリプトがあるボード（再生成で ntfy_topic が消えないか注意）
GENERATORS = {
    "adkaiwai": "sync_adkaiwai_companies.py",
    "caster-companies": "sync_caster_companies.py",
    "ec-brands": "sync_ec_brands.py",
    "ec-mailorder": "sync_ec_mailorder.py",
    "ec-support": "sync_ec_support.py",
    "ecnopro-companies": "sync_ecnopro_companies.py",
    "ipo-approved": "sync_ipo_approved_news.py",
    "livecommerce-4companies": "sync_livecommerce_companies.py",
    "nrtv-companies": "sync_nrtv_companies.py",
    "shopify-partners": "sync_shopify_partners.py",
    "smartnews-partners": "sync_smartnews_partners.py",
    "sns-agencies": "sync_sns_agencies.py",
    "unicorns": "sync_unicorn_startups.py",
}


def board_config(name: str) -> tuple:
    """ボード名から (設定ファイルのパス, ボード情報) を返す。無ければ (None, None)。"""
    for board in U.discover_boards():
        if board.get("static") or not board.get("config"):
            continue
        if board["name"] == name:
            return board["config"], board
    return None, None


def board_names() -> list:
    names = []
    for path in sorted(glob.glob(os.path.join(BASE_DIR, "sources-*.json"))):
        names.append(os.path.basename(path)[len("sources-"):-len(".json")])
    if os.path.exists(os.path.join(BASE_DIR, "sources.json")):
        names.insert(0, "main")
    return names


def read_config(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, list):
        data = {"settings": {}, "sources": data}
    data.setdefault("settings", {})
    data.setdefault("sources", [])
    return data


def write_config(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def board_label(settings: dict) -> str:
    return (settings.get("nav_label") or settings.get("title") or "").strip()


def state_count(board_name: str) -> int:
    state = U.load_ntfy_state()
    return len((state.get("boards", {}).get(board_name, {}) or {}).get("notified") or [])


def cmd_list() -> int:
    """各ボードの ntfy 設定を一覧（読み取りのみ）。"""
    rows = []
    for name in board_names():
        path = os.path.join(BASE_DIR, "sources.json" if name == "main"
                            else f"sources-{name}.json")
        try:
            cfg = read_config(path)
        except (OSError, ValueError):
            continue
        settings = cfg["settings"]
        topic = (settings.get("ntfy_topic") or "").strip()
        rows.append((name, board_label(settings), topic, state_count(name),
                     GENERATORS.get(name, "")))
    if not rows:
        print("ボードが見つかりません。")
        return 1
    width = max(len(r[0]) for r in rows)
    print(f"{'ボード':<{width}}  {'ntfy':<5} {'トピック':<28} {'送信済み':>6}  {'表示名':<18} 生成元")
    on = 0
    for name, label, topic, n, gen in rows:
        if topic:
            on += 1
        print(
            f"{name:<{width}}  {'ON' if topic else '-':<5} "
            f"{(topic or '-'):<28} {n:>6}  {label[:18]:<18} {gen}"
        )
    print(f"\nntfy 通知が付いているボード: {on} / {len(rows)}")
    if on:
        print("通知は GitHub Actions の実行時だけ送られます（update.py --board <name> --dry-run --notify で内容確認）。")
    return 0


def cmd_set(name: str, topic: str, board_url: str = "") -> int:
    """ボードに ntfy_topic を設定する（＝そのボードの新着が ntfy に届くようになる）。"""
    topic = (topic or "").strip()
    if not topic:
        print("--topic が空です。", file=sys.stderr)
        return 2
    if not topic.replace("-", "").replace("_", "").isalnum():
        print(f"トピック名は英数字・-・_ だけにしてください: {topic}", file=sys.stderr)
        return 2
    path, board = board_config(name)
    if not path:
        print(f"ボードがありません: {name}（--list で一覧）", file=sys.stderr)
        return 2
    cfg = read_config(path)
    settings = cfg["settings"]
    settings["ntfy_topic"] = topic
    if not (settings.get("board_url") or "").strip():
        settings["board_url"] = board_url or U.board_url_for({}, board)
    elif board_url:
        settings["board_url"] = board_url
    write_config(path, cfg)
    print(f"{name}: ntfy 通知を ON にしました（topic={topic}）")
    print(f"  設定ファイル: {os.path.relpath(path, BASE_DIR)}")
    print(f"  リンク先: {settings['board_url']}")
    gen = GENERATORS.get(name)
    if gen:
        print(f"  注意: {gen} が settings を作り直すので、その make_document() にも "
              f"ntfy_topic を書いておいてください（消えると通知も止まります）。")
    return 0


def cmd_off(name: str) -> int:
    path, _ = board_config(name)
    if not path:
        print(f"ボードがありません: {name}（--list で一覧）", file=sys.stderr)
        return 2
    cfg = read_config(path)
    settings = cfg["settings"]
    old = (settings.pop("ntfy_topic", "") or "").strip()
    if not old:
        print(f"{name}: ntfy 通知はもともと付いていません。")
        return 0
    write_config(path, cfg)
    print(f"{name}: ntfy 通知を OFF にしました（topic={old}）")
    print("  送信済みの記録は残すので、付け直しても過去の記事は再送しません。")
    return 0


def sample_items(board: dict, settings: dict, count: int = 2) -> list:
    """テスト通知の見本。ボードの最新記事があればそれを [テスト] として使う。"""
    items = []
    try:
        with open(board["data"], encoding="utf-8") as fh:
            data = json.load(fh)
        for src in data.get("sources", []):
            for item in src.get("items", []):
                link = item.get("link")
                if not link:
                    continue
                items.append({
                    "names": [src.get("name") or ""],
                    "title": "[テスト] " + (item.get("title") or "")[:80],
                    "link": link,
                    "published": item.get("published") or "",
                })
                break
            if len(items) >= count:
                break
    except (OSError, ValueError):
        pass
    if not items:
        board_url = U.board_url_for(settings, board)
        items = [{
            "names": [board_label(settings) or board["name"]],
            "title": "[テスト] ntfy 通知のテストです。この通知が見えれば設定できています。",
            "link": board_url,
            "published": "",
        }]
    return items


def cmd_test(name: str, topic: str, dry_run: bool) -> int:
    """テスト通知を送る（[テスト] 付きなので、本物の新着と見分けられる）。"""
    path, board = board_config(name)
    if not path:
        print(f"ボードがありません: {name}（--list で一覧）", file=sys.stderr)
        return 2
    settings = read_config(path)["settings"]
    topic = (topic or settings.get("ntfy_topic") or "").strip()
    if not topic:
        print(f"{name} には ntfy_topic がありません。先に --board {name} --topic <topic> で付けてください。",
              file=sys.stderr)
        return 2
    items = sample_items(board, settings)
    payload = U.build_ntfy_payload(dict(settings, ntfy_topic=topic), board, items)
    payload["title"] = "[テスト] " + payload["title"]
    if dry_run:
        print(f"POST {U.NTFY_PUBLISH_URL}  topic={topic}（--dry-run なので送信しません）")
        print(f"Title: {payload['title']}")
        print(f"Click: {payload['click']}")
        print("Message:")
        for line in payload["message"].split("\n"):
            print(f"  {line}")
        return 0
    try:
        U.post_ntfy(payload)
    except Exception as exc:  # noqa: BLE001
        print(f"送信できませんでした（{type(exc).__name__}: {exc}）", file=sys.stderr)
        return 1
    print(f"テスト通知を送りました（topic={topic}）。スマホの ntfy アプリで確認してください。")
    print("  届かないとき: アプリで同じトピックを購読しているか、通知の許可・省電力設定を確認。")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ボードの ntfy 新着通知を付け外し・一覧・テストする")
    ap.add_argument("--board", help="ボード名（sources-<name>.json。例: adkaiwai）")
    ap.add_argument("--topic", help="ntfy のトピック名（例: kiwi-yuji-8m4k9p2x）")
    ap.add_argument("--off", action="store_true", help="そのボードの通知を外す")
    ap.add_argument("--list", action="store_true", help="ボードごとの設定を一覧（読み取りのみ）")
    ap.add_argument("--test", action="store_true", help="テスト通知を送る")
    ap.add_argument("--dry-run", action="store_true", help="--test の内容を表示するだけ")
    args = ap.parse_args(argv)

    if args.list:
        return cmd_list()
    if not args.board:
        ap.print_help()
        print("\n例: python3 set_ntfy.py --board adkaiwai --topic kiwi-yuji-8m4k9p2x", file=sys.stderr)
        return 2
    if args.off:
        return cmd_off(args.board)
    if args.test:
        return cmd_test(args.board, args.topic, args.dry_run)
    if args.topic:
        return cmd_set(args.board, args.topic)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
