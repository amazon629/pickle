#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nrtv登壇企業ボード — Excel の「登壇企業一覧」から sources-nrtv-companies.json を作る。

使い方:
    python3 sync_nrtv_companies.py                       # 既定の Excel から sources-nrtv-companies.json を書き出す
    python3 sync_nrtv_companies.py --xlsx path/to.xlsx   # Excel を指定
    python3 sync_nrtv_companies.py --check               # 書き出し後に各社のクエリを実際に引いて件数を確認

出力した `sources-nrtv-companies.json` は update.py が自動で拾い、nrtv-companies.html を作ります。
Excel の読み取り・クエリの組み立ては `speaker_boards.py`（標準ライブラリのみ）に共通化しています。
"""

from __future__ import annotations

import os
import sys

import speaker_boards as sb

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

BOARD = {
    "title": "nrtv登壇企業 ニュース監視",
    "nav_label": "nrtv企業",
    "id_prefix": "nc",
    "xlsx": os.path.join(BASE_DIR, "..", "downloads", "nrtv-events-speakers.xlsx"),
    "out": os.path.join(BASE_DIR, "sources-nrtv-companies.json"),
    "sheet": "登壇企業一覧",
    "name_column": "企業名",
    "sessions_column": "登場回数（部）",
    "events_column": "登壇イベント数",
    # 同じ主催の中の表記ゆれ（小さいほうの名前 → まとめ先）。nrtv の一覧は現状ゆれなし
    "aliases": {},
    # 社名（法人格を外した名前）ごとの調整。
    #   query    … Google ニュースに投げる文字列（既定は "<名前>"）
    #   match    … 見出しにこれを含む記事だけを残す（別名・表記ゆれを足せる。既定は [名前]）
    #   context  … True なら EC・マーケティング業界の語を含む見出しだけに絞る
    "overrides": {
        # 一般語・短い名前。業界語を足して同名の別物を落とす
        "キャスター": {"query": '"キャスター" EC', "context": True},
        "これから": {"query": '"株式会社これから"', "match": ["株式会社これから", "これから、"]},
        "そばに": {"query": '"株式会社そばに"', "match": ["株式会社そばに", "そばにの", "そばにが"]},
        "NE": {"query": '"NE株式会社"', "match": ["NE株式会社", "NE Korea", "441A"]},
        "Mico": {"query": '"Mico" EC', "context": "strict"},
        # 一般語そのもの。狭い文脈語（context: "strict"）か「株式会社」付きのクエリで絞る
        "スタークス": {"query": '"スタークス" EC', "context": "strict"},
        "グリニッジ": {"context": "strict"},
        "ガラパゴス": {"context": "strict"},
        "ジグザグ": {"query": '"ジグザグ" EC', "context": "strict"},
        "ハックルベリー": {"context": True},
        "ムジン": {"context": "strict"},
        "GROOVE": {"query": '"GROOVE" Amazon', "context": "strict"},
        "オプト": {"query": '"オプト" EC', "context": True},
        "E-Grant": {"context": True},
        "LeaguE": {"query": '"株式会社LeaguE"', "match": ["LeaguE"], "context": "strict"},
        "MADS": {"query": '"MADS" 広告', "match": ["MADS"], "context": "strict"},
        "LANY": {"query": '"LANY" 株式会社', "match": ["LANY"], "context": "strict"},
        "mtc": {"query": '"mtc" EC', "context": "strict"},
        "GIVE&GIVE": {"query": '"GIVE&GIVE" EC', "context": "strict"},
        "Minto": {"query": '"Minto" 株式会社', "match": ["Minto"], "context": "strict"},
        "LOCUS": {"query": '"株式会社LOCUS"', "match": ["LOCUS"], "context": "strict"},
        "CAMPFIRE": {"query": '"CAMPFIRE" 株式会社', "match": ["CAMPFIRE"], "context": "strict"},
        "ワンスター": {"context": "strict"},
        "ジーニー": {"context": "strict"},
        "スマートシェア": {"context": "strict"},
        # 社名に中黒が入る（見出しは中黒なしで書かれることが多い）
        "ファン・マーケティング": {
            "query": '"ファン・マーケティング" 株式会社',
            "match": ["ファン・マーケティング", "ファンマーケティング"],
        },
        # カタカナの一般語。社名として使われている形（助詞・「株式会社」が続く）だけを残す
        "narrative": {
            "match": [
                "narrative株式会社", "narrativeが", "narrativeの", "narrativeと",
                "narrative、", "narrative「", "narrative×", "narrative x", "narrativeを",
            ]
        },
        "コミューン": {
            "match": [
                "コミューン株式会社", "コミューン「", "コミューンが", "コミューンは", "コミューンを",
                "コミューンに", "コミューンと", "コミューン、", "コミューンの", "コミューン運営",
            ]
        },
        # 表記ゆれ（スペースなし表記）を拾う
        "Gastroducejapan": {"query": '"Gastroduce"', "match": ["Gastroduce"]},
        # かっこ書きの別名を検索語に足す
        "サブスパイア": {"match": ["サブスパイア", "U-KOMI"]},
        "ファンコミュニケーションズ": {"match": ["ファンコミュニケーションズ", "A8.net"]},
    },
}


if __name__ == "__main__":
    sys.exit(sb.run(BOARD))
