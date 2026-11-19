#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ボードごとの「2026年記事あり社数」「記事0件社数」を data-*.json から数える。

  python3 measure_boards.py smartnews-partners caster-companies ...
  python3 measure_boards.py --all-targets
"""
from __future__ import annotations

import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGETS = ["smartnews-partners", "caster-companies", "ec-companies", "nrtv-companies", "shopify-partners"]


def measure(board: str) -> dict:
    path = os.path.join(BASE, f"data-{board}.json")
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    srcs = d.get("sources") or []
    total = len(srcs)
    zero = with2026 = 0
    no2026 = []
    for s in srcs:
        items = s.get("items") or []
        if not items:
            zero += 1
        if any((it.get("published") or "").startswith("2026") for it in items):
            with2026 += 1
        else:
            no2026.append({"id": s.get("id"), "name": s.get("name"),
                           "count": len(items),
                           "latest": max([(it.get("published") or "")[:10] for it in items] or [""])})
    return {"board": board, "generated_at": d.get("generated_at"), "total": total,
            "with2026": with2026, "no2026": total - with2026, "zero": zero,
            "no2026_list": no2026}


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    boards = TARGETS if (not argv or argv[0] == "--all-targets") else argv
    detail = "--detail" in argv
    out = []
    for b in boards:
        r = measure(b)
        out.append(r)
        print(f"{b:22} 全{r['total']:3}社  2026あり {r['with2026']:3} / ない {r['no2026']:3} "
              f"(うち0件 {r['zero']:3})  [{r['generated_at']}]")
        if detail:
            for c in r["no2026_list"]:
                print(f"    - {c['id']:10} {c['name'][:34]:<34} {c['count']}件 latest={c['latest']}")
    json.dump(out, open(os.path.join(BASE, "tools", "measure_out.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
