"""Build richer ground textures for one terrain TEX from CC0 photo materials.
usage: python -I build_ground.py <job.json>
job.json: {"orig_png": dir with NN__name.png (texconv output of the extracted TEX),
           "photos": dir, "out_png": dir, "size": 2048,
           "colour": [{"entry": "01__...png", "photo": "park_dirt", "mode": "stat"|"detail", "gain_l": 1.15, "detail": 0.9}],
           "normal": [{"entry": "02__...png", "photo": "park_dirt", "strength": 1.0}]}
mode stat   : photo recoloured so its Lab mean/std match the original texture; "own" (0..1) moves the palette toward the photo's own colours
mode detail : original kept as the low-frequency base, photo high-frequency detail added on top
Normals: game uses OpenGL-style tangent normals (verified on wood planks), so Poly Haven nor_gl is used as-is.
"""
import sys, json, os
import numpy as np
from PIL import Image

def srgb_to_lin(c): return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
def lin_to_srgb(c): return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.clip(c, 0, None) ** (1 / 2.4) - 0.055)
M = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]])
WP = np.array([0.95047, 1.0, 1.08883])
def rgb_to_lab(rgb):
    xyz = srgb_to_lin(rgb) @ M.T / WP
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)
def lab_to_rgb(lab):
    fy = (lab[..., 0] + 16) / 116; fx = fy + lab[..., 1] / 500; fz = fy - lab[..., 2] / 200
    f = np.stack([fx, fy, fz], -1)
    xyz = np.where(f ** 3 > 216 / 24389, f ** 3, (116 * f - 16) / (24389 / 27)) * WP
    return np.clip(lin_to_srgb(np.clip(xyz @ np.linalg.inv(M).T, 0, None)), 0, 1)

def blur(a, s):  # periodic gaussian blur (textures tile)
    H, W = a.shape[:2]; fx = np.fft.fftfreq(W)[None, :]; fy = np.fft.fftfreq(H)[:, None]
    k = np.exp(-2 * (np.pi * s) ** 2 * (fx ** 2 + fy ** 2))
    if a.ndim == 2: return np.real(np.fft.ifft2(np.fft.fft2(a) * k))
    return np.stack([np.real(np.fft.ifft2(np.fft.fft2(a[..., i]) * k)) for i in range(a.shape[2])], -1)

def resize_wrap(a, size):  # tile-aware resize: pad with wrapped content, resize, crop
    h, w = a.shape[:2]; p = max(4, h // 16)
    pad = np.pad(a, ((p, p), (p, p)) + ((0, 0),) * (a.ndim - 2), mode='wrap')
    s = size / h
    chans = [pad] if pad.ndim == 2 else [pad[..., i] for i in range(pad.shape[2])]
    out = [np.asarray(Image.fromarray(c.astype(np.float32), 'F').resize((round(pad.shape[1] * s), round(pad.shape[0] * s)), Image.BICUBIC)) for c in chans]
    out = out[0] if pad.ndim == 2 else np.stack(out, -1)
    q = round(p * s)
    return out[q:q + size, q:q + size]

def load(p, mode='RGBA'): return np.asarray(Image.open(p).convert(mode)).astype(np.float64) / 255

def main():
    job = json.load(open(sys.argv[1])); S = job['size']; os.makedirs(job['out_png'], exist_ok=True)
    report = []
    for c in job['colour']:
        o = load(os.path.join(job['orig_png'], c['entry']))
        p = load(os.path.join(job['photos'], c['photo'] + '_diff_2k.jpg'), 'RGB')
        if p.shape[0] != S: p = resize_wrap(p, S)
        lo, lp = rgb_to_lab(o[..., :3]), rgb_to_lab(p)
        mo, so = lo.reshape(-1, 3).mean(0), lo.reshape(-1, 3).std(0)
        mp, sp = lp.reshape(-1, 3).mean(0), lp.reshape(-1, 3).std(0)
        if c['mode'] == 'stat':
            gain = np.array([c.get('gain_l', 1.15), c.get('gain_ab', 1.0), c.get('gain_ab', 1.0)])
            own = c.get('own', 0.0)  # 0 = original palette, 1 = the photo's own colours
            tm, ts = (1 - own) * mo + own * mp, (1 - own) * so + own * sp
            new = (lp - mp) / np.maximum(sp, 1e-6) * ts * gain + tm
        else:  # detail transfer onto the upscaled original
            base = resize_wrap(lo, S)
            sig = S / 32
            hp = lp - blur(lp, sig)
            k = c.get('detail', 0.9)
            new = base + hp * np.array([k, k * 0.5, k * 0.5])
        rgb = lab_to_rgb(new)
        alpha = np.clip(resize_wrap(o[..., 3], S), 0, 1)
        out = np.concatenate([rgb, alpha[..., None]], -1)
        Image.fromarray((out * 255).round().astype(np.uint8), 'RGBA').save(os.path.join(job['out_png'], c['entry']))
        ln = rgb_to_lab(rgb).reshape(-1, 3)
        report.append({'entry': c['entry'], 'photo': c['photo'], 'mode': c['mode'], 'orig_Lab_mean': mo.round(1).tolist(), 'new_Lab_mean': ln.mean(0).round(1).tolist(),
                       'orig_L_std': round(float(so[0]), 1), 'new_L_std': round(float(ln[:, 0].std()), 1)})
    for n in job['normal']:
        p = load(os.path.join(job['photos'], n['photo'] + '_nor_gl_2k.jpg'), 'RGB')
        if p.shape[0] != S: p = resize_wrap(p, S)
        v = p * 2 - 1; st = n.get('strength', 1.0)
        v[..., :2] *= st; v[..., 2] = np.clip(v[..., 2], 0.05, 1)
        v /= np.linalg.norm(v, axis=-1, keepdims=True)
        out = np.concatenate([(v + 1) / 2, np.ones(v.shape[:2] + (1,))], -1)
        Image.fromarray((out * 255).round().astype(np.uint8), 'RGBA').save(os.path.join(job['out_png'], n['entry']))
        report.append({'entry': n['entry'], 'photo': n['photo'], 'normal_strength': st})
    for r in report: print(json.dumps(r))

if __name__ == '__main__':
    main()
