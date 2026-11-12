"""Carriage (basha_*) atlases: 4x (second AI 2x pass on the upscale) + wood grain that follows each plank's direction.
usage: python -I carriage.py <upall_cfg.json> [build|preview|encode|all]
One atlas covers the whole carriage, so it gets more texels (VRAM: ~11 MB for a 2048x4096 BC7 atlas) and photo grain
(rough_wood) only where the atlas is wood-coloured; per-pixel orientation from a smoothed structure tensor blends a
horizontal and a vertical copy of the grain. The matching normal maps (basha_n / basha_002_n) get the photo relief the same way.
Writes work/special/<sha>.dds and work/special_index.json (picked up by upscale_all.py pack, overriding up/grain).
"""
import sys, os, re, json, math, hashlib, time, site, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from upscale_all import read_tex, ename, dds_info, load_toc, scope_files, texconv_batch, run, W

COL = re.compile(r'^basha_(d|\d{3})\.png$'); NRM = re.compile(r'^basha(_\d{3})?_n\.png$')
K_COL, K_NRM, SIG_DIV = 0.20, 0.9, 48

def targets(cfg):
    toc = load_toc(cfg['toc']); cpk = open(cfg['cpk'], 'rb'); info = {}; six = collections.defaultdict(list); pairs = {}
    os.makedirs(W(cfg, 'sp_src'), exist_ok=True)
    for f in scope_files(toc):
        if not ('/field/' in f or '/vehicle/' in f): continue
        size, off = toc[f]; cpk.seek(off); blob = cpk.read(size)
        if b'basha_' not in blob: continue
        ents = read_tex(blob); col_here = None
        for j, (r, d) in enumerate(ents):
            n = ename(r)
            if not (COL.match(n) or NRM.match(n)): continue
            inf = dds_info(d); sha = hashlib.sha1(d).hexdigest()[:12]
            if not inf or inf['fmt'] != 98: continue
            kind = 'col' if COL.match(n) else 'nrm'
            info.setdefault(sha, dict(name=n, w=inf['w'], h=inf['h'], mips=inf['mips'], kind=kind))
            p = W(cfg, 'sp_src', sha + '.dds')
            if not os.path.exists(p): open(p, 'wb').write(d)
            six[f].append([j, sha])
            if kind == 'col': col_here = sha
        for j, s in six.get(f, []):
            if info[s]['kind'] == 'nrm' and col_here: pairs[s] = col_here
    return info, six, pairs

