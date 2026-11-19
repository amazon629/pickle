#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EC・D2C企業ボード — 社名リストから sources-ec-brands.json を作る。

使い方:
    python3 sync_ec_brands.py                     # 515社ぶんの sources-ec-brands.json を書き出す
    python3 sync_ec_brands.py --check             # 書き出したあと各社のクエリを実際に引いて件数を確認
    python3 sync_ec_brands.py --check --only 3well,'S&K',TTT   # 一部の会社だけ確認（まず小さく試す）

出力 `sources-ec-brands.json` は update.py が自動で拾い、`ec-brands.html`（タイトル
「EC・D2C企業515社」）と全ページ共通ナビのピルを作る。

社名リストの整理（元: Gmail本文コピペ 1,045行）
  * 完全一致の重複を除去（1,045行 → 540社）
  * 法人格・空白・記号の違いを正規化して統合（株式会社_mtc / 株式会社mtc. など）
  * 英字表記とカタカナ表記など字面が違う同一社を `ALIASES` で統合
    （BreakingDown / ブレイキングダウン、LINE / LINEヤフー、DeNA / ディー・エヌ・エー など）
  → 515社（`COMPANIES`）。並び順は元リストの初出順。

クエリの作り方（既存の sync_ec_support.py と同じ設計）
  * 社名から法人格を外した名前を Google ニュースで検索し、その名前を見出しに含む記事だけ残す
    （`must_match`）＝同名他社・一般語のノイズを落とす。
  * 3〜5文字の英字名・短いカタカナ名・一般語の社名は自動で「あいまい」と判定し、
    `context_match`（EC/D2C/通販/製薬/マーケ等の文脈語）を含む見出しだけに絞る。
  * 法人格付きで検索したほうが精度が高い名前（株式会社AX・株式会社NR・one株式会社 など）は
    法人格込みのクエリにする。
  * 個別の調整は `OVERRIDES`（query / match / context / exclude）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(BASE_DIR, "sources-ec-brands.json")

