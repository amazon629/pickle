# 監視ボード

X・YouTube・note・ニュースを1枚にまとめる、スマホ向けの監視ボードです。
`update.py` が各フィードを取得して `index.html`（＋`data.json`）を作り直します。
GitHub Actions が1時間ごとに自動で更新するので、一度置けば放っておけます。

ボードは `sources*.json` ごとに1枚。1回の実行で全部まとめて更新されます。

| ボード | 設定 | ページ | データ |
| --- | --- | --- | --- |
| 監視ボード（メイン） | `sources.json` | `index.html` | `data.json` |
| AI監視ボード | `sources-ai.json` | `ai.html` | `data-ai.json` |
| IPO・マーケット監視ボード | `sources-ipo.json` | `ipo.html` | `data-ipo.json` |
| PR TIMES監視ボード | `sources-prtimes.json` | `prtimes.html` | `data-prtimes.json` |
| スタートアップ監視ボード | `sources-startup.json` | `startup.html` | `data-startup.json` |
| ピュアフラット登壇企業 ニュース監視 | `sources-ec-companies.json` | `ec-companies.html` | `data-ec-companies.json` |
| EC・支援企業34社 ニュース監視 | `sources-ec-support.json` | `ec-support.html` | `data-ec-support.json` |
| EC・D2C企業515社 ニュース監視 | `sources-ec-brands.json` | `ec-brands.html` | `data-ec-brands.json` |
| 通販・健康・コスメ54社 ニュース監視 | `sources-ec-mailorder.json` | `ec-mailorder.html` | `data-ec-mailorder.json` |
| SmartNews 認定代理店 ニュース監視 | `sources-smartnews-partners.json` | `smartnews-partners.html` | `data-smartnews-partners.json` |
| Shopify 受賞パートナー ニュース監視 | `sources-shopify-partners.json` | `shopify-partners.html` | `data-shopify-partners.json` |
| SNS・広告代理店 ニュース監視 | `sources-sns-agencies.json` | `sns-agencies.html` | `data-sns-agencies.json` |
| キャスター登壇企業 ニュース監視 | `sources-caster-companies.json` | `caster-companies.html` | `data-caster-companies.json` |
| nrtv登壇企業 ニュース監視 | `sources-nrtv-companies.json` | `nrtv-companies.html` | `data-nrtv-companies.json` |
| 広告・マーケ X監視ボード | `sources-ad-marketing.json` | `ad-marketing.html` | `data-ad-marketing.json` |
| ポケカ・買取 X監視ボード | `sources-pokeca.json` | `pokeca.html` | `data-pokeca.json` |
| 10億ドル以上ユニコーン ニュース監視 | `sources-unicorns.json` | `unicorns.html` | `data-unicorns.json` |
| 日本株・投資 X監視ボード | `sources-jp-stocks.json` | `jp-stocks.html` | `data-jp-stocks.json` |
| AI新着 X監視ボード | `sources-ai-tools.json` | `ai-tools.html` | `data-ai-tools.json` |
| 都市開発 X監視ボード（キーワード絞り込み付き） | `sources-urban-dev.json` | `urban-dev.html` | `data-urban-dev.json` |

公開URL: `https://amazon629.github.io/pickle/` / `/ai.html` / `/ipo.html` / `/prtimes.html` / `/startup.html` / `/ec-companies.html` / `/ec-support.html` / `/ec-brands.html` / `/ec-mailorder.html` / `/smartnews-partners.html` / `/shopify-partners.html` / `/sns-agencies.html` / `/ad-marketing.html` / `/caster-companies.html` / `/nrtv-companies.html` / `/pokeca.html` / `/unicorns.html` / `/jp-stocks.html` / `/ai-tools.html` / `/urban-dev.html`
（各ページ上部のピルから互に行き来できます）

- 画面: グレー背景 `#f2f3f5` に白い角丸カード、カード上部にソース名と件数、各項目は バッジ / タイトル（タップで元記事へ）/ ドメイン / 時刻（JST）
- 新着（前回取得時になかった記事）は薄い緑の行と赤い点で表示
- 上部の検索欄で、表示中の全ソースを横断して絞り込み
- ボードごとに `settings.keyword_chips` を書くと、検索欄の下にキーワードのチップが出ます（今は都市開発ボードの
  「再開発／新築／供給／跡地／新駅」）。押すとその語を含む投稿だけが残り、語は黄色でハイライトされます（複数選択可）
