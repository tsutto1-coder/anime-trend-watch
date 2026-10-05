#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ANIME TREND WATCH
「いま伸びているアニメ公式映像を毎週届ける速報メディア」

※作品の優劣を序列化するものではなく、公式映像(本PV・予告・特報・
　ノンクレジットOP/ED等)の話題度をデータで集計するランキングです。
※画像・動画・サムネイルは一切保存せず、公式チャンネルへのリンクのみ掲載。

使い方:
  環境変数 YOUTUBE_API_KEY, ANTHROPIC_API_KEY を設定して
    python anime_trend_watch.py
  APIキーなしで出力フォーマットを確認したい場合:
    python anime_trend_watch.py --demo
"""

import argparse
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ============================================================
# 設定(必要に応じてここを編集)
# ============================================================

CONFIG = {
    # YouTube検索クエリ(この語で新着動画を横断検索する)
    "search_queries": [
        "アニメ PV", "アニメ 本PV", "TVアニメ PV", "アニメ 予告",
        "アニメ 特報", "ノンクレジットOP", "ノンクレジットED",
        "アニメ ティザーPV", "劇場アニメ 予告", "アニメ化 発表",
    ],
    # 何日以内に公開された動画を対象にするか
    "lookback_days": 7,
    # 掲載保持期間(3か月)
    "retention_days": 92,
    # AI分析に回す最大候補数(話題性スコア上位から)
    "max_candidates": 30,
    # 最終ランキング件数
    "top_n": 10,
    # ランキング区分(統合1本)
    "categories": [("all", "アニメ")],
    # 動画の長さ制限(秒)。PV・予告・NCOP/EDは15秒〜8分程度
    "min_duration_sec": 15,
    "max_duration_sec": 480,
    # Claudeモデル(コスト重視なら claude-haiku-4-5-20251001)
    "claude_model": "claude-sonnet-4-6",
    # ハッシュタグ
    "hashtags": "#アニメ #新作アニメ #PV #アニメ好きと繋がりたい #ランキング",
    # メディア名
    "brand": "ANIME TREND WATCH",
    "tagline": "いま伸びているアニメ公式映像を毎週届ける速報",
}

JST = timezone(timedelta(hours=9))
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUT_DIR = BASE_DIR / "outputs"
ARCHIVE_FILE = DATA_DIR / "archive.json"   # 掲載済みコンテンツの蓄積(3か月ルールに使用)
SEEN_FILE = DATA_DIR / "seen.json"         # 取り上げ済み動画ID(重複掲載防止)

MEDAL = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

# ============================================================
# ユーティリティ
# ============================================================

def http_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "cm-trend-watch/1.0"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def http_post_json(url: str, payload: dict, headers: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8")[:600]
        except Exception:
            detail = "(詳細取得不可)"
        hint = ""
        if e.code == 400 and ("credit" in detail or "billing" in detail.lower()):
            hint = "\n→ Anthropic APIのクレジット残高切れの可能性が高いです。" \
                   "platform.claude.com のBillingで残高を確認・チャージしてください。"
        raise RuntimeError(f"APIエラー HTTP {e.code} ({url}):\n{detail}{hint}") from None


def parse_iso_duration(s: str) -> int:
    """ISO8601 (PT1M30S) → 秒"""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0
    h, mi, sec = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + sec


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def jst_now() -> datetime:
    return datetime.now(JST)

# ============================================================
# 1. YouTube収集
# ============================================================

def yt_search(api_key: str, query: str, published_after: str) -> list[str]:
    params = urllib.parse.urlencode({
        "part": "id",
        "q": query,
        "type": "video",
        "regionCode": "JP",
        "relevanceLanguage": "ja",
        "publishedAfter": published_after,
        "order": "viewCount",
        "maxResults": 25,
        "key": api_key,
    })
    data = http_get_json(f"https://www.googleapis.com/youtube/v3/search?{params}")
    return [it["id"]["videoId"] for it in data.get("items", []) if it.get("id", {}).get("videoId")]


def yt_videos(api_key: str, ids: list[str]) -> list[dict]:
    out = []
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        params = urllib.parse.urlencode({
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(chunk),
            "key": api_key,
        })
        data = http_get_json(f"https://www.googleapis.com/youtube/v3/videos?{params}")
        out.extend(data.get("items", []))
    return out


CM_TITLE_HINTS = re.compile(r"(PV|予告|特報|ティザー|ノンクレジット|NCOP|NCED|アニメ|ANIME|OPムービー|EDムービー|劇場版|TVアニメ)", re.IGNORECASE)


def collect_candidates(api_key: str, seen_ids: set[str]) -> list[dict]:
    published_after = (jst_now() - timedelta(days=CONFIG["lookback_days"])) \
        .astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    ids: list[str] = []
    for q in CONFIG["search_queries"]:
        try:
            ids += yt_search(api_key, q, published_after)
        except Exception as e:
            print(f"[warn] search failed for '{q}': {e}", file=sys.stderr)
    ids = [i for i in dict.fromkeys(ids) if i not in seen_ids]  # 重複除去+既出除外
    if not ids:
        return []

    videos = yt_videos(api_key, ids)
    now = jst_now()
    candidates = []
    for v in videos:
        sn, st = v.get("snippet", {}), v.get("statistics", {})
        dur = parse_iso_duration(v.get("contentDetails", {}).get("duration", ""))
        title = sn.get("title", "")
        if not (CONFIG["min_duration_sec"] <= dur <= CONFIG["max_duration_sec"]):
            continue
        if not CM_TITLE_HINTS.search(title + " " + sn.get("description", "")[:200]):
            continue

        published = datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00")).astimezone(JST)
        days = max((now - published).total_seconds() / 86400, 0.25)
        views = int(st.get("viewCount", 0))
        likes = int(st.get("likeCount", 0) or 0)
        comments = int(st.get("commentCount", 0) or 0)

        # 話題性スコア: 1日あたり再生数 × エンゲージメント補正(対数で暴れを抑制)
        vpd = views / days
        engagement = (likes + comments * 2) / max(views, 1)
        buzz = math.log10(vpd + 1) * (1 + min(engagement * 20, 1.0))

        candidates.append({
            "video_id": v["id"],
            "title": title,
            "channel": sn.get("channelTitle", ""),
            "description": sn.get("description", "")[:600],
            "published_at": published.strftime("%Y-%m-%d"),
            "duration_sec": dur,
            "views": views,
            "likes": likes,
            "comments": comments,
            "views_per_day": round(vpd),
            "buzz_score": round(buzz, 3),
            "url": f"https://www.youtube.com/watch?v={v['id']}",
        })

    candidates.sort(key=lambda c: c["buzz_score"], reverse=True)
    return candidates[:CONFIG["max_candidates"]]

# ============================================================
# 2. Claude分析(CM判定・情報抽出・一言分析・5軸評価)
# ============================================================

ANALYSIS_SYSTEM = """あなたはアニメ専門メディア「ANIME TREND WATCH」の編集AIです。
YouTube動画のメタデータ(タイトル・チャンネル名・説明文・統計)から、アニメ作品の
公式映像かどうかを判定し、掲載情報を抽出します。

