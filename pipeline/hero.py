"""Hero props seen huge on screen (Eht Ria well, Eht Ria church door): one small atlas (256) covers a big object.

usage: python -I hero.py <upall_cfg.json> <out dir> build|sheet|encode [name ...]
Needs each target's original normal map decoded to PNG in <out dir> (nrm_png below, e.g. texconv -ft png).

colour 8x: 4x-UltraSharpV2 Lite, then RCAN 2x (both back-projected).
  wood  (hue x orientation mask, like props_batch): variant B rebuild with oriented rough_wood grain, strength from the
        painting's own detail, gated where the paint is flat
  stone (stone_rect): crevice/occlusion shading from the height integrated out of the game's own normal
        (Frankot-Chellappa, the strip wraps around the well so it is periodic in x) + rock_face_03 photo detail
normal 8x: the game's normal is denoised (its 1-px noise is what turns into "hammered" blotches when magnified),
  deepened on stone, + photo relief (wood grain on wood, rock on stone).
"""
import sys, os, json, math, site, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, site.getusersitepackages())
from upscale_all import W

T = {
    'well': dict(col='02182dea0160', nrm_png='bg04_paganIsland_well_01_m1_normal__orig.png', mips=9,
                 stone_rect=(0.0, 0.535, 1.0, 1.0), ptile=1024, rtile=1024),
    'door': dict(col='56e1a581e815', nrm_png='bg82_paganIsland_churchDoor_01_m1_normal__orig.png', mips=5,
                 stone_rect=None, ptile=1024, rtile=1024),
}
P = dict(lp=2.0, keep=0.5, k_rebuild=0.26, k_per_sd=0.72, k_min=0.08, sig_div=48, k_nrm=0.9,
         nrm_denoise=1.0,            # source px: gaussian on the game's normal before upscaling
         st_a1=0.32, st_a2=0.20, st_hl=0.05, st_ns=1.35, st_k=0.10, st_nk=0.6)
SC = 8


