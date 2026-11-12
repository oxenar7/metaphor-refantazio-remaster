"""Replace textures by entry name in EVERY game TEX file that contains them, and write a CriFs mod folder.

usage: python -I remaster.py <recipe.json> [--only-build]

recipe.json:
{
  "cpk": "...base.cpk", "toc": "cpk_toc.tsv", "detail": "tex_detail.tsv", "texconv": "...texconv.exe",
  "photos": "dir with <id>_diff_2k.jpg / <id>_nor_gl_2k.jpg", "work": "work dir", "out": "mod CPK folder (gets COMMON/..., XBOX/...)",
  "scope": ["COMMON/model/field/object"],
  "prebuilt": {"<entry name>": {"dds": "path", "src_sha": "sha1 of the original DDS it was made from"}},
  "materials": {"<entry name>": {...recipe...}},
  "upscaled_map": "optional upscaled_map.json from upscale_all.py (sha1[:12] of original DDS -> upscaled DDS path)"
}
colour recipes:
  {"mode": "stat",   "photo": id, "own": 0..1, "gain_l": 1.0, "rot": 0|90, "scale": 4}
       photo replaces the texture; Lab mean/std move from the original (own=0) toward the photo's own (own=1)
  {"mode": "detail", "photo": id, "detail": 0.9, "own": 0..1, "rot": 0|90, "scale": 4}
       original kept as the base (layout, painted details); photo high frequencies added; own recolours the base
normal recipes:
  {"mode": "nreplace", "photo": id, "strength": 1.0, "rot": 0|90, "scale": 4}
  {"mode": "ncombine", "photo": id, "strength": 0.8, "rot": 0|90, "scale": 4}   original + photo detail (whiteout blend)
Output size = min(2048, original width * scale). Alpha comes from the original.
Same-name entries are built once per distinct original (sha1); "stat"/"nreplace" are built once per name.
Game normals are OpenGL-style (+Y up), so Poly Haven nor_gl is used as-is.
"""
import sys, os, json, struct, hashlib, subprocess, tempfile, shutil, collections
import numpy as np
from PIL import Image

# ---------- colour helpers ----------
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

def blur(a, s):  # periodic gaussian blur
    H, W = a.shape[:2]; fx = np.fft.fftfreq(W)[None, :]; fy = np.fft.fftfreq(H)[:, None]
    k = np.exp(-2 * (np.pi * s) ** 2 * (fx ** 2 + fy ** 2))
    if a.ndim == 2: return np.real(np.fft.ifft2(np.fft.fft2(a) * k))
    return np.stack([np.real(np.fft.ifft2(np.fft.fft2(a[..., i]) * k)) for i in range(a.shape[2])], -1)

