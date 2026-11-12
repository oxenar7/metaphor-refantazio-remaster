"""Best-source pass: the game often ships the same atlas twice, a full copy in one object pack and a 1/2..1/8 copy in
another (e.g. the Grand Trad shopfront atlas: 1024 px in o_0103_390, 256 px in o_0003_247 / o_0103_185 / ...).
Every low-res slot gets the shipped (upscaled / grain / cavity) version of its big sibling instead of an AI upscale
of the small copy. Colour maps and normal maps.

usage: python -I best_source.py <cfg.json> scan|build|sheet
  scan   group entries by name stem (resolution token _0128.._4096 dropped), check the layouts really match
         (big copy box-downsampled vs small copy: RGB corr >= MIN_CORR, mean abs diff <= MAX_MAD, alpha too)
         -> work/best_pairs.json, work/best_scan.tsv
  build  pick per slot the big sibling's final DDS (cavity > special > grain > up > original), downsample when it
         would be more than MAX_RATIO x the slot or larger than MAX_SIDE -> work/best/<sha>_<w>.dds,
         work/best_index.json {file: [[entry, slot_sha, dds_path, kind]]}   (pack layer above cavity)
  sheet  before/after crops of a few slots -> work/best_sheet.png
A slot that already ships a processed version (grain / special / cavity) is only replaced by a processed big sibling.
Files shipped by the terrain mod (cfg exclude_files) are skipped.
"""
import sys, os, re, json, hashlib, collections, site, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from upscale_all import read_tex, ename, dds_info, load_toc, scope_files, texconv_batch, W
sys.path.insert(0, site.getusersitepackages())
import numpy as np
from PIL import Image

MIN_CORR, MAX_MAD, MIN_ACORR = 0.98, 6.0, 0.95
HYB_MIN_CORR, HYB_SIGMA, HYB_REC_CORR, HYB_REC_MAD = 0.85, 1.0, 0.985, 4.0
MAX_RATIO, MAX_SIDE, MIN_SLOT = 8, 2048, 256
SKIP_CATS = ('character', 'enemy')  # people / monsters keep their own copies
RES = re.compile(r'_(0064|0128|0256|0512|1024|2048|4096)(?=_|\.|$)')
NORMAL = re.compile(r'normal', re.I)
def stem(n): return RES.sub('', os.path.splitext(n)[0]).lower()

def load_img(p, size=None):
    im = Image.open(p).convert('RGBA')
    if size and im.size != size:
        rgb = im.convert('RGB').resize(size, Image.BOX); a = im.getchannel('A').resize(size, Image.BOX)
        im = Image.merge('RGBA', (*rgb.split(), a))
    return np.asarray(im).astype(np.float32)

def lin(x): return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
def srgb(x): x = np.clip(x, 0, 1); return np.where(x <= 0.0031308, x * 12.92, 1.055 * x ** (1 / 2.4) - 0.055)
def gauss(a, s):
    h, w = a.shape[:2]; fy = np.fft.fftfreq(h)[:, None]; fx = np.fft.fftfreq(w)[None, :]
    k = np.exp(-2 * (np.pi * s) ** 2 * (fx ** 2 + fy ** 2))
    if a.ndim == 3: k = k[..., None]
    return np.real(np.fft.ifft2(np.fft.fft2(a, axes=(0, 1)) * k, axes=(0, 1)))

def gain_map(pb, ps, size):
    """Low-frequency linear-light gain that turns the (downsampled) big copy into the small variant: a variant that only
    differs by dirt / tint keeps the big copy's detail with the variant's dirt."""
    b = lin(load_img(pb, size)[..., :3].astype(np.float64) / 255); s = lin(load_img(ps, size)[..., :3].astype(np.float64) / 255)
    g = np.clip((gauss(s, HYB_SIGMA) + 0.01) / (gauss(b, HYB_SIGMA) + 0.01), 0.25, 4.0)
    return g, b, s

def hybrid_ok(pb, ps, size):
    g, b, s = gain_map(pb, ps, size); rec = srgb(b * g); s8 = srgb(s)
    return float(np.corrcoef(rec.ravel(), s8.ravel())[0, 1]), float(np.abs(rec - s8).mean() * 255)

def compare(pb, ps, size):
    a, b = load_img(pb, size), load_img(ps, size)
    if a[..., :3].std() < 1 or b[..., :3].std() < 1: return None
    c = float(np.corrcoef(a[..., :3].ravel(), b[..., :3].ravel())[0, 1]); mad = float(np.abs(a[..., :3] - b[..., :3]).mean())
    aa, ab = a[..., 3], b[..., 3]
    ac = 1.0 if aa.std() < 2 and ab.std() < 2 and abs(aa.mean() - ab.mean()) < 4 else (
        float(np.corrcoef(aa.ravel(), ab.ravel())[0, 1]) if aa.std() >= 2 and ab.std() >= 2 else 0.0)
    return c, mad, ac