def build(cfg, out, names):
    import numpy as np, torch, torch.nn.functional as F
    from PIL import Image
    from spandrel import ModelLoader
    dev = 'cuda'
    m2 = ModelLoader().load_from_file(cfg['model']).eval().to(dev); m2.model.half()
    m4 = ModelLoader().load_from_file(os.path.join(os.path.dirname(cfg['model']), '4x-UltraSharpV2_Lite.safetensors')).eval().to(dev); m4.model.half()

    def run(model, x, s):
        Pd = 8; xp = F.pad(x[None], (Pd, Pd, Pd, Pd), mode='replicate')
        with torch.inference_mode():
            sr = model(xp.half()).float().clamp(0, 1)[:, :, s * Pd:-s * Pd, s * Pd:-s * Pd]
            for _ in range(3): sr = (sr + F.interpolate(x[None] - F.avg_pool2d(sr, s), scale_factor=s, mode='bicubic', align_corners=False)).clamp(0, 1)
        return sr[0]
    def T_(a): return torch.from_numpy(np.ascontiguousarray(a)).float().to(dev)
    def load(p): return T_(np.asarray(Image.open(p).convert('RGBA')).astype(np.float32) / 255).permute(2, 0, 1)
    def save(x, p): Image.fromarray((x.clamp(0, 1).permute(1, 2, 0).cpu().numpy() * 255).round().astype(np.uint8), 'RGBA').save(p)
    def blur(x, s):
        H, Wd = x.shape[-2:]
        fy = torch.fft.fftfreq(H, device=dev)[:, None]; fx = torch.fft.fftfreq(Wd, device=dev)[None, :]
        return torch.fft.ifft2(torch.fft.fft2(x) * torch.exp(-2 * (math.pi * s) ** 2 * (fx ** 2 + fy ** 2))).real
    def lin(s): return torch.where(s <= 0.04045, s / 12.92, ((s + 0.055) / 1.055) ** 2.4)
    def srgb(l): l = l.clamp(0, 1); return torch.where(l <= 0.0031308, l * 12.92, 1.055 * l ** (1 / 2.4) - 0.055)
    def lum(l): return 0.2126 * l[0] + 0.7152 * l[1] + 0.0722 * l[2]
    def tile_to(x, H, Wd):
        ry, rx = math.ceil(H / x.shape[-2]), math.ceil(Wd / x.shape[-1]); return x.repeat(1, ry, rx)[:, :H, :Wd]
    sm = lambda x, a, b_: ((x - a) / (b_ - a)).clamp(0, 1)
    def hue_mask(rgb):
        mx, _ = rgb.max(0); mn, _ = rgb.min(0); c = mx - mn; v = mx; s = c / mx.clamp_min(1e-4); r, g, b = rgb
        hue = torch.where(mx == r, ((g - b) / c.clamp_min(1e-4)) % 6, torch.where(mx == g, (b - r) / c.clamp_min(1e-4) + 2, (r - g) / c.clamp_min(1e-4) + 4)) * 60
        return sm(hue, 8, 16) * (1 - sm(hue, 42, 55)) * sm(s, 0.12, 0.24) * (1 - sm(s, 0.62, 0.78)) * sm(v, 0.08, 0.18) * (1 - sm(v, 0.92, 0.99))
    def tensor(lg, sig):
        gx = lg.roll(-1, 1) - lg.roll(1, 1); gy = lg.roll(-1, 0) - lg.roll(1, 0)
        return blur(gx * gx, sig), blur(gy * gy, sig), blur(gx * gy, sig)
    def rect(H, Wd, r, soft):
        m = torch.zeros(H, Wd, device=dev)
        if r: x0, y0, x1, y1 = r; m[int(y0 * H):int(y1 * H), int(x0 * Wd):int(x1 * Wd)] = 1
        return blur(m, soft).clamp(0, 1)
    def photo(pid, kind, size):
        im = Image.open(os.path.join(cfg['photos'], f'{pid}_{kind}_2k.jpg')).convert('RGB').resize((size, size), Image.LANCZOS)
        return T_(np.asarray(im).astype(np.float32) / 255).permute(2, 0, 1)

    os.makedirs(out, exist_ok=True)
    for name in names:
        t = T[name]
        o = load(W(cfg, 'png', t['col'] + '.png')); h, w = o.shape[1:]
        nrm0 = load(os.path.join(out, t['nrm_png']))
        x4 = run(m4, o[:3], 4); col = run(m2, x4, 2)                                   # 8x colour
        al = F.interpolate(o[None, 3:], scale_factor=SC, mode='bicubic', align_corners=False)[0].clamp(0, 1)
        H, Wd = col.shape[1:]
        st = rect(H, Wd, t['stone_rect'], 2 * SC)
        l = lin(col); lg = torch.log(lum(l) + 1e-3)
        # ---- wood ----
        jxx, jyy, jxy = tensor(lg, 6 * SC)
        coh = ((jxx - jyy) ** 2 + 4 * jxy ** 2).sqrt() / (jxx + jyy + 1e-6)
        m = (blur(hue_mask(col), SC) * blur(sm(coh, 0.06, 0.20), 4 * SC)).clamp(0, 1) * (1 - st)
        lpw = torch.exp(blur(lg, P['lp'] * SC)); det = (lum(l) + 1e-3) / lpw
        ld2 = det.clamp(1e-3).log() ** 2
        sd = float(((m * ld2).sum() / m.sum().clamp_min(1)).sqrt())
        k = min(P['k_rebuild'], max(P['k_min'], P['k_per_sd'] * sd))
        ld = (blur(m * ld2, 4 * SC) / blur(m, 4 * SC).clamp_min(1e-3)).clamp_min(0).sqrt()
        m = m * ((ld - 0.06) / 0.12).clamp(0, 1)
        jxx, jyy, _ = tensor(lg, max(H, Wd) / 128); wh = (0.5 + 0.5 * (jyy - jxx) / (jxx + jyy + 1e-6)).clamp(0, 1)
        ph = photo('rough_wood', 'diff', t['ptile']); pn = photo('rough_wood', 'nor_gl', t['ptile'])
        pl = torch.log(lum(lin(ph)) + 1e-3); hp = pl - blur(pl, t['ptile'] / P['sig_div'])
        gx_ = hp.roll(-1, 1) - hp.roll(1, 1); gy_ = hp.roll(-1, 0) - hp.roll(1, 0); vert = (gx_ * gx_).mean() > (gy_ * gy_).mean()
        rot = lambda x: torch.rot90(x, 1, dims=(-2, -1))
        hpH, hpV = (rot(hp), hp) if vert else (hp, rot(hp)); hpH, hpV = hpH / hpH.std(), hpV / hpV.std()
        rv = rot(pn) * 2 - 1; pnr = (torch.stack([-rv[1], rv[0], rv[2]]) + 1) / 2
        pnH, pnV = (pnr, pn) if vert else (pn, pnr)
        g = wh * tile_to(hpH[None], H, Wd)[0] + (1 - wh) * tile_to(hpV[None], H, Wd)[0]
        e = torch.exp(k * g.clamp(-3, 3) * m); e = e / blur(e, max(H, Wd) / P['sig_div']); e = 1 + (e - 1) * m
        e = e * torch.where(m > 0, det.clamp(1e-3) ** ((P['keep'] - 1) * m), torch.ones_like(m))
        l = l * e
        # ---- stone ----
        n_den = blur(nrm0[:3] * 2 - 1, P['nrm_denoise'])                                  # source res, denoised
        nrmU = F.interpolate(n_den[None], size=(H, Wd), mode='bicubic', align_corners=False)[0]
        rd = torch.zeros(2, H, Wd, device=dev)
        if t['stone_rect']:
            x0, y0, x1, y1 = t['stone_rect']; r0, r1 = int(y0 * h), int(y1 * h)
            band = n_den[:, r0:r1].cpu().numpy()
            band = np.concatenate([band, band[:, ::-1]], 1)                               # mirror in y -> periodic
            nx, ny = band[0], band[1]; nz = np.clip(band[2], 0.25, 1)
            gxh, gyh = -nx / nz, -ny / nz; hh, ww = gxh.shape
            WX, WY = np.meshgrid(np.fft.fftfreq(ww) * 2 * np.pi, np.fft.fftfreq(hh) * 2 * np.pi); den = WX ** 2 + WY ** 2; den[0, 0] = 1
            Z = (-1j * WX * np.fft.fft2(gxh) - 1j * WY * np.fft.fft2(gyh)) / den; Z[0, 0] = 0
            hgt = np.real(np.fft.ifft2(Z))[:r1 - r0]; hgt = (hgt - hgt.mean()) / max(hgt.std(), 1e-6)
            Hs = torch.zeros(h, w, device=dev); Hs[r0:r1] = T_(hgt)
            Hb = F.interpolate(Hs[None, None], size=(H, Wd), mode='bicubic', align_corners=False)[0, 0]
            d1 = blur(Hb, Wd / 96) - Hb; d2 = blur(Hb, Wd / 20) - Hb
            q = lambda d: torch.quantile((d * st).abs().flatten()[::7], 0.995).clamp_min(1e-6)
            c1 = (d1 / q(d1)).clamp(0, 1) ** 0.9; c2 = (d2 / q(d2)).clamp(0, 1); hl = (-d1 / q(d1)).clamp(0, 1)
            shade = (1 - P['st_a1'] * c1 - P['st_a2'] * c2 + P['st_hl'] * hl).clamp(0.45, 1.08)
            rp = photo('rock_face_03', 'diff', t['rtile']); rn = photo('rock_face_03', 'nor_gl', t['rtile'])
            rl = torch.log(lum(lin(rp)) + 1e-3); rh = rl - blur(rl, t['rtile'] / 32); rh = rh / rh.std()
            re_ = torch.exp(P['st_k'] * tile_to(rh[None], H, Wd)[0].clamp(-3, 3))
            L0 = (lum(l) * st).sum() / st.sum()
            l = l * (1 + (shade * re_ - 1) * st)[None]
            L1 = (lum(l) * st).sum() / st.sum(); l = l * (1 + ((L0 / L1) ** 0.5 - 1) * st)[None]
            rnd = tile_to(rn, H, Wd)[:2] * 2 - 1; rnd = rnd - blur(rnd, t['rtile'] / 32)
            rd = rnd * P['st_nk'] * st
            nrmU[:2] = nrmU[:2] * (1 + (P['st_ns'] - 1) * st)
            Image.fromarray((shade.clamp(0, 1).cpu().numpy() * 255).astype(np.uint8)).save(os.path.join(out, name + '_shade.png'))
        # ---- normal ----
        wd = wh * tile_to(pnH, H, Wd)[:2] + (1 - wh) * tile_to(pnV, H, Wd)[:2]; wd = wd * 2 - 1
        wd = (wd - blur(wd, max(H, Wd) / P['sig_div'])) * P['k_nrm'] * (k / P['k_rebuild']) * m
        d = wd + rd
        n = nrmU / nrmU.norm(dim=0, keepdim=True).clamp_min(1e-6)
        dz = (1 - (d ** 2).sum(0)).clamp_min(0.05).sqrt()
        v = torch.stack([n[0] + d[0], n[1] + d[1], n[2] * dz]); v[2] = v[2].clamp_min(0.05); v = v / v.norm(dim=0, keepdim=True)
        na = F.interpolate(nrm0[None, 3:], size=(H, Wd), mode='bicubic', align_corners=False)[0].clamp(0, 1)
        save(torch.cat([srgb(l), al]), os.path.join(out, name + '_col.png'))
        save(torch.cat([(v + 1) / 2, na]), os.path.join(out, name + '_nrm.png'))
        Image.fromarray((m.cpu().numpy() * 255).astype(np.uint8)).save(os.path.join(out, name + '_woodmask.png'))
        print(f'{name}: {w}->{Wd}, wood {m.mean().item():.2f} (sd {sd:.3f}, k {k:.3f}), stone {st.mean().item():.2f}', flush=True)


