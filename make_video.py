#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TREND WATCH - POPランキング動画生成(季節テーマ対応)
outputs/<日付>/ranking_all.json を読み、明るくポップなランキング動画を生成する。

出力(outputs/<日付>/):
  reel.mp4    1080x1920 (9:16)  Instagramリール / ストーリーズ / YouTubeショート用
  tiktok.mp4  1080x1920 (9:16)  TikTok用(reelと同内容。TikTokのUI被りを考慮した
                                 セーフゾーン設計は全フォーマット共通)
  feed.mp4    1080x1350 (4:5)   Instagramフィード投稿用

デザイン:
  実行日の季節を自動判定し、背景色・飾り(花びら/泡/落ち葉/雪)・
  季節バッジが切り替わるPOPテイスト。SEASONS を編集すれば配色変更可、
  SEASON_OVERRIDE で季節固定も可。

使用素材: 自前で生成したテキスト・図形のみ(権利物は一切使わない)
必要環境: ffmpeg, fonts-noto-cjk, fonts-mplus, pillow
使い方:   python make_video.py            # 今日のフォルダを対象
          python make_video.py 2026-10-05 # 日付指定
"""

import json
import math
import random
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ===== メディア設定(アイドル版/アニメ版でここだけ異なる)=====
BRAND = "ANIME TREND WATCH"
TAGLINE = "いま伸びているアニメ公式映像を毎週届ける速報"
CATEGORIES = [("all", "アニメ")]

# ===== 出力フォーマット =====
SIZES = {"reel": (1080, 1920), "feed": (1080, 1350)}
TIKTOK_FROM = "reel"  # tiktok.mp4 はこのフォーマットの複製として書き出す

SLIDE_SEC = 4.6
FADE = 0.4
FPS = 30

BASE_DIR = Path(__file__).resolve().parent
OUT_ROOT = BASE_DIR / "outputs"
JST = timezone(timedelta(hours=9))

# ============================================================
# 季節テーマ(自由に編集OK)
# ============================================================
SEASONS = {
    "spring": {
        "ja": "春", "en": "SPRING",
        "bg_top": (255, 245, 249), "bg_bottom": (255, 214, 231),
        "ink": (93, 44, 72),            # 文字のメイン色(濃いプラム)
        "muted": (164, 120, 142),       # 補助テキスト
        "accents": [(255, 111, 165), (126, 203, 111), (255, 200, 61), (143, 184, 255)],
        "deco": "petal",
    },
    "summer": {
        "ja": "夏", "en": "SUMMER",
        "bg_top": (234, 251, 255), "bg_bottom": (195, 236, 255),
        "ink": (21, 69, 107),           # 濃いマリンネイビー
        "muted": (104, 145, 173),
        "accents": [(0, 166, 214), (255, 200, 61), (255, 123, 107), (52, 201, 163)],
        "deco": "bubble",
    },
    "autumn": {
        "ja": "秋", "en": "AUTUMN",
        "bg_top": (255, 247, 233), "bg_bottom": (255, 224, 185),
        "ink": (92, 51, 23),            # 濃いチョコブラウン
        "muted": (166, 124, 92),
        "accents": [(242, 140, 40), (201, 79, 46), (227, 181, 5), (140, 98, 57)],
        "deco": "leaf",
    },
    "winter": {
        "ja": "冬", "en": "WINTER",
        "bg_top": (244, 250, 255), "bg_bottom": (217, 234, 250),
        "ink": (31, 58, 95),            # 冬のネイビー
        "muted": (116, 142, 173),
        "accents": [(74, 144, 217), (93, 194, 192), (179, 157, 219), (255, 182, 100)],
        "deco": "snow",
    },
}
SEASON_OVERRIDE = None  # "spring"/"summer"/"autumn"/"winter" で固定。Noneなら月から自動

WHITE = (255, 255, 255)
RANK_GOLD = (255, 179, 0)
RANK_SILVER = (139, 162, 189)
RANK_BRONZE = (199, 123, 74)


def season_of(month: int) -> str:
    if 3 <= month <= 5:
        return "spring"
    if 6 <= month <= 8:
        return "summer"
    if 9 <= month <= 11:
        return "autumn"
    return "winter"


def rank_label(i: int) -> str:
    return f"第{i + 1}位" if i < 3 else f"{i + 1}位"


# ============================================================
# フォント(丸みのあるM PLUS 2を優先、無ければNoto)
# ============================================================
FONT_HEAVY_CANDIDATES = [
    "/usr/share/fonts/opentype/mplus/Mplus2-Black.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
]
FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/opentype/mplus/Mplus2-Bold.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
]


def pick_font(cands: list[str]) -> str:
    for p in cands:
        if Path(p).exists():
            return p
    out = subprocess.run(["fc-list", ":lang=ja", "file"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        f = line.split(":")[0].strip()
        if f.endswith((".ttc", ".ttf", ".otf")):
            return f
    sys.exit("日本語フォントが見つかりません。'sudo apt-get install fonts-noto-cjk fonts-mplus' を実行してください")


FONT_HEAVY = pick_font(FONT_HEAVY_CANDIDATES)
FONT_BOLD = pick_font(FONT_BOLD_CANDIDATES)
# ★☆はM PLUS未収録のためNotoで描く
FONT_SYMBOL = pick_font(["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
                         FONT_BOLD_CANDIDATES[-1]])


def stars_text(v) -> str:
    try:
        v = int(v)
        return "★" * v + "☆" * (5 - v)
    except (TypeError, ValueError):
        return ""


# ============================================================
# レンダラー(縦1920px基準で設計し、比例縮尺)
# ============================================================
class Renderer:
    def __init__(self, w: int, h: int, season: dict):
        self.w, self.h = w, h
        self.k = h / 1920
        self.s = season

    # --- 基本ヘルパー ---
    def f(self, size: int, heavy=True) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(FONT_HEAVY if heavy else FONT_BOLD, max(int(size * self.k), 12))

    def y(self, v: float) -> int:
        return int(v * self.k)

    def rank_color(self, i: int):
        return [RANK_GOLD, RANK_SILVER, RANK_BRONZE][i] if i < 3 else self.s["accents"][0]

    # --- 背景(グラデ+紙吹雪+季節の飾り) ---
    def bg(self) -> Image.Image:
        img = Image.new("RGB", (self.w, self.h))
        top, bottom = self.s["bg_top"], self.s["bg_bottom"]
        for yy in range(self.h):
            t = yy / self.h
            c = tuple(int(a + (b - a) * t) for a, b in zip(top, bottom))
            img.paste(c, (0, yy, self.w, yy + 1))

        ov = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)
        rnd = random.Random(7)

        # 紙吹雪(小さな丸・四角)
        for _ in range(26):
            x, yy = rnd.randint(0, self.w), rnd.randint(0, self.h)
            r = self.y(rnd.randint(8, 18))
            col = rnd.choice(self.s["accents"]) + (rnd.randint(50, 95),)
            if rnd.random() < 0.5:
                d.ellipse([x - r, yy - r, x + r, yy + r], fill=col)
            else:
                d.rounded_rectangle([x - r, yy - r, x + r, yy + r], radius=r // 3, fill=col)

        # 季節の飾り
        deco = self.s["deco"]
        for _ in range(14):
            x, yy = rnd.randint(0, self.w), rnd.randint(0, self.h)
            r = self.y(rnd.randint(16, 30))
            a = rnd.randint(60, 110)
            if deco == "petal":      # 桜の花びら(傾いた楕円)
                pink = (255, 150, 190, a)
                d.ellipse([x - r, yy - r // 2, x + r, yy + r // 2], fill=pink)
            elif deco == "bubble":   # 泡(輪っか)
                aqua = (90, 200, 235, a)
                d.ellipse([x - r, yy - r, x + r, yy + r], outline=aqua, width=max(self.y(5), 2))
            elif deco == "leaf":     # 落ち葉(楕円+軸)
                col = rnd.choice([(242, 140, 40), (201, 79, 46), (227, 181, 5)]) + (a,)
                d.ellipse([x - r, yy - r // 2, x + r, yy + r // 2], fill=col)
                d.line([x - r, yy, x + r, yy], fill=(120, 70, 30, a), width=max(self.y(3), 1))
            else:                    # 雪の結晶(アスタリスク)
                blue = (120, 170, 225, a)
                wdt = max(self.y(5), 2)
                for ang in (0, 60, 120):
                    rad = math.radians(ang)
                    dx, dy = r * math.cos(rad), r * math.sin(rad)
                    d.line([x - dx, yy - dy, x + dx, yy + dy], fill=blue, width=wdt)

        img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
        return img

    # --- 白カード(影+アクセント枠) ---
    def card(self, d: ImageDraw.ImageDraw, x1, y1, x2, y2, border):
        off = self.y(10)
        d.rounded_rectangle([x1 + off, y1 + off, x2 + off, y2 + off],
                            radius=self.y(44), fill=self._shade())
        d.rounded_rectangle([x1, y1, x2, y2], radius=self.y(44),
                            fill=WHITE, outline=border, width=max(self.y(7), 3))

    def _shade(self):
        ink = self.s["ink"]
        bgb = self.s["bg_bottom"]
        return tuple((a * 35 + b * 65) // 100 for a, b in zip(ink, bgb))

    # --- テキスト ---
    def wrap(self, d, text, fnt, max_w):
        lines, cur = [], ""
        for ch in text:
            if ch == "\n":
                lines.append(cur); cur = ""
                continue
            if d.textlength(cur + ch, font=fnt) > max_w:
                lines.append(cur); cur = ch
            else:
                cur += ch
        if cur:
            lines.append(cur)
        return lines

    def center_wrapped(self, d, yy, text, fnt, fill, max_w, gap=14):
        for line in self.wrap(d, text, fnt, max_w):
            d.text(((self.w - d.textlength(line, font=fnt)) // 2, yy), line, font=fnt, fill=fill)
            yy += fnt.size + self.y(gap)
        return yy

    def center_line(self, d, yy, text, fnt, fill):
        d.text(((self.w - d.textlength(text, font=fnt)) // 2, yy), text, font=fnt, fill=fill)
        return yy + fnt.size

    def pill(self, d, cy, text, fnt, bg, fg):
        tw = d.textlength(text, font=fnt)
        pw, ph = tw + self.y(70), fnt.size + self.y(34)
        x1 = (self.w - pw) // 2
        d.rounded_rectangle([x1, cy, x1 + pw, cy + ph], radius=ph // 2, fill=bg)
        d.text(((self.w - tw) // 2, cy + self.y(15)), text, font=fnt, fill=fg)
        return cy + ph

    def star_line(self, d, yy, label, v, fill):
        """「話題性 ★★★★☆」をラベル=M PLUS、星=Notoの混植で中央描画"""
        f1 = self.f(50)
        f2 = ImageFont.truetype(FONT_SYMBOL, max(int(50 * self.k), 12))
        seg1, seg2 = f"{label} ", stars_text(v)
        w1, w2 = d.textlength(seg1, font=f1), d.textlength(seg2, font=f2)
        x = (self.w - (w1 + w2)) // 2
        d.text((x, yy), seg1, font=f1, fill=fill)
        d.text((x + w1, yy + self.y(3)), seg2, font=f2, fill=fill)
        return yy + f1.size

    def footer(self, d):
        fnt = self.f(34)
        # TikTok/リールの下部UIに被らないよう高めに配置(セーフゾーン)
        d.text(((self.w - d.textlength(BRAND, font=fnt)) // 2, self.h - self.y(190)),
               BRAND, font=fnt, fill=self.s["muted"])

    # --- スライド ---
    def cover(self, date_s: str, n: int, label: str) -> Image.Image:
        img = self.bg()
        d = ImageDraw.Draw(img)
        acc = self.s["accents"]

        yy = self.y(310)
        yy = self.center_line(d, yy, BRAND, self.f(40), self.s["muted"]) + self.y(60)
        # 季節バッジ
        yy = self.pill(d, yy, f"{self.s['en']}・{self.s['ja']}のランキング",
                       self.f(44), acc[1], WHITE) + self.y(65)

        self.card(d, self.y(70), yy, self.w - self.y(70), yy + self.y(760), acc[0])
        cy = yy + self.y(95)
        cy = self.center_line(d, cy, "今週の", self.f(84), self.s["ink"]) + self.y(35)
        cy = self.center_line(d, cy, label, self.f(120), acc[0]) + self.y(50)
        cy = self.center_line(d, cy, f"TOP{n}", self.f(170), RANK_GOLD) + self.y(70)
        self.center_line(d, cy, date_s, self.f(54), self.s["muted"])

        self.center_wrapped(d, yy + self.y(850), TAGLINE, self.f(38, heavy=False),
                            self.s["ink"], self.w - self.y(220))
        self.footer(d)
        return img

    def cm_slide(self, rank: int, c: dict) -> Image.Image:
        img = self.bg()
        d = ImageDraw.Draw(img)
        accent = self.rank_color(rank)

        # 順位バッジ(塗りつぶし円+白文字)
        cx, cy, r = self.w // 2, self.y(300), self.y(120)
        d.ellipse([cx - r - self.y(12), cy - r - self.y(12),
                   cx + r + self.y(12), cy + r + self.y(12)], fill=WHITE)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=accent)
        fnt = self.f(72)
        label = rank_label(rank)
        d.text((cx - d.textlength(label, font=fnt) // 2, cy - self.y(48)),
               label, font=fnt, fill=WHITE)

        # コンテンツカード
        top = self.y(480)
        self.card(d, self.y(70), top, self.w - self.y(70), self.h - self.y(370), accent)
        yy = top + self.y(70)
        yy = self.center_wrapped(d, yy, c.get("company", ""), self.f(78),
                                 self.s["ink"], self.w - self.y(260))
        if c.get("product"):
            yy = self.center_wrapped(d, yy + self.y(6), f"「{c['product']}」",
                                     self.f(56), accent, self.w - self.y(260))

        # ドット区切り
        yy += self.y(42)
        for i in range(5):
            dx = self.w // 2 + self.y((i - 2) * 56)
            d.ellipse([dx - self.y(9), yy, dx + self.y(9), yy + self.y(18)],
                      fill=self.s["accents"][i % 4])
        yy += self.y(70)

        yy = self.center_wrapped(d, yy, c.get("hitokoto", ""), self.f(54, heavy=False),
                                 self.s["ink"], self.w - self.y(300), gap=20)
        kw = c.get("keywords") or []
        if kw:
            yy += self.y(26)
            yy = self.center_wrapped(d, yy, "  ".join(f"#{k}" for k in kw[:4]),
                                     self.f(42), self.s["accents"][3], self.w - self.y(280))
        r_ = (c.get("ratings") or {}).get("話題性")
        yy += self.y(40)
        if stars_text(r_):
            yy = self.star_line(d, yy, "話題性", r_, RANK_GOLD) + self.y(34)
        self.center_line(d, yy, f"{c.get('published_at', '')} 公開", self.f(38, heavy=False),
                         self.s["muted"])
        self.footer(d)
        return img

    def list_slide(self, rest: list[dict], total: int) -> Image.Image:
        img = self.bg()
        d = ImageDraw.Draw(img)
        acc = self.s["accents"]

        yy = self.y(200)
        yy = self.pill(d, yy, f"4位 〜 {total}位", self.f(64), acc[0], WHITE) + self.y(70)
        self.card(d, self.y(70), yy, self.w - self.y(70), self.h - self.y(340), acc[1])

        row_y = yy + self.y(75)
        num_f, name_f = self.f(46), self.f(46)
        left = self.y(150)
        for i, c in enumerate(rest, start=4):
            col = acc[(i - 4) % 4]
            # 番号サークル
            cr = self.y(42)
            ccx = left + cr
            ccy = row_y + self.y(30)
            d.ellipse([ccx - cr, ccy - cr, ccx + cr, ccy + cr], fill=col)
            num_s = str(i)
            d.text((ccx - d.textlength(num_s, font=num_f) // 2, ccy - self.y(32)),
                   num_s, font=num_f, fill=WHITE)
            # 名前(1行省略)
            prod = f"「{c['product']}」" if c.get("product") else ""
            text = f"{c.get('company', '')}{prod}"
            full = text
            max_w = self.w - left - self.y(260)
            while text and d.textlength(text, font=name_f) > max_w:
                text = text[:-1]
            if text != full:
                text = text[:-1] + "…"
            d.text((left + self.y(115), row_y), text, font=name_f, fill=self.s["ink"])
            row_y += self.y(142)
        self.footer(d)
        return img

    def outro(self) -> Image.Image:
        img = self.bg()
        d = ImageDraw.Draw(img)
        acc = self.s["accents"]
        top = self.y(600)
        self.card(d, self.y(90), top, self.w - self.y(90), top + self.y(560), acc[0])
        yy = top + self.y(110)
        yy = self.center_line(d, yy, "リンクはすべて", self.f(66), self.s["ink"]) + self.y(42)
        yy = self.center_line(d, yy, "投稿本文からチェック", self.f(66), self.s["ink"]) + self.y(90)
        self.pill(d, yy, "フォローして最新回をチェック", self.f(46), acc[2], WHITE)
        self.footer(d)
        return img


# ============================================================
# 動画化
# ============================================================
def build_video(slides, out_path: Path):
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        clips = []
        for i, img in enumerate(slides):
            png = tdir / f"s{i}.png"
            img.save(png)
            clip = tdir / f"c{i}.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error",
                "-loop", "1", "-t", str(SLIDE_SEC), "-i", str(png),
                "-vf", (f"fade=t=in:st=0:d={FADE},"
                        f"fade=t=out:st={SLIDE_SEC - FADE}:d={FADE},format=yuv420p"),
                "-r", str(FPS), "-c:v", "libx264", "-preset", "medium", "-crf", "26",
                str(clip),
            ], check=True)
            clips.append(clip)
        lst = tdir / "list.txt"
        lst.write_text("".join(f"file '{c}'\n" for c in clips), encoding="utf-8")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                        "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c", "copy", str(out_path)], check=True)


def main():
    date_s = sys.argv[1] if len(sys.argv) > 1 else datetime.now(JST).strftime("%Y-%m-%d")
    day_dir = OUT_ROOT / date_s

    month = int(date_s.split("-")[1])
    season_key = SEASON_OVERRIDE or season_of(month)
    season = SEASONS[season_key]
    print(f"[info] 季節テーマ: {season_key}({season['ja']})")
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
        outputs = {}
        for size_name, (w, h) in SIZES.items():
            r = Renderer(w, h, season)
            slides = [r.cover(disp_date, n, label)]
            slides += [r.cm_slide(i, top[i]) for i in (0, 1, 2) if i < n]
            if n > 3:
                slides.append(r.list_slide(top[3:], n))
            slides.append(r.outro())
            fname = f"{size_name}.mp4" if key == "all" else f"{key}_{size_name}.mp4"
            out_path = day_dir / fname
            build_video(slides, out_path)
            outputs[size_name] = out_path
            print(f"[info] 動画生成完了: {out_path} ({w}x{h} / "
                  f"{out_path.stat().st_size / 1048576:.1f}MB / 約{SLIDE_SEC * len(slides):.0f}秒)")
            made += 1
        # TikTok用(9:16の複製)
        if TIKTOK_FROM in outputs:
            tk = outputs[TIKTOK_FROM].with_name(
                "tiktok.mp4" if key == "all" else f"{key}_tiktok.mp4")
            shutil.copyfile(outputs[TIKTOK_FROM], tk)
            print(f"[info] 動画生成完了: {tk} (TikTok用 / {TIKTOK_FROM}と同内容)")
            made += 1
    if made == 0:
        sys.exit("生成できた動画がありません。先に収集スクリプトを実行してください")


if __name__ == "__main__":
    main()
