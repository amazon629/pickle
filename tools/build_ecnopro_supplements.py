#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ECのプロ登壇企業ボード（ecnopro-companies）の補完ソースを news-supplements.json に足す。

Googleニュースだけでは2026年の記事が取れない社について、
  extra_feeds … PR TIMES 企業RSS（company_id は社名一致＋業種語/会社HPで同一性を確認済み）
  html_news   … 公式サイトの「お知らせ／ニュース」ページ（2026年の記事が取れることを実取得で確認済み）
を追加する。既存ボードの補完定義はそのまま残す（board キー "ecnopro-companies" だけ書き換える）。

  python3 tools/build_ecnopro_supplements.py [--dry-run]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("u", f"{REPO}/update.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)

BOARD = "ecnopro-companies"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# (社名, PR TIMES company_id) — 2026年のリリースがあり、社名と事業内容（EC・通販・マーケ）が一致
PRT = [
    ("かっこ株式会社", 9799),
    ("JAPANAI株式会社", 124536),          # PR TIMES 表記は「JAPAN AI株式会社」
    ("フュージョン株式会社", 30423),
    ("株式会社SpiderLabs", 31981),        # 表記は「株式会社Spider Labs」
    ("株式会社COIM", 123373),
    ("株式会社ガラパゴス", 48043),
    ("KURO HOLDINGS株式会社", 159279),
    ("Micoworks株式会社", 40834),         # 表記は「株式会社Mico」
    ("SHOPLINE Japan株式会社", 141754),
    ("株式会社mov", 24246),
    ("株式会社ピクルス", 2036),            # 診断クラウド「ヨミトル」／「キャンつく」の会社
    ("株式会社エフカフェ", 21177),
    ("サヴァリ株式会社", 33158),
    ("アジアンブリッジ株式会社", 34674),
    ("株式会社銀座千疋屋ホールディングス", 115451),  # PR TIMES 表記は「銀座千疋屋」
]

# (社名, 公式サイトのニュース／お知らせURL) — 2026年の記事が取れることを実取得で確認済み
SITE = [
    ("かっこ株式会社", "https://cacco.co.jp/news/"),
    ("株式会社ファイブスプリングス", "https://www.5springs.co.jp/blog/information/seminar/"),
    ("株式会社ガラパゴス", "https://glpgs.com/news/"),
    ("株式会社AirSol", "https://airsol.jp/news/"),
    ("株式会社スタークス", "https://starx.co.jp/news/"),
    ("Micoworks株式会社", "https://mico-inc.com/news/"),
    ("キッズ・ラボラトリー株式会社", "https://kids-laboratory.co.jp/content/category/news/"),
    ("株式会社ヘノブファクトリー", "https://henobufactory.co.jp/category/information/"),
    ("エンパワーショップ株式会社", "https://empowershop.co.jp/press/index.html"),
    ("manyC株式会社", "https://manyc-inc.com/#news"),
    ("センド株式会社", "https://www.sen-do.co.jp/news/"),
    ("株式会社セレクト", "https://select-ltd.jp/news/"),
    ("株式会社リテリア", "https://www.re-terior.com/news"),
    ("アジアンブリッジ株式会社", "https://www.asian-bridge.com/news/"),
]


def check_site(url: str):
    try:
        raw, status, final = u.http_get(url, 15, UA, 0)
        ents = u.parse_html_news(raw.decode("utf-8", "replace"), final)
    except Exception as exc:  # noqa: BLE001
        return url, 0, 0, str(exc)[:60]
    return url, len(ents), sum(1 for e in ents if e["published"].year == 2026), ""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    src = json.loads((os.path.join(REPO, "sources-ecnopro-companies.json") and
                      open(os.path.join(REPO, "sources-ecnopro-companies.json"), encoding="utf-8")).read())
    names = {s["name"] for s in src["sources"]}

    out = {}
    missing = []
    for name, cid in PRT:
        if name not in names:
            missing.append(("prt", name))
            continue
        out.setdefault(name, {})["extra_feeds"] = [
            {"url": f"https://prtimes.jp/companyrdf.php?company_id={cid}", "origin": "prtimes", "label": "PR TIMES"}
        ]

    with ThreadPoolExecutor(max_workers=6) as pool:
        checks = list(pool.map(lambda x: check_site(x[1]), SITE))
    for (name, url), (_, n, n2026, err) in zip(SITE, checks):
        if name not in names:
            missing.append(("site", name))
            continue
        if n2026 < 1:
            print(f"  SKIP {name} {url} n={n} n2026={n2026} {err}")
            continue
        out.setdefault(name, {})["html_news"] = [{"url": url, "origin": "site"}]
        print(f"  ok   {name} {url} n={n} n2026={n2026}")

    if missing:
        print("sources に無い社名:", missing)
        if any(k == "prt" for k, _ in missing):
            print("社名が変わっている可能性。中止します。", file=sys.stderr)
            return 1

    path = os.path.join(REPO, "news-supplements.json")
    data = json.load(open(path, encoding="utf-8"))
    before = len(data.get(BOARD) or {})
    data[BOARD] = dict(sorted(out.items()))
    if args.dry_run:
        print("dry-run:", json.dumps({BOARD: data[BOARD]}, ensure_ascii=False)[:600])
        return 0
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(f"{BOARD}: {before} → {len(data[BOARD])}社ぶんの補完を書き込みました（他ボードはそのまま）")
    print("boards:", {b: len(v) for b, v in data.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
