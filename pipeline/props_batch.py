"""Batch version of props.py (variant B chosen by the user on the Grand Trad meat stall) for close-up props.

usage: python -I props_batch.py <upall_cfg.json> <sel.json> targets|build|preview|encode|files|masks [--limit N] [--only SHA,SHA]
sel.json: {colour sha: {"wood": bool, "food": bool}} (from the name tokens, see prop_select.py)

colour, 4x:
  non-wood pixels -> 4x-UltraSharpV2 Lite from the original (+ back-projection)            (meat/fruit/fish/cloth/metal...)
  wood pixels     -> RCAN 2x on top of the shipped 2x, then the "rebuild": low-pass of the painting + keep*its own detail
                     + oriented photo grain (rough_wood), keep=0.5 k=0.26                          (only when sel says wood)
  wood mask = wood hue x oriented structure (coherence) -> blobby fruit/fish/meat of the same hue is left out
normal (wood textures only, when the TEX has the matching normal): 4x + photo relief on the wood mask, same orientation/tiling.
Work dir: work/pbatch.  encode -> work/special/<sha>.dds and MERGES into work/special_index.json (backup .bak_pre_pbatch).
"""
import sys, os, re, json, math, hashlib, time, site, collections, shutil, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from upscale_all import read_tex, ename, dds_info, load_toc, texconv_batch, W

P = dict(lp=2.0, keep=0.5, k_rebuild=0.26)     # variant B
K_NRM, SIG_DIV = 0.9, 48
FOOD_MODEL = '4x-UltraSharpV2_Lite.safetensors'
K_PER_SD, K_MIN = float(os.environ.get('K_PER_SD', 0.72)), 0.08   # sd 0.13 (clean painted table) -> k 0.09, sd >= 0.36 (meat stall 0.54) -> 0.26
MIN_WOOD = 0.04                                # below this wood fraction the texture gets no grain at all


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def targets(cfg, sel):
    ix = json.load(open(W(cfg, 'index.json'))); toc = load_toc(cfg['toc']); cpk = open(cfg['cpk'], 'rb')
    excl_files = set(cfg.get('exclude_files', [])); excl_names = {n.lower() for n in cfg.get('grain_exclude_names', [])}
    os.makedirs(W(cfg, 'pbatch', 'src'), exist_ok=True)
    info, files, why = {}, collections.defaultdict(list), collections.Counter()
    for f, lst in ix['files'].items():
        if '/field/object/' not in f or f in excl_files: continue
        ks = [(k, s) for k, s in lst if s in sel]
        if not ks: continue
        size, off = toc[f]; cpk.seek(off); ents = read_tex(cpk.read(size)); names = {ename(r).lower(): j for j, (r, d) in enumerate(ents)}
        for k, s in ks:
            n = ename(ents[k][0]); d = ents[k][1]; inf = dds_info(d)
            if hashlib.sha1(d).hexdigest()[:12] != s: why['sha_mismatch'] += 1; continue
            if n.lower() in excl_names: why['grain_excluded_name'] += 1; continue
            if not inf or inf['fmt'] != 98: why['not_bc7'] += 1; continue
            info.setdefault(s, dict(name=n, w=inf['w'], h=inf['h'], mips=inf['mips'], kind='col', **sel[s]))
            if [k, s] not in files[f]: files[f].append([k, s])
            if not sel[s]['wood']: continue
            jj = names.get(re.sub('basecolor', 'normal', n, flags=re.I).lower())
            if jj is None: why['no_normal'] += 1; continue
            nd = ents[jj][1]; ni = dds_info(nd)
            if not ni or ni['fmt'] != 98: why['normal_not_bc7'] += 1; continue
            ns = hashlib.sha1(nd).hexdigest()[:12]
            info.setdefault(ns, dict(name=ename(ents[jj][0]), w=ni['w'], h=ni['h'], mips=ni['mips'], kind='nrm', pair=s))
            p = W(cfg, 'pbatch', 'src', ns + '.dds')
            if not os.path.exists(p): open(p, 'wb').write(nd)
            if [jj, ns] not in files[f]: files[f].append([jj, ns])
    json.dump(dict(info=info, files=files), open(W(cfg, 'pbatch', 'targets.json'), 'w'))
    c = collections.Counter(u['kind'] for u in info.values())
    print('targets:', dict(c), 'files:', len(files), 'skipped:', dict(why))