# ------------------------------------------------------------------ 整理後の515社（元リストの初出順）
COMPANIES = [
    'DINETTE株式会社',
    'フロレゾン製薬株式会社',
    '株式会社FUMENT',
    '株式会社I-ne',
    '株式会社NR',
    '株式会社ダイエットプレミアム',
    '株式会社ニチレイフーズ',
    '株式会社ビタブリッドジャパン',
    '株式会社北の達人コーポレーション',
    '株式会社富山常備薬',
    '犬猫生活株式会社',
    '青成堂',
    '17LIVE株式会社',
    '3well',
    'AIGATE株式会社',
    'Amazon',
    'BreakingDown株式会社',
    'CATS株式会社',
    'LINEヤフー株式会社',
    'NAL製薬株式会社',
    'Shopee Japan株式会社',
    'TMALLジャパン',
    'ZEUS株式会社',
    'one株式会社',
    'pluskampo株式会社',
    'アリババ株式会社',
    'ウェペック株式会社',
    'ストライプジャパン',
    'スマートニュース株式会社',
    '株式会社AimForward',
    '株式会社E-Grant',
    '株式会社Isea',
    '株式会社MA',
    '株式会社Magnifi',
    '株式会社NOAH',
    '株式会社Oxxx',
    '株式会社SMD',
    '株式会社SOLIA',
    '株式会社WonderSpace',
    '株式会社Yogibo',
    '株式会社mtc.',
    '株式会社の運営統括',
    '株式会社アオキ',
    '株式会社アドレクス',
    '株式会社エクシーク',
    '株式会社オルリンクス製薬',
    '株式会社カワラヤ',
    '株式会社スリーピース',
    '株式会社協和',
    '売れるネット広告社グループ株式会社',
    '武内製薬株式会社',
    '満田工業株式会社',
    '話せるメディカル株式会社',
    'LeanGo',
    'S&K',
    'SUPER STUDIO',
    'UNITY株式会社',
    'VITAS',
    'katan',
    'りらいぶ',
    'クロスコンサルティング',
    '株式会社AX',
    '株式会社CARDS',
    '株式会社FID',
    '株式会社FUSION',
    '株式会社JAPANSOLUTIONS',
    '株式会社LADDER',
    '株式会社Vamosファイナンスサービス',
    '株式会社コマースファクトリー',
    '株式会社和漢',
    '森永乳業株式会社',
    'Blossom Me株式会社',
    'ECH株式会社',
    'Epic Marketing株式会社',
    'FAVO株式会社',
    'HelloVenus',
    'NDPマーケティング株式会社',
    'SHIRORU',
    'TTT',
    'ULTRA SOCIAL株式会社',
    'VIRTH株式会社',
    'WHRP株式会社',
    'ecxia株式会社',
    'victoriafruit株式会社',
    'アスクレピオス製薬株式会社',
    'アドコミ株式会社',
    'アンカー',
    'イングリウッド株式会社',
    'カティグレイス株式会社',
    'グロリアス製薬株式会社',
    'グローバルフィットネスジャパン株式会社',
    'サザキテック',
    'サヴァリ株式会社',
    'ジェイフロンティア株式会社',
    'トリガーホールディングス',
    'ネクストレベルホールディングス株式会社',
    'ネットプロテクションズ株式会社',
    'ノバセル株式会社',
    'バレットグループ株式会社',
    'フォーマルクライン株式会社',
    'マーベラス株式会社',
    'モメンタムスタジオ株式会社',
    'ヨミテ株式会社',
    'ラヴィリ株式会社',
    'レバレジーズM&Aアドバイザリー株式会社',
    'レバンテ株式会社',
    '和心本舗株式会社',
    '株式会社Anu cosmetics',
    '株式会社CFPコンサルティング',
    '株式会社IMK',
    '株式会社Nov',
    '株式会社SHINY',
    '株式会社SPYアテリア',
    '株式会社TeN',
    '株式会社アドバンスト・メディカル・ケア',
    '株式会社オリゾ',
    '株式会社セレス',
    '株式会社ドクターRe9',
    '株式会社ベスプラソリューションズ',
    '株式会社ベルタ',
    '株式会社レントラックス',
    '株式会社丸辰',
    '株式会社組織の左腕',
    '電通九州',
    'C Channel株式会社',
    'KickOff株式会社',
    'SIKI株式会社',
    'bfj株式会社',
    'ohpner株式会社',
    'テマヒマ',
    '株式会社A-sky',
    '株式会社ANTE marketing',
    '株式会社GrandBleu&Co.',
    '株式会社KASHIKA',
    '株式会社おくりバント',
    '株式会社アイヴィア',
    '株式会社アドバンス',
    '株式会社アンノウン',
    '株式会社エターナル',
    '株式会社クリアビジョン',
    '株式会社ダイレクトマーケティングゼロ',
    '株式会社マテリアルデジタル',
    '株式会社メルカリ',
    '株式会社メロン',
    '株式会社日本M&Aセンター',
    '株式会社星組',
    'EC-Consulting Japan株式会社',
    'M&Aロイヤルアドバイザリー株式会社',
    'NTTスマートコネクト株式会社',
    'Oz link',
    'VALX株式会社',
    'aisif',
    '三崎未来ホールディングス株式会社',
    '化粧品EC事業社',
    '株式会社Dowell',
    '株式会社ENRO',
    '株式会社LeaguE',
    '株式会社M&Aソーシングクラウド',
    '株式会社OneAI',
    '株式会社SLONE',
    '株式会社THE RICH',
    '株式会社オプト',
    '株式会社スマートセクション',
    '株式会社ピアラ',
    '株式会社ファンケル',
    '株式会社プレイスタイル',
    '株式会社ユーグレナ',
    'KDDI',
    'ファンコミ',
    'モグモ',
    'mederi株式会社',
    'イルミルド株式会社',
    '株式会社ECXグループ',
    '株式会社ReZARD YouTuber',
    '株式会社Sparty',
    '株式会社koujitsu',
    '株式会社office K',
    '株式会社サムライパートナーズ',
    '株式会社シンクロ',
    '株式会社九南サービス',
    '株式会社未来',
    '株式会社澤井珈琲',
    '株式会社BULK',
    '株式会社KINS',
    'JAPAN AI株式会社',
    'MonoDi株式会社',
    'ポジティブ株式会社',
    '一般社団法人国際ビジネス連結機構',
    '株式会社ALPACA',
    '株式会社Eifer',
    '株式会社Endeavor',
    '株式会社Kyogoku',
    '株式会社Strato Rise',
    '株式会社ripples',
    '株式会社uto',
    '株式会社けいぞう',
    '株式会社インターホールディングス',
    '株式会社クレストスキルパートナーズ',
    '株式会社ゼロワンブースター',
    '株式会社リテリア',
    '株式会社夢中',
    '株式会社通販の虎 マーケティング',
    '株式会社青木創士',
    '玉乃光酒造株式会社',
    '株式会社BIGO Ads',
    '株式会社Re/Quest',
    '株式会社Strato',
    '株式会社ドーモ',
    'Mirakl株式会社',
    'Paidy合同会社',
    'Repro',
    'SCSK',
    'W2株式会社',
    'eBay Japan合同会社',
    'かっこ株式会社',
    'アカマイテクノロジーズ',
    'アドエン',
    'キューサイ株式会社',
    'バリューコマース株式会社',
    'ワールド',
    '日本ネット経済新聞',
    '日本ロレアル株式会社',
    '株式会社KOMOJU',
    '株式会社SCORE',
    '株式会社Sprocket',
    '株式会社アイリスプラザ',
    '株式会社ウェブライフ',
    '株式会社オモヤ',
    '株式会社オートバックスデジタルイニシアチブ',
    '株式会社ナハト',
    '株式会社ブランドパトローラー',
    '株式会社ペイジェント',
    '株式会社ユナイテッドアローズ',
    '株式会社中川政七商店',
    '株式会社再春館製薬所',
    'Authense法律事務所',
    'CommerceXホールディングス株式会社',
    'ECのプロ',
    'Olks',
    'SINCEED株式会社',
    'レリック社会保険労務士事務所',
    'ロカボワークス',
    'ワンスター',
    '東京スポーツ新聞社',
    '株式会社CyberOwl',
    '株式会社EMUDA',
    '株式会社ENROジカル',
    '株式会社FORV',
    '株式会社KIPROJECT',
    '株式会社M&Aソーシング',
    '株式会社UPSIDER',
    '株式会社W-ENDLESS',
    '株式会社Yaaha',
    '株式会社アルク',
    '株式会社ウィナス',
    '株式会社クレディセゾン',
    '株式会社トリドリ',
    '株式会社プライムダイレクト',
    '株式会社メリル',
    '株式会社ユニメディア',
    '薬事パートナーズ法律事務所',
    '株式会社アドトラック',
    'Bell up',
    'DM0',
    'EN LOGICAL',
    'ecforce',
    'siracusa',
    '株式会社WUUZY',
    '通販通信',
    'DMM.com',
    'TieUps株式会社',
    '株式会社サイバーエージェント',
    '君のとなりの株式会社',
    '株式会社DeNA',
    '株式会社Greenspoon',
    '株式会社HighLink',
    '株式会社Icra',
    '株式会社オービックビジネス',
    '株式会社デイトラ',
    '株式会社ユナイテッドスクエア',
    '株式会社レミントン',
    '株式会社出前館',
    'てことでTikTokライバー事務所',
    'パロニム株式会社',
    '株式会社JTB',
    '株式会社ウィルゲート',
    '株式会社オムニバス',
    '株式会社キンダーガーデン',
    '株式会社デジタルアイデンティティ',
    '株式会社パルディア',
    'Facebook',
    'キャスティング',
    'チュラコス株式会社',
    '売れるAIマーケティング社',
    '株式会社CTF',
    '株式会社senjin',
    '株式会社fred',
    '株式会社ウルフ',
    '株式会社再建コンサルティング',
    'RMB株式会社',
    '株式会社電通',
    'Airsol',
    'HADO',
    'クリエイターズマッチ',
    'グランドビジョン',
    'トレンダーズ',
    'フラッグ',
    'マーケティングエンジン株式会社',
    '元株式会社テレシー',
    '株式会社EMOLVA',
    '株式会社GOKKO',
    '株式会社originals',
    '株式会社sizebook',
    '株式会社ココラブル',
    '株式会社サイカ/Momentum Partners',
    '株式会社ジーニー',
    '株式会社プログレス',
    '株式会社売れるD2C業界M&A社',
    'BEENOS株式会社',
    'Cresc.合同会社',
    'Dad株式会社',
    'SMN株式会社',
    'SideKicks株式会社',
    'popIn株式会社',
    'zealandbifu株式会社',
    '株式会社purum',
    'おれから',
    'ぱんどら株式会社',
    'アイリンクス株式会社',
    'イーモジャパン株式会社',
    'サイトキャッチャー株式会社',
    'サンテミナ株式会社',
    'ジー・プラン株式会社',
    'テクノロジアパートナー株式会社',
    'ワンクルーズ',
    '合同会社ROOM',
    '合同会社いちる',
    '合同会社トリップ',
    '塚本郵便逓送株式会社',
    '日本サプリメントフーズ株式会社',
    '有限会社ゴールド',
    '株式会社2-GEAR',
    '株式会社37Design',
    '株式会社BONNOU',
    '株式会社Collelis',
    '株式会社GEN NAKAMURA',
    '株式会社GROOVE',
    '株式会社KAYAK',
    '株式会社LDマーケティング',
    '株式会社RiLiSh',
    '株式会社SEIRYO',
    '株式会社SKD Promotion',
    '株式会社Weeare',
    '株式会社YORISOU',
    '株式会社chipper',
    '株式会社improom',
    "株式会社my's",
    '株式会社shinyspark',
    '株式会社tsworld',
    '株式会社unde',
    '株式会社wevnal',
    '株式会社あすなろわかさ',
    '株式会社もちもちデザイン',
    '株式会社アトラクト',
    '株式会社アドサンク',
    '株式会社ウキヨ',
    '株式会社エニモ',
    '株式会社エムエムラボ',
    '株式会社エースタイル',
    '株式会社オプティマエージェンシー',
    '株式会社コンボイスイッチ',
    '株式会社サイトスコープ',
    '株式会社サマー・スノウ',
    '株式会社サンアロー',
    '株式会社タウンズポスト',
    '株式会社ダイヤモンドファンタジー',
    '株式会社ネットショップ支援室',
    '株式会社メデタ',
    '株式会社京福堂',
    '株式会社日本美心',
    '株式会社獲れるコトバ執筆社',
    '登壇決定社',
    '登壇確定社',
    'FORCE-R株式会社',
    'Glide合同会社',
    'Ltd.',
    'Magboat Co.',
    'Toniez株式会社',
    'divi株式会社',
    'narrative株式会社',
    'アスニカ株式会社',
    'コラント',
    'トラストライン株式会社',
    '合同会社Earnest',
    '富士フイルムイメージングシステムズ株式会社',
    '日本ITビジネス研究所株式会社',
    '株式会社ACROVE',
    '株式会社Grow',
    '株式会社Insight science',
    '株式会社KURUKURU',
    '株式会社NuMeRo',
    '株式会社OTOGINO',
    '株式会社RIVASOL',
    '株式会社Tisms',
    '株式会社WHITECUBE',
    '株式会社WIHRP',
    '株式会社iiy',
    '株式会社アイル',
    '株式会社アドドットコム',
    '株式会社ウェブサポート',
    '株式会社オークランド',
    '株式会社サイリージャパン',
    '株式会社シコメルフードテック',
    '株式会社テクノパル',
    '株式会社ハーバルアイ',
    '株式会社レギネ',
    '株式会社美元',
    '福島鰹株式会社',
    '売れるM&A社',
    'アミジェス',
    'ルナドクター株式会社',
    '株式会社Essence Group',
    '株式会社Kuzuha',
    '株式会社LLL',
    '株式会社SUBARU マーケティング推進室',
    '株式会社allattain',
    '株式会社スーパースター',
    '株式会社バイオフィリア',
    '株式会社売れる越境EC社',
    'COSMAX',
    'D-PRIDE株式会社',
    'Flip Nine株式会社',
    'Nanoleaf Japan株式会社',
    'P2C Studio株式会社',
    'ROSE LABO株式会社',
    'アンファー株式会社',
    'シルクザリッチ',
    'トリガー',
    'ノットアホテル',
    'ファビウス株式会社',
    'プレミアアンチエイジング株式会社',
    'ベースフード',
    'マスター・コスメティック株式会社',
    'マルルー株式会社',
    '株式会社B4A Technologies',
    '株式会社Beautydoors',
    '株式会社CACTUS TOKYO',
    '株式会社CDエナジーダイレクト',
    '株式会社EssnceGroup',
    '株式会社F.PRODUCERS',
    '株式会社Foxx',
    '株式会社IRC',
    '株式会社Indigo',
    '株式会社Macbee Planet',
    '株式会社P2C',
    '株式会社Vieon',
    '株式会社アルパカ',
    '株式会社アールライト',
    '株式会社クロコス',
    '株式会社クロスアイ',
    '株式会社グローパス',
    '株式会社バルクオム',
    '株式会社フラクトライト',
    '株式会社ブランドクラウド',
    '株式会社マクロジ',
    '株式会社マーケマッチ',
    '株式会社会社Parasol',
    '株式会社食選館',
    '野口卓也✖︎株式会社',
    'MAGI-C株式会社',
    'MEDICAT株式会社',
    'Route66株式会社',
    'forest株式会社',
    'オーキ製薬',
    'サンクルーズ株式会社',
    'スタークス株式会社',
    'フューチャー株式会社',
    'ライフネット生命保険株式会社',
    '合同会社NIG',
    '合同会社グローベラ',
    '株式会社ANT・MARKETING',
    '株式会社BE:FANG',
    '株式会社Bets',
    '株式会社Canelle.',
    '株式会社IMAGICA IRIS',
    '株式会社J・Gripマーケティング',
    '株式会社Lily',
    '株式会社MouthEarKing',
    '株式会社Preheat',
    '株式会社TAGO STUDIO',
    '株式会社アイトリガー',
    '株式会社アーツ',
    '株式会社ウィルミナ',
    '株式会社ウルミー',
    '株式会社エイツ',
    '株式会社エフカ',
    '株式会社エンターキー',
    '株式会社オプティマイザー',
    '株式会社オレコン',
    '株式会社ディセンシア',
    '株式会社デジタルエージェンシー',
    '株式会社ドリーム',
    '株式会社ブランニューミール',
    '株式会社ラッシュ',
    '株式会社ラトシエ',
    '株式会社リアセス',
    '株式会社伊藤園',
    '株式会社利他フーズ',
    '株式会社富士ロジテックホールディングス',
    '株式会社湖池屋',
    '株式会社食通',
    '株式会社黒船物流',
    '黒船LABO株式会社',
    '株式会社みらいきれい',
    'バイドゥ株式会社',
]