def build(cfg):
    sys.path.insert(0, site.getusersitepackages())
    import numpy as np, torch, torch.nn.functional as F
    from PIL import Image
    from spandrel import ModelLoader
    dev = 'cuda'
    info, six, pairs = targets(cfg)
    print('targets:', {s: (v['name'], v['w'], v['h']) for s, v in info.items()}, flush=True)
    os.makedirs(W(cfg, 'sp_png'), exist_ok=True); os.makedirs(W(cfg, 'sp_out'), exist_ok=True)
    texconv_batch(cfg, [W(cfg, 'sp_src', s + '.dds') for s in info], W(cfg, 'sp_png'), ['-ft', 'png', '-f', 'R8G8B8A8_UNORM'])
    model = ModelLoader().load_from_file(cfg['model']).eval().to(dev); model.model.half()

    def load(p): return torch.from_numpy(np.asarray(Image.open(p).convert('RGBA')).astype(np.float32) / 255).permute(2, 0, 1).to(dev)
    def save(t, p): Image.fromarray((t.clamp(0, 1).permute(1, 2, 0).cpu().numpy() * 255).round().astype(np.uint8), 'RGBA').save(p)
    def blur(x, s):
        H, Wd = x.shape[-2:]
        fy = torch.fft.fftfreq(H, device=dev)[:, None]; fx = torch.fft.fftfreq(Wd, device=dev)[None, :]
        return torch.fft.ifft2(torch.fft.fft2(x) * torch.exp(-2 * (math.pi * s) ** 2 * (fx ** 2 + fy ** 2))).real
    def up2(x):  # x (C,H,W) in [0,1] -> AI 2x with 3-step back-projection
        P = 8; xp = F.pad(x[None], (P, P, P, P), mode='replicate')
        with torch.inference_mode():
            sr = model(xp.half()).float().clamp(0, 1)[:, :, 2 * P:-2 * P, 2 * P:-2 * P]
            for _ in range(3):
                sr = (sr + F.interpolate(x[None] - F.avg_pool2d(sr, 2), scale_factor=2, mode='bicubic', align_corners=False)).clamp(0, 1)
        return sr[0]
    def lin(s): return torch.where(s <= 0.04045, s / 12.92, ((s + 0.055) / 1.055) ** 2.4)
    def srgb(l): l = l.clamp(0, 1); return torch.where(l <= 0.0031308, l * 12.92, 1.055 * l ** (1 / 2.4) - 0.055)
    def lum(l): return 0.2126 * l[0] + 0.7152 * l[1] + 0.0722 * l[2]
    def tile_to(t, H, Wd):  # repeat a photo to cover HxW at its native scale
        ry, rx = math.ceil(H / t.shape[1]), math.ceil(Wd / t.shape[2]); return t.repeat(1, ry, rx)[:, :H, :Wd]
    def wood_mask(rgb):  # brown / orange, not grey metal, not dark red velvet, not pale canvas
        mx, _ = rgb.max(0); mn, _ = rgb.min(0); c = mx - mn; v = mx; s = c / mx.clamp_min(1e-4)
        r, g, b = rgb
        hue = torch.where(mx == r, ((g - b) / c.clamp_min(1e-4)) % 6, torch.where(mx == g, (b - r) / c.clamp_min(1e-4) + 2, (r - g) / c.clamp_min(1e-4) + 4)) * 60
        sm = lambda x, a, b_: ((x - a) / (b_ - a)).clamp(0, 1)
        m = sm(hue, 8, 16) * (1 - sm(hue, 42, 55)) * sm(s, 0.18, 0.30) * sm(v, 0.12, 0.22) * (1 - sm(v, 0.92, 0.99))
        return blur(m, 2).clamp(0, 1)
    def orient(lg, sig):  # weight of horizontal grain (1 = planks run horizontally)
        gx = lg.roll(-1, 1) - lg.roll(1, 1); gy = lg.roll(-1, 0) - lg.roll(1, 0)
        jxx, jyy = blur(gx * gx, sig), blur(gy * gy, sig)
        return (0.5 + 0.5 * (jyy - jxx) / (jxx + jyy + 1e-6)).clamp(0, 1)

    ph = torch.from_numpy(np.asarray(Image.open(os.path.join(cfg['photos'], 'rough_wood_diff_2k.jpg')).convert('RGB')).astype(np.float32) / 255).permute(2, 0, 1).to(dev)
    pn = torch.from_numpy(np.asarray(Image.open(os.path.join(cfg['photos'], 'rough_wood_nor_gl_2k.jpg')).convert('RGB')).astype(np.float32) / 255).permute(2, 0, 1).to(dev)
    pl = torch.log(lum(lin(ph)) + 1e-3); hp = pl - blur(pl, 2048 / SIG_DIV)
    gx = hp.roll(-1, 1) - hp.roll(1, 1); gy = hp.roll(-1, 0) - hp.roll(1, 0)
    photo_vertical = (gx * gx).mean() > (gy * gy).mean()  # gradients along x -> streaks run vertically
    def oriented(t, vec=False):  # -> (horizontal-grain copy, vertical-grain copy)
        r = torch.rot90(t, 1, dims=(-2, -1))
        if vec: v = r * 2 - 1; r = (torch.stack([-v[1], v[0], v[2]]) + 1) / 2  # rotate tangent vectors with the image (CCW)
        return (r, t) if photo_vertical else (t, r)
    hpH, hpV = oriented(hp[None]); hpH, hpV = hpH[0] / hpH.std(), hpV[0] / hpV.std()
    pnH, pnV = oriented(pn, vec=True)
    masks = {}; log = []
    for s, u in info.items():
        if u['kind'] != 'col': continue
        t0 = time.time()
        base = W(cfg, 'up', s + '.png')
        x2 = load(base) if os.path.exists(base) else None
        if x2 is None:
            o = load(W(cfg, 'sp_png', s + '.png')); x2 = torch.cat([up2(o[:3]), F.interpolate(o[3:][None], scale_factor=2, mode='bicubic')[0].clamp(0, 1)])
        x4 = torch.cat([up2(x2[:3]), F.interpolate(x2[3:][None], scale_factor=2, mode='bicubic')[0].clamp(0, 1)])
        H, Wd = x4.shape[1:]; l = lin(x4[:3]); lg = torch.log(lum(l) + 1e-3)
        m = wood_mask(x4[:3]); wh = orient(lg, max(H, Wd) / 128)
        g = wh * tile_to(hpH[None], H, Wd)[0] + (1 - wh) * tile_to(hpV[None], H, Wd)[0]
        e = torch.exp(K_COL * g.clamp(-3, 3) * m); e = e / blur(e, max(H, Wd) / SIG_DIV)
        e = 1 + (e - 1) * m  # outside wood: untouched
        save(torch.cat([srgb(l * e), x4[3:]]), W(cfg, 'sp_out', s + '.png'))
        masks[s] = (m, wh); log.append(f'{u["name"]} {u["w"]}x{u["h"]} -> {Wd}x{H} wood={m.mean().item():.2f} {time.time() - t0:.1f}s')
        print(log[-1], flush=True)
    for s, c in pairs.items():
        if c not in masks: continue
        u = info[s]; o = load(W(cfg, 'sp_png', s + '.png')); H, Wd = u['h'] * 4, u['w'] * 4
        n = F.interpolate(o[None, :3] * 2 - 1, size=(H, Wd), mode='bicubic', align_corners=False)[0]; n = n / n.norm(dim=0, keepdim=True).clamp_min(1e-6)
        m, wh = (F.interpolate(t[None, None], size=(H, Wd), mode='area')[0, 0] for t in masks[c])
        d = wh * tile_to(pnH, H, Wd)[:2] + (1 - wh) * tile_to(pnV, H, Wd)[:2]; d = d * 2 - 1
        d = (d - blur(d, max(H, Wd) / SIG_DIV)) * K_NRM * m
        dz = (1 - (d ** 2).sum(0)).clamp_min(0.05).sqrt()
        v = torch.stack([n[0] + d[0], n[1] + d[1], n[2] * dz]); v[2] = v[2].clamp_min(0.05); v = v / v.norm(dim=0, keepdim=True)
        al = F.interpolate(o[None, 3:], size=(H, Wd), mode='bicubic')[0].clamp(0, 1)
        save(torch.cat([(v + 1) / 2, al]), W(cfg, 'sp_out', s + '.png')); print(f'normal {u["name"]} -> {Wd}x{H}', flush=True)
    json.dump(dict(info=info, files=six), open(W(cfg, 'special_targets.json'), 'w'))

