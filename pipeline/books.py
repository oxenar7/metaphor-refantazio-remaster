"""Akademeia bookshelves: the book spines are flat boxes lit evenly, so they read as a painted wall.
Gives every spine strip of the bg01_AK_book02_m1 atlas a rounded look: a cylindrical normal (whiteout-blended with the
game's own normal detail) and a baked edge darkening, so neighbouring books separate and each spine has volume.

usage: python -I books.py <upall_cfg.json> overlay|build|preview|encode
  overlay  draws the detected spine strips on the atlas (check)
  build    writes work/books/<sha>.png (colour from the mod's current final texture, normal 2x)
  preview  lit before/after sheet -> work/books/books_preview.png
  encode   BC7 -> work/special/<sha>.dds and adds the slots to special_index.json (pack picks them up)
"""
import sys, os, json, shutil, subprocess, tempfile, site
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, site.getusersitepackages())
import numpy as np
from PIL import Image, ImageDraw
from upscale_all import read_tex, ename, dds_info, load_toc, texconv_batch, W
from cavity import sha12
from best_source import final_of

FILE = 'COMMON/model/field/object/o_0001_055_00.TEX'
COL, NRM = 'bg01_AK_book02_m1_basecolor.tga', 'bg01_AK_book02_m1_normal.tga'
# atlas 1024x512: 4 bands of book cells; each cell = spine strip (left) + front cover. (cell start, spine end) in x.
BANDS = [(0, 128), (128, 256), (256, 384), (384, 512)]
CELLS = {
    0: [(0, 12), (86, 97), (171, 183), (256, 268), (342, 353), (427, 439), (512, 525), (597, 609), (682, 695), (767, 779), (852, 865)],
    2: [(0, 21), (87, 107), (172, 192), (257, 277), (343, 363), (428, 448), (513, 543), (616, 645), (718, 746), (820, 848)],
    3: [(0, 21), (87, 107), (171, 192), (257, 277), (342, 363), (428, 448), (513, 543), (616, 645), (718, 746), (820, 848), (923, 950)],
}
CELLS[1] = CELLS[0]
P = dict(theta=1.05, edge=0.62, pow=2.4, crown=0.08)   # cylinder half-angle (rad), edge darkening, falloff, centre lift
P.update(json.loads(os.environ.get('BOOKS_P', '{}')))


def lin(x): return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
def srgb(x): x = np.clip(x, 0, 1); return np.where(x <= 0.0031308, x * 12.92, 1.055 * x ** (1 / 2.4) - 0.055)


def slots(cfg):
    toc = load_toc(cfg['toc']); size, off = toc[FILE]
    with open(cfg['cpk'], 'rb') as f: f.seek(off); ents = read_tex(f.read(size))
    out = {}
    for k, (n, d) in enumerate(ents):
        if ename(n) in (COL, NRM): out[ename(n)] = (k, sha12(d), d)
    return out


def decode(cfg, path, tag, tmp):
    d = os.path.join(tmp, tag + '.dds'); shutil.copy(path, d)
    subprocess.run([cfg['texconv'], '-nologo', '-y', '-ft', 'png', '-f', 'R8G8B8A8_UNORM', '-m', '1', '-o', tmp, d], stdout=subprocess.DEVNULL, check=True)
    return np.asarray(Image.open(os.path.join(tmp, tag + '.png')).convert('RGBA')).astype(np.float64) / 255


def ucoord(w, h):
    """u in [-1,1] across each spine strip, NaN elsewhere (atlas scaled to w x h)."""
    sx, sy = w / 1024, h / 512; u = np.full((h, w), np.nan)
    xs = (np.arange(w) + 0.5) / sx
    for b, (y0, y1) in enumerate(BANDS):
        for a, e in CELLS[b]:
            m = (xs >= a) & (xs < e + 1)
            u[int(y0 * sy):int(y1 * sy), m] = (2 * (xs[m] - a) / (e + 1 - a) - 1)[None, :]
    return u


def lay(cfg):
    return tuple(json.load(open(W(cfg, n))) if os.path.exists(W(cfg, n)) else {} for n in ('cavity_index.json', 'special_index.json', 'grain_index.json'))


