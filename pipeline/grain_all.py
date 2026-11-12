"""Game-wide photo grain for wood / ground / stone / plaster field textures (GPU), on top of the AI upscale.

usage: python -I grain_all.py <upall_cfg.json> <phase> [--limit N] [--only REGEX]
phases: select  pick colour entries by name category, pair each with its normal map in the same TEX -> work/grain_sel.json
        decode  texconv the originals that the upscale did not already give us (normals, >512 colour) -> work/gpng
        build   GPU: colour = base * exp(k * photo log-luminance high-pass), local mean kept, painted details protected;
                normal = whiteout blend of the (2x) original normal and the photo normal's high-pass slopes -> work/gout
        preview sheet of before/after crops -> work/grain_preview.png
        encode  texconv BC7 -> work/grain/<sha>.dds ; writes work/grain_index.json {file: [[entry, sha], ...]}
cfg keys used: cpk, toc, texconv, work, photos (dir with <id>_diff_2k.jpg / <id>_nor_gl_2k.jpg), grain_exclude_names
Colours keep the original palette (only luminance is modulated). Wood photo is rotated 90 deg when its grain
runs across the texture's own grain (structure tensor).
"""
import sys, os, json, re, math, time, struct, hashlib, collections, site
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from upscale_all import read_tex, ename, dds_info, load_toc, scope_files, texconv_batch, W

SKIPW = set('''leaf leaves flower flowers tree trees bush plant plants blades foliage sky cloud clouds water glass window windows
cloth carpet curtain flag banner metal iron gold silver bronze copper steel chain rope paper papers book books poster sign food
fruit bread fish meat bottle bottles candle lamp lantern light winlight fire smoke eff decal hair face skin logo emblem crest map
sword shield armor weapon spice bag hemp straw thatch emissive mask alpha blend grunge spt billboard color'''.split())
PH = dict(ground='forest_ground_04', sand='coast_sand_04', grass='leafy_grass', mud='mud_forest', rocky='rocky_terrain_02',
          wood='rough_wood', stone='rock_face_03', plaster='painted_plaster_wall')
# k = target std of the grain in log-luminance, sig_div: high-pass sigma = size / sig_div, normal strength
PARAM = dict(ground=(0.14, 32, 0.8), wood=(0.15, 48, 0.9), stone=(0.14, 64, 0.8), plaster=(0.10, 64, 0.7),
             roof=(0.08, 64, 0.5), floor=(0.11, 64, 0.6))

def words(name):
    base = name.rsplit('.', 1)[0]
    return [w.lower() for w in re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?![a-z])', base)]

def has(ws, *keys): return any(k in w for w in ws for k in keys)

def category(name):
    """-> (cat, photo, k, sig_div, nstr) or None"""
    l = name.lower()
    if 'basecolor' not in l: return None
    ws = [w for w in words(name) if w not in ('basecolor', 'base', 'color', 'tga', 'png', 'dds')]
    if any(w in SKIPW for w in ws): return None
    if has(ws, 'torch', 'wallpic', 'picture', 'painting', 'portrait', 'groundtable', 'bone'): return None
    obj = has(ws, 'wall', 'door', 'warp', 'pillar', 'stair', 'table', 'gate', 'statue', 'rock', 'stone', 'brick', 'column', 'box', 'chair', 'shelf', 'ceiling')
    if 'sd' in ws: ws = [w for w in ws if w not in ('sand', 'swamp')]  # sdXxx dungeons: _sand/_swamp are theme variants, not materials
    cliff_town = 'cliff' in ws and 'town' in ws
    if has(ws, 'cobble', 'pavement', 'brick', 'tile', 'slate'):
        cat = 'roof' if has(ws, 'roof') else 'stone'; ph = PH['stone']
    elif not obj and (has(ws, 'ground', 'groun', 'soil', 'dirt', 'mud', 'sand', 'beach', 'terrain', 'gravel', 'riverbed') or 'path' in ws or 'road' in ws):
        cat = 'ground'
        ph = (PH['sand'] if has(ws, 'sand', 'beach', 'desert') else PH['grass'] if has(ws, 'grass') else
              PH['rocky'] if has(ws, 'rock', 'stone', 'gravel', 'riverbed') else PH['mud'] if has(ws, 'mud', 'soil') else PH['ground'])
        if 'sandstone' in l: cat, ph = 'stone', PH['stone']
    elif has(ws, 'wood', 'plank', 'board', 'lumber', 'timber', 'beam', 'deck', 'barrel', 'crate', 'ladder', 'fence', 'pier') or 'log' in ws:
        cat, ph = 'wood', PH['wood']
    elif has(ws, 'stone', 'rock', 'cobble', 'marble', 'basalt', 'granite', 'boulder', 'masonry') or ('cliff' in ws and not cliff_town):
        cat, ph = 'stone', PH['stone']
    elif has(ws, 'plaster', 'concrete', 'cement', 'wall', 'stucco'):
        cat, ph = 'plaster', PH['plaster']
    elif has(ws, 'floor'):
        cat, ph = 'floor', PH['stone']
    else:
        return None
    k, sd, ns = PARAM[cat]
    if ws and ws[0] in ('sbs', 'textures', 'tc') or 'texturescom' in l or '_sbs' in l or '_tc' in l:  # already photo-sourced
        k, ns = k * 0.6, ns * 0.6
    return cat, ph, k, sd, ns

