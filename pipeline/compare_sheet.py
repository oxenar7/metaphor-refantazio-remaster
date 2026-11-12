"""Side-by-side sheet: original entry (base.cpk) vs what the mods ship, for visual checks.

usage: python -I compare_sheet.py <cfg.json> <out.png> <spec.txt> [tile=360]
spec.txt lines: <cpk TEX path>\t<entry name regex>   (every matching entry of that file is shown)
Mods searched in load-order priority: remaster.terrain, then remaster.upscale.
"""
import sys, os, re, json, tempfile, shutil, site
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from upscale_all import read_tex, ename, dds_info, load_toc, run
sys.path.insert(0, site.getusersitepackages())
from PIL import Image, ImageDraw

cfg = json.load(open(sys.argv[1])); out = sys.argv[2]; spec = sys.argv[3]; T = int(sys.argv[4]) if len(sys.argv) > 4 else 360
MODS = os.path.join(os.environ['LOCALAPPDATA'], 'Programs', 'Reloaded-II', 'Mods')
ROOTS = [('terrain', os.path.join(MODS, 'metaphor.remaster.terrain', 'MFEssentials', 'CPK', 'Remaster')),
         ('upscale', os.path.join(MODS, 'metaphor.remaster.upscale', 'MFEssentials', 'CPK', 'Upscale'))]
toc = load_toc(cfg['toc']); cpk = open(cfg['cpk'], 'rb'); tmp = tempfile.mkdtemp(prefix='cmp_'); rows = []; seen = set()
for line in open(spec, encoding='utf-8'):
    if not line.strip() or line.startswith('#'): continue
    f, rx = line.rstrip('\n').split('\t'); rx = re.compile(rx, re.I)
    size, off = toc[f]; cpk.seek(off); orig = read_tex(cpk.read(size))
    src = next(((n, os.path.join(r, *f.split('/'))) for n, r in ROOTS if os.path.exists(os.path.join(r, *f.split('/')))), (None, None))
    mod = read_tex(open(src[1], 'rb').read()) if src[1] else orig
    for k, ((n0, d0), (_, d1)) in enumerate(zip(orig, mod)):
        name = ename(n0)
        if not rx.search(name) or (name, len(d0)) in seen: continue
        seen.add((name, len(d0))); i = len(rows)
        open(os.path.join(tmp, f'{i}_o.dds'), 'wb').write(d0); open(os.path.join(tmp, f'{i}_m.dds'), 'wb').write(d1)
        a, b = dds_info(d0), dds_info(d1)
        rows.append((f.split('/')[-1], name, f'{a["w"]}x{a["h"]}', f'{b["w"]}x{b["h"]}', src[0] if d0.rstrip(b'\0') != d1.rstrip(b'\0') else 'UNCHANGED'))
ddss = [os.path.join(tmp, x) for x in sorted(os.listdir(tmp))]
if ddss:
    l = os.path.join(tmp, 'l.txt'); open(l, 'w', newline='\r\n').write('\n'.join(ddss) + '\n')
    run([cfg['texconv'], '-nologo', '-y', '-ft', 'png', '-f', 'R8G8B8A8_UNORM', '-m', '1', '-o', tmp, '-flist', l])
sheet = Image.new('RGB', (2 * T + 30, max(1, len(rows)) * (T + 28)), 'white'); d = ImageDraw.Draw(sheet)
for i, (fn, name, so, sm, who) in enumerate(rows):
    y = i * (T + 28)
    for j, suf in enumerate('om'):
        im = Image.open(os.path.join(tmp, f'{i}_{suf}.png')).convert('RGB')
        sc = T / max(im.size); im = im.resize((max(1, round(im.width * sc)), max(1, round(im.height * sc))), Image.NEAREST if j == 0 else Image.LANCZOS)
        sheet.paste(im, (j * (T + 30), y + 24))
    d.text((4, y + 2), f'{fn}  {name[:60]}', fill='black'); d.text((4, y + 13), f'orig {so}   ->   {who} {sm}', fill='blue')
sheet.save(out); shutil.rmtree(tmp, ignore_errors=True); print(len(rows), 'rows ->', out)
