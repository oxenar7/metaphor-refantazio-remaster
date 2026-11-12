"""Baked "fake parallax" for Grand Trad stone: cavity + ambient occlusion in the crevices, crisp stone edges, deeper normals.

usage: python -I cavity.py <upall_cfg.json> <phase> [--only REGEX]
phases: select  colour targets by name (paving / stone blocks / door frames / brick), used by Grand Trad (o_0003_, o_0103_);
                pairs each with its normal (and the game's own height map when the TEX has one) -> work/cavity_sel.json
        decode  texconv the paired normals / heights -> work/cavity_src/<sha>.png
        build   height = game height map, else Frankot-Chellappa integration of the normal (R+, G+ convention, checked
                against TcPavementStoneGray's height map); shade = 1 - a1*cavity - a2*ao (+ small edge highlight), applied
                in linear light to the shipped colour (grain or AI upscale); normals: xy * NS, renormalised
                -> work/cavity_out/<sha>.png
        sheet   before / after crops -> work/cavity_sheet_*.png
        encode  BC7 -> work/cavity/<sha>.dds, work/cavity_index.json {file: [[entry, sha], ...]} (pack layer above special)
"""
import sys, os, json, re, struct, hashlib, difflib, collections, site
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from upscale_all import read_tex, ename, dds_info, load_toc, texconv_batch, W, run
sys.path.insert(0, site.getusersitepackages())

INCL = re.compile(r'Pavement|Cobblestone|streets_0|squareBlock|RockDoorFrame|rockDoorStep|stoneBalcony|BrickWall|Brick(Old|Large|Groutless|Rustic)'
                  r'|BlockWall|stoneBorder|StoneBlocks|Limestone|rockPillar|blockPillar|rockStairs|stoneBollard|OldStoneFloor|stonePavement'
                  r'|wallPillar|archPillar|archpillar|BasaltBrick|RockFace', re.I)
EXCL = re.compile(r'grunge|_far_|mountainCity|operaHouse|forest|academia|bigRock|snow|\.jpg|fencePillar|church_01_stairs', re.I)
GT = re.compile(r'/o_0(1)?03_')
# strengths: a1 crevice darkening, a2 broad occlusion, hl edge highlight, NS normal xy gain
P = dict(a1=0.30, a2=0.22, hl=0.05, NS=1.3, s1=96, s2=20, keep_mean=0.5)
PAVE = re.compile(r'Pavement|Cobblestone|streets_0|stonePavement|OldStoneFloor', re.I)

def stem(n):
    n = n.rsplit('.', 1)[0]
    n = re.sub(r'_(basecolor|normal|height)$', '', n, flags=re.I)
    return re.sub(r'_\d{4}$', '', n)

def pair(colour_name, ents, kind):
    """best entry of `kind` (normal / height) for a colour entry, by name similarity (variant letter may be dropped)"""
    cs = stem(colour_name); best = (0, None)
    for k, (raw, d) in enumerate(ents):
        n = ename(raw)
        if not re.search(rf'_{kind}\.\w+$', n, re.I) or n.lower().startswith(('rme', 'sesi', 'lll')): continue
        r = difflib.SequenceMatcher(None, cs, stem(n)).ratio()
        if r > best[0]: best = (r, k)
    return best[1] if best[0] >= 0.85 else None

def sha12(d): return hashlib.sha1(d).hexdigest()[:12]

