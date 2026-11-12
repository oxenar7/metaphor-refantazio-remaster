"""Close-up props (cutscenes): one small atlas covers a whole object, so even the 2x upscale is soft on screen.
Same method as carriage.py: 4x (second AI 2x pass on top of the shipped 2x) + photo wood grain that follows each plank,
only where the atlas is wood (colour mask minus per-target excluded rectangles: food, canvas...). Normals: 4x + wood relief.

usage: python -I props.py <upall_cfg.json> build|preview|encode|files
Writes work/props/<sha>.png, then work/special/<sha>.dds, and MERGES the slots into work/special_index.json
(backup special_index.json.bak_pre_props). `files` prints the TEX files to repack (for upscale_all.py pack --only).
"""
import sys, os, re, json, math, hashlib, time, site, collections, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from upscale_all import read_tex, ename, dds_info, load_toc, scope_files, texconv_batch, W

# name regex (colour), normal is the same stem with _normal; exclude = rects (x0,y0,x1,y1 in 0..1) never treated as wood
SPECS = [
    dict(col=r'^bg81_meatshop_01a_m1_atlas_etc(_1024)?_basecolor\.tga$', exclude=[(0.585, 0.0, 1.0, 0.5), (0.0, 0.605, 0.30, 1.0)], k=0.18,
         food=[(0.585, 0.0, 1.0, 0.5)]),
    dict(col=r'^bg82_unglazedPot_01_a_m1_0128_basecolor\.tga$', exclude=[(0, 0, 1, 1)], k=0.0),
    dict(col=r'^bg81_shopAwning_02_a_m1_0512_basecolor\.tga$', exclude=[(0, 0, 1, 1)], k=0.0),  # canvas: 4x only, no wood grain
]
K_NRM, SIG_DIV = 0.9, 48
FOOD_MODEL = '4x-UltraSharpV2_Lite.safetensors'
# wood: keep the original's low frequencies (colour, stains) + a fraction `keep` of its own detail (the blocky painting),
# replace the rest with oriented photo grain of strength k_rebuild.  keep=1, k_rebuild=k -> plain grain on top (first try)
P = dict(lp=2.0, keep=0.5, k_rebuild=0.26)
P.update(json.loads(os.environ.get('PROPS_P', '{}')))


def targets(cfg):
    toc = load_toc(cfg['toc']); cpk = open(cfg['cpk'], 'rb'); info = {}; six = collections.defaultdict(list); pairs = {}
    os.makedirs(W(cfg, 'props', 'src'), exist_ok=True)
    rx = [re.compile(s['col']) for s in SPECS]
    for f in scope_files(toc):
        if '/field/' not in f: continue
        size, off = toc[f]; cpk.seek(off); blob = cpk.read(size)
        if not any(k in blob for k in (b'meatshop_01a', b'unglazedPot_01_a', b'shopAwning_02_a')): continue
        ents = read_tex(blob); names = {ename(r): j for j, (r, d) in enumerate(ents)}
        for j, (r, d) in enumerate(ents):
            n = ename(r)
            for si, x in enumerate(rx):
                if not x.match(n): continue
                nn = n.replace('_basecolor.tga', '_normal.tga')
                for kind, jj in (('col', j), ('nrm', names.get(nn))):
                    if jj is None: continue
                    dd = ents[jj][1]; inf = dds_info(dd)
                    if not inf or inf['fmt'] != 98: continue
                    sha = hashlib.sha1(dd).hexdigest()[:12]
                    info.setdefault(sha, dict(name=ename(ents[jj][0]), w=inf['w'], h=inf['h'], mips=inf['mips'], kind=kind, spec=si))
                    p = W(cfg, 'props', 'src', sha + '.dds')
                    if not os.path.exists(p): open(p, 'wb').write(dd)
                    if [jj, sha] not in six[f]: six[f].append([jj, sha])
                    if kind == 'nrm': pairs[sha] = hashlib.sha1(ents[j][1]).hexdigest()[:12]
    return info, dict(six), pairs


