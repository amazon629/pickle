# 監視ボード

X・YouTube・note・ニュースを1枚にまとめる、スマホ向けの監視ボードです。
`update.py` が各フィードを取得して `index.html`（＋`data.json`）を作り直します。
GitHub Actions が1時間ごとに自動で更新するので、一度置けば放っておけます。

- 画面: グレー背景 `#f2f3f5` に白い角丸カード、カード上部にソース名と件数、各項目は バッジ / タイトル（タップで元記事へ）/ ドメイン / 時刻（JST）
- 新着（前回取得時になかった記事）は薄い緑の行と赤い点で表示
- 上部の検索欄で、表示中の全ソースを横断して絞り込み
- 依存ライブラリなし（Python 3.9+ の標準ライブラリだけ）／`index.html` はデータを埋め込んだ単体ファイルなので、ローカルでダブルクリックでも開けます

## ファイル

| ファイル | 役割 |
| --- | --- |
| `sources.json` | 監視対象の設定（ここだけ触れば増減できる） |
| `update.py` | 取得 → `index.html` / `data.json` を生成 |
| `add_source.py` | 監視対象を1行で追加／削除 |
| `index.html` | 生成物（GitHub Pages が公開するページ） |
| `data.json` | 生成物（アプリや他ツールから読む用） |
| `.github/workflows/update.yml` | 1時間ごとの自動更新 |

## すぐ使う

```bash
cd monitor-board
python3 update.py          # 取得して index.html / data.json を更新
python3 update.py -v       # どのフィードが何件取れたかを表示
python3 update.py --dry-run -v   # 取得だけしてファイルは書かない
```

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
```

`update.py --add ...` でも同じことができます（例: `python3 update.py --add x tkzwgrs "滝沢ガレソ"`）。

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
