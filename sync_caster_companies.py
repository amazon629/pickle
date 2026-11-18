#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
キャスター登壇企業ボード — Excel の「登壇企業一覧」から sources-caster-companies.json を作る。

使い方:
    python3 sync_caster_companies.py                       # 既定の Excel から sources-caster-companies.json を書き出す
    python3 sync_caster_companies.py --xlsx path/to.xlsx   # Excel を指定
    python3 sync_caster_companies.py --check               # 書き出し後に各社のクエリを実際に引いて件数を確認

出力した `sources-caster-companies.json` は update.py が自動で拾い、caster-companies.html を作ります。
Excel の読み取り・クエリの組み立ては `speaker_boards.py`（標準ライブラリのみ）に共通化しています。
"""

from __future__ import annotations

import os
import sys

import speaker_boards as sb

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

BOARD = {
    "title": "キャスター登壇企業 ニュース監視",
    "nav_label": "キャスター企業",
    "id_prefix": "cc",
    "xlsx": os.path.join(BASE_DIR, "..", "downloads", "caster-events-speakers.xlsx"),
    "out": os.path.join(BASE_DIR, "sources-caster-companies.json"),
    "sheet": "登壇企業一覧",
    "name_column": "登壇企業名",
    "sessions_column": "登場回数（部）",
    "events_column": "参加イベント数",
    # 同じ主催の中の表記ゆれ（小さいほうの名前 → まとめ先）
    "aliases": {
        "株式会社Picaro.AI": "株式会社Picaro",
    },
    # 社名（法人格を外した名前）ごとの調整。
    #   query    … Google ニュースに投げる文字列（既定は "<名前>"）
    #   match    … 見出しにこれを含む記事だけを残す（別名・表記ゆれを足せる。既定は [名前]）
    #   context  … True なら EC・マーケティング業界の語を含む見出しだけに絞る
    "overrides": {
        # 一般語・短い名前。業界語を足して同名の別物を落とす
        "キャスター": {
            "query": '"キャスター" EC',
            "match": ["株式会社キャスター", "キャスター、", "キャスターが", "キャスターの", "キャスター[", "キャスター（"],
        },
        "これから": {"query": '"株式会社これから"', "match": ["株式会社これから", "これから、"]},
        "そばに": {"query": '"株式会社そばに"', "match": ["株式会社そばに", "そばにの", "そばにが"]},
        "NE": {"query": '"NE株式会社"', "match": ["NE株式会社", "NE Korea", "441A"]},
        "Mico": {"query": '"Mico" EC', "context": "strict"},
        # 一般語そのもの。狭い文脈語（context: "strict"）か「株式会社」付きのクエリで絞る
        "オールイン": {"query": '"オールイン株式会社"', "match": ["オールイン株式会社"]},
        "しるし": {"query": '"しるし株式会社"', "match": ["しるし株式会社"]},
        "LeaguE": {"query": '"株式会社LeaguE"', "match": ["LeaguE"], "context": "strict"},
        "ジグザグ": {"query": '"ジグザグ" EC', "context": "strict"},
        "SIKI": {"query": '"SIKI株式会社"', "match": ["SIKI株式会社"], "context": "strict"},
        "オンサイト": {"query": '"オンサイト株式会社"', "match": ["オンサイト株式会社"]},
        "GIVE&GIVE": {"query": '"GIVE&GIVE" EC', "context": "strict"},
        "Cast a spell": {"query": '"Cast a spell" 株式会社', "match": ["Cast a spell合同会社", "Cast a spell、"]},
        "サポーターズ": {"query": '"株式会社サポーターズ"', "match": ["株式会社サポーターズ", "サポーターズ（"]},
        "スタークス": {"query": '"スタークス" EC', "context": "strict"},
        "アルド": {"query": '"アルド" EC', "context": "strict"},
        "ロックウェーブ": {"context": "strict"},
        "ダイレクトソーシング": {"query": '"株式会社ダイレクトソーシング"', "match": ["株式会社ダイレクトソーシング"]},
        "採用モンスター": {"context": "strict"},
        "Rodina": {"match": ["株式会社Rodina", "Rodinaが", "Rodina、", "Rodinaと", "Rodinaを", "Rodina（"]},
        "サヴァリ": {"context": "strict"},
        "OTOGI": {"query": '"OTOGI" 株式会社', "match": ["OTOGI"]},
        "ハックルベリー": {"context": True},
        "GROOVE": {"query": '"GROOVE" Amazon', "context": "strict"},
        # カタカナの一般語。社名として使われている形（助詞・「株式会社」が続く）だけを残す
        "narrative": {
            "match": [
                "narrative株式会社", "narrativeが", "narrativeの", "narrativeと",
                "narrative、", "narrative「", "narrative×", "narrative x", "narrativeを",
            ]
        },
        "コミューン": {"query": '"コミューン" 株式会社', "match": ["コミューン"]},
        # 広めの文脈語で十分なもの
        "メルカート": {"query": '"メルカート" EC', "context": True},
        "ウブン": {"context": True},
        "ネイビーグループ": {"context": True},
        "ライスカレープラス": {"context": True},
        "COIM": {"context": True},
        "E-Grant": {"context": True},
        "弥生": {"query": '"弥生" EC', "context": True},
        # 一般語そのもの。「インボイス株式会社」まで入れて絞る
        "インボイス": {"query": '"インボイス株式会社"', "match": ["インボイス株式会社"]},
        # 社名に記号が入る／社名が長い
        "mtc": {"query": '"mtc" EC', "context": True},
        "卒塔婆通販「卒塔婆屋さん」": {
            "query": '"卒塔婆屋さん"',
            "match": ["卒塔婆屋さん", "卒塔婆通販"],
        },
        "ビットエー ourlyカンパニー": {"query": '"ビットエー"', "match": ["ビットエー", "ourly"]},
        # かっこ書きの別名を検索語に足す
        "サブスパイア": {"match": ["サブスパイア", "U-KOMI"]},
        "ファンコミュニケーションズ": {"match": ["ファンコミュニケーションズ", "A8.net"]},
    },
}


if __name__ == "__main__":
    sys.exit(sb.run(BOARD))