LEGAL_RE = re.compile(
    r"^(株式会社|有限会社|合同会社|合資会社|合名会社|一般社団法人|一般財団法人|公益社団法人|公益財団法人)\s*"
)
PAREN_TAIL_RE = re.compile(r"\s*[（(][^（()）]*[)）]\s*$")
LEGAL_TAIL_RE = re.compile(r"\s*(株式会社|有限会社|合同会社|合資会社|合名会社)$")
LEGAL_WORDS = ("株式会社", "有限会社", "合同会社", "合資会社", "合名会社",
               "一般社団法人", "一般財団法人", "公益社団法人", "公益財団法人")


def core_name(name: str) -> str:
    """「株式会社ピュアフラット」→「ピュアフラット」「グリニッジ株式会社」→「グリニッジ」"""
    s = PAREN_TAIL_RE.sub("", (name or "").strip())
    s = LEGAL_RE.sub("", s)
    s = LEGAL_TAIL_RE.sub("", s)
    return s.strip()


# EC/D2C・通販・製薬・マーケの記事とみなす語。短い社名・一般語の同名別物を落とすための語彙。
EC_CONTEXT = [
    "EC", "D2C", "通販", "ネット通販", "ネットショップ", "ショップ", "ストア", "小売", "物販",
    "ブランド", "商品", "販売", "化粧品", "コスメ", "美容", "健康食品", "サプリ", "製薬", "医薬",
    "マーケティング", "広告", "プロモーション", "集客", "CRM", "物流", "倉庫", "在庫", "受注",
    "決済", "カート", "モール", "出店", "越境", "楽天", "Amazon", "アマゾン", "Yahoo", "ヤフー",
    "Shopify", "ショッピファイ", "Instagram", "TikTok", "SNS", "インフルエンサー", "ライブコマース",
    "動画", "アプリ", "SaaS", "導入", "提供", "事業", "株式会社", "決算", "売上", "資金調達", "上場",
    "買収", "提携", "リリース", "発表", "開始", "新商品", "発売", "サービス", "支援",
    "コンサルティング", "代理店", "M&A", "スタートアップ", "代表", "社長", "インタビュー",
]