def phase_select(cfg, only):
    ix = json.load(open(W(cfg, 'index.json'))); toc = load_toc(cfg['toc'])
    targets = {s for s, u in ix['uniq'].items() if INCL.search(u['name']) and not EXCL.search(u['name']) and (not only or re.search(only, u['name']))}
    gtuse = collections.Counter(s for f, picks in ix['files'].items() if GT.search(f) for _, s in picks)
    targets = {s for s in targets if gtuse[s]}
    files = sorted(f for f, picks in ix['files'].items() if any(s in targets for _, s in picks))
    os.makedirs(W(cfg, 'cavity_src'), exist_ok=True)
    cpk = open(cfg['cpk'], 'rb'); sel = dict(colour={}, normal={}, height={}, files={})
    pairs = collections.defaultdict(collections.Counter)
    for f in files:
        size, off = toc[f]; cpk.seek(off); ents = read_tex(cpk.read(size)); out = []
        for k, s in ix['files'][f]:
            if s not in targets: continue
            out.append([k, s, 'colour'])
            nk = pair(ix['uniq'][s]['name'], ents, 'normal'); hk = pair(ix['uniq'][s]['name'], ents, 'height')
            ns = hs = None
            for kk, kind in ((nk, 'normal'), (hk, 'height')):
                if kk is None: continue
                d = ents[kk][1]; inf = dds_info(d); ss = sha12(d)
                if not inf or inf.get('arr', 1) != 1: continue
                p = W(cfg, 'cavity_src', ss + '.dds')
                if not os.path.exists(p): open(p, 'wb').write(d)
                sel[kind].setdefault(ss, dict(name=ename(ents[kk][0]), w=inf['w'], h=inf['h'], mips=inf['mips'], fmt=inf['fmt']))
                if kind == 'normal': ns = ss; out.append([kk, ss, 'normal'])
                else: hs = ss
            pairs[s][(ns, hs)] += 1
        if out: sel['files'][f] = out
    for s in targets:
        if not pairs[s]: continue
        (ns, hs), _ = pairs[s].most_common(1)[0]
        u = ix['uniq'][s]; sel['colour'][s] = dict(name=u['name'], w=u['w'], h=u['h'], mips=u['mips'], normal=ns, height=hs)
    # a normal is only rewritten where it is paired with a target colour (files list above)
    json.dump(sel, open(W(cfg, 'cavity_sel.json'), 'w'), indent=0)
    c = sel['colour']
    print(f"{len(c)} colour targets ({sum(1 for v in c.values() if v['normal'])} with normal, {sum(1 for v in c.values() if v['height'])} with game height map), "
          f"{len(sel['normal'])} normals, {len(sel['files'])} TEX files")

def phase_decode(cfg, only):
    sel = json.load(open(W(cfg, 'cavity_sel.json')))
    todo = [W(cfg, 'cavity_src', s + '.dds') for kind in ('normal', 'height') for s in sel[kind] if not os.path.exists(W(cfg, 'cavity_src', s + '.png'))]
    print('decode', len(todo))
    if todo: texconv_batch(cfg, todo, W(cfg, 'cavity_src'), ['-ft', 'png', '-f', 'R8G8B8A8_UNORM'])

# ---------------------------------------------------------------- image maths
def load(p):
    import numpy as np
    from PIL import Image
    return np.asarray(Image.open(p).convert('RGBA')).astype(np.float64) / 255

def resize(a, w, h):
    import numpy as np
    from PIL import Image
    if a.shape[1] == w and a.shape[0] == h: return a
    return np.stack([np.asarray(Image.fromarray(a[..., c].astype(np.float32), 'F').resize((w, h), Image.BICUBIC)) for c in range(a.shape[2])], -1).astype(np.float64)

def gauss(x, s):  # periodic gaussian (textures tile)
    import numpy as np
    h, w = x.shape; fy = np.fft.fftfreq(h)[:, None]; fx = np.fft.fftfreq(w)[None, :]
    return np.real(np.fft.ifft2(np.fft.fft2(x) * np.exp(-2 * (np.pi * s) ** 2 * (fx ** 2 + fy ** 2))))

def fc_height(nrm):
    import numpy as np
    nx = nrm[..., 0] * 2 - 1; ny = nrm[..., 1] * 2 - 1
    nz = np.clip(nrm[..., 2] * 2 - 1, 0.25, 1) if nrm[..., 2].std() > 0.005 or nrm[..., 2].mean() > 0.6 else np.sqrt(np.clip(1 - nx ** 2 - ny ** 2, 0.06, 1))
    gx, gy = -nx / nz, -ny / nz
    h, w = gx.shape; WX, WY = np.meshgrid(np.fft.fftfreq(w) * 2 * np.pi, np.fft.fftfreq(h) * 2 * np.pi)
    den = WX ** 2 + WY ** 2; den[0, 0] = 1
    Z = (-1j * WX * np.fft.fft2(gx) - 1j * WY * np.fft.fft2(gy)) / den; Z[0, 0] = 0
    return np.real(np.fft.ifft2(Z))

def srgb2lin(x):
    import numpy as np
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
def lin2srgb(x):
    import numpy as np
    x = np.clip(x, 0, 1); return np.where(x <= 0.0031308, x * 12.92, 1.055 * x ** (1 / 2.4) - 0.055)

def shade_map(H, W_, pave):
    import numpy as np
    s1, s2 = W_ / P['s1'], W_ / P['s2']
    d1 = gauss(H, s1) - H; d2 = gauss(H, s2) - H
    q = lambda d: max(np.percentile(np.abs(d), 99.5), 1e-6)
    c1 = np.clip(d1 / q(d1), 0, 1) ** 0.9; c2 = np.clip(d2 / q(d2), 0, 1)
    hl = np.clip(-d1 / q(d1), 0, 1)
    a1 = P['a1'] * (1.0 if pave else 0.85); a2 = P['a2']
    return np.clip(1 - a1 * c1 - a2 * c2 + P['hl'] * hl, 0.45, 1.08), c1, c2