- 外国語の投稿（英語など）は本文の直下に緑の `【和訳】` ボックスで日本語訳を表示。
  訳は前回の `data*.json` に残るので、次回以降は新しく増えた投稿だけを訳します
- 取得は全ボードまとめて並列（既定24並行・`--workers` で変更可）。X（nitter）だけは6並行に絞って
  レート制限を避けるので、20ページ・1,174ソースでも1回の更新は30秒前後（和訳の初回生成を含めて）で終わります
- 依存ライブラリなし（Python 3.9+ の標準ライブラリだけ）／各HTMLはデータを埋め込んだ単体ファイルなので、ローカルでダブルクリックでも開けます

## ファイル

| ファイル | 役割 |
| --- | --- |
| `sources.json` | メインボードの監視対象（ここだけ触れば増減できる） |
| `sources-ai.json` | AIボードの監視対象 |
| `sources-ipo.json` | IPO・マーケットボードの監視対象 |
| `sources-prtimes.json` | PR TIMESボードの監視対象 |
| `sources-startup.json` | スタートアップボードの監視対象 |
| `sources-unicorns.json` | 10億ドル以上ユニコーンボードの監視対象（48社。`sync_unicorn_startups.py` で生成） |
| `sources-jp-stocks.json` | 日本株・投資 X監視ボードの監視対象（X 4アカウント。`add_source.py --bulk` で作成） |
| `sources-ai-tools.json` | AI新着 X監視ボードの監視対象（X 6アカウント。`add_source.py --bulk` で作成） |
| `sources-urban-dev.json` | 都市開発 X監視ボードの監視対象（X 9アカウント＋`keyword_chips`。`add_source.py --bulk` で作成） |
| `sources-ec-companies.json` | ピュアフラット登壇企業ボードの監視対象（70社。`sync_pureflat_companies.py` で生成） |
| `sources-ec-support.json` | EC・支援企業34社ボードの監視対象（34社。`sync_ec_support.py` で生成） |
| `sources-ec-brands.json` | EC・D2C企業515社ボードの監視対象（515社。`sync_ec_brands.py` で生成） |
| `sources-ec-mailorder.json` | 通販・健康・コスメ54社ボードの監視対象（54社。`sync_ec_mailorder.py` で生成） |
| `sources-smartnews-partners.json` | SmartNews 認定代理店ボードの監視対象（90社。`sync_smartnews_partners.py` で生成） |
| `sources-shopify-partners.json` | Shopify 受賞パートナーボードの監視対象（25社。`sync_shopify_partners.py` で生成） |
| `update.py` | 取得 → 各ボードの HTML / data.json を生成 |
| `add_source.py` | 監視対象を1行で追加／削除（`--config` でボード指定／`--bulk` でまとめて追加） |
| `index.html` / `data.json` | メインボードの生成物 |
| `ai.html` / `data-ai.json` | AIボードの生成物 |
| `ipo.html` / `data-ipo.json` | IPO・マーケットボードの生成物 |
| `prtimes.html` / `data-prtimes.json` | PR TIMESボードの生成物 |
| `startup.html` / `data-startup.json` | スタートアップボードの生成物 |
| `ec-companies.html` / `data-ec-companies.json` | 登壇企業ボードの生成物 |
| `ec-support.html` / `data-ec-support.json` | EC・支援企業34社ボードの生成物 |
| `ec-brands.html` / `data-ec-brands.json` | EC・D2C企業515社ボードの生成物 |
| `ec-mailorder.html` / `data-ec-mailorder.json` | 通販・健康・コスメ54社ボードの生成物 |
| `smartnews-partners.html` / `data-smartnews-partners.json` | SmartNews 認定代理店ボードの生成物 |
| `shopify-partners.html` / `data-shopify-partners.json` | Shopify 受賞パートナーボードの生成物 |
| `sns-agencies.html` / `data-sns-agencies.json` | SNS・広告代理店ボードの生成物 |
| `caster-companies.html` / `data-caster-companies.json` | キャスター登壇企業ボードの生成物 |
| `nrtv-companies.html` / `data-nrtv-companies.json` | nrtv登壇企業ボードの生成物 |
| `ad-marketing.html` / `data-ad-marketing.json` | 広告・マーケ X監視ボードの生成物 |
| `pokeca.html` / `data-pokeca.json` | ポケカ・買取 X監視ボードの生成物 |
| `unicorns.html` / `data-unicorns.json` | 10億ドル以上ユニコーンボードの生成物 |
| `jp-stocks.html` / `data-jp-stocks.json` | 日本株・投資 X監視ボードの生成物 |
| `ai-tools.html` / `data-ai-tools.json` | AI新着 X監視ボードの生成物 |
| `urban-dev.html` / `data-urban-dev.json` | 都市開発 X監視ボードの生成物（キーワードチップ付き） |
| `sync_pureflat_companies.py` | Excel「登壇企業一覧」→ `sources-ec-companies.json` を生成（社名クエリの組み立て・確認付き） |
| `sync_ec_support.py` | `COMPANIES`（EC・支援34社）→ `sources-ec-support.json` を生成（`--check` / `--only` 付き） |
| `sync_ec_brands.py` | 社名リスト（1,045行）を名寄せした515社 → `sources-ec-brands.json` を生成（`--check` / `--only` 付き） |
| `sync_ec_mailorder.py` | `COMPANIES`（通販・健康・コスメ54社）→ `sources-ec-mailorder.json` を生成（`--check` / `--only` 付き） |
| `sync_smartnews_partners.py` | Excel「2026年／2025年認定企業一覧」→ `sources-smartnews-partners.json` を生成 |
| `sync_shopify_partners.py` | Excel「企業別受賞集計」→ `sources-shopify-partners.json` を生成 |
| `sync_sns_agencies.py` | `COMPANIES`（38社）→ `sources-sns-agencies.json` を生成（`--list` / `--check` 付き） |
| `sync_caster_companies.py` | Excel「登壇企業一覧」（キャスター）→ `sources-caster-companies.json` を生成（120社） |
| `sync_nrtv_companies.py` | Excel「登壇企業一覧」（nrtv）→ `sources-nrtv-companies.json` を生成（81社） |
| `sync_unicorn_startups.py` | Excel「評価額あり案件」→ `sources-unicorns.json` を生成（円換算1,500億円以上＝10億ドル以上・重複統合・ノイズ除去`--check` 付き） |
| `speaker_boards.py` | 上記2本の共通部品（Excel 読み取り・社名の名寄せ・クエリ組み立て・`--check`） |
| `sources-ad-marketing.json` | 広告・マーケ X監視ボードの監視対象（X 10アカウント。`add_source.py --bulk` で作成） |
| `sources-pokeca.json` | ポケカ・買取 X監視ボードの監視対象（X 7アカウント。`add_source.py --bulk` で作成） |
| `.github/workflows/update.yml` | 1時間ごとの自動更新 |

