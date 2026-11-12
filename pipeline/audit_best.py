"""Audit the best-source slots as shipped: for every [file, entry] of best_index.json read the entry from the mod TEX,
cut out the mip level that has the original size (no full-size decode), decode it next to the original and compare.
A wrong mapping (other texture in the slot) shows as low PSNR / correlation; a colour shift as a mean move.

usage: python -I audit_best.py <cfg.json> <out.tsv>
"""
import sys, os, json, struct, hashlib, tempfile, shutil, collections, site
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from upscale_all import read_tex, ename, dds_info, load_toc, texconv_batch, W
sys.path.insert(0, site.getusersitepackages())
import numpy as np
from PIL import Image

BPB = {98: 16, 99: 16, 71: 8, 72: 8, 74: 16, 75: 16, 77: 16, 78: 16}
def level_bytes(d, lvl):
    inf = dds_info(d); b = BPB[inf['fmt']]; w, h = inf['w'], inf['h']; off = 148
    for _ in range(lvl):
        off += max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * b; w, h = max(1, w // 2), max(1, h // 2)
    return d[off:off + max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * b], w, h

def main():
    cfg = json.load(open(sys.argv[1])); out = sys.argv[2]
    bix = json.load(open(W(cfg, 'best_index.json'))); toc = load_toc(cfg['toc']); cpk = open(cfg['cpk'], 'rb')
    root = cfg['out']; tmp = tempfile.mkdtemp(prefix='abest_'); jobs = []; seen = set(); why = collections.Counter()
    for f, slots in bix.items():
        size, off = toc[f]; cpk.seek(off); orig = read_tex(cpk.read(size))
        mp = os.path.join(root, *f.split('/'))
        if not os.path.exists(mp): why['file_not_shipped'] += len(slots); continue
        mod = read_tex(open(mp, 'rb').read())
        for k, s, path, kind in slots:
            d0, d1 = orig[k][1], mod[k][1]; i0, i1 = dds_info(d0), dds_info(d1)
            if d0.rstrip(b'\0') == d1.rstrip(b'\0'): why['UNCHANGED'] += 1; jobs.append((f, k, ename(orig[k][0]), kind, None, i0, i1)); continue
            r = i1['w'] // i0['w']; lvl = r.bit_length() - 1
            if (i0['w'] << lvl) != i1['w'] or i1['mips'] <= lvl: why['odd'] += 1; jobs.append((f, k, ename(orig[k][0]), kind, 'odd', i0, i1)); continue
            lb, w, h = level_bytes(d1, lvl)
            hdr = bytearray(d0[:148]); hdr[28:32] = struct.pack('<I', 1)
            tag = hashlib.sha1(d0).hexdigest()[:10] + '_' + hashlib.sha1(lb).hexdigest()[:10]
            jobs.append((f, k, ename(orig[k][0]), kind, tag, i0, i1))
            if tag in seen: continue
            seen.add(tag)
            open(os.path.join(tmp, tag + '_m.dds'), 'wb').write(bytes(hdr) + lb)
            o = bytearray(d0); o[28:32] = struct.pack('<I', 1); lb0, _, _ = level_bytes(d0, 0)
            open(os.path.join(tmp, tag + '_o.dds'), 'wb').write(bytes(hdr) + lb0)
    print(len(jobs), 'slots,', len(seen), 'unique to decode', dict(why), flush=True)
    texconv_batch(cfg, sorted(os.path.join(tmp, x) for x in os.listdir(tmp) if x.endswith('.dds')), tmp, ['-ft', 'png', '-f', 'R8G8B8A8_UNORM'])
    res = {}
    for tag in seen:
        try:
            a = np.asarray(Image.open(os.path.join(tmp, tag + '_o.png')).convert('RGBA')).astype(np.float32)
            b = np.asarray(Image.open(os.path.join(tmp, tag + '_m.png')).convert('RGBA')).astype(np.float32)
        except Exception as e:
            res[tag] = None; continue
        m = a[..., 3] > 127 if a[..., 3].std() > 2 else np.ones(a.shape[:2], bool)  # cut-outs: only the visible texels count
        if m.sum() < 16: m = np.ones(a.shape[:2], bool)
        A, B = a[..., :3][m], b[..., :3][m]
        mse = ((A - B) ** 2).mean(); ps = 99 if mse == 0 else 10 * np.log10(255 ** 2 / mse)
        c = float(np.corrcoef(A.ravel(), B.ravel())[0, 1]) if A.std() > 1 and B.std() > 1 else 1.0
        res[tag] = (ps, c, float(A.mean() - B.mean()), float(a[..., 3].mean() - b[..., 3].mean()))
    rows = ['file\tidx\tname\tkind\torig\tmod\tpsnr\tcorr\tdmean\tdalpha\tflag']; flags = collections.Counter()
    for f, k, name, kind, tag, i0, i1 in jobs:
        r = res.get(tag) if tag and tag != 'odd' else None
        fl = []
        if tag is None: fl.append('UNCHANGED')
        elif tag == 'odd': fl.append('ODD_SIZE')
        elif r is None: fl.append('DECODE_FAIL')
        else:
            if r[1] < 0.9: fl.append('LOWCORR')
            if r[0] < 20: fl.append('LOWPSNR')
            if abs(r[2]) > 12: fl.append('MEAN')
            if abs(r[3]) > 15: fl.append('ALPHA')
        for x in fl: flags[x] += 1
        rr = '\t'.join(f'{x:.3f}' for x in r) if r else '\t\t\t'
        rows.append(f"{f}\t{k}\t{name}\t{kind}\t{i0['w']}x{i0['h']}\t{i1['w']}x{i1['h']}\t{rr}\t{','.join(fl)}")
    open(out, 'w', encoding='utf-8').write('\n'.join(rows) + '\n'); shutil.rmtree(tmp, ignore_errors=True)
    print('flags:', dict(flags), '->', out)

if __name__ == '__main__':
    main()