def phase_build(cfg, only):
    import numpy as np
    from PIL import Image
    sel = json.load(open(W(cfg, 'cavity_sel.json'))); os.makedirs(W(cfg, 'cavity_out'), exist_ok=True)
    rep = ['sha\tname\tsrc\theight\tmeanL_in\tmeanL_out\tshade_p5\tcrevice_px\talign']
    for s, c in sel['colour'].items():
        if only and not re.search(only, c['name']): continue
        base = W(cfg, 'gout', s + '.png'); src = 'grain'
        if not os.path.exists(base): base = W(cfg, 'up', s + '.png'); src = 'up'
        if not os.path.exists(base) or not (c['normal'] or c['height']): continue
        col = load(base); Hh, Ww = col.shape[:2]
        # relief candidates: own height / normal, plus any collected height / normal of the same material stem
        cs = stem(c['name']); cands = []
        for kind in ('height', 'normal'):
            for ss, inf in sel[kind].items():
                if ss in (c['height'], c['normal']) or difflib.SequenceMatcher(None, cs, stem(inf['name'])).ratio() >= 0.85:
                    if os.path.exists(W(cfg, 'cavity_src', ss + '.png')): cands.append((kind, ss))
        # alignment guard: the relief must line up with what is painted (|corr| of the crevice map with the colour's own
        # local contrast); a variant paired with another variant's normal / height would draw grooves in the wrong place
        Lc = (col[..., :3] * [0.2126, 0.7152, 0.0722]).sum(-1); hp = gauss(Lc, Ww / P['s1']) - Lc
        best = (-1, None)
        for kind, ss in cands:
            a_ = resize(load(W(cfg, 'cavity_src', ss + '.png')), Ww, Hh)
            H_ = a_[..., 0] if kind == 'height' else fc_height(a_)
            H_ = (H_ - H_.mean()) / max(H_.std(), 1e-6)
            sh_, c1_, c2_ = shade_map(H_, Ww, bool(PAVE.search(c['name'])))
            r = np.corrcoef(c1_.ravel(), hp.ravel())[0, 1] if c1_.std() > 0 and hp.std() > 0 else 0.0
            if abs(r) > best[0]: best = (abs(r), (sh_, c1_, c2_, f'{kind}:{sel[kind][ss]["name"][:40]}', r))
        if best[1] is None: continue
        align, (sh, c1, c2, hsrc, rs) = best
        kk = float(np.clip((align - 0.05) / 0.15, 0, 1))
        # joints painted LIGHTER than the stones (pale mortar): darkening them would flatten the painted pattern
        # (the paler the joints, the less: r = -0.8 -> x0.3, -0.3 -> x0.55, -0.1 -> x0.85)
        if rs < 0: kk *= float(np.clip(1 + 1.5 * rs, 0.3, 1)); hsrc += f' (pale joints r={rs:.2f})'
        if kk == 0:
            rep.append(f"{s}\t{c['name']}\t{src}\t{hsrc}\t\t\t\t\tSKIP align {align:.3f}"); continue
        sh = 1 + (sh - 1) * kk
        lin = srgb2lin(col[..., :3]); L0 = (lin * [0.2126, 0.7152, 0.0722]).sum(-1).mean()
        out = lin * sh[..., None]; L1 = (out * [0.2126, 0.7152, 0.0722]).sum(-1).mean()
        out *= (L0 / max(L1, 1e-6)) ** P['keep_mean']
        rgb = (lin2srgb(out) * 255).round().clip(0, 255).astype(np.uint8)
        a = (col[..., 3] * 255).round().astype(np.uint8)
        Image.fromarray(np.dstack([rgb, a]), 'RGBA').save(W(cfg, 'cavity_out', s + '.png'), compress_level=1)
        Image.fromarray((sh.clip(0, 1) * 255).astype(np.uint8)).save(W(cfg, 'cavity_out', s + '_shade.png'))
        L2 = (out * [0.2126, 0.7152, 0.0722]).sum(-1).mean()
        rep.append(f"{s}\t{c['name']}\t{src}\t{hsrc}\t{L0:.4f}\t{L2:.4f}\t{np.percentile(sh, 5):.3f}\t{(c1 > 0.3).mean():.3f}\t{align:.3f}")
    nn = 0
    for s, n in sel['normal'].items():
        g = W(cfg, 'gout', s + '.png'); src = g if os.path.exists(g) else W(cfg, 'cavity_src', s + '.png')
        a = load(src); x = (a[..., 0] * 2 - 1) * P['NS']; y = (a[..., 1] * 2 - 1) * P['NS']
        l = np.sqrt(x * x + y * y); k = np.where(l > 0.95, 0.95 / np.maximum(l, 1e-6), 1); x, y = x * k, y * k
        z = np.sqrt(np.clip(1 - x * x - y * y, 0, 1))
        o = np.dstack([(np.dstack([x, y, z]) * 0.5 + 0.5), a[..., 3:4]])
        Image.fromarray((o * 255).round().clip(0, 255).astype(np.uint8), 'RGBA').save(W(cfg, 'cavity_out', s + '.png'), compress_level=1); nn += 1
    open(W(cfg, 'cavity_report.tsv'), 'w').write('\n'.join(rep) + '\n')
    print(f'built {len(rep) - 1} colours, {nn} normals')