## すぐ使う

```bash
cd monitor-board
python3 update.py          # 全ボードを取得して HTML / data.json を更新
python3 update.py -v       # どのフィードが何件取れたかを表示
python3 update.py --dry-run -v   # 取得だけしてファイルは書かない

python3 update.py --board ipo            # IPO ボードだけ
python3 update.py --config sources-ipo.json --out ipo.html --data data-ipo.json  # 出力先も指定
```

### ボードを増やす

`sources-<名前>.json` を作るだけで、`<名前>.html` と `data-<名前>.json` が生成されます。
（例: `sources-crypto.json` → `crypto.html` / `data-crypto.json`）
ページ上部のボード切り替えピルは `settings.nav_label` で短い名前を指定できます。

### 社名ごとにニュースを追う（登壇企業ボード）

`sources-ec-companies.json` / `sources-caster-companies.json` / `sources-nrtv-companies.json` は、
Excel の「登壇企業一覧」から起こした「1社＝1ソース（Google ニュース検索）」のボードです。
（ピュアフラットは `sync_pureflat_companies.py`、キャスターと nrtv は共通部品 `speaker_boards.py` を使います）

```bash
python3 sync_pureflat_companies.py --check   # Excel を読み直して設定を作り、各社のヒット件数も表示
python3 sync_ec_support.py --check           # EC・支援企業34社（まず --only これから,12 で数社だけ確認できる）
python3 sync_caster_companies.py --check     # キャスター登壇企業120社
python3 sync_nrtv_companies.py --check       # nrtv登壇企業81社
python3 update.py --board ec-companies,ec-support,caster-companies,nrtv-companies   # 企業ニュースの4枚だけ更新
```

