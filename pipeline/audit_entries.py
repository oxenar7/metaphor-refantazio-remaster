"""Audit what the mods actually ship against the original entries.

usage: python -I audit_entries.py <cfg.json> <out.tsv> <filter_regex_on_entry_name> <cpk_path_list.txt | ALL>
For every TEX in the list that a mod ships (terrain mod wins over upscale mod, as in AppConfig load order),
each entry whose bytes differ from base.cpk is decoded (mod + original), the mod image is box-downscaled to the
original size, and the TSV gets: mean RGB / alpha of both, PSNR, and a flag when colour or alpha moved a lot.
"""
import sys, os, re, json, struct, hashlib, subprocess, tempfile, shutil, site
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from upscale_all import read_tex, ename, dds_info, load_toc, run
sys.path.insert(0, site.getusersitepackages())
import numpy as np
from PIL import Image

cfg = json.load(open(sys.argv[1])); out_tsv = sys.argv[2]; rx = re.compile(sys.argv[3], re.I); lst = sys.argv[4]
MODS = os.path.join(os.environ['LOCALAPPDATA'], 'Programs', 'Reloaded-II', 'Mods')
ROOTS = [os.path.join(MODS, 'metaphor.remaster.terrain', 'MFEssentials', 'CPK', 'Remaster'),
         os.path.join(MODS, 'metaphor.remaster.upscale', 'MFEssentials', 'CPK', 'Upscale')]
toc = load_toc(cfg['toc'])
if lst == 'ALL':
    files = sorted({os.path.relpath(os.path.join(dp, f), r).replace('\\', '/') for r in ROOTS for dp, _, fs in os.walk(r) for f in fs if f.endswith('.TEX')})
else:
    files = [l.strip() for l in open(lst) if l.strip().endswith('.TEX')]
tmp = tempfile.mkdtemp(prefix='audit_'); cpk = open(cfg['cpk'], 'rb'); jobs = []
for f in files:
    src = next((os.path.join(r, *f.split('/')) for r in ROOTS if os.path.exists(os.path.join(r, *f.split('/')))), None)
    if not src or f not in toc: continue
    size, off = toc[f]; cpk.seek(off); orig = read_tex(cpk.read(size)); mod = read_tex(open(src, 'rb').read())
    for k, ((n0, d0), (n1, d1)) in enumerate(zip(orig, mod)):
        name = ename(n0)
        if d0.rstrip(b'\0') == d1.rstrip(b'\0') or not rx.search(name): continue
        i0, i1 = dds_info(d0), dds_info(d1)
        if not i0 or not i1 or i0.get('arr', 1) != 1: continue
        tag = hashlib.sha1(d0).hexdigest()[:10] + '_' + hashlib.sha1(d1).hexdigest()[:10]
        jobs.append((f, k, name, i0, i1, tag, 'ROOT' if 'terrain' in src else 'UP'))
        for suf, d in (('o', d0), ('m', d1)):
            p = os.path.join(tmp, f'{tag}_{suf}.dds')
            if not os.path.exists(p): open(p, 'wb').write(d)
print(len(jobs), 'changed entries matching', flush=True)
ddss = sorted(os.path.join(tmp, x) for x in os.listdir(tmp) if x.endswith('.dds'))
for i in range(0, len(ddss), 300):
    l = os.path.join(tmp, 'l.txt'); open(l, 'w', newline='\r\n').write('\n'.join(ddss[i:i + 300]) + '\n')
    run([cfg['texconv'], '-nologo', '-y', '-ft', 'png', '-f', 'R8G8B8A8_UNORM', '-m', '1', '-o', tmp, '-flist', l])
rows = ['file\tidx\tname\torig\tmod\tsrc\tmeanRGB_o\tmeanRGB_m\talpha_o\talpha_m\tpsnr\tflag']
seen = {}
for f, k, name, i0, i1, tag, srcmod in jobs:
    if tag not in seen:
        a = np.asarray(Image.open(os.path.join(tmp, tag + '_o.png')).convert('RGBA')).astype(np.float32)
        b = Image.open(os.path.join(tmp, tag + '_m.png')).convert('RGBA')
        if b.size != (a.shape[1], a.shape[0]):  # resize RGB and A apart: Pillow premultiplies RGBA, which blackens colour under alpha 0
            sz = (a.shape[1], a.shape[0]); b = Image.merge('RGBA', b.convert('RGB').resize(sz, Image.BOX).split() + (b.getchannel('A').resize(sz, Image.BOX),))
        b = np.asarray(b).astype(np.float32)
        mo, mm = a[..., :3].mean((0, 1)), b[..., :3].mean((0, 1)); ao, am = a[..., 3].mean(), b[..., 3].mean()
        mse = ((a[..., :3] - b[..., :3]) ** 2).mean(); ps = 99 if mse == 0 else 10 * np.log10(255 ** 2 / mse)
        flag = []
        if abs(mo.mean() - mm.mean()) > 25 or mm.mean() < 0.4 * mo.mean(): flag.append('LUMA')
        if abs(ao - am) > 20: flag.append('ALPHA')
        seen[tag] = (mo, mm, ao, am, ps, ','.join(flag))
    mo, mm, ao, am, ps, flag = seen[tag]
    fm = lambda v: '/'.join(f'{x:.0f}' for x in v)
    rows.append(f'{f}\t{k}\t{name}\t{i0["w"]}x{i0["h"]}\t{i1["w"]}x{i1["h"]}\t{srcmod}\t{fm(mo)}\t{fm(mm)}\t{ao:.0f}\t{am:.0f}\t{ps:.1f}\t{flag}')
open(out_tsv, 'w', encoding='utf-8').write('\n'.join(rows) + '\n')
shutil.rmtree(tmp, ignore_errors=True)
print('flagged:', sum(1 for r in rows[1:] if r.endswith(('LUMA', 'ALPHA', 'LUMA,ALPHA'))), 'of', len(rows) - 1)
