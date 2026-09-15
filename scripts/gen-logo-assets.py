#!/usr/bin/env python3
"""Regenerate the Orbital mark and every product icon from one set of numbers.

The logo is a locked geometric system (circles and quarter circles only).
Change the constants below, run `python3 scripts/gen-logo-assets.py`, commit.

Outputs
  assets/logo/orbital-logo.svg          vector master (512 canvas)
  assets/logo/orbital-logo.pdf / .ai    same vector, PDF-compatible (Illustrator opens .ai)
  assets/logo/orbital-logo-2048.png     transparent
  assets/logo/orbital-logo-2048.jpg     white background
  assets/icon.png / .ico / .icns        desktop app icon (white rounded tile)
  web/public/icon-512.png, icon-192.png, apple-touch-icon.png, favicon.ico
  docs/screenshots/orbital-logo.png     README logo (500, white)
  brand-video/logo-mark.png             512 transparent (gitignored)

Rendering uses the Playwright Chromium bundled in web/node_modules.
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- geometry
CANVAS = 512
O = (256.0, 256.0)        # orange disc centre = canvas centre
R_ORANGE = 138.0          # orange disc radius (max 141.9 before it touches a leaf)
D_NAVY = 123.0            # navy leaf body centre = O + (-D_NAVY, +D_NAVY)
D_RED = 129.0             # red bud body centre  = O + (+D_RED,  -D_RED)
NAVY_SIDE = 108.0         # navy quarter-disc square side; corner rounded side/2
RED_R = 40.5              # red bud body circle radius
STEM_W = 12.0             # black stem width
# Stem = quarter arc from the red centre to the navy centre; radius D_NAVY + D_RED.
# D_NAVY < D_RED by 6 so the orange disc has the same gap (≈4) to both leaves.

COLORS = dict(orange="#f4a38c", navy="#1a1a2e", red="#c1292e", stem="#000000")


def arc(c, r, p0, p1, through=None):
    """SVG arc on circle (c, r) from p0 to p1; short way unless `through` names a point on the wanted arc."""
    a0 = math.atan2(p0[1] - c[1], p0[0] - c[0])
    a1 = math.atan2(p1[1] - c[1], p1[0] - c[0])
    cw = (a1 - a0) % (2 * math.pi)
    if through is None:
        sweep = 1 if cw <= math.pi else 0
    else:
        am = (math.atan2(through[1] - c[1], through[0] - c[0]) - a0) % (2 * math.pi)
        sweep = 1 if am < cw else 0
    span = cw if sweep == 1 else 2 * math.pi - cw
    large = 1 if span > math.pi else 0
    return f" A {r:.3f} {r:.3f} 0 {large} {sweep} {p1[0]:.3f} {p1[1]:.3f}"


def navy_leaf(c, side):
    """Quarter disc: square of `side` centred on c; big arc centred on the square's
    bottom-left corner; that corner rounded with r = side/2."""
    rf = side / 2
    L, B = c[0] - rf, c[1] + rf
    top, right = (L, B - side), (L + side, B)
    f1, f2 = (L, B - rf), (L + rf, B)
    return (f"M {top[0]:.3f} {top[1]:.3f} L {f1[0]:.3f} {f1[1]:.3f}"
            + arc((L + rf, B - rf), rf, f1, f2)
            + f" L {right[0]:.3f} {right[1]:.3f}"
            + arc((L, B), side, right, top) + " Z")


def red_bud(c, r):
    """Circle body + cap arc through its top and right points; cap centre c + (-r/2, +r/2), radius r*sqrt(2.5)."""
    top, right = (c[0], c[1] - r), (c[0] + r, c[1])
    cap_c, cap_r = (c[0] - r / 2, c[1] + r / 2), r * math.sqrt(2.5)
    lowleft = (c[0] - r * 0.7071, c[1] + r * 0.7071)
    return (f"M {top[0]:.3f} {top[1]:.3f}"
            + arc(c, r, top, right, through=lowleft)
            + arc(cap_c, cap_r, right, top) + " Z")


def stem(navy_c, red_c):
    c = (navy_c[0], red_c[1])
    r = red_c[0] - navy_c[0]
    return f"M {red_c[0]:.3f} {red_c[1]:.3f}" + arc(c, r, red_c, navy_c)


def build_svg():
    navy_c = (O[0] - D_NAVY, O[1] + D_NAVY)
    red_c = (O[0] + D_RED, O[1] - D_RED)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CANVAS} {CANVAS}" width="{CANVAS}" height="{CANVAS}">
  <title>Orbital logo mark</title>
  <circle cx="{O[0]:g}" cy="{O[1]:g}" r="{R_ORANGE:g}" fill="{COLORS['orange']}"/>
  <path d="{stem(navy_c, red_c)}" fill="none" stroke="{COLORS['stem']}" stroke-width="{STEM_W:g}"/>
  <path d="{navy_leaf(navy_c, NAVY_SIDE)}" fill="{COLORS['navy']}"/>
  <path d="{red_bud(red_c, RED_R)}" fill="{COLORS['red']}"/>
</svg>
'''


# ---------------------------------------------------------------- rendering
RENDER_MJS = """
import { chromium } from 'playwright';
import fs from 'fs';
const [svgPath, outDir] = process.argv.slice(2);
const svg = fs.readFileSync(svgPath, 'utf8').replace('width="512" height="512"', 'width="512" height="512" style="display:block"');
const b = await chromium.launch();
const page = await b.newPage({ viewport: { width: 512, height: 512 }, deviceScaleFactor: 4 });
await page.setContent(`<html><body style="margin:0;background:transparent">${svg}</body></html>`);
await page.screenshot({ path: `${outDir}/mark-2048.png`, omitBackground: true });
await page.setContent(`<html><body style="margin:0;background:#fff">${svg}</body></html>`);
await page.screenshot({ path: `${outDir}/mark-2048-white.png` });
await page.setContent(`<html><head><style>@page{size:512px 512px;margin:0}body{margin:0}</style></head><body>${svg}</body></html>`);
await page.pdf({ path: `${outDir}/mark.pdf`, width: '512px', height: '512px', printBackground: true, preferCSSPageSize: true });
await b.close();
"""


def render(svg_path: Path, out_dir: Path):
    web = ROOT / "web"
    mjs = web / "_gen_logo_tmp.mjs"
    mjs.write_text(RENDER_MJS)
    try:
        subprocess.run(["node", str(mjs), str(svg_path), str(out_dir)], cwd=web, check=True)
    finally:
        mjs.unlink(missing_ok=True)


# ---------------------------------------------------------------- icons
def main():
    from PIL import Image, ImageDraw

    tmp = Path(tempfile.mkdtemp(prefix="orbital-logo-"))
    svg = build_svg()
    (tmp / "mark.svg").write_text(svg)
    render(tmp / "mark.svg", tmp)

    logo_dir = ROOT / "assets" / "logo"
    logo_dir.mkdir(parents=True, exist_ok=True)
    (logo_dir / "orbital-logo.svg").write_text(svg)
    shutil.copy(tmp / "mark.pdf", logo_dir / "orbital-logo.pdf")
    shutil.copy(tmp / "mark.pdf", logo_dir / "orbital-logo.ai")
    mark = Image.open(tmp / "mark-2048.png").convert("RGBA")
    mark.save(logo_dir / "orbital-logo-2048.png")
    Image.open(tmp / "mark-2048-white.png").convert("RGB").save(
        logo_dir / "orbital-logo-2048.jpg", quality=95, subsampling=0)

    def rounded_white(size, radius, scale=4):
        big = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
        ImageDraw.Draw(big).rounded_rectangle([0, 0, size * scale - 1, size * scale - 1],
                                              radius=radius * scale, fill=(255, 255, 255, 255))
        return big.resize((size, size), Image.LANCZOS)

    def canvas_at(size):
        return mark.resize((size, size), Image.LANCZOS)

    def web_tile(size):  # white rounded tile, full bleed, r = 20 %, mark = whole canvas
        t = rounded_white(size, int(round(size * 0.2)))
        t.alpha_composite(canvas_at(size))
        return t

    pub = ROOT / "web" / "public"
    web_tile(512).save(pub / "icon-512.png")
    web_tile(192).save(pub / "icon-192.png")
    web_tile(180).save(pub / "apple-touch-icon.png")
    web_tile(256).save(pub / "favicon.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])

    # desktop icon: 500 canvas, white tile 400 with r 65. The crop is a square centred
    # on the ORANGE DISC (not the mark's bounding box, which is lopsided because the
    # navy leaf is bigger than the red bud), sized to the farthest opaque pixel.
    import numpy as np
    alpha = np.array(mark)[:, :, 3]
    ys, xs = np.nonzero(alpha > 8)
    k = mark.width / CANVAS
    ocx, ocy = int(round(O[0] * k)), int(round(O[1] * k))
    half = int(max(ocx - xs.min(), xs.max() + 1 - ocx, ocy - ys.min(), ys.max() + 1 - ocy))
    crop = mark.crop((ocx - half, ocy - half, ocx + half, ocy + half))

    def desktop_icon(size, fill=0.88):
        s = size / 500
        sq, off = int(round(400 * s)), int(round(50 * s))
        t = rounded_white(sq, int(round(65 * s)))
        m = int(round(sq * fill))
        t.alpha_composite(crop.resize((m, m), Image.LANCZOS), ((sq - m) // 2, (sq - m) // 2))
        c = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        c.alpha_composite(t, (off, off))
        return c

    assets = ROOT / "assets"
    desktop_icon(500).save(assets / "icon.png")
    desktop_icon(256).save(assets / "icon.ico", format="ICO",
                           sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    if shutil.which("iconutil"):
        iconset = tmp / "orbital.iconset"
        iconset.mkdir()
        for name, sz in [("icon_16x16.png", 16), ("icon_16x16@2x.png", 32), ("icon_32x32.png", 32),
                         ("icon_32x32@2x.png", 64), ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
                         ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512), ("icon_512x512.png", 512),
                         ("icon_512x512@2x.png", 1024)]:
            desktop_icon(sz).save(iconset / name)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(assets / "icon.icns")], check=True)
    else:
        print("iconutil not found (not macOS) — assets/icon.icns left unchanged", file=sys.stderr)

    readme = Image.new("RGBA", (500, 500), (255, 255, 255, 255))
    readme.alpha_composite(canvas_at(500))
    readme.convert("RGB").save(ROOT / "docs" / "screenshots" / "orbital-logo.png")

    bv = ROOT / "brand-video"
    if bv.is_dir():
        canvas_at(512).save(bv / "logo-mark.png")

    shutil.rmtree(tmp, ignore_errors=True)
    print(svg)
    print("regenerated assets/logo, assets/icon.*, web/public icons, docs/screenshots/orbital-logo.png")


if __name__ == "__main__":
    main()
