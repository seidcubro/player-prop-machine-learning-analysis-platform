"""Generate the whole PropSignal brand pack from one definition of the mark.

Everything here is drawn from the same geometry and the same three colours, so
the favicon, the app icon and the LinkedIn banner cannot drift apart the way they
do when each one is exported by hand from a different file.

The mark is a broken ring with an ECG waveform running through it: the ring is
the market, the waveform is the signal cutting across it, and the gap in the ring
is where the signal breaks through. The beam off the upper right is the
"emitting" cue and only appears on the icon lockups, never inside a banner where
it would collide with type.

Colours are the product's own brand ramp, cyan to blue to violet. Nothing here is
green: green means "over" or "won" in the app, and a logo has no business
borrowing a semantic colour.

Run:  python scripts/build_brand.py [output_dir]
"""

import math
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

CYAN = (34, 211, 238)
BLUE = (77, 124, 254)
VIOLET = (168, 85, 247)
INK = (6, 8, 15)
PAPER = (247, 249, 255)
TEXT_DIM = (151, 161, 189)

# The waveform, in the mark's own 100x100 coordinate space.
WAVE = [(4, 56), (30, 56), (38, 34), (46, 74), (54, 24),
        (62, 70), (68, 48), (72, 56), (96, 56)]
RING_C = (50, 56)
RING_R = 36


def linear_gradient(size, stops, angle=45.0):
    """An RGB gradient image. `stops` is [(pos 0-1, (r,g,b)), ...]."""
    w, h = size
    grad = Image.new("RGB", (w, h))
    px = grad.load()
    rad = math.radians(angle)
    dx, dy = math.cos(rad), -math.sin(rad)
    # Project every pixel onto the gradient axis and normalise to 0..1.
    span = abs(dx) * w + abs(dy) * h
    ox = 0 if dx >= 0 else w
    oy = 0 if dy >= 0 else h
    for y in range(h):
        for x in range(w):
            t = ((x - ox) * dx + (y - oy) * dy) / (span or 1)
            t = min(1.0, max(0.0, t))
            for i in range(len(stops) - 1):
                p0, c0 = stops[i]
                p1, c1 = stops[i + 1]
                if p0 <= t <= p1:
                    f = (t - p0) / (p1 - p0 or 1)
                    px[x, y] = tuple(int(c0[k] + (c1[k] - c0[k]) * f) for k in range(3))
                    break
            else:
                px[x, y] = stops[-1][1]
    return grad


def mark_layer(size, beam=False, supersample=4):
    """The mark as a transparent RGBA image, gradient-filled and antialiased.

    Drawn at `supersample` times the requested size and reduced afterwards,
    because PIL has no antialiasing of its own and a hard-edged circle at
    favicon size looks like a screenshot of 1998.
    """
    s = size * supersample
    mask = Image.new("L", (s, s), 0)
    d = ImageDraw.Draw(mask)
    k = s / 100.0

    # The beam is gone.
    #
    # It was a filled triangle at partial alpha, which the gradient then painted
    # in dark violet against a dark ground, so at every size it read as a chip
    # out of the ring rather than light leaving it. A logo cannot have an
    # element that looks like a rendering fault. The ring and the waveform carry
    # the idea on their own.

    # Ring, with symmetric gaps exactly where the waveform crosses it.
    #
    # The waveform is horizontal at the ring's own centre height, so it meets
    # the circle at 0 and 180 degrees. Opening the ring at precisely those two
    # points makes the signal read as passing through, and keeps the two gaps
    # balanced. The previous arcs left a 24-degree gap on one side and a
    # 2-degree nick on the other, which just looked like a mistake.
    ring_w = max(1, int(5.0 * k))
    box = [(RING_C[0] - RING_R) * k, (RING_C[1] - RING_R) * k,
           (RING_C[0] + RING_R) * k, (RING_C[1] + RING_R) * k]
    for start, end in ((14, 166), (194, 346)):
        d.arc(box, start, end, fill=255, width=ring_w)

    # Waveform on top, with round joins so the peaks are not chopped flat.
    wave_w = max(1, int(5.5 * k))
    pts = [(x * k, y * k) for x, y in WAVE]
    d.line(pts, fill=255, width=wave_w, joint="curve")
    r = wave_w / 2
    for x, y in pts:
        d.ellipse([x - r, y - r, x + r, y + r], fill=255)

    grad = linear_gradient((s, s), [(0.0, CYAN), (0.55, BLUE), (1.0, VIOLET)], angle=35)
    out = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    return out.resize((size, size), Image.LANCZOS)


def rounded_tile(size, radius_ratio=0.22, bg=INK):
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    d.rounded_rectangle([0, 0, size - 1, size - 1],
                        radius=int(size * radius_ratio), fill=bg + (255,))
    return tile


def font(size, bold=True):
    """A geometric-ish system font. The brand face is Space Grotesk, which is a
    webfont and is not installed here, so the rasters fall back to whatever
    Windows can offer that is closest in feel."""
    for name in ("bahnschrift.ttf", "segoeuib.ttf", "seguisb.ttf", "arialbd.ttf"):
        for base in ("C:/Windows/Fonts/", "/usr/share/fonts/truetype/dejavu/"):
            try:
                return ImageFont.truetype(base + name, size)
            except OSError:
                continue
    return ImageFont.load_default()