# 一般語・英語の普通名詞・地名などで同名ノイズが大半を占める名前。
# 法人格込みの社名（「株式会社◯◯」）で検索して、その表記を含む記事だけ残す。
FULL_NAME_ONLY = {
    "協和", "メロン", "未来", "夢中", "キャスティング", "フラッグ", "ゴールド", "プログレス",
    "ドリーム", "ラッシュ", "グランドビジョン", "Kuzuha", "エンターキー", "TeN", "FUSION",
    "LADDER", "originals", "Endeavor", "GROOVE", "Indigo", "forest", "フューチャー",
    "スーパースター", "ワールド", "narrative", "ワンスター", "Route66", "オークランド",
    "オプティマイザー", "マーベラス", "アトラクト", "デジタルアイデンティティ", "LeaguE",
    "NuMeRo", "クロスアイ", "ポジティブ", "THE RICH", "プレイスタイル", "スリーピース",
    "アオキ", "DINETTE", "オムニバス", "アドバンス", "エターナル", "丸辰", "アンノウ",
}

# 一般語・同名他社が多い名前（自動判定に加えて明示的に文脈絞り込み）
GENERIC = {
    "アンカー", "フラッグ", "キャスティング", "ワールド", "ゴールド", "未来", "メロン", "トリガー",
    "アンノウ", "オムニバス", "アドバンス", "エターナル", "夢中", "協和", "丸辰", "テマヒマ",
    "キックオフ", "おれから", "青成堂", "アオキ", "カワラヤ", "和漢", "Wakan", "unity", "one",
    "pluskampo", "victoriafruit", "zealandbifu", "siracusa", "repro", "hanako",
}

