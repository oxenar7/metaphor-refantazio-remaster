"""Whole-pipeline audit on the intermediate PNGs (no CPK / mod reads needed).

usage: python -I audit_pipeline.py <upall dir> <out.tsv> [grain|up|all]
grain: every colour item in grain_build_log*.tsv: gout/<sha>.png vs its input (up/<sha>.png or gpng/<sha>.png)
up   : every AI upscale up/<sha>.png vs the original decode png/<sha>.png (box-downscaled to the original size)
Flags: LUMA (mean moved > 12/255 or output < 60% of input), DARK (output mean < 8/255 while input > 30),
       ALPHA (mean alpha moved > 10/255), STALE (grain .dds older than its .png), FLAT (output std < 25% of input std)
"""
import sys, os, site, csv
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0, site.getusersitepackages())
import numpy as np
from PIL import Image
Image.MAX_IMAGE_PIXELS = None

U = sys.argv[1]; OUT = sys.argv[2]; MODE = sys.argv[3] if len(sys.argv) > 3 else 'all'

def stats(p, size=None):
    im = Image.open(p).convert('RGBA')
    # resize RGB and A apart: Pillow premultiplies RGBA, which blackens colour under alpha 0 (false LUMA flags)
    sep = lambda f: Image.merge('RGBA', f(im.convert('RGB')).split() + (f(im.getchannel('A')),))
    if size and im.size != size: im = sep(lambda c: c.resize(size, Image.BOX))
    elif max(im.size) > 1024: k = max(1, max(im.size) // 1024); im = sep(lambda c: c.reduce(k))
    a = np.asarray(im, dtype=np.float32)
    return a[..., :3].mean((0, 1)), a[..., 3].mean(), a[..., :3].mean(-1).std(), im.size

def check(job):
    kind, sha, name, src, out = job
    try:
        so = Image.open(src).size
        ms, as_, ss, sz = stats(src)
        mo, ao, sd, _ = stats(out, sz)
    except Exception as e:
        return f'{kind}\t{sha}\t{name}\t\t\t\t\t\tERROR {e}'
    flag = []
    L0, L1 = ms.mean(), mo.mean()
    if abs(L1 - L0) > 12 or (L0 > 20 and L1 < 0.6 * L0): flag.append('LUMA')
    if L1 < 8 and L0 > 30: flag.append('DARK')
    if abs(ao - as_) > 10: flag.append('ALPHA')
    if ss > 4 and sd < 0.25 * ss: flag.append('FLAT')
    if kind == 'grain':
        d = os.path.join(U, 'grain', sha + '.dds')
        if not os.path.exists(d) or os.path.getmtime(d) < os.path.getmtime(out): flag.append('STALE')
    f = lambda v: '/'.join(f'{x:.0f}' for x in v)
    return f'{kind}\t{sha}\t{name}\t{so[0]}x{so[1]}\t{f(ms)}\t{f(mo)}\t{as_:.0f}\t{ao:.0f}\t{",".join(flag)}'

def main():
    jobs = []
    if MODE in ('grain', 'all'):
        seen = {}
        for fn in ('grain_build_log.tsv', 'grain_build_log_fix.tsv'):
            p = os.path.join(U, fn)
            if not os.path.exists(p): continue
            for r in csv.reader(open(p, encoding='utf-8'), delimiter='\t'):
                if len(r) > 2 and len(r[0]) == 12: seen[r[0]] = r[1]
        for sha, name in seen.items():
            out = os.path.join(U, 'gout', sha + '.png')
            src = os.path.join(U, 'up', sha + '.png')
            if not os.path.exists(src): src = os.path.join(U, 'gpng', sha + '.png')
            if os.path.exists(out) and os.path.exists(src): jobs.append(('grain', sha, name, src, out))
    if MODE in ('up', 'all'):
        for fn in os.listdir(os.path.join(U, 'up')):
            sha = fn[:-4]; o = os.path.join(U, 'png', fn)
            if os.path.exists(o): jobs.append(('up', sha, '', o, os.path.join(U, 'up', fn)))
    print(len(jobs), 'jobs', flush=True)
    rows = ['kind\tsha\tname\torig\tmeanRGB_in\tmeanRGB_out\talpha_in\talpha_out\tflag']
    with ProcessPoolExecutor(max_workers=max(2, (os.cpu_count() or 4) - 2)) as ex:
        for i, r in enumerate(ex.map(check, jobs, chunksize=8)):
            rows.append(r)
            if i % 1000 == 0: print(i, flush=True)
    open(OUT, 'w', encoding='utf-8').write('\n'.join(rows) + '\n')
    fl = [r for r in rows[1:] if r.rsplit('\t', 1)[-1]]
    from collections import Counter
    c = Counter(t for r in fl for t in r.rsplit('\t', 1)[-1].split(','))
    print('flagged', len(fl), 'of', len(rows) - 1, dict(c))

if __name__ == '__main__':
    main()