BP_MAX_BAD, PATCH_MAX = 0.02, 0.30
def layout_blocks(pb, ps, size):
    """Per block: does the band-pass detail (gauss 1 - gauss 4, luma) of the downsampled big copy correlate (>= 0.5)
    with the small one? Catches regions re-laid-out at another scale, which a global corr / a low-frequency gain can
    hide (e.g. the dirty Grand Trad shopfront variant whose plinth tiles are smaller). -> (bad fraction, bad grid)"""
    a, b = load_img(pb, size), load_img(ps, size)
    la = (a[..., :3].astype(np.float64) * [0.299, 0.587, 0.114]).sum(-1); lb = (b[..., :3].astype(np.float64) * [0.299, 0.587, 0.114]).sum(-1)
    ba = gauss(la, 1.0) - gauss(la, 4.0); bb = gauss(lb, 1.0) - gauss(lb, 4.0)
    h, w = la.shape; B = max(8, w // 16); grid = np.zeros((h // B, w // B), bool); n = 0
    for gy in range(h // B):
        for gx in range(w // B):
            pa = ba[gy * B:(gy + 1) * B, gx * B:(gx + 1) * B].ravel(); pq = bb[gy * B:(gy + 1) * B, gx * B:(gx + 1) * B].ravel()
            if pa.std() < 1.0 and pq.std() < 1.0: continue
            n += 1; c = np.corrcoef(pa, pq)[0, 1] if pa.std() > 0.05 and pq.std() > 0.05 else 0
            grid[gy, gx] = c < 0.5
    return grid.sum() / max(n, 1), grid
def layout_bad(pb, ps, size): return layout_blocks(pb, ps, size)[0]
def classify(p):
    """'ok' | 'patch' (colour variant: big copy except the re-laid-out blocks, which keep the variant's own version) | 'drop'"""
    bad, grid = layout_blocks(p['big_png'], p['small_png'], (p['sw'], p['sh']))
    if bad <= BP_MAX_BAD: return 'ok', None
    if p['kind'] == 'colour' and bad <= PATCH_MAX: return 'patch', grid.astype(int).tolist()
    return 'drop', None

def phase_scan(cfg):
    ix = json.load(open(W(cfg, 'index.json'))); uniq = ix['uniq']
    toc = load_toc(cfg['toc']); files = scope_files(toc); cpk = open(cfg['cpk'], 'rb')
    # colour: everything is already indexed and decoded by upscale_all.py
    ent = {}  # sha -> dict(kind, name, w, h, fmt, slots[[file, k]], png)
    for f, ps in ix['files'].items():
        for k, s in ps:
            u = uniq[s]; e = ent.setdefault(s, dict(kind='colour', name=u['name'], w=u['w'], h=u['h'], fmt=98, slots=[], png=W(cfg, 'png', s + '.png')))
            e['slots'].append([f, k])
    # normals: read every TEX in scope once, keep the normal-named entries
    os.makedirs(W(cfg, 'best_src'), exist_ok=True)
    for i, f in enumerate(files):
        size, off = toc[f]; cpk.seek(off)
        try: ents = read_tex(cpk.read(size))
        except Exception: continue
        for k, (raw, dds) in enumerate(ents):
            n = ename(raw)
            if not NORMAL.search(n) or n.lower().startswith(('rmei_', 'sesi_', 'rme_')): continue
            inf = dds_info(dds)
            if not inf or inf.get('cube') or inf.get('vol') or inf.get('arr', 1) != 1 or inf['fmt'] < 0: continue
            s = hashlib.sha1(dds).hexdigest()[:12]
            e = ent.setdefault(s, dict(kind='normal', name=n, w=inf['w'], h=inf['h'], fmt=inf['fmt'], slots=[], png=W(cfg, 'best_png', s + '.png')))
            e['slots'].append([f, k])
            p = W(cfg, 'best_src', s + '.dds')
            if not os.path.exists(p): open(p, 'wb').write(dds)
        if i % 1000 == 0: print(f'scan {i}/{len(files)}', flush=True)
    groups = collections.defaultdict(list)
    for s, e in ent.items(): groups[(e['kind'], stem(e['name']), e['fmt'])].append(s)
    cand = []
    for g, ss in groups.items():
        if len(ss) < 2: continue
        big = max(ss, key=lambda s: (ent[s]['w'] * ent[s]['h'], len(ent[s]['slots'])))
        B = ent[big]
        for s in ss:
            e = ent[s]
            if s == big or e['w'] * e['h'] >= B['w'] * B['h'] or B['w'] * e['h'] != B['h'] * e['w']: continue
            if max(e['w'], e['h']) < MIN_SLOT: continue
            cand.append((s, big))
    # decode the normal entries that take part
    need = sorted({x for p in cand for x in p if ent[x]['kind'] == 'normal' and not os.path.exists(ent[x]['png'])})
    if need: texconv_batch(cfg, [W(cfg, 'best_src', s + '.dds') for s in need], W(cfg, 'best_png'), ['-ft', 'png', '-f', 'R8G8B8A8_UNORM', '-m', '1'])
    pairs = []; rows = []
    for s, b in cand:
        e, B = ent[s], ent[b]
        try: r = compare(B['png'], e['png'], (e['w'], e['h']))
        except Exception as ex: r = None
        ok = bool(r and r[0] >= MIN_CORR and r[1] <= MAX_MAD and r[2] >= MIN_ACORR); hyb = False; hr = ('', '')
        if not ok and r and e['kind'] == 'colour' and r[0] >= HYB_MIN_CORR and r[2] >= MIN_ACORR:
            hr = hybrid_ok(B['png'], e['png'], (e['w'], e['h'])); hyb = ok = hr[0] >= HYB_REC_CORR and hr[1] <= HYB_REC_MAD
            hr = tuple(round(x, 4) for x in hr)
        patch = None
        if ok:
            cl, patch = classify(dict(big_png=B['png'], small_png=e['png'], sw=e['w'], sh=e['h'], kind=e['kind']))
            if cl == 'drop': ok = hyb = False; hr = ('layout', '')
            elif cl == 'patch': hyb = True; hr = ('patch', '')
        rows.append([e['kind'], e['name'], f"{e['w']}x{e['h']}", B['name'], f"{B['w']}x{B['h']}", len(e['slots'])] + ([round(x, 4) for x in r] if r else ['', '', '']) + [ok, hyb, *hr])
        if ok: pairs.append(dict(kind=e['kind'], small=s, big=b, sw=e['w'], sh=e['h'], bw=B['w'], bh=B['h'], name=e['name'], big_name=B['name'],
                                 slots=e['slots'], corr=r[0], mad=r[1], hybrid=hyb, patch=patch, small_png=e['png'], big_png=B['png']))
    json.dump(pairs, open(W(cfg, 'best_pairs.json'), 'w'))
    with open(W(cfg, 'best_scan.tsv'), 'w', encoding='utf-8') as fo:
        fo.write('kind\tname\tsize\tbig_name\tbig_size\tslots\tcorr\tmad\tacorr\tok\thybrid\trec_corr\trec_mad\n')
        for r in rows: fo.write('\t'.join(map(str, r)) + '\n')
    c = collections.Counter((p['kind'] + ('_hybrid' if p['hybrid'] else ''), p['bw'] // p['sw']) for p in pairs)
    print(f'{len(cand)} candidates, {len(pairs)} matching layouts:', dict(c), 'slots:', sum(len(p['slots']) for p in pairs))

def final_of(cfg, s, f=None, k=None, lay=None):
    """(path, kind) of what the mod ships for sha s; with f/k: what it ships in that very slot."""
    cix, six, gix = lay
    for name, ix in (('cavity', cix), ('special', six), ('grain', gix)):
        p = W(cfg, name, s + '.dds')
        if os.path.exists(p) and (f is None or [k, s] in ix.get(f, [])): return p, name
    p = W(cfg, 'dds', s + '.dds')
    if os.path.exists(p): return p, 'up'
    for p in (W(cfg, 'src', s + '.dds'), W(cfg, 'best_src', s + '.dds')):
        if os.path.exists(p): return p, 'orig'
    return None, None

def phase_build(cfg):
    pairs = json.load(open(W(cfg, 'best_pairs.json'))); excl = set(cfg.get('exclude_files', []))
    def lj(n): return json.load(open(W(cfg, n))) if os.path.exists(W(cfg, n)) else {}
    lay = (lj('cavity_index.json'), lj('special_index.json'), lj('grain_index.json'))
    RANK = dict(orig=0, up=1, grain=2, special=3, cavity=3)
    bix = collections.defaultdict(list); why = collections.Counter(); jobs = {}
    os.makedirs(W(cfg, 'best'), exist_ok=True)
    for p in pairs:
        bp, bk = final_of(cfg, p['big'], lay=lay)
        if not bp: why['no_big_final'] += 1; continue
        bi = dds_info(open(bp, 'rb').read(256))
        tw, th = bi['w'], bi['h']
        while tw > p['sw'] * MAX_RATIO or max(tw, th) > MAX_SIDE: tw //= 2; th //= 2
        r = tw // p['sw']
        if tw <= p['sw'] or tw % p['sw'] or r & (r - 1) or th != p['sh'] * r: why['no_gain_or_odd_ratio'] += 1; continue
        if p.get('hybrid'):  # the variant's own dirt / tint on top of the big copy's detail
            path = W(cfg, 'best', f"{p['small']}_{'p' if p.get('patch') else 'h'}{tw}.dds"); jobs[path] = (bp, tw, th, p['kind'], p)
        elif (tw, th) != (bi['w'], bi['h']):
            path = W(cfg, 'best', f"{p['big']}_{tw}.dds"); jobs[path] = (bp, tw, th, p['kind'], None)
        else: path = bp
        for f, k in p['slots']:
            if f in excl: why['terrain_mod_file'] += 1; continue
            if f.split('/')[2] in SKIP_CATS: why['skip_cat'] += 1; continue
            sp, sk = final_of(cfg, p['small'], f, k, lay)
            if sk in ('grain', 'special', 'cavity') and RANK[bk] < RANK[sk]: why[f'keep_{sk}'] += 1; continue
            if sp and sk != 'orig':
                si = dds_info(open(sp, 'rb').read(256))
                if si and si['w'] > tw: why['slot_already_bigger'] += 1; continue
            bix[f].append([k, p['small'], path, p['kind']]); why[f"slot_{p['kind']}{'_hybrid' if p.get('hybrid') else ''}_{bk}"] += 1
    used = {x[2] for v in bix.values() for x in v}
    jobs = {o: j for o, j in jobs.items() if o in used and not os.path.exists(o)}
    # resample / hybrid: decode the big final, resize (normals renormalised), apply the variant gain, BC7 full chain
    if jobs:
        tmp = W(cfg, 'best_tmp'); os.makedirs(tmp, exist_ok=True)
        todo = [j[0] for j in jobs.values()]
        for j in jobs.values():  # patched variants also need their own shipped version
            if j[4] and j[4].get('patch'): todo.append(final_of(cfg, j[4]['small'], lay=lay)[0])
        todo = sorted(set(todo))
        texconv_batch(cfg, todo, tmp, ['-ft', 'png', '-f', 'R8G8B8A8_UNORM', '-m', '1'])
        bymips = collections.defaultdict(list)
        for out, (bp, tw, th, kind, hp) in jobs.items():
            im = Image.open(os.path.join(tmp, os.path.splitext(os.path.basename(bp))[0] + '.png')).convert('RGBA')
            al = im.getchannel('A'); al = al.resize((tw, th), Image.LANCZOS) if al.size != (tw, th) else al
            if kind == 'normal':
                rgb = im.convert('RGB'); rgb = rgb.resize((tw, th), Image.LANCZOS) if rgb.size != (tw, th) else rgb
                n = np.asarray(rgb).astype(np.float32) / 127.5 - 1
                n /= np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-6)
                rgb = Image.fromarray(np.clip((n + 1) * 127.5 + 0.5, 0, 255).astype(np.uint8))
            else:
                rgb = im.convert('RGB'); rgb = rgb.resize((tw, th), Image.LANCZOS) if rgb.size != (tw, th) else rgb
                if hp:
                    g, _, _ = gain_map(hp['big_png'], hp['small_png'], (hp['sw'], hp['sh']))
                    gu = np.stack([np.asarray(Image.fromarray(g[..., c].astype(np.float32), 'F').resize((tw, th), Image.BICUBIC)) for c in range(3)], -1)
                    x = lin(np.asarray(rgb).astype(np.float64) / 255) * np.clip(gu, 0.25, 4.0)
                    if hp.get('patch'):  # re-laid-out blocks (+1 block margin) keep the variant's own shipped version
                        g = np.array(hp['patch'], bool); m = g.copy()
                        for dy in (-1, 0, 1):
                            for dx in (-1, 0, 1): m |= np.roll(np.roll(g, dy, 0), dx, 1)
                        mm = np.asarray(Image.fromarray((m * 255).astype(np.uint8)).resize((tw, th), Image.BILINEAR)).astype(np.float64)[..., None] / 255
                        sp = final_of(cfg, hp['small'], lay=lay)[0]
                        own = Image.open(os.path.join(tmp, os.path.splitext(os.path.basename(sp))[0] + '.png')).convert('RGB').resize((tw, th), Image.BICUBIC)
                        x = x * (1 - mm) + lin(np.asarray(own).astype(np.float64) / 255) * mm
                    rgb = Image.fromarray(np.round(srgb(x) * 255).astype(np.uint8))
            png = os.path.join(tmp, os.path.splitext(os.path.basename(out))[0] + '.png')
            Image.merge('RGBA', (*rgb.split(), al)).save(png)
            mips = 1; m = min(tw, th)
            while m > 4: m //= 2; mips += 1
            bymips[mips].append(png)
        for m, lst in bymips.items(): texconv_batch(cfg, lst, W(cfg, 'best'), ['-f', 'BC7_UNORM', '-m', str(m)] + cfg.get('bc7_args', []))
        shutil.rmtree(tmp, ignore_errors=True)
    miss = [x for v in bix.values() for x in v if not os.path.exists(x[2])]
    if miss: print('MISSING', len(miss), miss[:3])
    json.dump(dict(bix), open(W(cfg, 'best_index.json'), 'w'))
    print('best index:', len(bix), 'files,', sum(len(v) for v in bix.values()), 'slots;', dict(why))

def phase_sheet(cfg):
    """Before/after of the slots whose big sibling is at least 4x: current shipped slot vs best version, centre crop."""
    pairs = json.load(open(W(cfg, 'best_pairs.json'))); bix = json.load(open(W(cfg, 'best_index.json')))
    lay = tuple(json.load(open(W(cfg, n))) if os.path.exists(W(cfg, n)) else {} for n in ('cavity_index.json', 'special_index.json', 'grain_index.json'))
    used = {(x[1]) : x[2] for v in bix.values() for x in v}
    pick = [p for p in pairs if p['small'] in used and p['kind'] == 'colour' and 'shopFront' not in p['name']]
    pick.sort(key=lambda p: (-(p['bw'] // p['sw']), -len(p['slots'])))
    pick = [p for p in pairs if 'shopFront_02_a_m1' in p['name'] and p['small'] in used][:1] + pick[:5]
    tmp = W(cfg, 'best_tmp'); os.makedirs(tmp, exist_ok=True); todo = []
    for i, p in enumerate(pick):
        f, k = p['slots'][0]; a, _ = final_of(cfg, p['small'], f, k, lay); b = used[p['small']]
        for tag, src in (('a', a), ('b', b)):
            dst = os.path.join(tmp, f'{i}{tag}.dds'); shutil.copy(src, dst); todo.append(dst)
    texconv_batch(cfg, todo, tmp, ['-ft', 'png', '-f', 'R8G8B8A8_UNORM', '-m', '1'])
    from PIL import ImageDraw
    T = 520; sheet = Image.new('RGB', (2 * T + 20, len(pick) * (T + 26)), (18, 18, 18)); d = ImageDraw.Draw(sheet)
    for i, p in enumerate(pick):
        y = i * (T + 26); d.text((6, y + 6), f"{p['name'][:70]}  slot {p['sw']}  ->  big {p['bw']}   ora | dopo", fill=(235, 235, 235))
        for j, tag in enumerate('ab'):
            im = Image.open(os.path.join(tmp, f'{i}{tag}.png')).convert('RGB'); w, h = im.size
            c = im.crop((w // 4, h // 4, w // 4 + w // 4, h // 4 + h // 4)).resize((T, T), Image.BILINEAR)
            sheet.paste(c, (j * (T + 20), y + 24))
    sheet.save(W(cfg, 'best_sheet.png')); shutil.rmtree(tmp, ignore_errors=True); print(W(cfg, 'best_sheet.png'), len(pick))

def phase_refilter(cfg):
    """Apply the block layout check to an existing scan (best_pairs_unfiltered.json) without rescanning the game."""
    src = W(cfg, 'best_pairs_unfiltered.json'); src = src if os.path.exists(src) else W(cfg, 'best_pairs.json')
    pairs = json.load(open(src)); keep = []; c = collections.Counter()
    for p in pairs:
        cl, grid = classify(p); c[p['kind'] + ('_hybrid' if p['hybrid'] else '') + '_' + cl] += 1
        if cl == 'drop': continue
        if cl == 'patch': p['hybrid'] = True; p['patch'] = grid
        else: p.setdefault('patch', None)
        keep.append(p)
    json.dump(keep, open(W(cfg, 'best_pairs.json'), 'w')); print('kept', len(keep), dict(c))

if __name__ == '__main__':
    cfg = json.load(open(sys.argv[1])); dict(scan=phase_scan, build=phase_build, sheet=phase_sheet, refilter=phase_refilter)[sys.argv[2]](cfg)