# 社名（法人格を外した名前）ごとの調整。
#   query    … Google ニュースに投げる文字列（既定は "<名前>"）
#   match    … 見出しにこれを含む記事だけを残す（既定は [名前]）
#   context  … True なら EC_CONTEXT のどれかを含む見出しだけに絞る
#   exclude  … 見出しにこれを含む記事は落とす
OVERRIDES = {
    # 「1.3WELL」などスズキのグレード名ノイズ → 文脈語で落とす
    "3well": {"match": ["3WELL"], "context": True, "exclude": ["ソリオ", "スズキ", "中古車", "goo-net"]},
    "S&K": {
        "match": ["S&K", "S＆K"],
        "context": True,
        "exclude": ["ボクシング", "選手名鑑", "アートコレクション", "先島", "坂田", "熊本発"],
    },
    # TTT は自転車競技・スニーカー・飲食店など同名が多く、EC/D2C 記事はほぼ無い
    "TTT": {
        "match": ["TTT"],
        "context": True,
        "exclude": ["スニーカー", "プロレス", "タイムトライアル", "三宅", "徳島", "軟式野球",
                    "王座", "ベルト", "ガンバレ", "居酒屋", "カフェ", "ホットペッパー", "明石公園"],
    },
    "VITAS": {"match": ["VITAS"], "exclude": ["ソリオ"]},
    "katan": {"match": ["KATAN", "katan"]},
    # 「アンカー」は Anker（モバイルバッテリー）・将棋のアンカー・建築アンカーが大半 → 法人名で検索
    "アンカー": {
        "query": '"株式会社アンカー"',
        "match": ["株式会社アンカー", "アンカー株式会社"],
        "exclude": ["アンカーマン"],
    },
    # バモス（本田技研の軽自動車）・バモス後払い を1社に統合し、後払いサービス名で検索
    "Vamosファイナンスサービス": {
        "query": '"バモス後払い" OR "Vamosファイナンス"',
        "match": ["バモス後払い", "Vamosファイナンス", "バモスファイナンス"],
    },
    # 桃屋の「テマヒマ」キャンペーン・テマヒマ展が大半
    "テマヒマ": {
        "match": ["テマヒマ"],
        "context": True,
        "exclude": ["桃屋", "テマヒマ展", "21_21", "伊那", "CAMPFIRE", "フードウイークリー"],
    },
    "りらいぶ": {"match": ["りらいぶ", "リライブ"]},
    "株式会社MA": {"query": '"株式会社MA"', "match": ["株式会社MA", "株式会社 M.A", "株式会社M.A"]},
    "株式会社AX": {"query": '"株式会社AX"', "match": ["株式会社AX"]},
    "株式会社NR": {"query": '"株式会社NR"', "match": ["株式会社NR"]},
    "one株式会社": {
        "query": '"one株式会社" OR "ONE株式会社"',
        "match": ["ONE株式会社", "one株式会社"],
        "exclude": ["アセットマネジメント", "セリグマン", "ETF", "ファンド", "投資信託", "債券"],
    },
    # 一般語の社名は法人格込みで検索したほうが精度が高い
    "和漢": {"query": '"株式会社和漢"', "match": ["株式会社和漢", "和漢株式会社"]},
    "KickOff株式会社": {"query": '"KickOff株式会社" OR "キックオフ株式会社"',
                        "match": ["KickOff株式会社", "キックオフ株式会社"]},
    "アンノウン": {"query": '"株式会社アンノウン"', "match": ["株式会社アンノウン"]},
    "トリガー": {"query": '"株式会社トリガー"', "match": ["株式会社トリガー"]},
    "DeNA": {"query": '"DeNA"', "match": ["DeNA", "ディー・エヌ・エー"]},
    # 「マーベラス」はプロレス・映画・テレ東番組の同名が多い。ゲーム・EC 文脈だけ残す
    "マーベラス": {
        "query": '"マーベラス"',
        "match": ["マーベラス"],
        "exclude": ["レスリング", "プロレス", "東スポ", "ベースボール", "テレ東", "午後ロー", "岩田美香", "彩羽匠"],
        "context": True,
    },
    "HelloVenus": {"query": '"株式会社HelloVenus"', "match": ["株式会社HelloVenus"]},
    "Shopee Japan": {"query": '"Shopee"', "match": ["Shopee", "ショッピー"],
                     "exclude": ["Cup", "サッカー", "Vietnam", "ハノイ", "ライブスケジュール"]},
    "Amazon": {"match": ["Amazon", "アマゾン"]},
    "Facebook": {"match": ["Facebook", "フェイスブック"]},
    "TMALLジャパン": {"query": '"TMALL"', "match": ["TMALL", "天猫"]},
    "アリババ": {"match": ["アリババ", "Alibaba", "阿里巴巴"]},
    "DMM.com": {"query": '"DMM"', "match": ["DMM"]},
    "KDDI": {"query": '"KDDI"', "match": ["KDDI"]},
    "SCSK": {"query": '"SCSK"', "match": ["SCSK"]},
    "eBay Japan": {
        "query": '"eBay Japan"',
        "match": ["eBay Japan", "イーベイ・ジャパン", "イーベイジャパン"],
    },
    "Paidy": {"query": '"Paidy"', "match": ["Paidy", "ペイディ"]},
    "Mirakl": {"query": '"Mirakl"', "match": ["Mirakl", "ミラクル"]},
    "Repro": {"query": '"Repro"', "match": ["Repro"]},
    "CSS": {"query": '"CSS"', "match": ["CSS"]},
    # 「Ltd.」「株式会社の運営統括」など、社名として成立していない行は検索しても0件だが、
    # 元リストの行を消さずに残す（カードは0件表示）。
}