def preview(cfg):
    from PIL import Image
    t = json.load(open(W(cfg, 'special_targets.json'))); tiles = []
    for s, u in t['info'].items():
        if u['kind'] != 'col' or not os.path.exists(W(cfg, 'sp_out', s + '.png')) or u['name'] not in ('basha_d.png', 'basha_001.png'): continue
        b = W(cfg, 'up', s + '.png'); A = Image.open(b).convert('RGB'); B = Image.open(W(cfg, 'sp_out', s + '.png')).convert('RGB')
        A = A.resize(B.size, Image.BICUBIC); w, h = B.size; x0, y0 = int(w * 0.25), int(h * 0.62); cs = 420
        tiles.append((A.crop((x0, y0, x0 + cs, y0 + cs)), B.crop((x0, y0, x0 + cs, y0 + cs))))
    sheet = Image.new('RGB', (850, 430 * len(tiles)), 'white')
    for i, (a, b) in enumerate(tiles): sheet.paste(a, (0, i * 430)); sheet.paste(b, (430, i * 430))
    sheet.save(W(cfg, 'carriage_preview.png')); print('preview', len(tiles))

def encode(cfg):
    t = json.load(open(W(cfg, 'special_targets.json'))); groups = collections.defaultdict(list)
    for s, u in t['info'].items():
        if os.path.exists(W(cfg, 'sp_out', s + '.png')): groups[u['mips'] + 2].append(W(cfg, 'sp_out', s + '.png'))
    for m, lst in groups.items(): texconv_batch(cfg, lst, W(cfg, 'special'), ['-f', 'BC7_UNORM', '-m', str(m)])
    six = {f: [[j, s] for j, s in v if os.path.exists(W(cfg, 'special', s + '.dds'))] for f, v in t['files'].items()}
    json.dump({f: v for f, v in six.items() if v}, open(W(cfg, 'special_index.json'), 'w'))
    print('special index:', {f: len(v) for f, v in six.items()})

if __name__ == '__main__':
    cfg = json.load(open(sys.argv[1])); ph = sys.argv[2] if len(sys.argv) > 2 else 'all'
    for name, fn in (('build', build), ('preview', preview), ('encode', encode)):
        if ph in (name, 'all'): print('==', name, flush=True); fn(cfg)
