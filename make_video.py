#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ANIME TREND WATCH - 週次ランキング動画生成
outputs/<日付>/top5.json を読み、ランキング動画を2サイズ生成する:
  reel.mp4  1080x1920 (9:16)  リール / ストーリーズ / TikTok / YouTubeショート用
  feed.mp4  1080x1350 (4:5)   Instagramフィード投稿用(切り取られない最大縦比)

方式: PILでスライド画像を描画 → ffmpegでフェードつなぎのMP4に変換
使用素材: 自前で生成したテキスト・図形のみ(CM映像・サムネイルは一切使わない)
音声: 無音(BGMは投稿先アプリ内のライセンス楽曲を付けるのが安全)

必要環境: ffmpeg, fonts-noto-cjk, pillow
使い方:   python make_video.py            # 今日のフォルダを対象
          python make_video.py 2026-07-13 # 日付指定
"""

import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# 出力フォーマット: ファイル名 → (幅, 高さ)
SIZES = {"reel": (1080, 1920), "feed": (1080, 1350)}
CATEGORIES = [("all", "アニメ")]

SLIDE_SEC = 4.6      # 1スライドの表示秒数
FADE = 0.4           # フェード秒数
FPS = 30

BASE_DIR = Path(__file__).resolve().parent
OUT_ROOT = BASE_DIR / "outputs"
JST = timezone(timedelta(hours=9))

BRAND = "ANIME TREND WATCH"
TAGLINE = "いま伸びているアニメ公式映像を毎週届ける速報"

# ============================================================
# 配色設定
# ============================================================
# 季節ごとの背景グラデーション(上の色, 下の色)。自由に編集OK。
SEASON_PALETTES = {
    "spring": ((40, 22, 44), (78, 38, 66)),    # 3-5月: 夜桜プラム
    "summer": ((10, 26, 50), (16, 60, 84)),    # 6-8月: 夏の深海ブルー
    "autumn": ((38, 22, 14), (78, 42, 22)),    # 9-11月: 焦がしアンバー
    "winter": ((12, 16, 34), (30, 42, 74)),    # 12-2月: 冬のアイスネイビー
}
# 季節を固定したい場合はここに "spring" / "summer" / "autumn" / "winter" を指定。
# None なら実行日の月から自動判定。
SEASON_OVERRIDE = None

WHITE = (245, 245, 250)
GRAY = (168, 172, 190)
RANK_COLORS = [(255, 200, 60), (200, 205, 220), (205, 135, 80)]  # 金・銀・銅
OTHER_COLOR = (110, 160, 255)  # 4位以下のアクセント


def season_of(month: int) -> str:
    if 3 <= month <= 5:
        return "spring"
    if 6 <= month <= 8:
        return "summer"
    if 9 <= month <= 11:
        return "autumn"
    return "winter"


def rank_color(i: int):
    return RANK_COLORS[i] if i < 3 else OTHER_COLOR


def rank_label(i: int) -> str:
    return f"第{i + 1}位" if i < 3 else f"{i + 1}位"

FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
]


def find_font() -> str:
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return p
    try:
        out = subprocess.run(["fc-list", ":lang=ja", "file"], capture_output=True, text=True).stdout
        for line in out.splitlines():
            f = line.split(":")[0].strip()
            if f.endswith((".ttc", ".ttf", ".otf")):
                return f
    except Exception:
        pass
    sys.exit("日本語フォントが見つかりません。'sudo apt-get install fonts-noto-cjk' を実行してください")


FONT_PATH = find_font()


def stars_text(v) -> str:
    try:
        v = int(v)
        return "★" * v + "☆" * (5 - v)
    except (TypeError, ValueError):
        return ""


class Renderer:
    """縦1920px基準でデザインし、任意の縦横比に比例縮尺して描画する"""

    def __init__(self, w: int, h: int, palette):
        self.w, self.h = w, h
        self.k = h / 1920  # 縦方向スケール(フォント・座標・図形に共通適用)
        self.bg_top, self.bg_bottom = palette

    # --- スケール補助 ---
    def f(self, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(FONT_PATH, max(int(size * self.k), 12))

    def y(self, v: float) -> int:
        return int(v * self.k)

    # --- 描画補助 ---
    def bg(self) -> Image.Image:
        img = Image.new("RGB", (self.w, self.h))
        for yy in range(self.h):
            t = yy / self.h
            c = tuple(int(a + (b - a) * t) for a, b in zip(self.bg_top, self.bg_bottom))
            img.paste(c, (0, yy, self.w, yy + 1))
        return img

    def wrap(self, draw, text: str, fnt, max_width: int) -> list[str]:
        lines, cur = [], ""
        for ch in text:
            if ch == "\n":
                lines.append(cur); cur = ""
                continue
            if draw.textlength(cur + ch, font=fnt) > max_width:
                lines.append(cur); cur = ch
            else:
                cur += ch
        if cur:
            lines.append(cur)
        return lines

    def center_wrapped(self, draw, yy: int, text: str, fnt, fill, side_margin: int, line_gap=14) -> int:
        max_width = self.w - side_margin * 2
        for line in self.wrap(draw, text, fnt, max_width):
            lw = draw.textlength(line, font=fnt)
            draw.text(((self.w - lw) // 2, yy), line, font=fnt, fill=fill)
            yy += fnt.size + self.y(line_gap)
        return yy

    def center_line(self, draw, yy: int, text: str, fnt, fill) -> int:
        draw.text(((self.w - draw.textlength(text, font=fnt)) // 2, yy), text, font=fnt, fill=fill)
        return yy + fnt.size

    def footer(self, draw):
        fnt = self.f(34)
        draw.text(((self.w - draw.textlength(BRAND, font=fnt)) // 2, self.h - self.y(110)),
                  BRAND, font=fnt, fill=GRAY)

    # --- スライド ---
    def cover(self, date_s: str, n: int, label: str) -> Image.Image:
        img = self.bg()
        d = ImageDraw.Draw(img)
        d.rectangle([self.w // 2 - self.y(60), self.y(280),
                     self.w // 2 + self.y(60), self.y(288)], fill=RANK_COLORS[0])
        yy = self.y(400)
        yy = self.center_line(d, yy, "今週の", self.f(80), WHITE) + self.y(40)
        yy = self.center_line(d, yy, label, self.f(110), WHITE) + self.y(50)
        yy = self.center_line(d, yy, f"TOP{n}", self.f(150), RANK_COLORS[0]) + self.y(85)
        yy = self.center_line(d, yy, date_s, self.f(56), WHITE) + self.y(120)
        self.center_wrapped(d, yy, TAGLINE, self.f(38), GRAY, side_margin=100)
        self.footer(d)
        return img

    def cm_slide(self, rank: int, c: dict) -> Image.Image:
        img = self.bg()
        d = ImageDraw.Draw(img)
        accent = rank_color(rank)

        # 順位バッジ
        cx, cy, r = self.w // 2, self.y(310), self.y(125)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=accent, width=max(self.y(10), 4))
        fnt = self.f(76)
        label = rank_label(rank)
        d.text((cx - d.textlength(label, font=fnt) // 2, cy - self.y(52)),
               label, font=fnt, fill=accent)

        # 企業名・商品名
        yy = self.y(530)
        yy = self.center_wrapped(d, yy, c.get("company", ""), self.f(84), WHITE, side_margin=80)
        if c.get("product"):
            yy = self.center_wrapped(d, yy + self.y(8), f"「{c['product']}」",
                                     self.f(62), accent, side_margin=80)

        # 区切り線
        yy += self.y(38)
        d.rectangle([self.y(180), yy, self.w - self.y(180), yy + max(self.y(4), 2)],
                    fill=(80, 84, 110))
        yy += self.y(56)

        # 一言分析
        yy = self.center_wrapped(d, yy, c.get("hitokoto", ""), self.f(58), WHITE,
                                 side_margin=110, line_gap=22)

        # キーワード
        kw = c.get("keywords") or []
        if kw:
            yy += self.y(26)
            kw_s = "  ".join(f"#{k}" for k in kw[:4])
            yy = self.center_wrapped(d, yy, kw_s, self.f(44), GRAY, side_margin=100)

        # 話題性スター + 公開日
        r_ = (c.get("ratings") or {}).get("話題性")
        yy += self.y(44)
        if stars_text(r_):
            yy = self.center_line(d, yy, f"話題性 {stars_text(r_)}", self.f(52), accent) + self.y(36)
        self.center_line(d, yy, f"{c.get('published_at', '')} 公開", self.f(40), GRAY)

        self.footer(d)
        return img

    def list_slide(self, rest: list[dict], total: int) -> Image.Image:
        """4位〜N位の一覧スライド"""
        img = self.bg()
        d = ImageDraw.Draw(img)
        yy = self.y(200)
        yy = self.center_line(d, yy, f"4位〜{total}位", self.f(84), OTHER_COLOR) + self.y(50)
        d.rectangle([self.y(180), yy, self.w - self.y(180), yy + max(self.y(4), 2)],
                    fill=(80, 84, 110))
        yy += self.y(70)

        num_fnt = self.f(52)
        name_fnt = self.f(50)
        row_gap = self.y(150)
        left = self.y(150)
        for i, c in enumerate(rest, start=4):
            prod = f"「{c['product']}」" if c.get("product") else ""
            text = f"{c.get('company', '')}{prod}"
            # 1行に収まるよう末尾を省略
            max_w = self.w - left - self.y(210)
            while text and d.textlength(text, font=name_fnt) > max_w:
                text = text[:-1]
            if text != f"{c.get('company', '')}{prod}":
                text = text[:-1] + "…"
            d.text((left, yy), f"{i}位", font=num_fnt, fill=OTHER_COLOR)
            d.text((left + self.y(170), yy), text, font=name_fnt, fill=WHITE)
            yy += row_gap
        self.footer(d)
        return img

    def outro(self) -> Image.Image:
        img = self.bg()
        d = ImageDraw.Draw(img)
        yy = self.y(660)
        yy = self.center_line(d, yy, "各CMのリンクは", self.f(72), WHITE) + self.y(50)
        yy = self.center_line(d, yy, "投稿本文からチェック", self.f(72), WHITE) + self.y(110)
        self.center_line(d, yy, "毎週更新", self.f(64), RANK_COLORS[0])
        self.footer(d)
        return img


def build_video(slides, out_path: Path, w: int, h: int):
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        clips = []
        for i, img in enumerate(slides):
            png = tdir / f"slide_{i}.png"
            img.save(png)
            clip = tdir / f"clip_{i}.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error",
                "-loop", "1", "-t", str(SLIDE_SEC), "-i", str(png),
                "-vf", (f"fade=t=in:st=0:d={FADE},"
                        f"fade=t=out:st={SLIDE_SEC - FADE}:d={FADE},"
                        f"format=yuv420p"),
                "-r", str(FPS), "-c:v", "libx264", "-preset", "medium", "-crf", "26",
                str(clip),
            ], check=True)
            clips.append(clip)
        lst = tdir / "list.txt"
        lst.write_text("".join(f"file '{c}'\n" for c in clips), encoding="utf-8")
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(lst),
            "-c", "copy", str(out_path),
        ], check=True)


def main():
    date_s = sys.argv[1] if len(sys.argv) > 1 else datetime.now(JST).strftime("%Y-%m-%d")
    day_dir = OUT_ROOT / date_s

    month = int(date_s.split("-")[1])
    season = SEASON_OVERRIDE or season_of(month)
    palette = SEASON_PALETTES[season]
    print(f"[info] 背景パレット: {season}")
    disp_date = date_s.replace("-", "/")

    made = 0
    for key, label in CATEGORIES:
        data_file = day_dir / f"ranking_{key}.json"
        if not data_file.exists():
            print(f"[warn] {data_file} がないためスキップ")
            continue
        top = json.loads(data_file.read_text(encoding="utf-8"))
        if not top:
            print(f"[warn] {label} のランキングが空のためスキップ")
            continue
        n = len(top)
        for size_name, (w, h) in SIZES.items():
            r = Renderer(w, h, palette)
            slides = [r.cover(disp_date, n, label)]
            slides += [r.cm_slide(i, top[i]) for i in (0, 1, 2) if i < n]
            if n > 3:
                slides.append(r.list_slide(top[3:], n))
            slides.append(r.outro())
            fname = f"{size_name}.mp4" if key == "all" else f"{key}_{size_name}.mp4"
            out_path = day_dir / fname
            build_video(slides, out_path, w, h)
            size_mb = out_path.stat().st_size / 1024 / 1024
            print(f"[info] 動画生成完了: {out_path} ({w}x{h} / {size_mb:.1f}MB / "
                  f"約{SLIDE_SEC * len(slides):.0f}秒)")
            made += 1
    if made == 0:
        sys.exit("生成できた動画がありません。先に idol_trend_watch.py を実行してください")


if __name__ == "__main__":
    main()