def sheet(cfg, out, names):
    """before (current mod, colour + game normal lit) / after, on crops; a simple N.L render shows the relief."""
    import numpy as np
    from PIL import Image, ImageDraw
    crops = {'well': [(0.30, 0.56, 0.22), (0.05, 0.05, 0.25)], 'door': [(0.18, 0.30, 0.18), (0.70, 0.40, 0.18)]}
    rows = []
    for name in names:
        t = T[name]
        before = Image.open(W(cfg, 'up', t['col'] + '.png')).convert('RGB'); after = Image.open(os.path.join(out, name + '_col.png')).convert('RGB')
        nb = Image.open(os.path.join(out, t['nrm_png'])).convert('RGB'); na = Image.open(os.path.join(out, name + '_nrm.png')).convert('RGB')
        Wd, H = after.size; before = before.resize((Wd, H), Image.BICUBIC); nb = nb.resize((Wd, H), Image.BILINEAR)
        def lit(c, nm):
            n = np.asarray(nm).astype(np.float32) / 127.5 - 1; L = np.array([-0.45, 0.55, 0.70]); L /= np.linalg.norm(L)
            ndl = np.clip((n * L).sum(-1) / np.maximum(np.linalg.norm(n, axis=-1), 1e-6), 0, 1)
            sh = 0.35 + 0.85 * ndl
            return Image.fromarray(np.clip(np.asarray(c).astype(np.float32) * sh[..., None], 0, 255).astype(np.uint8))
        B, A = lit(before, nb), lit(after, na)
        for x0, y0, sz in crops[name]:
            c = (int(x0 * Wd), int(y0 * H), int(x0 * Wd) + int(sz * Wd), int(y0 * H) + int(sz * Wd))
            rows.append((name, B.crop(c).resize((520, 520), Image.LANCZOS), A.crop(c).resize((520, 520), Image.LANCZOS)))
    sh = Image.new('RGB', (1050, len(rows) * 546 + 30), (16, 16, 16)); d = ImageDraw.Draw(sh)
    d.text((6, 6), 'PRIMA (mod attuale, con luce)', fill=(255, 210, 140), font_size=20); d.text((536, 6), 'DOPO (8x, con luce)', fill=(150, 230, 160), font_size=20)
    for i, (n, a, b) in enumerate(rows):
        y = 30 + i * 546; d.text((6, y + 2), n, fill=(220, 220, 220), font_size=16); sh.paste(a, (0, y + 24)); sh.paste(b, (530, y + 24))
    p = os.path.join(out, 'hero_sheet.png'); sh.save(p); print(p, sh.size)


def encode(cfg, out, names):
    for name in names:
        for kind in ('col', 'nrm'):
            p = os.path.join(out, f'{name}_{kind}.png')
            subprocess.run([cfg['texconv'], '-nologo', '-y', '-f', 'BC7_UNORM', '-m', str(T[name]['mips'] + 3)] + cfg.get('bc7_args', []) + ['-o', out, p],
                           check=True, stdout=subprocess.DEVNULL)
            print(p[:-4] + '.dds', os.path.getsize(p[:-4] + '.dds'))


if __name__ == '__main__':
    cfg = json.load(open(sys.argv[1])); out = sys.argv[2]; ph = sys.argv[3]; names = sys.argv[4:] or list(T)
    dict(build=build, sheet=sheet, encode=encode)[ph](cfg, out, names)