def build(cfg, sel, masks_only=False):
    sys.path.insert(0, site.getusersitepackages())
    import numpy as np, torch, torch.nn.functional as F
    from PIL import Image
    from spandrel import ModelLoader
    dev = 'cuda'
    t = json.load(open(W(cfg, 'pbatch', 'targets.json'))); info = t['info']
    only = set(arg('--only', '').split(',')) - {''}; lim = int(arg('--limit', 0))
    cols = [s for s, u in info.items() if u['kind'] == 'col' and (not only or s in only)]
    if lim: cols = cols[:lim]
    out = W(cfg, 'pbatch', 'out'); npng = W(cfg, 'pbatch', 'npng'); os.makedirs(out, exist_ok=True); os.makedirs(npng, exist_ok=True)
    nrms = [s for s, u in info.items() if u['kind'] == 'nrm' and u['pair'] in cols]
    todo = [W(cfg, 'pbatch', 'src', s + '.dds') for s in nrms if not os.path.exists(os.path.join(npng, s + '.png'))]
    if todo and not masks_only: texconv_batch(cfg, todo, npng, ['-ft', 'png', '-f', 'R8G8B8A8_UNORM', '-m', '1'])
    m2 = ModelLoader().load_from_file(cfg['model']).eval().to(dev); m2.model.half()
    m4 = ModelLoader().load_from_file(os.path.join(os.path.dirname(cfg['model']), FOOD_MODEL)).eval().to(dev); m4.model.half()

    def run(model, x, s):
        Pd = 8; xp = F.pad(x[None], (Pd, Pd, Pd, Pd), mode='replicate')
        with torch.inference_mode():
            sr = model(xp.half()).float().clamp(0, 1)[:, :, s * Pd:-s * Pd, s * Pd:-s * Pd]
            for _ in range(3):
                sr = (sr + F.interpolate(x[None] - F.avg_pool2d(sr, s), scale_factor=s, mode='bicubic', align_corners=False)).clamp(0, 1)
        return sr[0]
    def load(p): return torch.from_numpy(np.asarray(Image.open(p).convert('RGBA')).astype(np.float32) / 255).permute(2, 0, 1).to(dev)
    def save(x, p): Image.fromarray((x.clamp(0, 1).permute(1, 2, 0).cpu().numpy() * 255).round().astype(np.uint8), 'RGBA').save(p)
    def blur(x, s):
        H, Wd = x.shape[-2:]
        fy = torch.fft.fftfreq(H, device=dev)[:, None]; fx = torch.fft.fftfreq(Wd, device=dev)[None, :]
        return torch.fft.ifft2(torch.fft.fft2(x) * torch.exp(-2 * (math.pi * s) ** 2 * (fx ** 2 + fy ** 2))).real
    def upA(a, s): return F.interpolate(a[None], scale_factor=s, mode='bicubic', align_corners=False)[0].clamp(0, 1)
    def lin(s): return torch.where(s <= 0.04045, s / 12.92, ((s + 0.055) / 1.055) ** 2.4)
    def srgb(l): l = l.clamp(0, 1); return torch.where(l <= 0.0031308, l * 12.92, 1.055 * l ** (1 / 2.4) - 0.055)
    def lum(l): return 0.2126 * l[0] + 0.7152 * l[1] + 0.0722 * l[2]
    def tile_to(x, H, Wd):
        ry, rx = math.ceil(H / x.shape[1]), math.ceil(Wd / x.shape[2]); return x.repeat(1, ry, rx)[:, :H, :Wd]
    def hue_mask(rgb):
        mx, _ = rgb.max(0); mn, _ = rgb.min(0); c = mx - mn; v = mx; s = c / mx.clamp_min(1e-4)
        r, g, b = rgb
        hue = torch.where(mx == r, ((g - b) / c.clamp_min(1e-4)) % 6, torch.where(mx == g, (b - r) / c.clamp_min(1e-4) + 2, (r - g) / c.clamp_min(1e-4) + 4)) * 60
        sm = lambda x, a, b_: ((x - a) / (b_ - a)).clamp(0, 1)
        # saturated orange/yellow (fruit, cheese, bread crust) is not wood
        return sm(hue, 8, 16) * (1 - sm(hue, 42, 55)) * sm(s, 0.12, 0.24) * (1 - sm(s, 0.62, 0.78)) * sm(v, 0.08, 0.18) * (1 - sm(v, 0.92, 0.99))
    def tensor(lg, sig):
        gx = lg.roll(-1, 1) - lg.roll(1, 1); gy = lg.roll(-1, 0) - lg.roll(1, 0)
        return blur(gx * gx, sig), blur(gy * gy, sig), blur(gx * gy, sig)
    def wood_mask(rgb, lg, srcpx, food):
        # regional decision: wood hue x oriented structure measured over ~6 source px, both smoothed, so plank
        # seams and ends do not punch holes in the mask; atlases that also hold food need clearer orientation
        jxx, jyy, jxy = tensor(lg, 6 * srcpx)
        coh = ((jxx - jyy) ** 2 + 4 * jxy ** 2).sqrt() / (jxx + jyy + 1e-6)
        sm = lambda x, a, b_: ((x - a) / (b_ - a)).clamp(0, 1)
        lo, hi = (0.15, 0.32) if food else (0.06, 0.20)
        gate = blur(sm(coh, lo, hi), 4 * srcpx)
        return (blur(hue_mask(rgb), srcpx) * gate).clamp(0, 1)
    def orient(lg, sig):
        jxx, jyy, _ = tensor(lg, sig); return (0.5 + 0.5 * (jyy - jxx) / (jxx + jyy + 1e-6)).clamp(0, 1)

    ph = torch.from_numpy(np.asarray(Image.open(os.path.join(cfg['photos'], 'rough_wood_diff_2k.jpg')).convert('RGB')).astype(np.float32) / 255).permute(2, 0, 1).to(dev)
    pn = torch.from_numpy(np.asarray(Image.open(os.path.join(cfg['photos'], 'rough_wood_nor_gl_2k.jpg')).convert('RGB')).astype(np.float32) / 255).permute(2, 0, 1).to(dev)
    pl = torch.log(lum(lin(ph)) + 1e-3); hp = pl - blur(pl, 2048 / SIG_DIV)
    gx = hp.roll(-1, 1) - hp.roll(1, 1); gy = hp.roll(-1, 0) - hp.roll(1, 0)
    photo_vertical = (gx * gx).mean() > (gy * gy).mean()
    def oriented(x, vec=False):
        r = torch.rot90(x, 1, dims=(-2, -1))
        if vec: v = r * 2 - 1; r = (torch.stack([-v[1], v[0], v[2]]) + 1) / 2
        return (r, x) if photo_vertical else (x, r)
    hpH, hpV = oriented(hp[None]); hpH, hpV = hpH[0] / hpH.std(), hpV[0] / hpV.std()
    pnH, pnV = oriented(pn, vec=True)

    log = open(W(cfg, 'pbatch', 'build_log.tsv'), 'a'); masks = {}; t00 = time.time()
    for i, s in enumerate(cols):
        u = info[s]; t0 = time.time()
        o = load(W(cfg, 'png', s + '.png')); h, w = o.shape[1:]
        if (w, h) != (u['w'], u['h']): print('size mismatch', s, u['name']); continue
        us = run(m4, o[:3], 4)                                                         # sharp 4x of everything
        al = upA(upA(o[3:], 2), 2)
        H, Wd = us.shape[1:]
        if u['wood']:
            x2p = W(cfg, 'up', s + '.png')
            x2 = load(x2p)[:3] if os.path.exists(x2p) else run(m2, o[:3], 2)
            if x2.shape[1] != h * 2: x2 = run(m2, o[:3], 2)
            x4 = run(m2, x2, 2)
            lg4 = torch.log(lum(lin(x4)) + 1e-3)
            m = wood_mask(x4, lg4, H / h, u['food'])
            if m.mean() < MIN_WOOD: m = torch.zeros_like(m)
        else:
            m = torch.zeros(H, Wd, device=dev); x4 = us
        if masks_only:
            Image.fromarray((m.cpu().numpy() * 255).astype(np.uint8)).save(os.path.join(out, s + '_woodmask.png')); print(s, u['name'], f'{m.mean().item():.2f}'); continue
        base = x4 * m + us * (1 - m)                                                    # wood: RCAN, rest: sharp
        l = lin(base); lg = torch.log(lum(l) + 1e-3)
        if m.max() > 0:
            wh = orient(lg, max(H, Wd) / 128)
            g = wh * tile_to(hpH[None], H, Wd)[0] + (1 - wh) * tile_to(hpV[None], H, Wd)[0]
            lpw = torch.exp(blur(lg, P['lp'] * H / h)); det = (lum(l) + 1e-3) / lpw
            # how much painted detail the wood has (log std on the mask): smooth, clean wood gets less photo grain,
            # blocky/busy wood (the meat stall) gets the full B strength
            sd = float(((m * det.clamp(1e-3).log() ** 2).sum() / m.sum().clamp_min(1)).sqrt())
            k = min(P['k_rebuild'], max(K_MIN, K_PER_SD * sd))
            # local gate: flat, cleanly painted areas (stylised highlights, plain boards) keep their look; grain and
            # de-blocking only where the painting itself already has detail
            ld2 = det.clamp(1e-3).log() ** 2; rr = 4 * H / h
            ld = (blur(m * ld2, rr) / blur(m, rr).clamp_min(1e-3)).clamp_min(0).sqrt()
            m = m * ((ld - 0.06) / 0.12).clamp(0, 1)
            e = torch.exp(k * g.clamp(-3, 3) * m); e = e / blur(e, max(H, Wd) / SIG_DIV); e = 1 + (e - 1) * m
            e = e * torch.where(m > 0, det.clamp(1e-3) ** ((P['keep'] - 1) * m), torch.ones_like(m))
            l = l * e; masks[s] = (m, wh, k / P['k_rebuild'])
            Image.fromarray((m.cpu().numpy() * 255).astype(np.uint8)).save(os.path.join(out, s + '_woodmask.png'))
        save(torch.cat([srgb(l), al]), os.path.join(out, s + '.png'))
        ks = f"{sd:.3f}\t{k:.3f}" if m.max() > 0 else "-\t-"
        log.write(f"{s}\t{u['name']}\t{w}\t{m.mean().item():.3f}\t{ks}\t{time.time() - t0:.1f}\n"); log.flush()
        if only: print(u['name'], f'wood={m.mean().item():.2f}', ks.replace('\t', ' '), flush=True)
        if i % 25 == 0: print(f'{i}/{len(cols)} {u["name"]} wood={m.mean().item():.2f} {time.time() - t00:.0f}s', flush=True)
    if masks_only: return
    n_n = 0
    for s in nrms:
        u = info[s]; c = u['pair']
        if c not in masks: continue
        o = load(os.path.join(npng, s + '.png')); H, Wd = u['h'] * 4, u['w'] * 4
        n = F.interpolate(o[None, :3] * 2 - 1, size=(H, Wd), mode='bicubic', align_corners=False)[0]; n = n / n.norm(dim=0, keepdim=True).clamp_min(1e-6)
        m, wh, ks = masks[c]; m, wh = (F.interpolate(x[None, None], size=(H, Wd), mode='area')[0, 0] for x in (m, wh))
        d = wh * tile_to(pnH, H, Wd)[:2] + (1 - wh) * tile_to(pnV, H, Wd)[:2]; d = d * 2 - 1
        d = (d - blur(d, max(H, Wd) / SIG_DIV)) * K_NRM * ks * m
        dz = (1 - (d ** 2).sum(0)).clamp_min(0.05).sqrt()
        v = torch.stack([n[0] + d[0], n[1] + d[1], n[2] * dz]); v[2] = v[2].clamp_min(0.05); v = v / v.norm(dim=0, keepdim=True)
        a = F.interpolate(o[None, 3:], size=(H, Wd), mode='bicubic')[0].clamp(0, 1)
        save(torch.cat([(v + 1) / 2, a]), os.path.join(out, s + '.png')); n_n += 1
    print(f'colour {len(cols)}, normals {n_n}, {time.time() - t00:.0f}s')