# ---------------- select ----------------
def phase_select(cfg, a):
    toc = load_toc(cfg['toc']); excl = set(cfg.get('grain_exclude_names', []))
    files = [f for f in scope_files(toc) if '/model/field/' in f]
    cpk = open(cfg['cpk'], 'rb'); col = {}; nrm = {}; per_file = collections.defaultdict(list); why = collections.Counter()
    for i, f in enumerate(files):
        size, off = toc[f]; cpk.seek(off); ents = read_tex(cpk.read(size))
        names = [ename(r) for r, _ in ents]; low = {n.lower(): j for j, n in enumerate(names)}
        for j, (r, d) in enumerate(ents):
            n = names[j]
            if a.only and not re.search(a.only, n): continue
            c = category(n)
            if not c: continue
            if n in excl: why['excluded_ehtria'] += 1; continue
            inf = dds_info(d)
            if not inf or inf['fmt'] != 98 or inf.get('arr', 1) != 1 or inf.get('cube'): why['format'] += 1; continue
            if max(inf['w'], inf['h']) > 1024: why['already_2048'] += 1; continue
            sha = hashlib.sha1(d).hexdigest()[:12]
            if sha not in col:
                col[sha] = dict(name=n, w=inf['w'], h=inf['h'], mips=inf['mips'], cat=c[0], photo=c[1], k=c[2], sd=c[3], ns=c[4])
                dst = W(cfg, 'gsrc', sha + '.dds'); os.makedirs(os.path.dirname(dst), exist_ok=True)
                if inf['w'] > 512 or inf['h'] > 512 or not (os.path.exists(W(cfg, 'up', sha + '.png')) or os.path.exists(W(cfg, 'png', sha + '.png'))):
                    if not os.path.exists(dst): open(dst, 'wb').write(d)
            per_file[f].append([j, sha]); why[c[0]] += 1
            nn = n.lower().replace('basecolor', 'normal')
            if nn in low:
                jn = low[nn]; dn = ents[jn][1]; ni = dds_info(dn)
                if ni and ni['fmt'] == 98 and ni.get('arr', 1) == 1 and max(ni['w'], ni['h']) <= 1024:
                    ns_ = hashlib.sha1(dn).hexdigest()[:12]
                    if ns_ not in nrm:
                        nrm[ns_] = dict(name=names[jn], w=ni['w'], h=ni['h'], mips=ni['mips'], colour=sha)
                        dst = W(cfg, 'gsrc', ns_ + '.dds')
                        if not os.path.exists(dst): open(dst, 'wb').write(dn)
                    per_file[f].append([jn, ns_]); why['normal_pairs'] += 1
        if i % 1000 == 0: print(f'select {i}/{len(files)}: {len(col)} colour, {len(nrm)} normal', flush=True)
        if a.limit and len(col) >= a.limit: break
    json.dump(dict(colour=col, normal=nrm, files=per_file), open(W(cfg, 'grain_sel.json'), 'w'))
    print('entries:', dict(why)); print(f'{len(col)} unique colour, {len(nrm)} unique normal, {len(per_file)} files')
    print('by cat:', collections.Counter(c['cat'] for c in col.values()), collections.Counter(c['photo'] for c in col.values()))