def build(cfg):
    sys.path.insert(0, site.getusersitepackages())
    import numpy as np, torch, torch.nn.functional as F
    from PIL import Image
    from spandrel import ModelLoader
    dev = 'cuda'
    info, six, pairs = targets(cfg)
    for s, v in info.items(): print(' ', s, v['name'], v['w'], v['h'], v['kind'], 'files', sum(1 for f in six if any(x[1] == s for x in six[f])))
    png = W(cfg, 'props', 'png'); out = W(cfg, 'props', 'out'); os.makedirs(png, exist_ok=True); os.makedirs(out, exist_ok=True)
    texconv_batch(cfg, [W(cfg, 'props', 'src', s + '.dds') for s in info], png, ['-ft', 'png', '-f', 'R8G8B8A8_UNORM', '-m', '1'])
    model = ModelLoader().load_from_file(cfg['model']).eval().to(dev); model.model.half()
    fmodel = ModelLoader().load_from_file(os.path.join(os.path.dirname(cfg['model']), FOOD_MODEL)).eval().to(dev); fmodel.model.half()
    def up4f(x):  # sharper 4x model + back-projection
        Pd = 8; xp = F.pad(x[None], (Pd, Pd, Pd, Pd), mode='replicate')
        with torch.inference_mode():
            sr = fmodel(xp.half()).float().clamp(0, 1)[:, :, 4 * Pd:-4 * Pd, 4 * Pd:-4 * Pd]
            for _ in range(3):
                sr = (sr + F.interpolate(x[None] - F.avg_pool2d(sr, 4), scale_factor=4, mode='bicubic', align_corners=False)).clamp(0, 1)
        return sr[0]

    def load(p): return torch.from_numpy(np.asarray(Image.open(p).convert('RGBA')).astype(np.float32) / 255).permute(2, 0, 1).to(dev)
    def save(t, p): Image.fromarray((t.clamp(0, 1).permute(1, 2, 0).cpu().numpy() * 255).round().astype(np.uint8), 'RGBA').save(p)
    def blur(x, s):
        H, Wd = x.shape[-2:]
        fy = torch.fft.fftfreq(H, device=dev)[:, None]; fx = torch.fft.fftfreq(Wd, device=dev)[None, :]
        return torch.fft.ifft2(torch.fft.fft2(x) * torch.exp(-2 * (math.pi * s) ** 2 * (fx ** 2 + fy ** 2))).real
    def up2(x):
        P = 8; xp = F.pad(x[None], (P, P, P, P), mode='replicate')
        with torch.inference_mode():
            sr = model(xp.half()).float().clamp(0, 1)[:, :, 2 * P:-2 * P, 2 * P:-2 * P]
            for _ in range(3):
                sr = (sr + F.interpolate(x[None] - F.avg_pool2d(sr, 2), scale_factor=2, mode='bicubic', align_corners=False)).clamp(0, 1)
        return sr[0]
    def upA(a): return F.interpolate(a[None], scale_factor=2, mode='bicubic', align_corners=False)[0].clamp(0, 1)
    def lin(s): return torch.where(s <= 0.04045, s / 12.92, ((s + 0.055) / 1.055) ** 2.4)
    def srgb(l): l = l.clamp(0, 1); return torch.where(l <= 0.0031308, l * 12.92, 1.055 * l ** (1 / 2.4) - 0.055)
    def lum(l): return 0.2126 * l[0] + 0.7152 * l[1] + 0.0722 * l[2]
    def tile_to(t, H, Wd):
        ry, rx = math.ceil(H / t.shape[1]), math.ceil(Wd / t.shape[2]); return t.repeat(1, ry, rx)[:, :H, :Wd]
    def wood_mask(rgb):
        mx, _ = rgb.max(0); mn, _ = rgb.min(0); c = mx - mn; v = mx; s = c / mx.clamp_min(1e-4)
        r, g, b = rgb
        hue = torch.where(mx == r, ((g - b) / c.clamp_min(1e-4)) % 6, torch.where(mx == g, (b - r) / c.clamp_min(1e-4) + 2, (r - g) / c.clamp_min(1e-4) + 4)) * 60
        sm = lambda x, a, b_: ((x - a) / (b_ - a)).clamp(0, 1)
        m = sm(hue, 8, 16) * (1 - sm(hue, 42, 55)) * sm(s, 0.12, 0.24) * sm(v, 0.08, 0.18) * (1 - sm(v, 0.92, 0.99))
        return blur(m, 2).clamp(0, 1)
    def orient(lg, sig):
        gx = lg.roll(-1, 1) - lg.roll(1, 1); gy = lg.roll(-1, 0) - lg.roll(1, 0)
        jxx, jyy = blur(gx * gx, sig), blur(gy * gy, sig)
        return (0.5 + 0.5 * (jyy - jxx) / (jxx + jyy + 1e-6)).clamp(0, 1)
    def rect_mask(H, Wd, rects):
        m = torch.ones(H, Wd, device=dev)
        for x0, y0, x1, y1 in rects: m[int(y0 * H):int(y1 * H), int(x0 * Wd):int(x1 * Wd)] = 0
        return blur(m, 3).clamp(0, 1)

    ph = torch.from_numpy(np.asarray(Image.open(os.path.join(cfg['photos'], 'rough_wood_diff_2k.jpg')).convert('RGB')).astype(np.float32) / 255).permute(2, 0, 1).to(dev)
    pn = torch.from_numpy(np.asarray(Image.open(os.path.join(cfg['photos'], 'rough_wood_nor_gl_2k.jpg')).convert('RGB')).astype(np.float32) / 255).permute(2, 0, 1).to(dev)
    pl = torch.log(lum(lin(ph)) + 1e-3); hp = pl - blur(pl, 2048 / SIG_DIV)
    gx = hp.roll(-1, 1) - hp.roll(1, 1); gy = hp.roll(-1, 0) - hp.roll(1, 0)
    photo_vertical = (gx * gx).mean() > (gy * gy).mean()
    def oriented(t, vec=False):
        r = torch.rot90(t, 1, dims=(-2, -1))
        if vec: v = r * 2 - 1; r = (torch.stack([-v[1], v[0], v[2]]) + 1) / 2
        return (r, t) if photo_vertical else (t, r)
    hpH, hpV = oriented(hp[None]); hpH, hpV = hpH[0] / hpH.std(), hpV[0] / hpV.std()
    pnH, pnV = oriented(pn, vec=True)
    masks = {}
    for s, u in info.items():
        if u['kind'] != 'col': continue
        sp = SPECS[u['spec']]; t0 = time.time()
        base = W(cfg, 'up', s + '.png')                       # the shipped 2x (before grain), if any
        o = load(os.path.join(png, s + '.png'))
        x2 = load(base) if os.path.exists(base) else torch.cat([up2(o[:3]), upA(o[3:])])
        if x2.shape[1] != u['h'] * 2: x2 = torch.cat([up2(o[:3]), upA(o[3:])])
        x4 = torch.cat([up2(x2[:3]), upA(x2[3:])])
        H, Wd = x4.shape[1:]
        if sp.get('food'):
            fm = 1 - rect_mask(H, Wd, sp['food'])
            x4[:3] = x4[:3] * (1 - fm) + up4f(o[:3]) * fm
        l = lin(x4[:3]); lg = torch.log(lum(l) + 1e-3)
        m = wood_mask(x4[:3]) * rect_mask(H, Wd, sp['exclude']); wh = orient(lg, max(H, Wd) / 128)
        if sp['k'] > 0:
            g = wh * tile_to(hpH[None], H, Wd)[0] + (1 - wh) * tile_to(hpV[None], H, Wd)[0]
            e = torch.exp(sp.get('k_rebuild', P['k_rebuild']) * g.clamp(-3, 3) * m); e = e / blur(e, max(H, Wd) / SIG_DIV); e = 1 + (e - 1) * m
            # rebuild: low-pass of the painting (sigma lp source px) times a fraction of its own detail
            sig = sp.get('lp', P['lp']) * H / u['h']; keep = sp.get('keep', P['keep'])
            lpw = torch.exp(blur(lg, sig)); det = (lum(l) + 1e-3) / lpw
            e = e * torch.where(m > 0, det.clamp(1e-3) ** ((keep - 1) * m), torch.ones_like(m))
        else:
            e = torch.ones_like(m)
        save(torch.cat([srgb(l * e), x4[3:]]), os.path.join(out, s + '.png'))
        Image.fromarray((m.cpu().numpy() * 255).astype(np.uint8)).save(os.path.join(out, s + '_woodmask.png'))
        masks[s] = (m, wh, sp['k'] > 0)
        print(f"col {u['name']} {u['w']}x{u['h']} -> {Wd}x{H} wood={m.mean().item():.2f} {time.time() - t0:.1f}s", flush=True)
    for s, c in pairs.items():
        if c not in masks: continue
        u = info[s]; o = load(os.path.join(png, s + '.png')); H, Wd = u['h'] * 4, u['w'] * 4
        n = F.interpolate(o[None, :3] * 2 - 1, size=(H, Wd), mode='bicubic', align_corners=False)[0]; n = n / n.norm(dim=0, keepdim=True).clamp_min(1e-6)
        m, wh, grain = masks[c]
        m, wh = (F.interpolate(t[None, None], size=(H, Wd), mode='area')[0, 0] for t in (m, wh))
        if grain:
            d = wh * tile_to(pnH, H, Wd)[:2] + (1 - wh) * tile_to(pnV, H, Wd)[:2]; d = d * 2 - 1
            d = (d - blur(d, max(H, Wd) / SIG_DIV)) * K_NRM * m
        else:
            d = torch.zeros(2, H, Wd, device=dev)
        dz = (1 - (d ** 2).sum(0)).clamp_min(0.05).sqrt()
        v = torch.stack([n[0] + d[0], n[1] + d[1], n[2] * dz]); v[2] = v[2].clamp_min(0.05); v = v / v.norm(dim=0, keepdim=True)
        al = F.interpolate(o[None, 3:], size=(H, Wd), mode='bicubic')[0].clamp(0, 1)
        save(torch.cat([(v + 1) / 2, al]), os.path.join(out, s + '.png')); print(f"nrm {u['name']} -> {Wd}x{H}", flush=True)
    json.dump(dict(info=info, files=six, pairs=pairs), open(W(cfg, 'props', 'targets.json'), 'w'))