def resize_wrap(a, w, h):  # tile-aware resize
    ih, iw = a.shape[:2]; p = max(4, min(ih, iw) // 16)
    pad = np.pad(a, ((p, p), (p, p)) + ((0, 0),) * (a.ndim - 2), mode='wrap')
    sx, sy = w / iw, h / ih
    chans = [pad] if pad.ndim == 2 else [pad[..., i] for i in range(pad.shape[2])]
    out = [np.asarray(Image.fromarray(c.astype(np.float32), 'F').resize((round(pad.shape[1] * sx), round(pad.shape[0] * sy)), Image.BICUBIC)) for c in chans]
    out = out[0] if pad.ndim == 2 else np.stack(out, -1)
    qx, qy = round(p * sx), round(p * sy)
    return out[qy:qy + h, qx:qx + w]

_photo_cache = {}
def photo(dirp, pid, kind, rot, w, h):
    key = (pid, kind, rot, w, h)
    if key in _photo_cache: return _photo_cache[key]
    a = np.asarray(Image.open(os.path.join(dirp, f'{pid}_{kind}_2k.jpg')).convert('RGB')).astype(np.float64) / 255
    if rot:
        a = np.rot90(a, rot // 90).copy()
        if kind == 'nor_gl':  # rotate the tangent vectors with the image (CCW): x' = -y, y' = x
            v = a * 2 - 1
            for _ in range(rot // 90): v = np.stack([-v[..., 1], v[..., 0], v[..., 2]], -1)
            a = (v + 1) / 2
    a = resize_wrap(a, w, h)
    _photo_cache[key] = a
    return a

# ---------- builders ----------
def build_colour(orig, r, P):
    h0, w0 = orig.shape[:2]; sc = r.get('scale', 4)
    W, H = min(2048, w0 * sc), min(2048, h0 * sc)
    lo = rgb_to_lab(orig[..., :3]); mo, so = lo.reshape(-1, 3).mean(0), lo.reshape(-1, 3).std(0)
    ph = photo(P, r['photo'], 'diff', r.get('rot', 0), W, H)
    lp = rgb_to_lab(ph); mp, sp = lp.reshape(-1, 3).mean(0), lp.reshape(-1, 3).std(0)
    own = r.get('own', 0.0)
    tm, ts = (1 - own) * mo + own * mp, (1 - own) * so + own * sp
    # pass 6: explicit pre-light target colour (Eht Ria sun is ~(2.2, 2.15, 1.36) linear gain, so grey reads pale yellow)
    if 'lab' in r: tm = np.array(r['lab'], dtype=np.float64)
    ts = ts * np.array([r.get('contrast', 1.0), 1, 1])
    if r['mode'] == 'stat':
        g = r.get('gain_l', 1.0)
        new = (lp - mp) / np.maximum(sp, 1e-6) * ts * np.array([g, 1, 1]) + tm
    else:  # detail
        base = resize_wrap(lo, W, H)
        if own or 'lab' in r or 'contrast' in r: base = (base - mo) / np.maximum(so, 1e-6) * ts + tm
        sig = max(W, H) / r.get('sig_div', 32)
        hp = lp - blur(lp, sig)
        k = r.get('detail', 0.9)
        new = base + hp * np.array([k, k * 0.5, k * 0.5])
    rgb = lab_to_rgb(new)
    alpha = np.clip(resize_wrap(orig[..., 3], W, H), 0, 1)
    return np.concatenate([rgb, alpha[..., None]], -1)

def build_normal(orig, r, P):
    h0, w0 = orig.shape[:2]; sc = r.get('scale', 4)
    W, H = min(2048, w0 * sc), min(2048, h0 * sc)
    p = photo(P, r['photo'], 'nor_gl', r.get('rot', 0), W, H) * 2 - 1
    st = r.get('strength', 1.0)
    if r['mode'] == 'nreplace':
        v = p.copy(); v[..., :2] *= st
    else:  # ncombine: whiteout blend of the upscaled original and the photo detail
        o = resize_wrap(orig[..., :3], W, H) * 2 - 1
        o /= np.maximum(np.linalg.norm(o, axis=-1, keepdims=True), 1e-6)
        d = p.copy(); d[..., :2] *= st
        v = np.stack([o[..., 0] + d[..., 0], o[..., 1] + d[..., 1], o[..., 2] * d[..., 2]], -1)
    v[..., 2] = np.clip(v[..., 2], 0.05, None)
    v /= np.linalg.norm(v, axis=-1, keepdims=True)
    return np.concatenate([(v + 1) / 2, np.ones(v.shape[:2] + (1,))], -1)

# ---------- TEX io ----------
def read_tex(blob):
    n = struct.unpack('>I', blob[:4])[0]; p = 4; out = []
    for _ in range(n):
        name = blob[p:p + 256]; sz = struct.unpack('>I', blob[p + 256:p + 260])[0]
        out.append([name, blob[p + 260:p + 260 + sz]]); p += 260 + sz
    assert p == len(blob), 'trailing bytes'
    return out
def write_tex(entries):
    b = bytearray(struct.pack('>I', len(entries)))
    for name, dds in entries: b += name + struct.pack('>I', len(dds)) + dds
    return bytes(b)
def ename(raw): return raw.split(b'\0')[0].decode('latin-1')

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode: raise RuntimeError(' '.join(cmd) + '\n' + r.stdout[-2000:] + r.stderr[-2000:])

def main():
    R = json.load(open(sys.argv[1])); only_build = '--only-build' in sys.argv
    work = R['work']; lib = os.path.join(work, 'lib'); os.makedirs(lib, exist_ok=True)
    toc = {}
    with open(R['toc'], encoding='utf-8') as t:
        next(t)
        for line in t:
            d, n, size, ext, off = line.rstrip('\n').split('\t'); toc[f'{d}/{n}'] = (int(size), int(off))
    mats, pre = R.get('materials', {}), R.get('prebuilt', {})
    wanted = set(mats) | set(pre)
    files = set()
    with open(R['detail'], encoding='utf-8') as t:
        next(t)
        for line in t:
            c = line.rstrip('\n').split('\t')
            if c[2] in wanted and any(c[0] == s or c[0].startswith(s + '/') for s in R['scope']): files.add(f'{c[0]}/{c[1]}')
    files = sorted(files)
    print(f'{len(files)} TEX files contain recipe textures')
    cpk = open(R['cpk'], 'rb'); tex = {}
    for f in files:
        size, off = toc[f]; cpk.seek(off); tex[f] = read_tex(cpk.read(size))
    # distinct originals per name
    built = {}  # (name, sha) -> dds bytes
    stat_once = {}  # name -> dds bytes (for stat / nreplace)
    tmp = tempfile.mkdtemp(dir=work)
    todo = collections.OrderedDict()
    for f in files:
        for raw, dds in tex[f]:
            n = ename(raw)
            if n in mats: todo.setdefault((n, hashlib.sha1(dds).hexdigest()[:12]), dds)
    # for stat/nreplace keep only the largest original per name
    best = {}
    for (n, sha), dds in todo.items():
        if mats[n]['mode'] in ('stat', 'nreplace'):
            w = struct.unpack('<I', dds[16:20])[0]
            if n not in best or w > best[n][1]: best[n] = (sha, w)
    report = []
    for (n, sha), dds in todo.items():
        r = mats[n]; once = r['mode'] in ('stat', 'nreplace')
        if once and best[n][0] != sha: continue
        cache = os.path.join(lib, f'{n}__{"name" if once else sha}.dds')
        if os.path.exists(cache) and os.path.getmtime(cache) > os.path.getmtime(sys.argv[1]):
            data = open(cache, 'rb').read()
        else:
            src = os.path.join(tmp, f'{sha}.dds'); open(src, 'wb').write(dds)
            run([R['texconv'], '-nologo', '-y', '-ft', 'png', '-f', 'R8G8B8A8_UNORM', '-o', tmp, src])
            orig = np.asarray(Image.open(os.path.join(tmp, f'{sha}.png')).convert('RGBA')).astype(np.float64) / 255
            out = build_normal(orig, r, R['photos']) if r['mode'].startswith('n') else build_colour(orig, r, R['photos'])
            png = os.path.join(tmp, f'{n}.png')
            Image.fromarray((out * 255).round().astype(np.uint8), 'RGBA').save(png)
            run([R['texconv'], '-nologo', '-y', '-f', 'BC7_UNORM', '-m', '0', '-o', tmp, png])
            shutil.move(os.path.join(tmp, f'{n}.dds'), cache)
            data = open(cache, 'rb').read()
            report.append(f'built {n} [{r["mode"]} {r["photo"]}] {orig.shape[1]}->{struct.unpack("<I", data[16:20])[0]}')
            print(report[-1], flush=True)
        if once: stat_once[n] = data
        else: built[(n, sha)] = data
    for n, p in pre.items(): pre[n]['data'] = open(p['dds'], 'rb').read()
    shutil.rmtree(tmp, ignore_errors=True)
    if only_build: return
    # pack
    out_root = R['out']; total = 0; skipped = collections.Counter(); per_file = []
    # optional: AI-upscaled originals (upscale_all.py) for every entry this recipe does not replace
    upmap = json.load(open(R['upscaled_map'])) if R.get('upscaled_map') and os.path.exists(R['upscaled_map']) else {}
    n_up = 0
    for f in files:
        ents = []; nrep = 0
        for raw, dds in tex[f]:
            n = ename(raw); sha = hashlib.sha1(dds).hexdigest()[:12]
            if n in pre:
                if pre[n].get('src_sha') and pre[n]['src_sha'] != sha: skipped[n] += 1; ents.append([raw, dds]); continue
                ents.append([raw, pre[n]['data']]); nrep += 1
            elif n in stat_once: ents.append([raw, stat_once[n]]); nrep += 1
            elif (n, sha) in built: ents.append([raw, built[(n, sha)]]); nrep += 1
            elif sha in upmap and os.path.exists(upmap[sha]): ents.append([raw, open(upmap[sha], 'rb').read()]); n_up += 1
            else: ents.append([raw, dds])
        blob = write_tex(ents)
        targets = [f]
        xb = 'XBOX/' + f.split('/', 1)[1]
        if f.startswith('COMMON/') and xb in toc: targets.append(xb)
        for t in targets:
            dst = os.path.join(out_root, *t.split('/')); os.makedirs(os.path.dirname(dst), exist_ok=True)
            open(dst, 'wb').write(blob); total += len(blob)
        per_file.append(f'{f}\t{len(ents)}\t{nrep}\t{len(blob)}\t{len(targets)}')
    open(os.path.join(work, 'pack_report.tsv'), 'w').write('file\tentries\treplaced\tbytes\tcopies\n' + '\n'.join(per_file) + '\n')
    print(f'packed {len(files)} files ({sum(1 for p in per_file if p.split(chr(9))[4] == "2")} also as XBOX), {total / 1e6:.0f} MB written, {n_up} other entries taken from the AI upscale')
    if skipped: print('prebuilt skipped (different original):', dict(skipped))

if __name__ == '__main__':
    main()
