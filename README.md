# road-news-watcher — 道路・交通モニター記事の自動収集

対象32か国の道路・交通関連ニュースを毎朝自動で集め、国・分類（27項目）を付けて
**メール（Excel添付）** と **閲覧画面（GitHub Pages）** で届けます。
担当者は閲覧画面で「様式化する記事」にチェックを付け、CSVで書き出して様式作成に進みます。

```
GitHub Actions（毎朝 6:17）
  ├─ Googleニュース検索：国ごとに 英語3本＋日本語1本（仏語圏は仏語1本も）＋ NNA・ADB・AfDB 等の横断検索
  ├─ 媒体RSS：VnExpress / Kathmandu Post / Construction Review / Turkish Minute / AllAfrica 各国 ほか
  ├─ 重複除去 → 国・分類の判定 → 関連度スコア → 足切り（既定 4 点未満は除外）
  ├─ （任意）Claude API で見出しを和訳
  └─ 出力：data/articles.json（120日分）、data/archive/YYYY-MM.csv（月別の全記録）
          docs/data/articles.js（閲覧画面用・60日分）、Excel（メール添付＋Actions成果物）
```

## セットアップ（procurement-watcher と同じ手順です）

1. GitHub に新しいリポジトリ（例：`yuhigIDI/road-news-watcher`）を作り、このフォルダの中身をそのまま push
2. **Settings → Secrets and variables → Actions → Secrets** に登録
   | 名前 | 内容 | 必須 |
   |---|---|---|
   | `SMTP_USER` | 送信元 Gmail アドレス | メール通知する場合 |
   | `SMTP_PASS` | Gmail のアプリパスワード | 〃 |
   | `MAIL_TO` | 宛先（複数はカンマ区切り） | 〃 |
   | `ANTHROPIC_API_KEY` | Claude API キー（見出し和訳用） | 任意 |
3. **Variables** に登録（任意）
   | 名前 | 内容 |
   |---|---|
   | `VIEWER_URL` | 閲覧画面のURL（メールにリンクを載せる） |
   | `MIN_SCORE` | 足切りの関連度（既定 4。件数が多ければ 5〜6 に） |
4. **Settings → Pages** で Source を「Deploy from a branch」、ブランチ `main`・フォルダ `/docs` に設定
   → `https://yuhigidi.github.io/road-news-watcher/` が閲覧画面になります
   （非公開リポジトリで Pages を使えないプランの場合は、`docs/index.html` をダウンロードして
   `docs/data/articles.js` と同じ構成で置けば、ブラウザでそのまま開けます）
5. **Actions → collect-monitor-articles → Run workflow** で初回実行。
   初回だけ過去分も拾いたい場合は `config/sources.yaml` の `lookback_days` を 7 にして実行し、後で 3 に戻す

## 調整のしかた（コードを触らずに設定ファイルだけで可）

| やりたいこと | 編集するファイル |
|---|---|
| 対象国の追加・削除、国の判定語（都市名・道路機関略称）の追加 | `config/countries.yaml` |
| 分類キーワードの追加・削除、ノイズ除外の表現 | `config/keywords.yaml` |
| 検索式、RSS媒体の追加、優先媒体（加点） | `config/sources.yaml` |
| 現地語（ベトナム語・タイ語等）での検索を足す | `countries.yaml` の国に `extra_queries: [{q: '"cao tốc"', hl: vi, gl: VN}]` を追加（和訳オプション併用推奨） |

関連度スコアの内訳：見出しの道路・交通語（最大6点）＋分類の該当数（最大5点）＋国名が見出しにある（2点）＋優先媒体（3点）など。

## 手元で試す

```bash
pip install -r requirements.txt
python -m watcher --countries VN,IN --no-mail     # ベトナム・インドだけで試走
python -m watcher --fixture tests/fixture_gn.xml --no-mail   # ネット接続なしの動作確認
```

## 注意点

- Googleニュース経由の記事URLは `news.google.com/...` の転送URLです（開くと元記事に飛びます）。
  媒体RSSでも同じ記事を拾った場合は元URLに置き換えます
- NNA など会員制媒体は見出しとURLのみ取得します（本文は取得しません）
- 収集するのは見出し・概要・URLだけで、記事本文の転載はしていません。様式作成時の引用は従来どおりの扱いで
- 媒体RSSのURLは媒体側の変更で読めなくなることがあります。読めない場合はメール末尾とActionsのログに表示され、他の収集は続行します