def preview(cfg):
    sys.path.insert(0, site.getusersitepackages())
    from PIL import Image, ImageDraw
    t = json.load(open(W(cfg, 'props', 'targets.json'))); out = W(cfg, 'props', 'out'); png = W(cfg, 'props', 'png')
    crops = []
    for s, u in t['info'].items():
        if u['kind'] != 'col' or not os.path.exists(os.path.join(out, s + '.png')): continue
        A = Image.open(W(cfg, 'up', s + '.png')).convert('RGB') if os.path.exists(W(cfg, 'up', s + '.png')) else Image.open(os.path.join(png, s + '.png')).convert('RGB')
        B = Image.open(os.path.join(out, s + '.png')).convert('RGB'); A = A.resize(B.size, Image.BICUBIC); w, h = B.size
        boxes = {'meatshop': [(0.62, 0.30, 0.18), (0.20, 0.25, 0.14), (0.42, 0.62, 0.14)], 'unglazedPot': [(0.0, 0.25, 0.6)], 'shopAwning': [(0.05, 0.35, 0.3)]}
        for k, bl in boxes.items():
            if k not in u['name']: continue
            for x0, y0, sz in bl:
                c = (int(x0 * w), int(y0 * h), int(x0 * w) + int(sz * w), int(y0 * h) + int(sz * w))
                crops.append((u['name'][:34], A.crop(c).resize((480, 480), Image.LANCZOS), B.crop(c).resize((480, 480), Image.LANCZOS)))
    sheet = Image.new('RGB', (980, 510 * len(crops) + 30), (16, 16, 16)); d = ImageDraw.Draw(sheet)
    d.text((8, 6), 'PRIMA (mod attuale, 2x)', fill=(255, 210, 140), font_size=20); d.text((498, 6), 'DOPO (4x + venatura legno)', fill=(150, 230, 160), font_size=20)
    for i, (n, a, b) in enumerate(crops):
        y = 30 + i * 510; d.text((8, y + 2), n, fill=(220, 220, 220), font_size=16); sheet.paste(a, (0, y + 24)); sheet.paste(b, (500, y + 24))
    p = W(cfg, 'props', 'props_preview.png'); sheet.save(p); print(p, sheet.size)


