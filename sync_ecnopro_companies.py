#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ECのプロ 登壇企業ボード — Excel の「企業別集計」から sources-ecnopro-companies.json を作る。

使い方:
    python3 sync_ecnopro_companies.py                       # 既定の Excel から sources-ecnopro-companies.json を書き出す
    python3 sync_ecnopro_companies.py --xlsx path/to.xlsx   # Excel を指定
    python3 sync_ecnopro_companies.py --check               # 書き出し後に各社のクエリを実際に引いて件数を確認

出力した `sources-ecnopro-companies.json` は update.py が自動で拾い、
`ecnopro-companies.html`（タイトル「ECのプロ 登壇企業ボード」／ナビは 🎤 ECのプロ企業）を作ります。
Excel の読み取り・クエリの組み立ては `speaker_boards.py` を使い、キャスター／nrtv ボードと同じ方式です。

元データ: downloads/ecnopro-seminars.xlsx（work/process_ecnopro_seminars.py が毎回作り直す）
  * 企業別集計シートの「企業名」「登壇回数」「登壇セミナー数」を読む。
  * 1社が複数の表記で出てくる（株式会社WUUZY / 株式会社WUUZYマーケティンググループ /
    株式会社WUUZY所属プロ、株式会社Spider / SpiderLabs / SpidarLabs など）ので name_map で1社に寄せる。
  * 本文の切れ端が企業名として入っている行（「本セミナーでは…」「そんなお悩みは…」など）は
    exclude_prefixes で落とす。整理前 325表記 → 整理後 293社。
