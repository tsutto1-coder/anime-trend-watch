# ANIME TREND WATCH

「いま伸びているアニメ公式映像を毎週届ける速報」

週1回、手動実行で、YouTube上で話題のアニメ公式映像(本PV・ティザーPV・予告・特報・ノンクレジットOP/ED・アニメ化発表映像など)を自動収集・AI分析し、**統合TOP10ランキング**とSNS投稿文・縦型動画を生成するツールです。IDOL TREND WATCHと同じ基盤・同じ運用です。

## 重要な設計方針

- **作品の人気投票ではなく、公式映像の話題度ランキング**です
- 画像・動画・サムネイルは一切保存せず、**公式チャンネルへのリンクのみ**掲載
- AIコメントは映像・音楽・演出・期待ポイントについてのみ。**ネタバレ(PV公開範囲を超える展開)は書かない**方針をプロンプトで制御
- ファンMAD・AMV・切り抜き・ゲームPV・VTuberは対象外(AI判定。誤判定はありえるので投稿前チェック必須)

## 週1回の生成物(outputs/日付/)

- digest.txt … 確認用サマリー(TOP10+連続再生リンク)
- x.txt / threads.txt / instagram.txt / note.md … 投稿文(1位→3位詳細+4〜10位一覧)
- reel.mp4(9:16)/ feed.mp4(4:5)… ランキング動画。背景は季節で自動変化
- 保存先: GitHubの outputs/ と Googleドライブ「ANIME TREND WATCH/日付」

## セットアップ

IDOL TREND WATCHと同一手順です。新しいGitHubリポジトリ(例: anime-trend-watch)を作成し、このフォルダの中身をアップロード。Secretsは**既存2メディアと同じ値を再利用できます**:

- YOUTUBE_API_KEY / ANTHROPIC_API_KEY
- GDRIVE_CLIENT_ID / GDRIVE_CLIENT_SECRET / GDRIVE_REFRESH_TOKEN
- DISCORD_WEBHOOK_URL(任意)

実行は Actions →「ANIME TREND WATCH Weekly」→「Run workflow」(手動のみ。自動スケジュールなし)。

動作確認: `python anime_trend_watch.py --demo` → `python make_video.py`(架空作品のデータで出力されます)

## 運用上の注意

- 収集範囲は「直近7日の新着+既出除外」。**週1回以上**実行していれば取りこぼしは起きません
- アニメ公式映像は改編期(1月・4月・7月・10月)に集中します。期首は候補が溢れ、期中は少なめになるのが正常です
- 誤判定(ゲームPV・ファン動画の混入)に備え、投稿前のdigest.txt目視チェックを必ず行ってください
- YouTube API無料枠は3メディア合算でも余裕(1回約800ユニット×頻度)。Claude API費用は週1回なら月数十円規模