def build(cfg):
    sl = slots(cfg); L = lay(cfg); tmp = tempfile.mkdtemp(prefix='books_'); out = W(cfg, 'books'); os.makedirs(out, exist_ok=True)
    kc, sc, dc = sl[COL]; kn, sn, dn = sl[NRM]
    pc, kindc = final_of(cfg, sc, lay=L); pn, kindn = final_of(cfg, sn, lay=L)
    print('colour source', kindc, pc, '\nnormal source', kindn, pn)
    for tag, d in (('orig_c', dc), ('orig_n', dn)): open(os.path.join(out, tag + '.dds'), 'wb').write(d)
    c = decode(cfg, pc, 'c', tmp); n0 = decode(cfg, pn, 'n', tmp)
    h, w = c.shape[:2]
    n = np.asarray(Image.fromarray((n0 * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)).astype(np.float64) / 255
    u = ucoord(w, h); m = ~np.isnan(u); uu = np.where(m, u, 0)
    # colour: linear-light multiply, edges darker, slight lift at the crown
    f = np.where(m, 1 - P['edge'] * np.abs(uu) ** P['pow'] + P['crown'] * (1 - np.abs(uu)) ** 2, 1.0)
    c2 = c.copy(); c2[..., :3] = srgb(lin(c[..., :3]) * f[..., None])
    # normal: cylinder (x only) whiteout-blended with the original detail
    nd = n[..., :3] * 2 - 1
    th = uu * P['theta']; cyl = np.stack([np.sin(th), np.zeros_like(th), np.cos(th)], -1)
    nb = np.stack([cyl[..., 0] + nd[..., 0], cyl[..., 1] + nd[..., 1], cyl[..., 2] * nd[..., 2]], -1)
    nb /= np.linalg.norm(nb, axis=-1, keepdims=True)
    nb = np.where(m[..., None], nb, nd)
    n2 = n.copy(); n2[..., :3] = nb * 0.5 + 0.5
    Image.fromarray((np.clip(c2, 0, 1) * 255 + 0.5).astype(np.uint8), 'RGBA').save(os.path.join(out, sc + '.png'))
    Image.fromarray((np.clip(n2, 0, 1) * 255 + 0.5).astype(np.uint8), 'RGBA').save(os.path.join(out, sn + '.png'))
    Image.fromarray((np.clip(c, 0, 1) * 255 + 0.5).astype(np.uint8), 'RGBA').save(os.path.join(out, 'before_c.png'))
    Image.fromarray((np.clip(n, 0, 1) * 255 + 0.5).astype(np.uint8), 'RGBA').save(os.path.join(out, 'before_n.png'))
    json.dump(dict(colour=[kc, sc, dc[:148].hex()], normal=[kn, sn, dn[:148].hex()]), open(os.path.join(out, 'slots.json'), 'w'))
    shutil.rmtree(tmp, ignore_errors=True); print('built', w, 'x', h, 'spine px', int(m.sum()))


def overlay(cfg):
    out = W(cfg, 'books'); os.makedirs(out, exist_ok=True); sl = slots(cfg); tmp = tempfile.mkdtemp(prefix='books_')
    p = os.path.join(tmp, 'src.dds'); open(p, 'wb').write(sl[COL][2]); c = decode(cfg, p, 'o', tmp)
    im = Image.fromarray((c[..., :3] * 255).astype(np.uint8)); d = ImageDraw.Draw(im)
    for b, (y0, y1) in enumerate(BANDS):
        for a, e in CELLS[b]: d.rectangle([a, y0 + 2, e, y1 - 3], outline=(255, 255, 0))
    im.save(os.path.join(out, 'overlay.png')); print(os.path.join(out, 'overlay.png'))


def preview(cfg):
    out = W(cfg, 'books'); sl = json.load(open(os.path.join(out, 'slots.json')))
    sc, sn = sl['colour'][1], sl['normal'][1]
    B = [np.asarray(Image.open(os.path.join(out, x))).astype(np.float64) / 255 for x in ('before_c.png', 'before_n.png')]
    A = [np.asarray(Image.open(os.path.join(out, x))).astype(np.float64) / 255 for x in (sc + '.png', sn + '.png')]
    Lt = np.array([0.55, 0.15, 0.82]); Lt /= np.linalg.norm(Lt)
    def lit(c, n, box, Wd=900):
        h, w = c.shape[:2]; y0, y1, x0, x1 = int(box[1] * h), int(box[3] * h), int(box[0] * w), int(box[2] * w)
        cc, nn = c[y0:y1, x0:x1, :3], n[y0:y1, x0:x1, :3] * 2 - 1
        nn = nn / np.maximum(np.linalg.norm(nn, axis=-1, keepdims=True), 1e-6)
        ndl = np.clip((nn * Lt).sum(-1), 0, 1)[..., None]
        img = srgb(lin(cc) * (0.35 + 0.95 * ndl)) * 1.25
        im = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8)); return im.resize((Wd, int(Wd * im.height / im.width)), Image.BILINEAR)
    rows = [((0.0, 0.5, 0.45, 1.0), 'dorsi decorati (sinistra)'), ((0.5, 0.5, 0.9, 1.0), 'dorsi decorati (destra)'), ((0.0, 0.0, 0.5, 0.5), 'dorsi semplici')]
    tiles = [(t, lit(*B, b), lit(*A, b)) for b, t in rows]
    H = sum(x[1].height + 34 for x in tiles) + 34; sheet = Image.new('RGB', (1820, H), (16, 16, 16)); dr = ImageDraw.Draw(sheet)
    dr.text((8, 8), 'PRIMA (attuale)', fill=(255, 200, 120)); dr.text((928, 8), 'DOPO (dorsi arrotondati + ombra tra i libri)', fill=(150, 230, 150))
    y = 30
    for t, b, a in tiles:
        dr.text((8, y), t, fill=(230, 230, 230)); sheet.paste(b, (0, y + 18)); sheet.paste(a, (920, y + 18)); y += b.height + 34
    p = os.path.join(out, 'books_preview.png'); sheet.save(p); print(p)


def encode(cfg):
    out = W(cfg, 'books'); sl = json.load(open(os.path.join(out, 'slots.json'))); six_p = W(cfg, 'special_index.json')
    six = json.load(open(six_p)) if os.path.exists(six_p) else {}
    if not os.path.exists(six_p + '.bak_pre_books'): shutil.copy(six_p, six_p + '.bak_pre_books')
    for key in ('colour', 'normal'):
        k, s, hdr = sl[key]; mips = dds_info(bytes.fromhex(hdr) + b'\0' * 16)['mips'] + 1
        texconv_batch(cfg, [os.path.join(out, s + '.png')], W(cfg, 'special'), ['-f', 'BC7_UNORM', '-m', str(mips)] + cfg.get('bc7_args', []))
        lst = six.setdefault(FILE, [])
        if [k, s] not in lst: lst.append([k, s])
    json.dump(six, open(six_p, 'w')); print('special index:', FILE, six[FILE])


if __name__ == '__main__':
    cfg = json.load(open(sys.argv[1])); dict(overlay=overlay, build=build, preview=preview, encode=encode)[sys.argv[2]](cfg)