"""

from __future__ import annotations

import os
import sys

import speaker_boards as sb

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

BOARD = {
    "title": "ECのプロ 登壇企業ニュース監視ボード",
    "nav_label": "🎤 ECのプロ企業",
    "id_prefix": "ecnp",
    "xlsx": os.path.join(BASE_DIR, "..", "downloads", "ecnopro-seminars.xlsx"),
    "out": os.path.join(BASE_DIR, "sources-ecnopro-companies.json"),
    "sheet": "企業別集計",
    "name_column": "企業名",
    "sessions_column": "登壇回数",
    "events_column": "登壇セミナー数",
    # 企業名として入ってしまった本文・見出しの切れ端（登壇企業ではない）
    "exclude_names": ["2023年 株式会社", "ウェブコロCMO.バジンガ株式会社"],
    "exclude_prefixes": [
        "本セミナーでは",
        "そんなお悩みはありませんか？",
        "SNSマーケティング会社に新卒で入社し",
        "人工知能が急速に進化し",
    ],
    # 同じ企業の表記ゆれ・部署名付き・別名（左 → まとめ先の表記）
    "name_map": {
        "W２株式会社": "W2株式会社",
        "Ｗ２株式会社": "W2株式会社",
        "w2株式会社": "W2株式会社",
        "EC-ConsultingJapan株式会社": "EC-Consulting Japan株式会社",
        "ecvision株式会社": "ec vision株式会社",
        "ハックルベリー株式会社": "株式会社ハックルベリー",
        "スタークス株式会社": "株式会社スタークス",
        "株式会社Paidy": "Paidy合同会社",
        "narrative株式会社": "株式会社narrative",
        "ripples株式会社": "株式会社ripples",
        "Vooster株式会社": "株式会社Vooster",
        "株式会社Hide&Seek": "株式会社hide&seek",
        "コマースメディア株式会社": "株式会社コマースメディア",
        "マーケティングアソシエーション株式会社": "株式会社マーケティングアソシエーション",
        "eBay Japan 合同会社": "eBay Japan合同会社",
        "eBayJapan合同会社": "eBay Japan合同会社",
        "株式会社クレッシェンド・ラボ": "株式会社クレッシェンドラボ",
        "株式会社 Spider Labs": "株式会社SpiderLabs",
        "株式会社WUUZYマーケティンググループ": "株式会社WUUZY",
        "株式会社WUUZY所属プロ": "株式会社WUUZY",
        "ウブン株式会社": "株式会社ウブン",
        "売れるネット広告社株式会社": "株式会社売れるネット広告社",
        "GIVE＆GIVE 株式会社": "GIVE&GIVE株式会社",
        "GIVE＆GIVE株式会社": "GIVE&GIVE株式会社",
        "株式会社 ACROVE CX第一": "株式会社ACROVE",
        "株式会社 ピアラ トレーディング": "株式会社ピアラ",
        "株式会社社マクアケ": "株式会社マクアケ",
        "ネクストエンジン（NE株式会社": "NE株式会社",
        "Qoo10（eBay Japan合同会社": "eBay Japan合同会社",
        "ECサポート事業部 サヴァリ株式会社": "サヴァリ株式会社",
        "株式会社プラスアルファ・": "株式会社プラスアルファ・コンサルティング",
        "株式会社インゲージ、デマンドジェネレーション": "株式会社インゲージ",
        "株式会社リテリア/株式会社UrakaTA": "株式会社リテリア",
        "株式会社キャスター/株式会社Bloomstrategia": "株式会社Bloomstrategia",
        "ウェブコロCMO.バジンガ株式会社": "株式会社ウェブ・コロ",
        "一般社団法人 国際ビジネス連結機構 Chief Logistics Officer": "一般社団法人 国際ビジネス連結機構",
        "一般社団法人 国際ビジネス連結機構 事務局長/Chief Logistics Officer": "一般社団法人 国際ビジネス連結機構",
        "株式会社Spider": "株式会社SpiderLabs",
        "株式会社SpidarLabs": "株式会社SpiderLabs",
        "株式会社ライスカレープラス": "株式会社ライスカレー",
        "株式会社ファブリカコミュニケーションズアクションリンクプロダクト": "株式会社ファブリカコミュニケーションズ",
    },
    "aliases": {"株式会社Picaro.AI": "株式会社Picaro"},
    # 社名（法人格を外した名前）ごとの調整。キャスター／nrtv ボードで効いていたものを引き継ぐ。
    #   query    … Google ニュースに投げる文字列（既定は "<名前>"）
    #   match    … 見出しにこれを含む記事だけを残す（既定は [名前]）
    #   context  … True なら EC・マーケティング業界の語を含む見出しだけに絞る
    "overrides": {
        # --- 一般語・短い名前（キャスター／nrtv ボードの設定をそのまま） ---
        "キャスター": {
            "query": '"キャスター" EC',
            "match": ["株式会社キャスター", "キャスター、", "キャスターが", "キャスターの", "キャスター[", "キャスター（"],
        },
        "これから": {"query": '"株式会社これから"', "match": ["株式会社これから", "これから、"]},
        "そばに": {"query": '"株式会社そばに"', "match": ["株式会社そばに", "そばにの", "そばにが"]},
        "NE": {"query": '"NE株式会社"', "match": ["NE株式会社", "NE Korea", "441A"]},
        "Mico": {"query": '"Mico" EC', "context": "strict"},
        "スタークス": {"query": '"スタークス" EC', "context": "strict"},
        "ジグザグ": {"query": '"ジグザグ" EC', "context": "strict"},
        "ハックルベリー": {"context": True},
        "GROOVE": {"query": '"GROOVE" Amazon', "context": "strict"},
        "E-Grant": {"context": True},
        "LeaguE": {"query": '"株式会社LeaguE"', "match": ["LeaguE"], "context": "strict"},
        "メルカート": {"query": '"メルカート" EC', "context": True},
        "mtc": {"query": '"mtc" EC', "context": "strict"},
        "GIVE&GIVE": {"query": '"GIVE&GIVE" EC', "context": "strict"},
        "Minto": {"query": '"Minto" 株式会社', "match": ["Minto"], "context": "strict"},
        "CAMPFIRE": {"query": '"CAMPFIRE" 株式会社', "match": ["CAMPFIRE"], "context": "strict"},
        "ワンスター": {"context": "strict"},
        "ジーニー": {"context": "strict"},
        "スマートシェア": {"context": "strict"},
        "グリニッジ": {"context": "strict"},
        "ガラパゴス": {"context": "strict"},
        "ファン・マーケティング": {
            "query": '"ファン・マーケティング" 株式会社',
            "match": ["株式会社ファン・マーケティング", "ファン・マーケティングが", "ファン・マーケティングの"],
        },
        "narrative": {
            "match": [
                "narrative株式会社", "narrativeが", "narrativeの", "narrativeと",
                "narrative、", "narrative「", "narrative×", "narrative x", "narrativeを",
            ]
        },
        "コミューン": {"query": '"コミューン" 株式会社', "match": ["コミューン"]},
        "Gastroduce Japan": {"query": '"Gastroduce"', "match": ["Gastroduce"]},
        "サブスパイア": {"match": ["サブスパイア", "U-KOMI"]},
        "ファンコミュニケーションズ": {"match": ["ファンコミュニケーションズ", "A8.net"]},
        "アルド": {"query": '"アルド" EC', "context": "strict"},
        "ウブン": {"context": True},
        "ライスカレー": {"context": True},
        "ネイビーグループ": {"context": True},
        "ロックウェーブ": {"context": "strict"},
        "サヴァリ": {"context": "strict"},
        "しるし": {"query": '"しるし株式会社"', "match": ["しるし株式会社"]},
        "COIM": {"context": True},
        # --- ECのプロ固有の短い社名・英字名・一般語 ---
        "W2": {"query": '"W2" EC', "context": "strict"},
        "Momentum": {"query": '"Momentum" EC', "context": "strict"},
        "Tangerine": {"query": '"Tangerine" EC', "context": "strict"},
        "Limelight": {"query": '"Limelight" EC', "context": "strict"},
        "INFLUENCER": {"query": '"INFLUENCER" 株式会社', "match": ["INFLUENCER"], "context": "strict"},
        "funnel": {"query": '"funnel" EC', "context": "strict"},
        "ec": {
            "query": '"株式会社ec"',
            "match": ["株式会社ec", "株式会社EC"],
            "exclude": ["EC PARK", "ECスタジオ", "E-Grant", "ECホールディングス", "ECX", "ECMS", "EC-Consulting", "EC研究所", "EC-GAIN", "株式会社Ec."],
            "context": "strict",
        },
        "ALL": {
            "query": '"株式会社ALL" EC',
            "match": ["株式会社ALL", "ALL株式会社"],
            "exclude": ["ALL CONNECT", "ALL WEB"],
            "context": "strict",
        },
        "SUPER": {"query": '"株式会社SUPER"', "match": ["株式会社SUPER", "SUPER株式会社"], "context": "strict"},
        "TeN": {"query": '"株式会社TeN"', "match": ["株式会社TeN", "TeN株式会社"], "context": "strict"},
        "on": {
            "query": '"株式会社on" EC',
            "match": ["株式会社on", "株式会社ON"],
            "exclude": ["on the bakery", "ON-TAMA", "オンタマ"],
            "context": "strict",
        },
        "free": {"query": '"株式会社free"', "match": ["株式会社free", "free株式会社"], "context": "strict"},
        "inc": {"query": '"株式会社inc."', "match": ["株式会社inc", "inc."], "context": "strict"},
        "12": {"query": '"株式会社12"', "match": ["株式会社12"], "context": "strict"},
        "CS": {"query": '"株式会社CS"', "match": ["株式会社CS"], "context": "strict"},
        "Digital": {"query": '"株式会社Digital"', "match": ["株式会社Digital"], "context": "strict"},
        "Channel": {"query": '"株式会社Channel"', "match": ["株式会社Channel"], "context": "strict"},
        "SEAM": {"query": '"株式会社SEAM"', "match": ["株式会社SEAM", "SEAM株式会社"], "context": "strict"},
        "WAND": {"query": '"株式会社WAND"', "match": ["株式会社WAND", "WAND株式会社"], "context": "strict"},
        "mov": {"query": '"株式会社mov"', "match": ["株式会社mov"], "context": "strict"},
        "Minato": {"query": '"株式会社Minato"', "match": ["株式会社Minato", "Minato株式会社"], "context": "strict"},
        "blends": {"query": '"blends" EC', "context": "strict"},
        "ピクルス": {"query": '"株式会社ピクルス"', "match": ["株式会社ピクルス", "ピクルス株式会社"]},
        "かっこ": {"query": '"株式会社かっこ"', "match": ["株式会社かっこ", "かっこ株式会社"]},
        "キャッチボール": {"query": '"株式会社キャッチボール"', "match": ["株式会社キャッチボール"]},
        "セレクト": {"query": '"株式会社セレクト"', "match": ["株式会社セレクト"], "context": "strict"},
        "センド": {"query": '"株式会社センド"', "match": ["株式会社センド", "センド株式会社"], "context": "strict"},
        "ディール": {"query": '"株式会社ディール"', "match": ["株式会社ディール", "ディール株式会社"], "context": "strict"},
        "フュージョン": {"query": '"フュージョン株式会社"', "match": ["フュージョン株式会社", "株式会社フュージョン"], "context": "strict"},
        "グロース": {"query": '"株式会社グロース"', "match": ["株式会社グロース"], "context": "strict"},
        "アイズ": {"query": '"株式会社アイズ"', "match": ["株式会社アイズ"], "context": "strict"},
        "リンクス": {"query": '"株式会社リンクス" EC', "match": ["株式会社リンクス", "リンクス株式会社"], "context": "strict"},
        "メディアリンク": {"query": '"メディアリンク" 株式会社', "match": ["メディアリンク"], "context": "strict"},
        "ネットショップ支援": {"query": '"ネットショップ支援" 株式会社', "match": ["ネットショップ支援"]},
        "SpiderLabs": {"match": ["SpiderLabs", "Spider Labs", "Spider AF"]},
        "フュージョン": {
            "match": ["フュージョン株式会社", "株式会社フュージョン", "フュージョン、", "フュージョンが", "フュージョンの"],
            "exclude": ["ニューロフュージョン", "ヘリカルフュージョン", "核融合", "アルファフュージョン", "ホットドッグ"],
        },
        "JAPANAI": {"query": '"JAPANAI" AI', "match": ["JAPANAI", "JAPAN AI"]},
        "ec vision": {"query": '"ec vision株式会社"', "match": ["ec vision", "ecvision", "ECvision"]},
        # --- 表記ゆれ（中黒なし・スペースなしで書かれる） ---
        "ウェブ・コロ": {"match": ["ウェブ・コロ", "ウェブコロ"]},
        "エフ・コード": {"match": ["エフ・コード", "エフコード"]},
        # --- かっこ書きの別名を検索語に足す ---
        "インゲージ": {"match": ["インゲージ", "Re:lation"]},
        "リテリア": {"match": ["リテリア", "UrakaTA"]},
    },
}


if __name__ == "__main__":
    sys.exit(sb.run(BOARD))