判定基準(is_official_anime=true の条件):
- アニメ作品の公式チャンネル(製作委員会・レーベル・配給・放送局公式含む)の投稿
- 本PV、ティザーPV、予告、特報、ノンクレジットOP/ED、CM、場面カット映像、
  アニメ化発表映像など、アニメ作品そのものの公式映像
- TVアニメ・劇場アニメ・配信アニメいずれも対象
- ファンによるMAD・AMV・切り抜き・考察・リアクション動画は false
- ゲーム・VTuber・実写映画・声優個人チャンネルの動画は false(アニメ作品の映像のみ)
- 主題歌アーティストのMVは、アニメ映像を使用した公式タイアップMVのみ true

編集ポリシー(重要):
- コメントは映像・音楽・演出・制作陣・期待ポイントについて書く
- 本編の展開・結末に触れるネタバレは書かない(PVで公開されている範囲まで)
- 声優・スタッフ個人への言及は公式発表の事実の範囲にとどめる

各動画について以下のJSONを返してください。確認できないことは推測せず null に。

出力は必ず次の形式のJSON配列のみ。前置き・後書き・コードブロック記号は一切不要:
[
  {
    "video_id": "...",
    "is_official_anime": true/false,
    "company": "作品タイトル(正式名称寄りに。『』は付けない)",
    "product": "映像の種別(本PV第2弾 / ティザーPV / ノンクレジットOP / 特報 など) or null",
    "category": "anime",
    "cast": "主題歌アーティスト名(説明文に明記がある場合のみ) or null",
    "media": "TV" / "劇場" / "配信" など判別できる範囲 or null,
    "keywords": ["ファンタジー", "作画"] (2〜4語。ジャンルや見どころ),
    "hitokoto": "15〜40文字の一言分析(映像・音楽・期待ポイントを編集者目線で)",
    "ratings": {"話題性": 1-5, "SNS拡散性": 1-5, "期待度": 1-5,
                 "映像インパクト": 1-5 or null}
  }
]"""


def parse_json_array_lenient(text: str) -> list[dict]:
    """Claudeの返答からJSON配列を取り出す。
    途中で切れていても、完成しているオブジェクトだけ救出する。"""
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass  # 下の救出処理へ
    # 完全なJSONオブジェクト({...})を先頭から順に拾えるだけ拾う
    dec = json.JSONDecoder()
    objs, i = [], text.find("{")
    while i != -1:
        try:
            obj, consumed = dec.raw_decode(text[i:])
            if isinstance(obj, dict) and obj.get("video_id"):
                objs.append(obj)
            i = text.find("{", i + consumed)
        except json.JSONDecodeError:
            i = text.find("{", i + 1)
    if not objs:
        raise RuntimeError("Claudeの返答からJSONを取り出せませんでした:\n" + text[:500])
    print(f"[warn] JSONが不完全だったため {len(objs)} 件を救出して続行します", file=sys.stderr)
    return objs


def analyze_with_claude(candidates: list[dict]) -> list[dict]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY が設定されていません")

    payload_videos = [
        {k: c[k] for k in ("video_id", "title", "channel", "description",
                           "published_at", "duration_sec", "views", "views_per_day", "likes")}
        for c in candidates
    ]
    resp = http_post_json(
        "https://api.anthropic.com/v1/messages",
        {
            "model": CONFIG["claude_model"],
            "max_tokens": 10000,
            "system": ANALYSIS_SYSTEM,
            "messages": [{
                "role": "user",
                "content": "以下の動画リストを分析してください:\n"
                           + json.dumps(payload_videos, ensure_ascii=False),
            }],
        },
        {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
    )
    text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
    analyses = {a["video_id"]: a for a in parse_json_array_lenient(text)}

    results = []
    for c in candidates:
        a = analyses.get(c["video_id"])
        if a and a.get("is_official_anime"):
            results.append({**c, **a})
    return results

# ============================================================
# 3. 投稿文生成
# ============================================================

def fmt_views(n: int) -> str:
    return f"{n/10000:.1f}万" if n >= 10000 else f"{n:,}"


def stars(v) -> str:
    return "★" * int(v) + "☆" * (5 - int(v)) if v else "—"


def playlist_url(top: list[dict]) -> str:
    """TOP全件をYouTube上で連続自動再生するURL"""
    ids = ",".join(c["video_id"] for c in top)
    return f"https://www.youtube.com/watch_videos?video_ids={ids}"


def rank_label(i: int) -> str:
    medals = {0: "🥇第1位", 1: "🥈第2位", 2: "🥉第3位"}
    return medals.get(i, f"{i + 1}位")


def podium_order(top: list[dict]) -> list[int]:
    """発表順: 1位→2位→3位(存在するものだけ)"""
    return [i for i in (0, 1, 2) if i < len(top)]


def x_section(top: list[dict], label: str) -> list[str]:
    n = len(top)
    lines = [f"◤ 今週の{label} TOP{n} ◢",
             "",
             "今日いちばん伸びているのは…🧵",
             "",
             CONFIG["hashtags"],
             "\n--- ↓スレッドに続ける ---\n"]
    for i in podium_order(top):
        c = top[i]
        prod = f"「{c['product']}」" if c.get("product") else ""
        lines.append(f"{rank_label(i)} {c['company']}{prod}")
        lines.append(f"📅 {c['published_at']}公開 / ▶️ {fmt_views(c['views'])}回再生")
        lines.append(f"💬 {c['hitokoto']}")
        lines.append(c["url"])
        lines.append("\n---\n")
    rest = top[3:]
    if rest:
        lines.append(f"続いて4位〜{n}位はこちら👇")
        lines.append("")
        for i, c in enumerate(rest, start=4):
            prod = f"「{c['product']}」" if c.get("product") else ""
            lines.append(f"{i}位 {c['company']}{prod}")
            lines.append(c["url"])
        lines.append("\n(長い場合は2ツイートに分割してください)")
    return lines


def render_x(rankings: dict, date_s: str) -> str:
    blocks = [f"【{date_s}】"]
    for key, label in CONFIG["categories"]:
        top = rankings.get(key) or []
        if not top:
            continue
        blocks += x_section(top, label)
        blocks.append("\n========【ここから別スレッド】========\n")
    return "\n".join(blocks)


def threads_section(top: list[dict], label: str) -> list[str]:
    n = len(top)
    lines = [f"🎬 今週の{label} TOP{n}", ""]
    for i in podium_order(top):
        c = top[i]
        prod = f"「{c['product']}」" if c.get("product") else ""
        lines.append(f"{rank_label(i)} {c['company']}{prod}")
        lines.append(f"　{c['hitokoto']}")
        lines.append(f"　{c['url']}")
        lines.append("")
    rest = top[3:]
    if rest:
        lines.append(f"— 4位〜{n}位 —")
        for i, c in enumerate(rest, start=4):
            prod = f"「{c['product']}」" if c.get("product") else ""
            lines.append(f"{i}位 {c['company']}{prod} {c['url']}")
        lines.append("")
    lines.append(f"🎬 全部まとめて見る→ {playlist_url(top)}")
    lines.append("")
    return lines


def render_threads(rankings: dict, date_s: str) -> str:
    blocks = [f"({date_s})"]
    for key, label in CONFIG["categories"]:
        top = rankings.get(key) or []
        if not top:
            continue
        blocks += threads_section(top, label)
        blocks.append("========【ここから別投稿】========\n")
    blocks.append(f"※{CONFIG['tagline']}")
    return "\n".join(blocks)


def ig_section(top: list[dict], label: str, date_s: str) -> list[str]:
    n = len(top)
    out = [f"■ {label}用カルーセル原稿",
           "",
           "── スライド1(表紙) ──",
           f"今週の{label} TOP{n}",
           date_s,
           CONFIG["brand"], ""]
    slide_no = 2
    for i in podium_order(top):
        c = top[i]
        prod = f"「{c['product']}」" if c.get("product") else ""
        kw = " / ".join(c.get("keywords") or [])
        r = c.get("ratings") or {}
        out += [f"── スライド{slide_no}({rank_label(i)}) ──",
                f"{rank_label(i)} {c['company']}{prod}",
                f"公開日: {c['published_at']}",
                f"キーワード: {kw}",
                f"話題性 {stars(r.get('話題性'))}  拡散性 {stars(r.get('SNS拡散性'))}",
                f"見どころ: {c['hitokoto']}", ""]
        slide_no += 1
    rest = top[3:]
    if rest:
        out.append(f"── スライド{slide_no}(4位〜{n}位一覧) ──")
        for i, c in enumerate(rest, start=4):
            prod = f"「{c['product']}」" if c.get("product") else ""
            out.append(f"{i}位 {c['company']}{prod}")
        out.append("")
        slide_no += 1
    out += [f"── スライド{slide_no}(最終) ──",
            "リンクはプロフィール・キャプションから",
            "毎週更新", "",
            "── キャプション ──",
            f"今週の{label} TOP{n}({date_s})", ""]
    for i, c in enumerate(top):
        out.append(f"{i + 1}位 {c['company']} → {c['url']}")
    out += ["", CONFIG["hashtags"] + " #春アニメ #覇権アニメ", ""]
    return out


def render_instagram(rankings: dict, date_s: str) -> str:
    blocks = []
    for key, label in CONFIG["categories"]:
        top = rankings.get(key) or []
        if not top:
            continue
        blocks += ig_section(top, label, date_s)
        blocks.append("=" * 40)
        blocks.append("")
    return "\n".join(blocks)


def note_section(top: list[dict], label: str) -> list[str]:
    n = len(top)
    lines = [f"# {label} TOP{n}", ""]
    for i in podium_order(top):
        c = top[i]
        prod = f"「{c['product']}」" if c.get("product") else ""
        r = c.get("ratings") or {}
        lines += [f"## {rank_label(i)} {c['company']}{prod}", "",
                  f"- 公開日: {c['published_at']}",
                  f"- 再生数: {fmt_views(c['views'])}回(1日あたり約{fmt_views(c['views_per_day'])}回)"]
        if c.get("media"):
            lines.append(f"- 種別: {c['media']}")
        if c.get("keywords"):
            lines.append(f"- キーワード: {' / '.join(c['keywords'])}")
        lines += ["",
                  f"**一言分析**: {c['hitokoto']}", "",
                  "|話題性|SNS拡散性|期待度|映像インパクト|",
                  "|---|---|---|---|",
                  f"|{stars(r.get('話題性'))}|{stars(r.get('SNS拡散性'))}"
                  f"|{stars(r.get('期待度'))}|{stars(r.get('映像インパクト'))}|", "",
                  f"▶️ [公式チャンネルで見る]({c['url']})", ""]
    rest = top[3:]
    if rest:
        lines += [f"## 4位〜{n}位", ""]
        for i, c in enumerate(rest, start=4):
            prod = f"「{c['product']}」" if c.get("product") else ""
            lines.append(f"**{i}位 {c['company']}{prod}** — {c.get('hitokoto', '')}")
            lines.append(f"　▶️ [公式チャンネルで見る]({c['url']})")
            lines.append("")
    lines.append(f"🎬 [{label} TOP{n}を連続再生でまとめて見る]({playlist_url(top)})")
    lines.append("")
    return lines


def render_note(rankings: dict, date_s: str) -> str:
    lines = [f"# 【{date_s}】今週のアニメ公式映像TOP10|{CONFIG['brand']}", "",
             f"{CONFIG['tagline']}。",
             "YouTube上の新着アニメ公式映像(本PV・予告・ノンクレジットOP/EDなど)を再生数の伸び・エンゲージメントで"
             "自動集計し、AIが分析した週間ランキングです。集計対象は直近7日の新着。人気投票ではなく、"
             "コンテンツの話題度をデータで測る趣旨のランキングです。"
             "映像は各公式チャンネルでご覧ください。", ""]
    for key, label in CONFIG["categories"]:
        top = rankings.get(key) or []
        if not top:
            continue
        lines += note_section(top, label)
        lines.append("---")
        lines.append("")
    lines += ["※本メディアは画像・動画を保持せず、公式チャンネルへのリンクのみ掲載しています。",
              "※評価はタイトル・説明文・公開統計に基づくAI分析であり、映像自体の視聴評価ではありません。",
              "※公式映像の話題度を測る趣旨のランキングであり、作品の優劣を示すものではありません。"]
    return "\n".join(lines)


def render_digest(rankings: dict, date_s: str) -> str:
    lines = [f"📋 {CONFIG['brand']} {date_s} 投稿文生成完了", ""]
    for key, label in CONFIG["categories"]:
        top = rankings.get(key) or []
        lines.append(f"◆ {label} TOP{len(top)}")
        for i, c in enumerate(top):
            prod = f"「{c['product']}」" if c.get("product") else ""
            lines.append(f"{i + 1}位 {c['company']}{prod} ({fmt_views(c['views'])}回) {c['url']}")
        if top:
            lines.append(f"▶️ 連続再生チェック: {playlist_url(top)}")
        lines.append("")
    lines += ["outputs/ フォルダの各ファイルを確認してコピペ投稿してください:",
              "x.txt / threads.txt / instagram.txt / note.md / 各動画"]
    return "\n".join(lines)


# ============================================================
# 4. 週間ランキング(月曜のみ)
# ============================================================

def render_weekly(archive: list[dict], today: datetime) -> str | None:
    if today.weekday() != 0:  # 月曜以外はスキップ
        return None
    week_ago = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    pool = [c for c in archive if c.get("ranked_on", "") >= week_ago]
    pool.sort(key=lambda c: c.get("buzz_score", 0), reverse=True)
    top = pool[:5]
    if not top:
        return None
    lines = [f"🏆 今週のバズ・アイドルコンテンツ BEST{len(top)}({week_ago}〜)", ""]
    for i, c in enumerate(top):
        prod = f"「{c.get('product')}」" if c.get("product") else ""
        lines += [f"{MEDAL[i]} {c['company']}{prod}",
                  f"　{c.get('hitokoto','')}",
                  f"　{c['url']}", ""]
    lines.append(CONFIG["hashtags"] + " #今週のベスト")
    return "\n".join(lines)

# ============================================================
# 5. デモデータ(--demo用。実在企業ではなく架空データ)
# ============================================================

DEMO_DATA = [
    {"video_id": "a1", "company": "星霜のアルカディア", "product": "本PV第2弾",
     "category": "anime", "title": "TVアニメ『星霜のアルカディア』本PV第2弾", "channel": "『星霜のアルカディア』公式",
     "published_at": "2026-10-02", "views": 1860000, "likes": 124000, "comments": 15200,
     "views_per_day": 620000, "buzz_score": 7.2, "cast": "(架空)宵待シンフォニア",
     "keywords": ["SFファンタジー", "作画", "主題歌"], "media": "TV",
     "hitokoto": "雲海都市の俯瞰ロングカットと主題歌の入りが完璧に同期",
     "ratings": {"話題性": 5, "SNS拡散性": 5, "期待度": 5, "映像インパクト": 5},
     "url": "https://www.youtube.com/watch?v=a1"},
    {"video_id": "a2", "company": "うちの猫が魔王でした", "product": "ノンクレジットOP",
     "category": "anime", "title": "TVアニメ『うちの猫が魔王でした』ノンクレジットOP", "channel": "『うちの猫が魔王でした』公式",
     "published_at": "2026-10-03", "views": 940000, "likes": 78000, "comments": 9800,
     "views_per_day": 940000, "buzz_score": 6.8, "cast": None,
     "keywords": ["日常コメディ", "OP", "猫"], "media": "TV",
     "hitokoto": "猫の肉球視点で始まるOP、1話放送前から振り付けが拡散",
     "ratings": {"話題性": 5, "SNS拡散性": 5, "期待度": 4, "映像インパクト": 4},
     "url": "https://www.youtube.com/watch?v=a2"},
    {"video_id": "a3", "company": "蒼き機兵のレクイエム", "product": "特報",
     "category": "anime", "title": "TVアニメ『蒼き機兵のレクイエム』特報", "channel": "『蒼き機兵のレクイエム』公式",
     "published_at": "2026-10-01", "views": 720000, "likes": 51000, "comments": 8400,
     "views_per_day": 180000, "buzz_score": 6.1, "cast": None,
     "keywords": ["ロボット", "劇場版", "手描き"], "media": "劇場",
     "hitokoto": "手描き作画のコックピット演出15秒だけで期待値が沸騰",
     "ratings": {"話題性": 4, "SNS拡散性": 4, "期待度": 5, "映像インパクト": 5},
     "url": "https://www.youtube.com/watch?v=a3"},
    {"video_id": "a4", "company": "放課後クロニクル", "product": "ティザーPV",
     "category": "anime", "title": "TVアニメ『放課後クロニクル』ティザーPV", "channel": "『放課後クロニクル』公式",
     "published_at": "2026-10-03", "views": 410000, "likes": 29000, "comments": 3600,
     "views_per_day": 410000, "buzz_score": 5.8, "cast": None,
     "keywords": ["青春", "群像劇"], "media": "TV",
     "hitokoto": "セリフ無し・環境音のみのティザーが考察班を刺激",
     "ratings": {"話題性": 4, "SNS拡散性": 4, "期待度": 4, "映像インパクト": 3},
     "url": "https://www.youtube.com/watch?v=a4"},
    {"video_id": "a5", "company": "剣と燐光", "product": "本PV",
     "category": "anime", "title": "TVアニメ『剣と燐光』本PV", "channel": "『剣と燐光』公式",
     "published_at": "2026-09-30", "views": 380000, "likes": 24000, "comments": 2900,
     "views_per_day": 76000, "buzz_score": 5.5, "cast": "(架空)灰音レイ",
     "keywords": ["ダークファンタジー", "剣戟"], "media": "配信",
     "hitokoto": "燐光エフェクトの剣戟作画、配信独占とは思えない物量",
     "ratings": {"話題性": 3, "SNS拡散性": 3, "期待度": 4, "映像インパクト": 5},
     "url": "https://www.youtube.com/watch?v=a5"},
    {"video_id": "a6", "company": "メテオガール疾走中", "product": "ノンクレジットED",
     "category": "anime", "title": "TVアニメ『メテオガール疾走中』ノンクレジットED", "channel": "『メテオガール疾走中』公式",
     "published_at": "2026-10-02", "views": 290000, "likes": 31000, "comments": 2700,
     "views_per_day": 96000, "buzz_score": 5.3, "cast": "(架空)ななせ こまち",
     "keywords": ["ED", "ループ動画"], "media": "TV",
     "hitokoto": "走り続けるだけのEDが作業用ループ需要で伸び続ける",
     "ratings": {"話題性": 3, "SNS拡散性": 4, "期待度": 3, "映像インパクト": 3},
     "url": "https://www.youtube.com/watch?v=a6"},
    {"video_id": "a7", "company": "百年喫茶物語", "product": "本PV第1弾",
     "category": "anime", "title": "TVアニメ『百年喫茶物語』本PV第1弾", "channel": "『百年喫茶物語』公式",
     "published_at": "2026-09-29", "views": 240000, "likes": 19000, "comments": 2100,
     "views_per_day": 40000, "buzz_score": 5.0, "cast": None,
     "keywords": ["レトロ", "飯テロ", "お仕事"], "media": "TV",
     "hitokoto": "湯気と琥珀色の照明、飯テロ作画が深夜帯に強そう",
     "ratings": {"話題性": 3, "SNS拡散性": 3, "期待度": 4, "映像インパクト": 4},
     "url": "https://www.youtube.com/watch?v=a7"},
    {"video_id": "a8", "company": "怪異高専イロハ", "product": "予告",
     "category": "anime", "title": "TVアニメ『怪異高専イロハ』予告", "channel": "『怪異高専イロハ』公式",
     "published_at": "2026-10-03", "views": 210000, "likes": 17000, "comments": 2500,
     "views_per_day": 210000, "buzz_score": 4.9, "cast": None,
     "keywords": ["ホラー", "学園"], "media": "TV",
     "hitokoto": "予告の最後0.5秒に仕込まれたカットで考察が加速中",
     "ratings": {"話題性": 3, "SNS拡散性": 4, "期待度": 3, "映像インパクト": 3},
     "url": "https://www.youtube.com/watch?v=a8"},
    {"video_id": "a9", "company": "シグナルブルー", "product": "アニメ化発表映像",
     "category": "anime", "title": "TVアニメ『シグナルブルー』アニメ化発表映像", "channel": "『シグナルブルー』公式",
     "published_at": "2026-09-28", "views": 180000, "likes": 22000, "comments": 3300,
     "views_per_day": 26000, "buzz_score": 4.7, "cast": None,
     "keywords": ["原作人気", "発表"], "media": "TV",
     "hitokoto": "原作ファン待望のアニメ化発表、コメント欄が同窓会状態",
     "ratings": {"話題性": 3, "SNS拡散性": 3, "期待度": 5, "映像インパクト": 2},
     "url": "https://www.youtube.com/watch?v=a9"},
    {"video_id": "a10", "company": "木曜日のテラリウム", "product": "本PV",
     "category": "anime", "title": "TVアニメ『木曜日のテラリウム』本PV", "channel": "『木曜日のテラリウム』公式",
     "published_at": "2026-10-01", "views": 120000, "likes": 11000, "comments": 1400,
     "views_per_day": 30000, "buzz_score": 4.4, "cast": None,
     "keywords": ["SF", "ミニマル"], "media": "配信",
     "hitokoto": "室内劇SFの静かなPV、色彩設計の緑が目に残る",
     "ratings": {"話題性": 2, "SNS拡散性": 3, "期待度": 3, "映像インパクト": 4},
     "url": "https://www.youtube.com/watch?v=a10"},
]

# ============================================================
# メイン
# ============================================================

def notify_discord(text: str):
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        return
    try:
        http_post_json(url, {"content": text[:1900]}, {})
        print("[info] Discordに通知しました")
    except Exception as e:
        print(f"[warn] Discord通知失敗: {e}", file=sys.stderr)


def split_by_category(items: list[dict]) -> dict:
    """分析済みリストを統合ランキングへ"""
    ok = sorted(items, key=lambda x: x.get("buzz_score", 0), reverse=True)
    return {"all": ok[:CONFIG["top_n"]]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="APIキーなしで架空データを使い出力を確認")
    args = ap.parse_args()

    today = jst_now()
    date_s = today.strftime("%Y/%m/%d")
    out_dir = OUT_DIR / today.strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)

    seen = set(load_json(SEEN_FILE, []))
    archive = load_json(ARCHIVE_FILE, [])

    if args.demo:
        print("[info] デモモード: 架空データで出力を生成します")
        rankings = split_by_category(DEMO_DATA)
    else:
        yt_key = os.environ.get("YOUTUBE_API_KEY")
        if not yt_key:
            sys.exit("YOUTUBE_API_KEY が設定されていません(動作確認だけなら --demo を付けてください)")
        print("[info] YouTubeから候補を収集中...")
        candidates = collect_candidates(yt_key, seen)
        print(f"[info] 候補 {len(candidates)} 件 → Claudeで分析中...")
        if not candidates:
            sys.exit("本日の新着候補が見つかりませんでした")
        analyzed = analyze_with_claude(candidates)
        rankings = split_by_category(analyzed)
        if not any(rankings.values()):
            sys.exit("公式アイドルコンテンツと判定された動画がありませんでした")

    # 出力生成
    files = {
        "x.txt": render_x(rankings, date_s),
        "threads.txt": render_threads(rankings, date_s),
        "instagram.txt": render_instagram(rankings, date_s),
        "note.md": render_note(rankings, date_s),
        "digest.txt": render_digest(rankings, date_s),
    }
    for name, text in files.items():
        (out_dir / name).write_text(text, encoding="utf-8")
        print(f"[info] 出力: {out_dir / name}")

    # 動画生成(make_video.py)用の構造化データ(男女別)
    for key, _ in CONFIG["categories"]:
        save_json(out_dir / f"ranking_{key}.json", rankings.get(key) or [])
        print(f"[info] 出力: {out_dir / f'ranking_{key}.json'}")

    # 状態更新(デモ時は保存しない)
    if not args.demo:
        for top in rankings.values():
            for c in top:
                seen.add(c["video_id"])
                archive.append({**c, "ranked_on": today.strftime("%Y-%m-%d")})
        cutoff = (today - timedelta(days=CONFIG["retention_days"])).strftime("%Y-%m-%d")
        archive = [c for c in archive if c.get("published_at", "") >= cutoff]
        save_json(SEEN_FILE, sorted(seen))
        save_json(ARCHIVE_FILE, archive)

    notify_discord(files["digest.txt"])
    print("\n" + files["digest.txt"])


if __name__ == "__main__":
    main()