def phase_decode(cfg, a):
    sel = json.load(open(W(cfg, 'grain_sel.json')))
    todo = [W(cfg, 'gsrc', s + '.dds') for s in list(sel['normal']) + list(sel['colour'])
            if os.path.exists(W(cfg, 'gsrc', s + '.dds')) and not os.path.exists(W(cfg, 'gpng', s + '.png'))]
    print('decode', len(todo)); texconv_batch(cfg, todo, W(cfg, 'gpng'), ['-ft', 'png', '-f', 'R8G8B8A8_UNORM'])

# ---------------- build (GPU) ----------------
def phase_build(cfg, a):
    sys.path.insert(0, site.getusersitepackages())
    import numpy as np, torch, torch.nn.functional as F
    from PIL import Image
    dev = 'cuda' if torch.cuda.is_available() else 'cpu'; print('device', dev, flush=True)
    sel = json.load(open(W(cfg, 'grain_sel.json'))); os.makedirs(W(cfg, 'gout'), exist_ok=True)
    only = set(open(a.shas).read().split()) if a.shas else None  # rebuild just these colour shas (no normals)
    if not a.limit and not only:  # full rebuild: drop results of earlier runs so skipped textures don't keep a stale grain
        for fn in os.listdir(W(cfg, 'gout')): os.remove(W(cfg, 'gout', fn))

    def load(p): return torch.from_numpy(np.asarray(Image.open(p).convert('RGBA')).astype(np.float32) / 255).permute(2, 0, 1).to(dev)
    def save(t, p):
        Image.fromarray((t.clamp(0, 1).permute(1, 2, 0).cpu().numpy() * 255).round().astype(np.uint8), 'RGBA').save(p, compress_level=1)
    def blur(x, s):
        H, Wd = x.shape[-2:]
        fy = torch.fft.fftfreq(H, device=dev)[:, None]; fx = torch.fft.fftfreq(Wd, device=dev)[None, :]
        k = torch.exp(-2 * (math.pi * s) ** 2 * (fx ** 2 + fy ** 2))
        return torch.fft.ifft2(torch.fft.fft2(x) * k).real
    def resize_wrap(x, H, Wd):
        c, h, w = x.shape; p = max(4, min(h, w) // 16)
        xp = F.pad(x[None], (p, p, p, p), mode='circular'); sy, sx = H / h, Wd / w
        o = F.interpolate(xp, size=(round((h + 2 * p) * sy), round((w + 2 * p) * sx)), mode='bicubic', align_corners=False, antialias=sy < 1 or sx < 1)[0]
        qy, qx = round(p * sy), round(p * sx); return o[:, qy:qy + H, qx:qx + Wd]
    def lin(s): return torch.where(s <= 0.04045, s / 12.92, ((s + 0.055) / 1.055) ** 2.4)
    def srgb(l): l = l.clamp(0, 1); return torch.where(l <= 0.0031308, l * 12.92, 1.055 * l ** (1 / 2.4) - 0.055)
    def lum(l): return 0.2126 * l[0] + 0.7152 * l[1] + 0.0722 * l[2]
    def aniso(lg):  # >0: gradients mostly along x (features run vertically); coherence-weighted
        gx = lg.roll(-1, 1) - lg.roll(1, 1); gy = lg.roll(-1, 0) - lg.roll(1, 0)
        jxx, jyy, jxy = (gx * gx).mean(), (gy * gy).mean(), (gx * gy).mean()
        return ((jxx - jyy) / (jxx + jyy + 1e-12)).item()
    photos = {}
    def photo(pid, kind, rot):
        key = (pid, kind, rot)
        if key not in photos:
            t = torch.from_numpy(np.asarray(Image.open(os.path.join(cfg['photos'], f'{pid}_{kind}_2k.jpg')).convert('RGB')).astype(np.float32) / 255).permute(2, 0, 1).to(dev)
            if rot:
                t = torch.rot90(t, 1, dims=(1, 2))
                if kind == 'nor_gl':  # rotate tangent vectors with the image (CCW): x' = -y, y' = x
                    v = t * 2 - 1; t = (torch.stack([-v[1], v[0], v[2]]) + 1) / 2
            photos[key] = t
        return photos[key]
    photo_aniso = {}
    rot_of = {}; photo_of = {}; t0 = time.time(); n = 0; logl = []
    items = list(sel['colour'].items())
    if a.limit: items = items[:a.limit]
    if only: items = [(s, c) for s, c in items if s in only]
    for sha, c in items:
        out_p = W(cfg, 'gout', sha + '.png')
        src = W(cfg, 'up', sha + '.png')
        if not os.path.exists(src): src = W(cfg, 'gpng', sha + '.png')
        if not os.path.exists(src): src = W(cfg, 'png', sha + '.png')  # upscale skipped it (flat etc.): grain at original size
        if not os.path.exists(src): logl.append(f'{sha}\t{c["name"]}\tno_source'); continue
        img = load(src); rgb, al = img[:3], img[3:]; H, Wd = rgb.shape[1:]
        l = lin(rgb); lb = torch.log(lum(l) + 1e-3)
        cat, cp, rot = c['cat'], c['photo'], 0
        ab = aniso(lb - blur(lb, max(H, Wd) / 16))
        if cat == 'ground':
            gx = lb.roll(-1, 1) - lb.roll(1, 1); gy = lb.roll(-1, 0) - lb.roll(1, 0); e = gx * gx + gy * gy
            axis = ((((gx * gx - gy * gy) ** 2) / (e + 1e-9)).sum() / e.sum()).item()  # 1 = edges all horizontal/vertical
            if axis > 0.6:  # paved / tiled / parquet "ground": man-made layout, natural grain would fight it
                logl.append(f'{sha}\t{c["name"]}\tskip_structured_ground\taxis={axis:.2f}'); continue
        if cat == 'wood':
            if cp not in photo_aniso:
                pl = torch.log(lum(lin(photo(cp, 'diff', 0))) + 1e-3); photo_aniso[cp] = aniso(pl - blur(pl, 64))
            if abs(ab) > 0.08 and ab * photo_aniso[cp] < 0: rot = 90
        rot_of[sha] = rot; photo_of[sha] = cp
        p = resize_wrap(photo(cp, 'diff', rot), H, Wd).clamp(0, 1)  # bicubic overshoot < 0 made log(lum) NaN -> black (pass 5 fix)
        lp = torch.log(lum(lin(p)) + 1e-3); sig = max(H, Wd) / c['sd']
        hp = lp - blur(lp, sig); sd = hp.std(); hp = (hp / sd).clamp(-3, 3)  # unit std: c['k'] is the target strength
        bh = blur((lb - blur(lb, max(H, Wd) / 64)).abs(), 2)
        m = (1 - bh / 0.35).clamp(0.35, 1)
        g = torch.exp(hp * c['k'] * m); g = g / blur(g, sig)
        new = srgb(l * g)
        if not torch.isfinite(new).all(): logl.append(f'{sha}	{c["name"]}	nan_skip'); continue
        save(torch.cat([new, al]), out_p); n += 1
        logl.append(f'{sha}\t{c["name"]}\t{cat}\t{cp}\trot{rot}\t{H}x{Wd}\thp_sd={sd.item():.3f}')
    print(f'colour built {n} in {time.time() - t0:.0f}s', flush=True)
    t0 = time.time(); nn = 0
    for sha, nm in ([] if only else sel['normal'].items()):
        c = sel['colour'].get(nm['colour'])
        if not c or nm['colour'] not in rot_of: continue
        src = W(cfg, 'gpng', sha + '.png')
        if not os.path.exists(src): continue
        img = load(src); o = img[:3] * 2 - 1
        if o[2].mean() < 0.4: logl.append(f'{sha}\t{nm["name"]}\tnot_a_normal'); continue
        s2 = 2 if max(nm['w'], nm['h']) <= 512 else 1; H, Wd = nm['h'] * s2, nm['w'] * s2
        o = resize_wrap(o, H, Wd); o = o / o.norm(dim=0, keepdim=True).clamp_min(1e-6)
        al = resize_wrap(img[3:], H, Wd).clamp(0, 1)
        p = resize_wrap(photo(photo_of[nm['colour']], 'nor_gl', rot_of[nm['colour']]), H, Wd) * 2 - 1
        sig = max(H, Wd) / c['sd']
        dxy = (p[:2] - blur(p[:2], sig)) * c['ns']
        dz = (1 - (dxy ** 2).sum(0)).clamp_min(0.05).sqrt()
        v = torch.stack([o[0] + dxy[0], o[1] + dxy[1], o[2] * dz]); v[2] = v[2].clamp_min(0.05)
        v = v / v.norm(dim=0, keepdim=True)
        save(torch.cat([(v + 1) / 2, al]), W(cfg, 'gout', sha + '.png')); nn += 1
    print(f'normal built {nn} in {time.time() - t0:.0f}s')
    open(W(cfg, 'grain_build_log_fix.tsv' if only else 'grain_build_log.tsv'), 'w', encoding='utf-8').write('\n'.join(logl) + '\n')
    print('rotated wood:', sum(1 for v in rot_of.values() if v))

def phase_preview(cfg, a):
    from PIL import Image, ImageDraw
    sel = json.load(open(W(cfg, 'grain_sel.json'))); import random
    random.seed(int(a.limit) if a.limit else 7)
    bycat = collections.defaultdict(list)
    for s, c in sel['colour'].items():
        if os.path.exists(W(cfg, 'gout', s + '.png')): bycat[c['cat']].append(s)
    picks = []
    for cat in ('ground', 'wood', 'stone', 'plaster', 'roof', 'floor'):
        if bycat[cat]: picks += [(cat, s) for s in random.sample(bycat[cat], min(2, len(bycat[cat])))]
    T = 300; sheet = Image.new('RGB', (2 * T + 30, len(picks) * (T + 24)), 'white'); d = ImageDraw.Draw(sheet)
    for i, (cat, s) in enumerate(picks):
        c = sel['colour'][s]
        b = W(cfg, 'up', s + '.png'); b = b if os.path.exists(b) else W(cfg, 'gpng', s + '.png')
        if not os.path.exists(b): b = W(cfg, 'png', s + '.png')
        A = Image.open(b).convert('RGB'); B = Image.open(W(cfg, 'gout', s + '.png')).convert('RGB')
        w, h = A.size; cs = min(w, h, 512); x0, y0 = (w - cs) // 2, (h - cs) // 2
        for k, im in enumerate((A, B)):
            sheet.paste(im.crop((x0, y0, x0 + cs, y0 + cs)).resize((T, T), Image.LANCZOS), (k * (T + 30), i * (T + 24) + 20))
        d.text((4, i * (T + 24) + 4), f'{cat}: {c["name"][:70]}  [{c["photo"]}]', fill='black')
    sheet.save(W(cfg, 'grain_preview.png')); print('preview', len(picks))

def phase_encode(cfg, a):
    sel = json.load(open(W(cfg, 'grain_sel.json'))); groups = collections.defaultdict(list)
    info = dict(sel['colour']); info.update(sel['normal'])
    def fresh(s):  # grain DDS exists and was encoded from the current build output
        p, d = W(cfg, 'gout', s + '.png'), W(cfg, 'grain', s + '.dds')
        return os.path.exists(p) and os.path.exists(d) and os.path.getmtime(d) >= os.path.getmtime(p)
    for s, u in info.items():
        p = W(cfg, 'gout', s + '.png')
        if not os.path.exists(p) or fresh(s): continue
        from PIL import Image
        w = Image.open(p).size[0]; up = int(round(math.log2(w / u['w'])))
        groups[u['mips'] + up].append(p)
    for m, lst in sorted(groups.items()):
        print(f'encode mips={m}: {len(lst)}', flush=True)
        texconv_batch(cfg, lst, W(cfg, 'grain'), ['-f', 'BC7_UNORM', '-m', str(m)])
    idx = {f: [[j, s] for j, s in picks if fresh(s)] for f, picks in sel['files'].items()}
    idx = {f: v for f, v in idx.items() if v}
    json.dump(idx, open(W(cfg, 'grain_index.json'), 'w'))
    print(f'grain index: {len(idx)} files, {sum(len(v) for v in idx.values())} entries')

PH_ = dict(select=phase_select, decode=phase_decode, build=phase_build, preview=phase_preview, encode=phase_encode)
if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument('cfg'); ap.add_argument('phase')
    ap.add_argument('--limit', type=int, default=0); ap.add_argument('--only', default=''); ap.add_argument('--shas', default='')
    a = ap.parse_args(); cfg = json.load(open(a.cfg))
    for p in (PH_ if a.phase == 'all' else [a.phase]):
        t = time.time(); print(f'== {p}', flush=True); PH_[p](cfg, a); print(f'== {p} done in {time.time() - t:.0f}s', flush=True)