- 社名は法人格を外して検索し（`"ピュアフラット"`）、`must_match` でその社名を含む見出しだけを残します。
- 「これから」「そばに」「グリニッジ」のように一般語でも使われる社名は、`context_match`（EC・通販・楽天…などの語）も
  含む見出しに絞り、同名の別物（グリニッジ天文台、フュージョンエネルギー等）を落としています。
  「オンサイト」「ジグザグ」「サポーターズ」のような語は、より狭い `context_match`（`EC_STRICT`）で絞ります。
  調整は各スクリプトの `overrides` に1行足すだけです。
- 同じ主催の中で「株式会社Picaro」と「株式会社Picaro.AI」のような表記ゆれは、`aliases` で1社にまとめます
  （キャスター側は 121行 → 120社）。
- 社名検索でヒット0件でもエラーにはせず「新しい記事はありません」と表示します。
- 一時的に取れない社があっても他のボードの更新を止めないよう、このボードは `settings.tolerate_errors: true` です
  （取得できなかった社はカードに「取得エラー」と出ます）。

### 通販・健康・コスメ54社ボード

通販・健康食品・コスメD2Cの54社を1社＝1ソースにしたボードです（キューサイ、エバーライフ、ランクアップ、
世田谷自然食品、RIZAP、ジャパネットたかた、ユーグレナ、ベースフード、長寿乃里 など）。

```bash
python3 sync_ec_mailorder.py                                         # 54社ぶんの設定を作り直す
python3 sync_ec_mailorder.py --check --only ルアン,フローラ,イング   # まず数社だけ件数を確認
python3 sync_ec_mailorder.py --check --workers 3                     # 全54社のヒット件数
python3 update.py --config sources-ec-mailorder.json                 # この1枚だけ更新
```

- ノイズ対策は `OVERRIDES` に集約。`ランクアップ`（マナラ／スポーツの昇格記事）、`ルアン`（競走馬ではなく
  スーパーミリオンヘアー）、`フローラ`（HB-101／ジルスチュアートのライン名）、`イング`（ビューイング等の複合語）、
  `Right Here`（曲名）、`エポラ`（出血熱）、`ナチュラルガーデン`（園芸記事）のように、クエリの言い換え・
  `context_match`（通販・化粧品・健康食品の語）・`exclude_match` を社名ごとに指定しています。
- ニュースが少ない中小の通販会社（ヴァーナル、ナチュラルガーデン、東京ディベロ、奈良大和生薬）は0件のこともあります。
  0件はエラーにせず「新しい記事はありません」と表示し、`tolerate_errors: true` で他ボードの更新を止めません。

### EC・D2C企業ボード（515社）

Gmailで受け取った社名リスト（1,045行）を名寄せして1社＝1ソースにしたボードです。

```bash
python3 sync_ec_brands.py                                         # 515社ぶんの設定を作り直す
python3 sync_ec_brands.py --check --only 3well,S\&K,TTT          # まず数社だけ件数を確認
python3 sync_ec_brands.py --check --workers 10                    # 全515社のヒット件数
python3 update.py --config sources-ec-brands.json                 # この1枚だけ更新
```

- 名寄せ: 完全一致の重複除去（1,045行 → 540社）→ 法人格・空白・記号の違いを統合（`株式会社_mtc` /
  `株式会社mtc.`）→ 英字とカタカナの表記ゆれを `ALIASES` で統合（`BreakingDown` / `ブレイキングダウン`、
  `LINE` / `LINEヤフー株式会社`、`株式会社DeNA` / `株式会社ディー・エヌ・エー` など）→ **515社**。