def encode(cfg):
    t = json.load(open(W(cfg, 'props', 'targets.json'))); out = W(cfg, 'props', 'out'); groups = collections.defaultdict(list)
    for s, u in t['info'].items():
        if os.path.exists(os.path.join(out, s + '.png')): groups[u['mips'] + 2].append(os.path.join(out, s + '.png'))
    for m, lst in groups.items(): texconv_batch(cfg, lst, W(cfg, 'special'), ['-f', 'BC7_UNORM', '-m', str(m)] + cfg.get('bc7_args', []))
    six_p = W(cfg, 'special_index.json'); six = json.load(open(six_p)) if os.path.exists(six_p) else {}
    if not os.path.exists(six_p + '.bak_pre_props'): shutil.copy(six_p, six_p + '.bak_pre_props')
    n = 0
    for f, lst in t['files'].items():
        cur = six.setdefault(f, [])
        for j, s in lst:
            if not os.path.exists(W(cfg, 'special', s + '.dds')): continue
            cur[:] = [x for x in cur if x[0] != j] + [[j, s]]; n += 1
    json.dump(six, open(six_p, 'w')); print('special slots merged:', n, 'files:', len(t['files']))


def files(cfg):
    t = json.load(open(W(cfg, 'props', 'targets.json'))); print('\n'.join(sorted(t['files'])))


if __name__ == '__main__':
    cfg = json.load(open(sys.argv[1])); dict(build=build, preview=preview, encode=encode, files=files)[sys.argv[2]](cfg)