LATIN_RE = re.compile(r"^[\x00-\x7f]+$")


def is_ambiguous(core: str) -> tuple[bool, bool]:
    """(あいまいか, 法人格込みで検索したほうがよいか)"""
    if core in GENERIC:
        return True, False
    if LATIN_RE.match(core):
        if len(core) <= 2:
            return True, True
        if len(core) <= 5:
            return True, True
        return False, False
    # カタカナ・ひらがな・漢字の短い名前は同名が多い（協和・未来・丸辰など）
    if len(core) <= 4:
        return True, True if len(core) <= 3 else False
    return False, False


def build_source(name: str, index: int) -> dict:
    core = core_name(name)
    override = OVERRIDES.get(name) or OVERRIDES.get(core) or {}
    ambiguous, prefer_legal = is_ambiguous(core)
    if core in FULL_NAME_ONLY and any(w in name for w in LEGAL_WORDS):
        prefer_legal = True
    query = override.get("query")
    full_name = name
    if core in FULL_NAME_ONLY and not any(w in name for w in LEGAL_WORDS):
        # 法人格が付いていない一般語の社名は「株式会社◯◯」の形で検索する
        full_name = f"株式会社{core}"
    if not query:
        if core in FULL_NAME_ONLY and not any(w in name for w in LEGAL_WORDS):
            query = f'"{full_name}"'
        elif prefer_legal:
            query = f'"{name}"'
        else:
            query = f'"{core}"'
    source = {
        "id": f"ecb_{index:03d}",
        "type": "google_news",
        "name": name,
        "query": query,
        "must_match": override.get("match") or [full_name if prefer_legal else core],
        "limit": 5,
        "enabled": True,
    }
    if override.get("context") or ambiguous:
        source["context_match"] = EC_CONTEXT
    if override.get("exclude"):
        source["exclude_match"] = override["exclude"]
    return source