- ノイズ対策: `3well`（スズキのグレード名）、`S&K`（ボクシング）、`TTT`（自転車競技・スニーカー）のような
  短い英字名・一般語は自動で `context_match`（EC／D2C／通販／製薬／マーケの語）を付け、
  `株式会社AX` `株式会社NR` `one株式会社` のように法人格込みで検索したほうが正確な名前は
  クエリを `"株式会社◯◯"` にする（`FULL_NAME_ONLY` / `OVERRIDES`）。
- 515社中381社が記事あり・134社が0件（0件もエラーにせず「新しい記事はありません」と表示）。

### SmartNews 認定代理店ボード / Shopify 受賞パートナーボード

Excel から起こした「1社＝1ソース」のボードです。元データを直したら同期スクリプトを流し直すだけで、
監視対象が追随します（スクリプトは標準ライブラリだけで Excel を読みます）。

```bash
python3 sync_smartnews_partners.py --check   # 2026年＋2025年の認定企業90社 → sources-smartnews-partners.json
python3 sync_shopify_partners.py --check     # 2022〜2025年の受賞パートナー25社 → sources-shopify-partners.json
python3 update.py --board smartnews-partners,shopify-partners   # この2枚だけ更新
```

- SmartNews 側は 2026年の認定企業62社と、2026年はランク外になった2025年認定企業28社の計90社。
  社名非公表の行は除外し、`株式会社WEB GRAM`→`WEBGRAM` などの表記ゆれは `ALIASES` で統合しています。
- Shopify 側は受賞回数の多い順（R6B が3年連続、ほか4社が2回）に並べた25社。
- 「無限」「ブリーチ」「ヴァンクリーフ」「タイズ」「バローズ」のような一般語・同名他社と衝突する社名は、
  `OVERRIDES` でクエリを「株式会社〈社名〉」に絞り、`context_match`（広告／EC 業界の語）で別物を落としています。
  Shopify 側は「Stack」「Tsun」「AMS」のような短い英字名が対象です。
- 記事0件の社はエラーにせず「新しい記事はありません」と表示し、`tolerate_errors: true` で他ボードの更新を止めません。

```jsonc
// sources-ec-companies.json の1社ぶん
{
  "id": "gn_02",
  "type": "google_news",
  "name": "株式会社そばに",
  "query": "\"株式会社そばに\"",
  "must_match": ["株式会社そばに", "そばにの", "そばにが"],
  "context_match": ["EC", "通販", "楽天", "…"],
  "limit": 5
}
```

### SNS・広告代理店ボード / 広告・マーケ Xボード

```bash
python3 sync_sns_agencies.py --check      # 38社ぶんの設定を作り直し、各社のヒット件数も表示
python3 sync_sns_agencies.py --list       # 登録社名の一覧
python3 add_source.py --bulk --config sources-ad-marketing.json --title "広告・マーケ X監視ボード" \
  --nav-label "広告・マーケ" --text "名前 @handle ..."   # X アカウントのボードを作る／足す
```

- 監視する企業は `sync_sns_agencies.py` の `COMPANIES` に並べるだけ。`--check` で
  「どの社が何件ヒットしているか」を確認でき、ノイズが多い社は `OVERRIDES` に1行足して
  `query` を「株式会社〈社名〉」に絞る／`context_match`（広告・マーケの語）を付ける。
- 「ブリーチ」「ベクトル」「オプト」「ナハト」のような一般語や、「UUUM」「DYM」「CATS」
  「studio15」のような短い英字名は、同名他社（美容のブリーチ、猫の CATS など）が混ざるので
  必ず `must_match` / `context_match` で絞る。

## 1社を複数の取得元で補完する（3ソース併用）

Googleニュースの社名検索だけでは、プレスリリースを出さない会社・小さい会社の「2026年の記事」が
0件〜数件になりがちです。そういう社には、1つのカードに追加の取得元を足して合算表示できます。

| フィールド | 中身 | 例 |
| --- | --- | --- |
| `extra_feeds` | PR TIMES の企業RSS（社名がタイトルに無くて当然なので `must_match` を通さない） | `https://prtimes.jp/companyrdf.php?company_id=69527` |
| `html_news` | 公式サイトの「お知らせ／ニュース」ページ（HTMLから日付・タイトル・リンクを抽出） | `https://www.herb-kenko.com/news/` |

