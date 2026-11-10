"""Build the whole PriorLine brand pack from one definition of the mark.

The identity is a ledger. The mark is a Clarendon capital P standing on the
double rule a bookkeeper strikes under a final total, stamped in gold on dark
bookcloth or written in ink on ruled paper. Every asset here is drawn from the
same outline, the same six colours and the same two typefaces the site uses, so
the favicon, the app icon and a LinkedIn banner cannot drift apart the way they
do when each is exported by hand from a different file.

What it writes:

  marketing/logo        the mark, the wordmark and both lockups, SVG and PNG
  marketing/icons       favicon set, Apple and Android icons, the iOS app icon
  marketing/social      profile pictures, banners and the link-preview image
  marketing/templates   post templates, each as a blank and a worked example
  apps/web/public       the handful of those the site itself serves

The ledger template is filled from the live record and is dated. Pass the API
base to fill it, or it prints nothing rather than a number I typed in:

  python scripts/build_brand.py --api https://api.priorline.io/api/v1

Nothing in a template is a claim the record does not make. Losses are set in
red brackets here exactly as they are on the site.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import urllib.request
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont
from PIL import Image, ImageChops, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "marketing"
FONTS = OUT / "fonts"
PUBLIC = ROOT / "apps" / "web" / "public"

# The site's own tokens, from apps/web/src/index.css.
CLOTH = "#0f1a17"
GOLD = "#cda64e"
PAPER = "#e4ecdc"
INK = "#18213b"
INK_DIM = "#4a556f"
BLUE = "#1c3c9c"
RULE = "#a9c0c6"
MARGIN = "#c9585a"
RED = "#a31b1b"
OXBLOOD = "#6e1423"
BLACK = "#000000"
WHITE = "#ffffff"

SITE = "priorline.io"
TAG_A = "Our prior."
TAG_B = "Their line."
LEGAL = "21+. Projections, not betting advice. Gambling problem? Call 1-800-GAMBLER."

# Besley Black capital P, font units, 2000 to the em, y up. The same outline
# the site draws in components/Logo.tsx.
P_OUTLINE = (
    "M214 1230Q249 1230 259.5 1223.5Q270 1217 270 1182V334Q270 294 262 282Q254 270 "
    "212 270H96Q65 270 57.5 261Q50 252 50 220V46Q50 15 59 7.5Q68 0 98 0H954Q989 0 "
    "994.5 11Q1000 22 1000 56V220Q1000 252 994 261Q988 270 956 270H792Q758 270 749 "
    "277.5Q740 285 740 318V508Q740 540 746 550Q752 560 784 560H880Q1016 560 1138.5 "
    "587.5Q1261 615 1356 672.5Q1451 730 1505.5 821.5Q1560 913 1560 1040Q1560 1168 "
    "1505.5 1256Q1451 1344 1356 1397.5Q1261 1451 1138.5 1475.5Q1016 1500 880 1500H106Q72 "
    "1500 61 1492Q50 1484 50 1448V1278Q50 1244 60 1237Q70 1230 104 1230ZM782 830Q754 "
    "830 747 838Q740 846 740 874V1186Q740 1212 745 1221Q750 1230 776 1230H840Q939 1230 "
    "999.5 1187Q1060 1144 1060 1040Q1060 937 999.5 883.5Q939 830 840 830Z"
)
P_TRANSFORM = (19.98, 68.0, 0.0373)

# Two cuts of the mark. The full one carries a heavy rule and a hairline. Under
# about 48px the hairline is a third of a pixel and vanishes, so small sizes
# get a cut with both rules thickened and the P set larger.
RULES_FULL = [(14, 75.5, 72, 5.5), (14, 85, 72, 2.2)]
RULES_SMALL = [(12, 76, 76, 7.5), (12, 87.5, 76, 4.5)]
# At 16px even that closes up into a smear, so the smallest cut keeps one rule.
RULES_TINY = [(10, 79, 80, 12)]


def rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(a: str, b: str, t: float) -> tuple[int, int, int]:
    x, y = rgb(a), rgb(b)
    return tuple(round(x[i] + (y[i] - x[i]) * t) for i in range(3))


# ---------------------------------------------------------------- the mark


def _subpaths(d: str) -> list[list[tuple[float, float]]]:
    """Flatten the outline. It only uses M, H, V, Q and Z."""
    out, cur, pos = [], [], (0.0, 0.0)
    for cmd, args in re.findall(r"([MHVQZ])([^MHVQZ]*)", d):
        n = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", args)]
        if cmd == "M":
            cur = [(n[0], n[1])]
            pos = cur[0]
        elif cmd == "H":
            pos = (n[0], pos[1])
            cur.append(pos)
        elif cmd == "V":
            pos = (pos[0], n[0])
            cur.append(pos)
        elif cmd == "Q":
            for i in range(0, len(n), 4):
                cx, cy, ex, ey = n[i:i + 4]
                for s in range(1, 17):
                    t = s / 16
                    cur.append((
                        (1 - t) ** 2 * pos[0] + 2 * (1 - t) * t * cx + t * t * ex,
                        (1 - t) ** 2 * pos[1] + 2 * (1 - t) * t * cy + t * t * ey,
                    ))
                pos = (ex, ey)
        elif cmd == "Z":
            out.append(cur)
            cur = []
    return out


P_POLYS = _subpaths(P_OUTLINE)
SMALL_GROW = 1.06


def mark_mask(px: int, small: bool = False, tiny: bool = False) -> Image.Image:
    """The mark as an alpha mask, px square, drawn 4x and reduced."""
    ss = 4
    n = px * ss
    k = n / 100
    tx, ty, sc = P_TRANSFORM
    grow = SMALL_GROW if small else 1.0
    mask = Image.new("L", (n, n), 0)
    for poly in P_POLYS:
        layer = Image.new("L", (n, n), 0)
        pts = []
        for x, y in poly:
            ux, uy = tx + x * sc, ty - y * sc
            ux, uy = 50 + (ux - 50) * grow, 72 + (uy - 72) * grow
            pts.append((ux * k, uy * k))
        ImageDraw.Draw(layer).polygon(pts, fill=255)
        # Even-odd: the bowl's counter is a second contour and knocks out.
        mask = ImageChops.difference(mask, layer)
    d = ImageDraw.Draw(mask)
    for x, y, w, h in (RULES_TINY if tiny else RULES_SMALL if small else RULES_FULL):
        d.rectangle([x * k, y * k, (x + w) * k - 1, (y + h) * k - 1], fill=255)
    return mask.resize((px, px), Image.LANCZOS)


def mark_svg(fg: str, bg: str | None = None, small: bool = False) -> str:
    tx, ty, sc = P_TRANSFORM
    p = f'<path fill="{fg}" transform="translate({tx} {ty}) scale({sc} -{sc})" d="{P_OUTLINE}"/>'
    if small:
        p = f'<g transform="translate(50 72) scale({SMALL_GROW}) translate(-50 -72)">{p}</g>'
    rules = "".join(
        f'<rect fill="{fg}" x="{x}" y="{y}" width="{w}" height="{h}"/>'
        for x, y, w, h in (RULES_SMALL if small else RULES_FULL)
    )
    back = f'<rect width="100" height="100" fill="{bg}"/>' if bg else ""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" role="img" '
        f'aria-label="PriorLine"><title>PriorLine</title>{back}{p}{rules}</svg>\n'
    )


# ------------------------------------------------------------------- type


def font(name: str, size: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / f"{name}.ttf"), round(size))


SERIF = "Besley-800"
SERIF_BLACK = "Besley-900"
ITALIC = "Besley-Italic-500"
SANS = "Archivo-100-400"
SANS_BOLD = "Archivo-100-700"
CAPS = "Archivo-78-700"


class Sheet:
    """A canvas in logical pixels, drawn at twice the size and reduced."""

    S = 2

    def __init__(self, w: int, h: int, bg: str):
        self.w, self.h = w, h
        self.im = Image.new("RGB", (w * self.S, h * self.S), rgb(bg))
        self.d = ImageDraw.Draw(self.im)

    def rect(self, x, y, w, h, fill):
        s = self.S
        self.d.rectangle([x * s, y * s, (x + w) * s - 1, (y + h) * s - 1], fill=fill)

    def frame(self, x, y, w, h, fill, t=1.0):
        self.rect(x, y, w, t, fill)
        self.rect(x, y + h - t, w, t, fill)
        self.rect(x, y, t, h, fill)
        self.rect(x + w - t, y, t, h, fill)

    def width(self, s, name, size, track=0.0):
        f = font(name, size * self.S)
        return (f.getlength(s) + track * size * self.S * max(len(s) - 1, 0)) / self.S

    def text(self, x, y, s, name, size, fill, track=0.0, align="l"):
        """Baseline text. `track` is letterspacing in ems."""
        f = font(name, size * self.S)
        if align != "l":
            w = self.width(s, name, size, track)
            x = x - w if align == "r" else x - w / 2
        px, py = x * self.S, y * self.S
        if not track:
            self.d.text((px, py), s, font=f, fill=fill, anchor="ls")
            return
        for ch in s:
            self.d.text((px, py), ch, font=f, fill=fill, anchor="ls")
            px += f.getlength(ch) + track * size * self.S

    def wrap(self, x, y, s, name, size, fill, max_w, lead=1.4):
        """Wrapped text. Returns the baseline after the last line."""
        line, lines = "", []
        for word in s.split():
            trial = f"{line} {word}".strip()
            if self.width(trial, name, size) > max_w and line:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
        for ln in lines:
            self.text(x, y, ln, name, size, fill)
            y += size * lead
        return y

    def mark(self, x, y, size, fill, small=False):
        m = mark_mask(round(size * self.S), small)
        colour = rgb(fill) if isinstance(fill, str) else fill
        self.im.paste(Image.new("RGB", m.size, colour), (round(x * self.S), round(y * self.S)), m)

    def double_rule(self, x, y, w, fill, heavy=3.0, gap=3.0, light=1.0):
        self.rect(x, y, w, heavy, fill)
        self.rect(x, y + heavy + gap, w, light, fill)

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.im.resize((self.w, self.h), Image.LANCZOS).save(path, optimize=True)


def paper(w: int, h: int, margin: float | None = None, pitch: float = 0) -> Sheet:
    """Ledger paper with the red double margin rule down the left."""
    c = Sheet(w, h, PAPER)
    if pitch:
        feint = mix(PAPER, RULE, 0.38)
        y = pitch
        while y < h:
            c.rect(0, y, w, 1, feint)
            y += pitch
    if margin is not None:
        c.rect(margin, 0, 1.5, h, rgb(MARGIN))
        c.rect(margin + 6, 0, 1.5, h, rgb(MARGIN))
    return c


def cloth(w: int, h: int) -> Sheet:
    """Dark bookcloth with the oxblood headband across the top."""
    c = Sheet(w, h, CLOTH)
    c.rect(0, 0, w, max(4, h * 0.012), rgb(OXBLOOD))
    return c


def lockup(c: Sheet, x, y, size, fill):
    """Mark and wordmark on one baseline. y is the top of the mark."""
    c.mark(x, y, size, fill)
    c.text(x + size * 0.98, y + size * 0.69, "PriorLine", SERIF, size * 0.62, fill)


def tagline(c: Sheet, x, y, size, first, second, rule, align="l", stacked=True):
    """The line itself: the prior upright, the line in italic on a double rule."""
    kw = dict(heavy=max(2, size * 0.035), gap=max(2, size * 0.03), light=max(1, size * 0.014))
    w = c.width(TAG_B, ITALIC, size)
    if stacked:
        c.text(x, y, TAG_A, SERIF_BLACK, size, first, align=align)
        y += size * 1.06
        c.text(x, y, TAG_B, ITALIC, size, second, align=align)
        c.double_rule(x if align == "l" else x - w / 2, y + size * 0.14, w, rule, **kw)
        return y + size * 0.3
    wa = c.width(TAG_A + " ", SERIF_BLACK, size)
    c.text(x, y, TAG_A, SERIF_BLACK, size, first)
    c.text(x + wa, y, TAG_B, ITALIC, size, second)
    c.double_rule(x + wa, y + size * 0.14, w, rule, **kw)
    return y + size * 0.3


# ------------------------------------------------------------------ logos


def wordmark_svg(fill: str, with_mark: bool, stacked: bool = False) -> str:
    """Outlined, so it renders anywhere without the font installed."""
    f = TTFont(str(FONTS / f"{SERIF}.ttf"))
    gs, cmap, upm = f.getGlyphSet(), f.getBestCmap(), f["head"].unitsPerEm
    x, parts = 0.0, []
    for ch in "PriorLine":
        g = cmap[ord(ch)]
        pen = SVGPathPen(gs)
        gs[g].draw(pen)
        parts.append(f'<path transform="translate({x:.1f} 0)" d="{pen.getCommands()}"/>')
        x += gs[g].width
    k = 62 / upm  # the same cap-to-mark ratio as the site masthead
    w = x * k
    tx, ty, sc = P_TRANSFORM

    def word(ox, oy):
        return f'<g fill="{fill}" transform="translate({ox:.2f} {oy}) scale({k:.5f} -{k:.5f})">{"".join(parts)}</g>'

    def mark(ox):
        return (
            f'<g fill="{fill}" transform="translate({ox:.2f} 0)">'
            f'<path transform="translate({tx} {ty}) scale({sc} -{sc})" d="{P_OUTLINE}"/>'
            + "".join(f'<rect x="{a}" y="{b}" width="{c_}" height="{d_}"/>' for a, b, c_, d_ in RULES_FULL)
            + "</g>"
        )

    if not with_mark:
        vb, body = f"0 0 {w + 8:.1f} 76", word(4, 58)
    elif stacked:
        width = max(w, 100) + 8
        vb = f"0 0 {width:.1f} 172"
        body = mark((width - 100) / 2) + word((width - w) / 2, 156)
    else:
        vb, body = f"0 0 {100 + w + 6:.1f} 100", mark(0) + word(98, 69)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" role="img" aria-label="PriorLine">'
        f"<title>PriorLine</title>{body}</svg>\n"
    )


def png_from_mask(mask: Image.Image, fg: str, bg: str | None) -> Image.Image:
    if bg:
        im = Image.new("RGB", mask.size, rgb(bg))
        im.paste(Image.new("RGB", mask.size, rgb(fg)), (0, 0), mask)
        return im
    im = Image.new("RGBA", mask.size, rgb(fg) + (0,))
    im.putalpha(mask)
    return im


def lockup_png(path: Path, fg: str, bg: str | None, stacked: bool, height: int = 512):
    """Raster lockups on a transparent or solid ground."""
    size = height * 0.58 if stacked else height
    ww = Sheet(4, 4, BLACK).width("PriorLine", SERIF, size * 0.62)
    pad = size * 0.18
    if stacked:
        w, h = round(max(ww, size) + pad * 2), round(size * 1.72 + pad * 2)
    else:
        w, h = round(size * 0.98 + ww + pad * 2), round(size + pad * 2)
    # Drawn white on black, then used as the alpha of the ink colour.
    c = Sheet(w, h, BLACK)
    if stacked:
        c.mark((w - size) / 2, pad, size, WHITE)
        c.text(w / 2, pad + size * 1.52, "PriorLine", SERIF, size * 0.62, WHITE, align="c")
    else:
        lockup(c, pad, pad, size, WHITE)
    mask = c.im.resize((w, h), Image.LANCZOS).convert("L")
    path.parent.mkdir(parents=True, exist_ok=True)
    png_from_mask(mask, fg, bg).save(path, optimize=True)


def build_logos():
    d = OUT / "logo"
    (d / "svg").mkdir(parents=True, exist_ok=True)
    (d / "png").mkdir(parents=True, exist_ok=True)
    looks = {
        "gold-on-cloth": (GOLD, CLOTH),
        "ink-on-paper": (INK, PAPER),
        "gold": (GOLD, None),
        "ink": (INK, None),
        "black": (BLACK, None),
        "white": (WHITE, None),
    }
    for name, (fg, bg) in looks.items():
        (d / "svg" / f"mark-{name}.svg").write_text(mark_svg(fg, bg), encoding="utf-8")
        for px in (256, 1024, 2048):
            png_from_mask(mark_mask(px), fg, bg).save(d / "png" / f"mark-{name}-{px}.png", optimize=True)
        if bg is None:
            (d / "svg" / f"wordmark-{name}.svg").write_text(wordmark_svg(fg, False), encoding="utf-8")
            (d / "svg" / f"lockup-horizontal-{name}.svg").write_text(wordmark_svg(fg, True), encoding="utf-8")
            (d / "svg" / f"lockup-stacked-{name}.svg").write_text(wordmark_svg(fg, True, True), encoding="utf-8")
        lockup_png(d / "png" / f"lockup-horizontal-{name}.png", fg, bg, False)
        lockup_png(d / "png" / f"lockup-stacked-{name}.png", fg, bg, True, 900)


# ------------------------------------------------------------------ icons


def icon(px: int, fg=GOLD, bg=CLOTH, scale=1.0, small=None) -> Image.Image:
    small = px <= 48 if small is None else small
    im = Image.new("RGB", (px, px), rgb(bg))
    inner = round(px * scale)
    m = mark_mask(inner, small, tiny=inner <= 20)
    off = (px - inner) // 2
    im.paste(Image.new("RGB", m.size, rgb(fg)), (off, off), m)
    return im


IOS = {20: (1, 2, 3), 29: (1, 2, 3), 40: (1, 2, 3), 60: (2, 3), 76: (1, 2), 83.5: (2,)}


def build_icons():
    d = OUT / "icons"
    (d / "ios").mkdir(parents=True, exist_ok=True)
    (d / "favicon.svg").write_text(mark_svg(GOLD, CLOTH, small=True), encoding="utf-8")
    for px in (16, 32, 48, 96, 192, 512):
        icon(px).save(d / f"favicon-{px}.png", optimize=True)
    icon(48).save(d / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)],
                  append_images=[icon(16), icon(32)])
    icon(180).save(d / "apple-touch-icon.png", optimize=True)
    icon(192).save(d / "android-chrome-192.png", optimize=True)
    icon(512).save(d / "android-chrome-512.png", optimize=True)
    # A launcher crops a maskable icon to a circle or a squircle, so the mark
    # sits inside the central 80% safe zone.
    icon(512, scale=0.72).save(d / "android-maskable-512.png", optimize=True)
    # App Store icon: 1024 square, opaque, corners left square. iOS rounds them.
    icon(1024, scale=0.86).save(d / "ios" / "app-icon-1024.png", optimize=True)
    icon(1024, fg=INK, bg=PAPER, scale=0.86).save(d / "ios" / "app-icon-1024-paper.png", optimize=True)
    for pt, scales in IOS.items():
        for s in scales:
            px = round(pt * s)
            icon(px, scale=0.86, small=px <= 48).save(d / "ios" / f"icon-{pt}@{s}x.png", optimize=True)
    manifest = {
        "name": "PriorLine",
        "short_name": "PriorLine",
        "description": "NFL player prop projections, graded against the price.",
        "start_url": "/",
        "display": "standalone",
        "background_color": PAPER,
        "theme_color": CLOTH,
        "icons": [
            {"src": "/android-chrome-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/android-chrome-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/android-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    (d / "site.webmanifest").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


# ----------------------------------------------------------------- social


def footer(c: Sheet, pad: float, fg, dim, size=None):
    """Every post carries the address and the notice. No exceptions."""
    size = size or max(15, c.w * 0.02)
    y = c.h - (c.h * 0.14 if c.h > c.w * 1.6 else pad)
    c.rect(pad, y - size * 2.5, c.w - pad * 2, 1, dim)
    c.mark(pad, y - size * 1.75, size * 1.9, fg)
    c.text(pad + size * 2.1, y - size * 0.45, SITE, CAPS, size, fg, track=0.08)
    c.text(c.w - pad, y - size * 0.45, LEGAL, SANS, size * 0.86, dim, align="r")


def build_social():
    d = OUT / "social"
    soft = mix(CLOTH, PAPER, 0.78)
    hair = mix(CLOTH, GOLD, 0.32)

    # Profile pictures. Every network crops these to a circle, so the mark
    # holds inside the middle 62%.
    (d / "profile").mkdir(parents=True, exist_ok=True)
    for name, fg, bg in (("cloth", GOLD, CLOTH), ("paper", INK, PAPER)):
        for px in (400, 1080):
            icon(px, fg, bg, scale=0.62, small=False).save(d / "profile" / f"profile-{name}-{px}.png", optimize=True)

    # X header, 1500x500. The avatar covers the lower left, so that corner is
    # left empty and the type sits right of centre.
    c = cloth(1500, 500)
    c.frame(28, 34, 1444, 438, hair)
    tagline(c, 470, 215, 78, PAPER, GOLD, rgb(GOLD), stacked=False)
    c.wrap(474, 300, "I project every NFL player prop, pick a side, and grade every pick against the price it was offered at. The whole record is posted, including the parts written in red.", SANS, 25, soft, 900, lead=1.45)
    c.text(1440, 440, SITE, CAPS, 20, GOLD, track=0.12, align="r")
    c.save(d / "banners" / "x-header-1500x500.png")

    # LinkedIn company cover, 1128x191. The logo covers the lower left.
    c = cloth(1128, 191)
    tagline(c, 300, 108, 58, PAPER, GOLD, rgb(GOLD), stacked=False)
    c.text(302, 158, "NFL player props, graded against the price. The record is public.", SANS, 19, soft)
    c.text(1096, 158, SITE, CAPS, 15, GOLD, track=0.12, align="r")
    c.save(d / "banners" / "linkedin-company-1128x191.png")

    # LinkedIn personal cover, 1584x396.
    c = cloth(1584, 396)
    c.frame(24, 28, 1536, 344, hair)
    tagline(c, 560, 180, 84, PAPER, GOLD, rgb(GOLD), stacked=False)
    c.wrap(563, 258, "I build PriorLine: NFL player prop projections, graded in public against the prices the books offered.", SANS, 25, soft, 900, lead=1.45)
    c.text(1524, 344, SITE, CAPS, 19, GOLD, track=0.12, align="r")
    c.save(d / "banners" / "linkedin-personal-1584x396.png")

    # Facebook cover, 1640x624, centred so the mobile crop keeps the type.
    c = cloth(1640, 624)
    c.frame(30, 36, 1580, 558, hair)
    end = tagline(c, 820, 270, 112, PAPER, GOLD, rgb(GOLD), align="c")
    c.text(820, end + 62, "NFL player props, graded against the price.", SANS, 28, soft, align="c")
    c.save(d / "banners" / "facebook-cover-1640x624.png")

    # YouTube channel art, 2560x1440, everything inside the 1546x423 safe area.
    c = cloth(2560, 1440)
    end = tagline(c, 1280, 690, 150, PAPER, GOLD, rgb(GOLD), align="c")
    c.text(1280, end + 66, "NFL player props, graded against the price.", SANS, 38, soft, align="c")
    c.save(d / "banners" / "youtube-channel-2560x1440.png")

    # Link preview, 1200x630. The site in one frame: cloth masthead, ruled
    # paper, the red margin and the line.
    c = paper(1200, 630, margin=64, pitch=42)
    c.rect(0, 0, 1200, 96, rgb(CLOTH))
    c.rect(0, 0, 1200, 6, rgb(OXBLOOD))
    lockup(c, 98, 22, 58, GOLD)
    c.text(1136, 62, SITE, CAPS, 18, GOLD, track=0.12, align="r")
    end = tagline(c, 104, 262, 118, INK, BLUE, rgb(BLUE))
    c.wrap(106, end + 62, "NFL player prop projections, graded against the price they were offered at. The whole record is posted, including the parts written in red.", SANS, 29, INK, 940, lead=1.42)
    c.save(d / "og-image-1200x630.png")


# -------------------------------------------------------------- templates

TIER_INK = {"elite": "#4a2a7a", "strong": "#17524f", "medium": "#6b4a12", "small": INK_DIM}


def stamp(c: Sheet, x, y, label, colour, size):
    """A rubber-stamp tier label. y is the baseline."""
    w = c.width(label, CAPS, size, 0.1) + size * 1.1
    c.frame(x, y - size * 1.22, w, size * 1.72, rgb(colour), t=max(1.5, size * 0.09))
    c.text(x + size * 0.55, y, label, CAPS, size, colour, track=0.1)


def acct(v: float) -> tuple[str, str]:
    """A figure the way a ledger writes it: losses in red brackets."""
    return (f"({abs(v):.1f})", RED) if v < 0 else (f"+{v:.1f}", INK)


def head(c: Sheet, pad, title, sub, size):
    """Leaf heading: serif title, italic line under it, heavy rule."""
    # A story loses its top and bottom 250px to the app chrome, so the leaf
    # starts lower there.
    top = c.h * 0.17 if c.h > c.w * 1.6 else pad
    c.text(pad, top + size, title, SERIF, size, INK)
    y = top + size * 1.62
    if sub:
        c.text(pad, y, sub, ITALIC, size * 0.44, INK_DIM)
        y += size * 0.4
    c.rect(pad, y, c.w - pad * 2, max(2, size * 0.05), rgb(INK))
    return y


def leaf(w, h):
    return paper(w, h, margin=round(w * 0.045)), round(w * 0.1)


def t_ledger(path: Path, w, h, record, asof):
    c, pad = leaf(w, h)
    u = w / 1080
    inner = w - pad * 2
    y = head(c, pad, "Posted account", f"Every graded pick, by tier. {asof}" if record else "Every graded pick, by tier", 62 * u)
    tall = h >= 1200 * u
    row, fig = (118, 37) if tall else (92, 31)
    if h > w * 1.6:
        row = 150
    cols = [("PICKS", 0.50), ("WON", 0.665), ("NEEDED", 0.83), ("DIFF", 1.0)]
    y += 46 * u
    c.text(pad, y, "TIER", CAPS, 19 * u, INK_DIM, track=0.1)
    for label, f in cols:
        c.text(pad + inner * f, y, label, CAPS, 19 * u, INK_DIM, track=0.1, align="r")
    y += 18 * u
    c.rect(pad, y, inner, 1.5, rgb(INK))
    by = {r["edge_tier"]: r for r in (record or [])}
    for t in ("elite", "strong", "medium", "small"):
        y += row * u
        r = by.get(t)
        stamp(c, pad, y - 6 * u, t.upper(), TIER_INK[t], 21 * u)
        if r and r.get("hit_rate") is not None and r.get("breakeven") is not None:
            vals = [f"{r['picks']:,}", f"{r['hit_rate'] * 100:.1f}%", f"{r['breakeven'] * 100:.1f}%"]
            for (label, f), v, nm in zip(cols, vals, (SANS, SANS_BOLD, SANS)):
                c.text(pad + inner * f, y, v, nm, fig * u, INK, align="r")
            dtxt, dcol = acct((r["hit_rate"] - r["breakeven"]) * 100)
            c.text(pad + inner, y, dtxt, SANS_BOLD, fig * u, dcol, align="r")
        c.rect(pad, y + 30 * u, inner, 1, rgb(RULE))
    y += 30 * u
    c.double_rule(pad, y, inner, rgb(INK), heavy=3 * u, gap=4 * u, light=1.5 * u)
    y += 70 * u
    y = c.wrap(pad, y, "Won is the share of picks that were correct. Needed is the share that had to be correct, at the prices offered, to finish level. Brackets mean a loss.", SANS, (30 if tall else 25) * u, INK, inner, lead=1.5)
    if h - y > 330 * u:
        c.text(pad, y + 70 * u, "Elite is the tier I put forward.", SERIF, 50 * u, INK)
        c.text(pad, y + 128 * u, "The rest is shown so you can check me.", ITALIC, 36 * u, BLUE)
    footer(c, pad, INK, rgb(INK_DIM), size=22 * u)
    c.save(path)


def t_entry(path: Path, w, h, blank=False):
    """One pick. The layout version carries placeholders, never a real name."""
    c, pad = leaf(w, h)
    u = w / 1080
    inner = w - pad * 2
    y = head(c, pad, "This week's entry", None if blank else "Week 0, game, kickoff", 62 * u)
    if not blank:
        y += 96 * u
        stamp(c, pad, y, "ELITE", TIER_INK["elite"], 21 * u)
        y += 104 * u
        c.text(pad, y, "Player Name", SERIF, 78 * u, INK)
        c.text(pad, y + 52 * u, "Market, opponent", ITALIC, 32 * u, INK_DIM)
        y += 160 * u
        rows = (("OUR PRIOR", "00.0", INK), ("THEIR LINE", "00.5", BLUE), ("THE PICK", "Under 00.5", INK))
        tight = h < 1200 * u
        for i, (label, val, col) in enumerate(rows):
            c.text(pad, y, label, CAPS, 20 * u, INK_DIM, track=0.1)
            c.text(pad + inner, y + 10 * u, val, SERIF, (54 if tight else 86) * u, col, align="r")
            if i < 2:
                c.rect(pad, y + 34 * u, inner, 1, rgb(RULE))
            else:
                c.double_rule(pad, y + 38 * u, inner, rgb(INK), heavy=3 * u, gap=4 * u, light=1.5 * u)
            y += (88 if tight else 140) * u
        if not tight:
            c.wrap(pad, y + 20 * u, "Elite picks have won 00.0% against the 00.0% their prices needed. This one is graded in public either way.", SANS, 31 * u, INK, inner, lead=1.5)
    footer(c, pad, INK, rgb(INK_DIM), size=22 * u)
    c.save(path)


def t_statement(path: Path, w, h, blank=False):
    c = cloth(w, h)
    u = w / 1080
    pad = round(w * 0.1)
    c.frame(pad * 0.45, pad * 0.5, w - pad * 0.9, h - pad * 0.95, mix(CLOTH, GOLD, 0.32), t=1.5)
    if not blank:
        lines = [("A sportsbook keeps", SERIF, PAPER), ("its book closed.", SERIF, PAPER), ("This one is open.", ITALIC, GOLD)]
        size = 82 * u
        y = h * 0.46 - (len(lines) - 1) * size * 0.58
        for s, f, col in lines:
            c.text(pad, y, s, f, size, col)
            y += size * 1.16
        y -= size * 1.16
        c.double_rule(pad, y + size * 0.2, c.width(lines[-1][0], ITALIC, size), rgb(GOLD), heavy=3 * u, gap=4 * u, light=1.5 * u)
        c.text(pad, y + size * 1.05, "Including the parts written in red.", SANS, 30 * u, mix(CLOTH, PAPER, 0.75))
    footer(c, pad, GOLD, mix(CLOTH, PAPER, 0.5), size=22 * u)
    c.save(path)


def t_red(path: Path, w, h, blank=False):
    """The losing week. It gets the same care as a winning one."""
    c, pad = leaf(w, h)
    u = w / 1080
    inner = w - pad * 2
    y = head(c, pad, "In red this week", None if blank else "Week 0, every pick graded", 62 * u)
    if not blank:
        tight = h < 1200 * u
        y += (200 if tight else 270) * u
        c.text(pad, y, "(0.0)", SERIF_BLACK, (160 if tight else 210) * u, RED)
        y += 66 * u
        c.text(pad, y, "points short of what the prices needed", ITALIC, 34 * u, INK_DIM)
        y += 40 * u
        c.double_rule(pad, y, inner, rgb(INK), heavy=3 * u, gap=4 * u, light=1.5 * u)
        y += 80 * u
        c.wrap(pad, y, "00 picks, 00 won. I needed 00.0% and got 00.0%. The week is in the record with the rest, and it stays there.", SANS, 34 * u, INK, inner, lead=1.5)
    footer(c, pad, INK, rgb(INK_DIM), size=22 * u)
    c.save(path)


STEPS = [
    ("Project", "A model projects every stat for every player the books have priced. It gives the middle of the range, the number a player beats half the time, because that is what a line is."),
    ("Compare", "The projection goes beside the sportsbook line. The gap between them, as a share of the line, is how strongly the two disagree."),
    ("Rank", "Within each market the board is ranked by that gap. The top fifth is Elite, and Elite is the only tier I put forward as a pick."),
    ("Grade", "After the game every pick is graded against the price it was offered at, and the result goes in the record. Wins and losses both stay."),
]


def t_explainer(path: Path, w, h, number=None, title="", body="", blank=False):
    c, pad = leaf(w, h)
    u = w / 1080
    inner = w - pad * 2
    if blank:
        pass
    elif number is None:
        y = h * 0.36
        for ln in ("How an entry", "gets written"):
            c.text(pad, y, ln, SERIF_BLACK, 104 * u, INK)
            y += 112 * u
        c.text(pad, y - 20 * u, "Four steps, in the order they happen.", ITALIC, 38 * u, BLUE)
        c.double_rule(pad, y + 20 * u, inner, rgb(INK), heavy=3 * u, gap=4 * u, light=1.5 * u)
    else:
        c.text(pad, pad + 150 * u, f"{number}.", ITALIC, 170 * u, BLUE)
        c.text(pad, pad + 300 * u, title, SERIF_BLACK, 92 * u, INK)
        c.rect(pad, pad + 344 * u, inner, 3 * u, rgb(INK))
        c.wrap(pad, pad + 440 * u, body, SANS, 37 * u, INK, inner, lead=1.5)
    footer(c, pad, INK, rgb(INK_DIM), size=22 * u)
    c.save(path)


def build_templates(record, asof):
    d = OUT / "templates"
    for name, (w, h) in {"portrait-1080x1350": (1080, 1350), "square-1080x1080": (1080, 1080)}.items():
        if record:
            t_ledger(d / "ledger" / f"ledger-live-{name}.png", w, h, record, asof)
        t_ledger(d / "ledger" / f"ledger-blank-{name}.png", w, h, None, asof)
        t_entry(d / "entry" / f"entry-layout-{name}.png", w, h)
        t_entry(d / "entry" / f"entry-blank-{name}.png", w, h, blank=True)
        t_statement(d / "statement" / f"statement-example-{name}.png", w, h)
        t_statement(d / "statement" / f"statement-blank-{name}.png", w, h, blank=True)
        t_red(d / "in-red" / f"in-red-layout-{name}.png", w, h)
        t_red(d / "in-red" / f"in-red-blank-{name}.png", w, h, blank=True)
    w, h = 1080, 1350
    t_explainer(d / "explainer" / "explainer-0-cover.png", w, h)
    for i, (title, body) in enumerate(STEPS, 1):
        t_explainer(d / "explainer" / f"explainer-{i}-{title.lower()}.png", w, h, i, title, body)
    t_explainer(d / "explainer" / "explainer-blank.png", w, h, blank=True)
    # Stories and Reels covers, 1080x1920.
    t_statement(d / "story" / "story-statement-1080x1920.png", 1080, 1920)
    t_statement(d / "story" / "story-blank-cloth-1080x1920.png", 1080, 1920, blank=True)
    t_explainer(d / "story" / "story-blank-paper-1080x1920.png", 1080, 1920, blank=True)
    if record:
        t_ledger(d / "story" / "story-ledger-live-1080x1920.png", 1080, 1920, record, asof)
    # Landscape cards for X and LinkedIn.
    for name, (w, h) in {"x-1600x900": (1600, 900), "linkedin-1200x627": (1200, 627)}.items():
        u = w / 1600
        pad = round(w * 0.07)
        c = cloth(w, h)
        c.frame(pad * 0.45, pad * 0.5, w - pad * 0.9, h - pad * 0.95, mix(CLOTH, GOLD, 0.32), t=1.5)
        end = tagline(c, pad, h * 0.4, 118 * u, PAPER, GOLD, rgb(GOLD), stacked=False)
        c.wrap(pad + 4 * u, end + 70 * u, "Every NFL player prop, graded against the price it was offered at. The whole record is posted.", SANS, 40 * u, mix(CLOTH, PAPER, 0.78), w * 0.7, lead=1.45)
        footer(c, pad, GOLD, mix(CLOTH, PAPER, 0.5), size=22 * u)
        c.save(d / "landscape" / f"statement-{name}.png")
        c = paper(w, h, margin=round(w * 0.035))
        footer(c, pad, INK, rgb(INK_DIM), size=22 * u)
        c.save(d / "landscape" / f"blank-paper-{name}.png")
        c = cloth(w, h)
        footer(c, pad, GOLD, mix(CLOTH, PAPER, 0.5), size=22 * u)
        c.save(d / "landscape" / f"blank-cloth-{name}.png")


# ------------------------------------------------------------------- main


def fetch_record(api: str | None):
    if not api:
        return None
    req = urllib.request.Request(
        f"{api.rstrip('/')}/record/by_tier?source=all",
        headers={"User-Agent": "priorline-brand-build"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["tiers"]


def publish_web():
    """Copy the assets the site itself serves."""
    icons = OUT / "icons"
    for name in ("favicon.svg", "favicon.ico", "favicon-32.png", "apple-touch-icon.png",
                 "android-chrome-192.png", "android-chrome-512.png",
                 "android-maskable-512.png", "site.webmanifest"):
        shutil.copyfile(icons / name, PUBLIC / name)
    shutil.copyfile(OUT / "social" / "og-image-1200x630.png", PUBLIC / "og-image.png")


def main():
    ap = argparse.ArgumentParser(description="Build the PriorLine brand pack.")
    ap.add_argument("--api", help="API base, to fill the ledger template from the live record")
    ap.add_argument("--no-web", action="store_true", help="do not copy icons into apps/web/public")
    a = ap.parse_args()
    record = fetch_record(a.api)
    today = dt.date.today()
    build_logos()
    build_icons()
    build_social()
    build_templates(record, f"As of {today.day} {today:%b %Y}.")
    if not a.no_web:
        publish_web()
    n = sum(1 for p in OUT.rglob("*") if p.is_file() and p.suffix in (".png", ".svg", ".ico"))
    print(f"{n} assets in {OUT}")


if __name__ == "__main__":
    main()