# ------------------------------------------------------------------- checking
def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


def match_any(text: str, terms: list) -> bool:
    """タイトルが terms のどれかを含むか（全角半角・大小の揺れを吸収、英字は単語境界）。"""
    t = normalize(text)
    for term in terms or []:
        n = normalize(term).strip()
        if not n:
            continue
        if re.fullmatch(r"[\x00-\x7f]+", n):
            if re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", t):
                return True
        elif n in t:
            return True
    return False


def check_source(src: dict, settings: dict) -> dict:
    """1社ぶんクエリを引いて、must_match / context_match で残る件数を数える（--check 用）。"""
    url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(src["query"])
        + "&hl=ja&gl=JP&ceid=JP:ja"
    )
    ua = settings.get("user_agent") or "Mozilla/5.0 (compatible; MonitorBoard/1.0)"
    last = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "identity"})
            with urllib.request.urlopen(req, timeout=20) as res:
                raw = res.read()
            root = ET.fromstring(
                re.sub(rb"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", b"&amp;", raw)
            )
            titles = [it.findtext("title") or "" for it in root.findall(".//item")]
            kept = [
                t
                for t in titles
                if match_any(t, src["must_match"])
                and (not src.get("context_match") or match_any(t, src["context_match"]))
                and not (src.get("exclude_match") and match_any(t, src["exclude_match"]))
            ]
            return {"total": len(titles), "kept": len(kept), "sample": kept[:3], "error": None}
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 + attempt)
    return {"total": 0, "kept": 0, "sample": [], "error": str(last)[:80]}