- 1ソース内の複数取得元は **まとめて重複除去（同じリリース・同じ見出し）→ 日付の新しい順** で `limit` 件。
- `html_news` は日付だけの表示（時刻は出さない）で、バッジは `公式`、PR TIMES は `PRT`。
- 公式サイトの事務連絡（配送遅延・休業・採用・ブログ更新など）は `max_routine_items`（既定2件）までに抑え、
  発売・キャンペーン・受賞などの本題が枠を埋められるようにしています。
- どの取得元を足すかは `news-supplements.json` にボード別・ソースID別で持ち、
  同期スクリプト（`sync_ec_mailorder.py` など）が `news_supplements.apply_supplements()` で毎回同じ内容を反映します。
- 取得に失敗した取得元があっても、他の取得元と他の社は止めません（`tolerate_errors: true`）。

```jsonc
// news-supplements.json（ec-mailorder の1社ぶん）
"ec-mailorder": {
  "ecm_07": {
    "extra_feeds": [{ "url": "https://prtimes.jp/companyrdf.php?company_id=69527", "origin": "prtimes" }],
    "html_news":  [{ "url": "https://shop.formalklein.com/shop/information_categories/news", "origin": "site" }]
  }
}
```

補完先を増やすときは、PR TIMES のキーワード検索で社名一致の企業IDを探し、
**同名他社でないこと**（リリース本文の「会社HP」や業種語、公式サイトの `<title>`／会社概要）を確認してから足します
（例：「イング」= 東京のアンケートSaaS、「ナチュラルガーデン」= 和歌山のドーナツ店は別会社なので足していません）。

## 和訳

本文にひらがな・カタカナがなく、ラテン文字が10字以上ある投稿は外国語とみなし、
Google 翻訳（公開エンドポイント）→ MyMemory の順で日本語に訳して `title_ja` に持ちます。
失敗してもボードは普通に作られます（その投稿は和訳なし）。設定は `sources*.json` の `settings`:

- `"translate": false` … 和訳しない
- `"translate_max_per_run": 120` … 1回の実行で新規に訳す上限
- `"translate_workers": 6` / `"fetch_workers": 8` … 並列数

## 監視対象の増減

```bash
python3 add_source.py x nBwQSzmg3qU2ysd "いちか | Ichika"      # X
python3 add_source.py youtube UCxxxxxxxxxxxx "チャンネル名"     # YouTube
python3 add_source.py note someuser "noteのアカウント"          # note
python3 add_source.py news https://example.com/feed.xml "サイト名"  # ニュース(RSS)
python3 add_source.py google_news "site:news.example.com" "exampleニュース"

python3 add_source.py --list             # 一覧
python3 add_source.py --remove x_tkzwgrs # 削除
python3 add_source.py ... --disabled     # 追加するが取得はしない
python3 add_source.py --config sources-ipo.json x MelvinInvests "Melvin"   # ボードを指定して追加
```

`update.py --add ...` でも同じことができます（例: `python3 update.py --add x tkzwgrs "滝沢ガレソ"`）。

### まとめて追加（貼り付けから / `--bulk`）

Xのアカウント一覧（`表示名 @handle` をスペース・タブ・改行で並べたもの）を貼り付けるだけで、
まとめて登録できます。JSONを手で書く必要はありません。

```bash
# 追加される内容だけ確認（ファイルは書き換えない）
python3 add_source.py --bulk --config sources-ai.json --dry-run \
  --text "鈴木 @ai_300 おかぴ🦒⚡️｜AIニュース解説 @okapi_fukugyo Codex研究ラボ @codexneo4"

# 貼り付けた内容で追加（登録済みのハンドルは自動でスキップ）
pbpaste | python3 add_source.py --bulk --config sources-ai.json

# 追加して、そのボードをすぐ更新
python3 add_source.py --bulk --config sources-ai.json --update < accounts.txt

# 新しいボードをこの1行から作る（sources-<名前>.json が無ければ settings も自動で作る）
python3 add_source.py --bulk --config sources-crypto.json \
  --title "仮想通貨監視ボード" --nav-label "暗号資産" --text "名前 @handle ..."
```