def preview(cfg, sel):
    sys.path.insert(0, site.getusersitepackages())
    import subprocess, tempfile
    from PIL import Image, ImageDraw
    from best_source import final_of
    t = json.load(open(W(cfg, 'pbatch', 'targets.json'))); out = W(cfg, 'pbatch', 'out')
    lj = lambda n: json.load(open(W(cfg, n))) if os.path.exists(W(cfg, n)) else {}
    lay = (lj('cavity_index.json'), lj('special_index.json'), lj('grain_index.json'))
    only = [x for x in arg('--only', '').split(',') if x]
    cols = only or [s for s, u in t['info'].items() if u['kind'] == 'col' and os.path.exists(os.path.join(out, s + '.png'))]
    if not only: random.seed(int(arg('--seed', 3))); cols = random.sample(cols, min(int(arg('--n', 8)), len(cols)))
    tmp = tempfile.mkdtemp(prefix='pbp_'); T = 360; rows = []
    for s in cols:
        u = t['info'][s]; p, kind = final_of(cfg, s, lay=lay)
        subprocess.run([cfg['texconv'], '-nologo', '-y', '-ft', 'png', '-f', 'R8G8B8A8_UNORM', '-m', '1', '-o', tmp, p], check=True, stdout=subprocess.DEVNULL)
        A = Image.open(os.path.join(tmp, os.path.basename(p)[:-4] + '.png')).convert('RGB'); B = Image.open(os.path.join(out, s + '.png')).convert('RGB')
        A = A.resize(B.size, Image.BICUBIC); w, h = B.size; k = w // 4
        # window: 70th percentile of local detail in the result (inside painted content, not gutters)
        import numpy as np
        g = np.asarray(B.convert('L').resize((w // 8, h // 8), Image.BOX)).astype(np.float32)
        best = None; sc = []
        for y in range(0, h - k + 1, k // 2):
            for x in range(0, w - k + 1, k // 2):
                sc.append((float(g[y // 8:(y + k) // 8, x // 8:(x + k) // 8].std()), x, y))
        sc.sort(); _, x0, y0 = sc[int(len(sc) * 0.7)]
        c = (x0, y0, x0 + k, y0 + k)
        rows.append((f"{u['name'][:58]} ({u['w']}px{', legno' if u.get('wood') else ''}{', cibo' if u.get('food') else ''})",
                     A.crop(c).resize((T, T), Image.LANCZOS), B.crop(c).resize((T, T), Image.LANCZOS)))
    sheet = Image.new('RGB', (2 * T + 10, len(rows) * (T + 24) + 30), (16, 16, 16)); d = ImageDraw.Draw(sheet)
    d.text((4, 6), 'PRIMA (mod attuale)', fill=(255, 210, 140), font_size=18); d.text((T + 14, 6), 'DOPO (4x, variante B)', fill=(150, 230, 160), font_size=18)
    for i, (n, a, b) in enumerate(rows):
        y = 30 + i * (T + 24); d.text((4, y), n, fill=(220, 220, 220), font_size=14); sheet.paste(a, (0, y + 20)); sheet.paste(b, (T + 10, y + 20))
    p = arg('--out', W(cfg, 'pbatch', 'preview.png')); sheet.save(p); print(p, sheet.size)


def encode(cfg, sel):
    t = json.load(open(W(cfg, 'pbatch', 'targets.json'))); out = W(cfg, 'pbatch', 'out'); groups = collections.defaultdict(list)
    for s, u in t['info'].items():
        if os.path.exists(os.path.join(out, s + '.png')): groups[u['mips'] + 2].append(os.path.join(out, s + '.png'))
    for m, lst in groups.items(): texconv_batch(cfg, lst, W(cfg, 'special'), ['-f', 'BC7_UNORM', '-m', str(m)] + cfg.get('bc7_args', []))
    six_p = W(cfg, 'special_index.json'); six = json.load(open(six_p)) if os.path.exists(six_p) else {}
    if not os.path.exists(six_p + '.bak_pre_pbatch'): shutil.copy(six_p, six_p + '.bak_pre_pbatch')
    n = 0; nf = set()
    for f, lst in t['files'].items():
        for j, s in lst:
            if not os.path.exists(os.path.join(out, s + '.png')) or not os.path.exists(W(cfg, 'special', s + '.dds')): continue
            cur = six.setdefault(f, []); cur[:] = [x for x in cur if x[0] != j] + [[j, s]]; n += 1; nf.add(f)
    json.dump(six, open(six_p, 'w')); json.dump(sorted(nf), open(W(cfg, 'pbatch', 'files.json'), 'w'))
    print('special slots merged:', n, 'files:', len(nf))


def files(cfg, sel):
    print('\n'.join(json.load(open(W(cfg, 'pbatch', 'files.json')))))


if __name__ == '__main__':
    cfg = json.load(open(sys.argv[1])); sel = json.load(open(sys.argv[2])); ph = sys.argv[3]
    if ph == 'targets': targets(cfg, sel)
    elif ph == 'masks': build(cfg, sel, masks_only=True)
    else: dict(build=build, preview=preview, encode=encode, files=files)[ph](cfg, sel)
