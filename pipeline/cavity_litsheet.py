"""Lit before/after preview for cavity.py: colour x (ambient + sun * N.L) with the shipped normal vs the deepened one,
low sun (35 deg) so the relief reads like a street in the afternoon; tiles 2x2 so the joints between repeats show.

usage: python -I cavity_litsheet.py <upall dir> <out.png> <sha> [<sha> ...]
"""
import sys, os, json, site
sys.path.insert(0, site.getusersitepackages())
import numpy as np
from PIL import Image, ImageDraw

U, OUT, shas = sys.argv[1], sys.argv[2], sys.argv[3:]
sel = json.load(open(os.path.join(U, 'cavity_sel.json')))
L = np.array([0.55, -0.45, 0.70]); L /= np.linalg.norm(L)  # light from upper-left, 44 deg elevation
def lin(x): return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
def srgb(x): x = np.clip(x, 0, 1); return np.where(x <= 0.0031308, x * 12.92, 1.055 * x ** (1 / 2.4) - 0.055)
def ld(p, size=None):
    im = Image.open(p).convert('RGBA')
    if size and im.size != size: im = im.resize(size, Image.BICUBIC)
    return np.asarray(im).astype(np.float64) / 255
def lit(col, nrm):
    n = nrm[..., :3] * 2 - 1; n /= np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-6)
    ndl = np.clip((n * L).sum(-1), 0, 1)
    return srgb(lin(col[..., :3]) * (0.35 + 1.6 * ndl[..., None]))
T = 360; rows = []
for s in shas:
    c = sel['colour'][s]; ns = c['normal']
    b = os.path.join(U, 'gout', s + '.png'); b = b if os.path.exists(b) else os.path.join(U, 'up', s + '.png')
    a = os.path.join(U, 'cavity_out', s + '.png')
    if not os.path.exists(a): continue
    col0 = ld(b); sz = (col0.shape[1], col0.shape[0]); col1 = ld(a)
    n0p = os.path.join(U, 'gout', ns + '.png'); n0p = n0p if os.path.exists(n0p) else os.path.join(U, 'cavity_src', ns + '.png')
    n0 = ld(n0p, sz); n1 = ld(os.path.join(U, 'cavity_out', ns + '.png'), sz)
    ims = []
    for col, n in ((col0, n0), (col1, n1)):
        im = Image.fromarray((lit(col, n) * 255).astype(np.uint8))
        w, h = im.size; tile = Image.new('RGB', (w * 2, h * 2))
        for i in range(2):
            for j in range(2): tile.paste(im, (i * w, j * h))
        ims.append(tile.crop((w // 2, h // 2, w // 2 + min(w, 512), h // 2 + min(h, 512))).resize((T, T), Image.LANCZOS))
    rows.append((c['name'], ims))
sheet = Image.new('RGB', (2 * T + 30, len(rows) * (T + 26)), (18, 18, 18)); d = ImageDraw.Draw(sheet)
for i, (n, ims) in enumerate(rows):
    y = i * (T + 26); d.text((6, y + 6), n[:60] + '   prima | dopo', fill=(235, 235, 235))
    for j, im in enumerate(ims): sheet.paste(im, (j * (T + 30), y + 24))
sheet.save(OUT); print(OUT, len(rows))