- 読み取れる形: `表示名 @handle` / `@handle` だけ / `https://x.com/handle`。区切りはスペース・タブ・改行のどれでもOK（`名前（@handle）` も可）。
- 登録済みのハンドルはスキップし、`追加 N / 登録済み M` を表示します（何度流しても増えません）。
- `--config` のファイルが無いときは `settings`（`title` / `nav_label` / `items_per_source: 15` / `translate: true` / `translate_max_per_run: 120` / nitter のミラー）と空の `sources` を作ります。
- `--dry-run` は書き込まずに予定だけ表示、`--update` は追加後に `update.py --config <そのボード>` を実行します。

### sources.json の書き方

```jsonc
{
  "settings": {
    "title": "監視ボード",
    "items_per_source": 15,      // 1ソースあたりの最大件数
    "timeout_seconds": 20,
    "retries": 1,
    "pinned_first": true,        // 固定ポストを先頭に置く
    "nitter_base": "https://nitter.cf",
    "nitter_mirrors": ["https://nitter.cf", "..."]   // X の予備ミラー
  },
  "sources": [
    { "id": "x_ichika", "type": "x", "name": "いちか | Ichika", "handle": "nBwQSzmg3qU2ysd", "enabled": true },
    { "id": "yahoo", "type": "news", "name": "Yahoo!ニュース 国内・経済",
      "feeds": ["https://news.yahoo.co.jp/rss/topics/domestic.xml",
                "https://news.yahoo.co.jp/rss/topics/business.xml"],
      "enabled": true },
    { "id": "livedoor", "type": "google_news", "name": "ライブドアニュース",
      "query": "site:news.livedoor.com", "enabled": true },
    { "id": "yt", "type": "youtube", "name": "チャンネル名", "channel_id": "UC...", "enabled": false },
    { "id": "note", "type": "note", "name": "noteのアカウント", "username": "someuser", "enabled": false }
  ]
}
```

- `type` は `x` / `youtube` / `note` / `news` / `google_news`。`news` は `feed`（1本）でも `feeds`（複数まとめて1カード）でも可。
- `x` は `https://nitter.cf/<handle>/rss` を読み、リンクは `https://x.com/<handle>/status/...` に直して出力します（スマホでそのまま開けるように）。
- `id` は任意。省略時は `x_<handle>` などから自動で作られます。
- `limit` をソースに書くと、そのソースだけ件数を変えられます。

## GitHub Pages で公開する

1. GitHub で新しいリポジトリを作る（例: `monitor-board`）。
2. `monitor-board/` の中身を **リポジトリ直下** に置いて push する
   （`.github/workflows/update.yml` がリポジトリ直下の `.github/workflows/` に来るように）。
3. リポジトリの **Settings → Pages** で Source を `Deploy from a branch` / Branch `main` / フォルダ `/ (root)` に設定。
4. **Actions** タブで `Update monitor board` を選び **Run workflow** を1回手で実行。
5. 数十秒で `https://<ユーザー名>.github.io/<リポジトリ名>/` にボードが出ます。以後は毎時 :07 に自動更新。

> Actions の `GITHUB_TOKEN` で push するため、追加の Secrets 設定は不要です。
> リポジトリの **Settings → Actions → General → Workflow permissions** が
> "Read and write permissions" になっていることを確認してください。

## うまく取れないとき

- **X が 0 件**: nitter のミラーが落ちている可能性があります。`sources.json` の
  `nitter_mirrors` に生きているミラーを足してください（例: `https://xcancel.com`）。
  各ソースの結果は `python3 update.py -v` で確認できます。
- **ライブドアニュース**: livedoor 公式の RSS (`news.livedoor.com/topics/rss/top.xml`) は
  2026年に配信終了（404）しました。現在は Google ニュースの `site:news.livedoor.com`
  検索フィードで代替しています（タイトル末尾の「 - ライブドアニュース」等は自動で削除）。
- 取得に失敗したソースは、カードごと消さずに「取得エラー」と理由を表示します。
  他のソースはそのまま更新されます。