def wordmark(draw, xy, size, on_dark=True):
    """"PropSignal", with "Signal" carrying the brand colour."""
    f = font(size)
    x, y = xy
    a, b = "Prop", "Signal"
    draw.text((x, y), a, font=f, fill=(PAPER if on_dark else INK) + (255,), anchor="ls")
    wa = draw.textlength(a, font=f)
    draw.text((x + wa, y), b, font=f, fill=BLUE + (255,), anchor="ls")
    return draw.textlength(a + b, font=f)


def glow(img, mark, box, radius, strength=0.55):
    """Soft brand light behind the mark so a flat banner has some depth."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    layer.paste(mark, box, mark)
    layer = layer.filter(ImageFilter.GaussianBlur(radius))
    layer.putalpha(layer.getchannel("A").point(lambda v: int(v * strength)))
    img.alpha_composite(layer)


def banner(w, h, mark_px, out, tagline="Bet the signal, not the noise"):
    img = Image.new("RGBA", (w, h), INK + (255,))
    # Faint diagonal brand wash across the whole field.
    wash = linear_gradient((w, h), [(0.0, (10, 14, 28)), (0.5, (14, 20, 42)),
                                    (1.0, (20, 14, 40))], angle=20)
    img.alpha_composite(wash.convert("RGBA"))

    # Centred, not left-aligned.
    #
    # X drops the profile picture over the lower left of the header and both X
    # and LinkedIn crop the sides at narrow widths, so anything parked in the
    # left third is the first thing to get covered or cut. Centring the lockup
    # keeps it intact on every crop.
    m = mark_layer(mark_px, beam=False)
    d = ImageDraw.Draw(img)
    name_size = int(h * 0.20)
    tag_size = int(h * 0.075)
    gap = int(mark_px * 0.30)
    f_name, f_tag = font(name_size), font(tag_size)
    name_w = d.textlength("PropSignal", font=f_name)
    tag_w = d.textlength(tagline, font=f_tag)
    text_w = max(name_w, tag_w)
    total = mark_px + gap + text_w

    mx = int((w - total) / 2)
    my = (h - mark_px) // 2
    glow(img, m, (mx, my), radius=max(8, mark_px // 8))
    img.alpha_composite(m, (mx, my))

    tx = mx + mark_px + gap
    baseline = h // 2 + int(name_size * 0.10)
    wordmark(d, (tx, baseline), name_size)
    d.text((tx, baseline + int(name_size * 0.62)), tagline,
           font=f_tag, fill=TEXT_DIM + (255,), anchor="ls")
    img.convert("RGB").save(out, quality=95)
    return out


def main():
    dest = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(dest, exist_ok=True)

    def p(name):
        return os.path.join(dest, name)

    written = []

    # ------------------------------------------------------------- app icons
    for size in (1024, 512, 256, 180, 128, 64, 32, 16):
        tile = rounded_tile(size)
        inner = int(size * 0.80)
        m = mark_layer(inner)
        tile.alpha_composite(m, ((size - inner) // 2, (size - inner) // 2))
        name = f"icon-{size}.png" if size not in (180,) else "apple-touch-icon-180.png"
        tile.save(p(name))
        written.append(name)

    # A real multi-resolution .ico, which is still what Windows and some
    # crawlers reach for before they look at the SVG.
    base = rounded_tile(256)
    inner = int(256 * 0.80)
    base.alpha_composite(mark_layer(inner), ((256 - inner) // 2,) * 2)
    base.save(p("favicon.ico"), sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])
    written.append("favicon.ico")

    # ------------------------------------------------------- transparent mark
    mark_layer(1024).save(p("mark-1024-transparent.png"))
    written.append("mark-1024-transparent.png")

    # ------------------------------------------------------------- avatars
    # Square profile picture. Circular crops are the norm, so the mark sits well
    # inside the safe area rather than filling the frame.
    for size, name in ((400, "profile-400.png"), (800, "profile-800.png")):
        av = Image.new("RGBA", (size, size), INK + (255,))
        inner = int(size * 0.62)
        m = mark_layer(inner)
        off = (size - inner) // 2
        glow(av, m, (off, off), radius=size // 12)
        av.alpha_composite(m, (off, off))
        av.convert("RGB").save(p(name), quality=95)
        written.append(name)

    # ------------------------------------------------------------- banners
    banner(1500, 500, 300, p("x-twitter-banner-1500x500.png"))
    written.append("x-twitter-banner-1500x500.png")
    banner(1584, 396, 230, p("linkedin-banner-1584x396.png"))
    written.append("linkedin-banner-1584x396.png")
    banner(1200, 630, 300, p("og-image-1200x630.png"))
    written.append("og-image-1200x630.png")
    banner(1280, 640, 300, p("github-social-1280x640.png"))
    written.append("github-social-1280x640.png")

    # ------------------------------------------------------------- lockups
    for on_dark, name in ((True, "lockup-dark-2048.png"), (False, "lockup-light-2048.png")):
        W, H = 2048, 512
        bg = INK if on_dark else PAPER
        img = Image.new("RGBA", (W, H), bg + (255,))
        mp = 300
        m = mark_layer(mp)
        my = (H - mp) // 2
        img.alpha_composite(m, (140, my))
        d = ImageDraw.Draw(img)
        wordmark(d, (140 + mp + 80, H // 2 + 40), 150, on_dark=on_dark)
        img.convert("RGB").save(p(name), quality=95)
        written.append(name)

    print(f"wrote {len(written)} files to {os.path.abspath(dest)}")
    for w in sorted(written):
        print("  " + w)


if __name__ == "__main__":
    main()