def phase_sheet(cfg, only):
    import numpy as np
    from PIL import Image, ImageDraw
    sel = json.load(open(W(cfg, 'cavity_sel.json'))); ix = json.load(open(W(cfg, 'index.json')))
    use = collections.Counter(s for f, picks in ix['files'].items() if GT.search(f) for _, s in picks)
    names = [s for s in sorted(sel['colour'], key=lambda s: -use[s]) if os.path.exists(W(cfg, 'cavity_out', s + '.png')) and (not only or re.search(only, sel['colour'][s]['name']))]
    per = 6; T = 300
    for page in range(0, min(len(names), 24), per):
        rows = names[page:page + per]; sheet = Image.new('RGB', (T * 3 + 40, len(rows) * (T + 24)), (20, 20, 20)); d = ImageDraw.Draw(sheet)
        for i, s in enumerate(rows):
            b = W(cfg, 'gout', s + '.png'); b = b if os.path.exists(b) else W(cfg, 'up', s + '.png')
            ims = [Image.open(b).convert('RGB'), Image.open(W(cfg, 'cavity_out', s + '.png')).convert('RGB'), Image.open(W(cfg, 'cavity_out', s + '_shade.png')).convert('RGB')]
            y = i * (T + 24)
            d.text((4, y + 4), sel['colour'][s]['name'][:70] + f'  (GT files {use[s]})', fill=(230, 230, 230))
            for j, im in enumerate(ims):
                w, h = im.size; cs = min(w, h, 512); crop = im.crop((0, 0, cs, cs)).resize((T, T), Image.LANCZOS)
                sheet.paste(crop, (j * (T + 20), y + 22))
        p = W(cfg, f'cavity_sheet_{page // per + 1}.png'); sheet.save(p); print(p)

def phase_encode(cfg, only):
    sel = json.load(open(W(cfg, 'cavity_sel.json'))); os.makedirs(W(cfg, 'cavity'), exist_ok=True)
    from PIL import Image
    groups = collections.defaultdict(list); info = {**sel['colour'], **sel['normal']}
    for s, u in info.items():
        p = W(cfg, 'cavity_out', s + '.png')
        if not os.path.exists(p): continue
        w = Image.open(p).size[0]; m = u['mips'] + (1 if w == 2 * u['w'] else 2 if w == 4 * u['w'] else 0)
        groups[m].append(p)
    for m, lst in sorted(groups.items()):
        print(f'encode mips={m}: {len(lst)}', flush=True); texconv_batch(cfg, lst, W(cfg, 'cavity'), ['-f', 'BC7_UNORM', '-m', str(m)] + cfg.get('bc7_args', []))
    six = {f: [[k, s] for k, s, _ in v if os.path.exists(W(cfg, 'cavity', s + '.dds'))] for f, v in sel['files'].items()}
    json.dump({f: v for f, v in six.items() if v}, open(W(cfg, 'cavity_index.json'), 'w'))
    print('cavity index:', len(six), 'files,', sum(len(v) for v in six.values()), 'entries')

PH = dict(select=phase_select, decode=phase_decode, build=phase_build, sheet=phase_sheet, encode=phase_encode)
if __name__ == '__main__':
    cfg = json.load(open(sys.argv[1])); only = sys.argv[sys.argv.index('--only') + 1] if '--only' in sys.argv else None
    for p in (PH if sys.argv[2] == 'all' else [sys.argv[2]]):
        print('==', p, flush=True); PH[p](cfg, only)