# --------------------------------------------------------------------- output
def make_document(sources: list) -> dict:
    return {
        "settings": {
            "title": f"EC・D2C企業{len(sources)}社 ニュース監視",
            "nav_label": f"EC・D2C {len(sources)}社",
            "items_per_source": 5,
            "timeout_seconds": 15,
            "retries": 2,
            "fetch_workers": 5,
            "tolerate_errors": True,
            "translate": True,
            "translate_max_per_run": 20,
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
        },
        "sources": sources,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="EC・D2C企業ボードのニュース監視設定を作る")
    ap.add_argument("--out", default=DEFAULT_OUT, help="出力する sources-*.json")
    ap.add_argument("--check", action="store_true", help="各社のクエリを実際に引いて件数を表示")
    ap.add_argument("--only", help="この文字列を含む会社だけ確認（カンマ区切り）")
    ap.add_argument("--workers", type=int, default=6, help="--check の並列数")
    args = ap.parse_args(argv)

    sources = [build_source(n, i + 1) for i, n in enumerate(COMPANIES)]
    doc = make_document(sources)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"{len(sources)}社を書き出しました: {os.path.relpath(args.out, BASE_DIR)}")

    if args.check:
        targets = sources
        if args.only:
            keys = [k.strip() for k in args.only.split(",") if k.strip()]
            targets = [s for s in sources if any(k in s["name"] for k in keys)]
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            results = list(pool.map(lambda s: check_source(s, doc["settings"]), targets))
        print("")
        print(f"{'件数':>4}  {'元記事':>5}  会社 / クエリ")
        empty = []
        for src, res in zip(targets, results):
            note = f"(エラー: {res['error']})" if res["error"] else ""
            print(f"{res['kept']:>4}  {res['total']:>5}  {src['name'][:34]:<34} {src['query']} {note}")
            for s in res["sample"][:2]:
                print(f"        · {s[:90]}")
            if res["kept"] == 0:
                empty.append(src["name"])
        if empty:
            print(f"\n記事0件の会社 {len(empty)}社: " + "、".join(empty))
        print(f"（確認にかかった時間 {time.time() - t0:.1f}秒）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
