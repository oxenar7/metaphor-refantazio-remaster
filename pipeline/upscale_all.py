"""Whole-game faithful AI upscale of Metaphor colour textures (2x, GPU), packed as a CriFs mod.

usage: python -I upscale_all.py <cfg.json> <phase> [--limit N]
phases (each resumable, results cached in cfg.work):
  index   scan TEX files, pick colour entries, dump unique originals to work/src/<sha>.dds, write work/index.json
  decode  texconv the originals to work/png/<sha>.png
  up      GPU upscale + back-projection -> work/up/<sha>.png   (skips normal-looking / flat images)
  encode  texconv BC7 (orig mips + 1) -> work/dds/<sha>.dds
  pack    write the mod (cfg.out/<tier path>), work/pack_report.tsv, work/upscaled_map.json (sha12 -> dds path)
  all     every phase in order
cfg.json: {"cpk", "toc", "texconv", "model", "work", "out", "exclude_files": [...], "max_side": 512, "half": true}
  exclude_files are indexed and upscaled but not packed (remaster.py merges them via upscaled_map.json)
Rules:
  scope    COMMON/model/<cat>/** for all TEX; plus XBOX/model/** files that have no COMMON twin (the game falls back to them)
  colour   by entry name (deny list below), BC7_UNORM, 2D, not array/cube, 32 <= side <= max_side, aspect <= 8
           (side <= big_max_side for the big_cats, e.g. field/vehicle 2048 -> 4096, run in overlapping tiles of cfg.tile)
  content  decoded image skipped if it looks like a tangent normal map or is flat
  fidelity 3 back-projection steps make avgpool2(result) == original (Atlus' colours / lines kept)
"""
import sys, os, json, struct, hashlib, subprocess, re, time, collections, site

DENY_PREFIX = ('sesi_', 'rmei_', 'rme_', 'rmee_', 'lll')  # RMEE_* = roughness/metal, LLLA_* = baked light/shadow maps
DENY_TOK = re.compile(r'^(normal\d*|normals|nm|nomal|norm|n|no\d*|nor|roughness|metallic|mask|alpha|blend|height|ao|eff|ramp\d*|'
                      r'gradation\d*|mebika|black|bl|pr|prm|para|ec|em\d*|sk\d*|bm|emissive|luminous|mlt|pm|multi|a|'
                      r'map\d*|gfdworldcreator2shader\w*|.*ramp|shadow)$')

COLOUR_END = re.compile(r'(base_?color|albedo|diffuse|color)\d*$')  # "color" = SpeedTree "..._a_Color" leaves/bark

def is_colour_name(n):
    l = n.lower()
    if l.startswith(DENY_PREFIX) or any(k in l for k in ('ramp', 'gradation', '_grad')): return False
    stem = l.rsplit('.', 1)[0]
    # an explicit colour suffix wins over variant tokens such as _a_ (bg03 "..._01_a_m1_0512_basecolor"), _normal_grass_, _blend_
    if COLOUR_END.search(stem): return True
    if 'mask' in l: return False
    toks = [t for t in re.split(r'[_\-. ]+', stem) if t]
    # a lone "a" before other tokens is a variant letter (bg03 "..._01_a_m1_1024_grunge", archetype "a_0001_..."); only a trailing "_a" may mean alpha
    return not any(DENY_TOK.match(t) and not (t == 'a' and i < len(toks) - 1) for i, t in enumerate(toks))

def dds_info(d):
    if d[:4] != b'DDS ': return None
    h, w = struct.unpack('<II', d[12:20]); mips = struct.unpack('<I', d[28:32])[0]
    caps2 = struct.unpack('<I', d[112:116])[0]
    if d[84:88] != b'DX10': return dict(w=w, h=h, mips=mips, fmt=-1)
    fmt, dim, misc, arr = struct.unpack('<IIII', d[128:144])
    return dict(w=w, h=h, mips=max(mips, 1), fmt=fmt, cube=bool(misc & 4) or bool(caps2 & 0x200), arr=arr, vol=bool(caps2 & 0x200000))

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
    if r.returncode: raise RuntimeError(' '.join(cmd[:6]) + '\n' + r.stdout[-3000:] + r.stderr[-2000:])
    return r.stdout

def load_toc(path):
    toc = {}
    with open(path, encoding='utf-8') as t:
        next(t)
        for line in t:
            d, n, size, ext, off = line.rstrip('\n').split('\t'); toc[f'{d}/{n}'] = (int(size), int(off))
    return toc

def scope_files(toc):
    com = {p for p in toc if p.startswith('COMMON/model/') and p.endswith('.TEX')}
    xb = {p for p in toc if p.startswith('XBOX/model/') and p.endswith('.TEX') and 'COMMON/' + p[5:] not in toc}
    return sorted(com) + sorted(xb)

def W(cfg, *p): return os.path.join(cfg['work'], *p)

# ---------------- phases ----------------
def phase_index(cfg, limit):
    toc = load_toc(cfg['toc']); files = scope_files(toc)
    os.makedirs(W(cfg, 'src'), exist_ok=True)
    cpk = open(cfg['cpk'], 'rb'); uniq = {}; per_file = {}; why = collections.Counter(); t0 = time.time()
    for i, f in enumerate(files):
        size, off = toc[f]; cpk.seek(off); ents = read_tex(cpk.read(size)); picks = []
        for k, (raw, dds) in enumerate(ents):
            n = ename(raw); inf = dds_info(dds)
            if not inf: why['not_dds'] += 1; continue
            if not is_colour_name(n): why['name_not_colour'] += 1; continue
            if inf['fmt'] != 98: why[f'fmt_{inf["fmt"]}'] += 1; continue
            if inf.get('cube') or inf.get('vol') or inf.get('arr', 1) != 1: why['array_cube'] += 1; continue
            w, h = inf['w'], inf['h']
            lim = cfg.get('max_side', 512)
            if f.split('/')[2] in cfg.get('big_cats', []): lim = max(lim, cfg.get('big_max_side', lim))  # e.g. Gauntlet Runner 2048 -> 4096
            if max(w, h) > lim: why['already_big'] += 1; continue
            if min(w, h) < 32 or max(w, h) > 8 * min(w, h): why['tiny_or_strip'] += 1; continue
            sha = hashlib.sha1(dds).hexdigest()[:12]
            if sha not in uniq:
                uniq[sha] = dict(name=n, w=w, h=h, mips=inf['mips'], cat=f.split('/')[2])
                dst = W(cfg, 'src', sha + '.dds')
                if not os.path.exists(dst): open(dst, 'wb').write(dds)
            picks.append([k, sha]); why['picked'] += 1
        if picks: per_file[f] = picks
        if i % 500 == 0: print(f'index {i}/{len(files)} files, {len(uniq)} unique, {time.time() - t0:.0f}s', flush=True)
        if limit and len(uniq) >= limit: break
    json.dump(dict(uniq=uniq, files=per_file), open(W(cfg, 'index.json'), 'w'))
    print('entries:', dict(why)); print(f'{len(per_file)} files to rewrite, {len(uniq)} unique textures')
    print('unique by size:', collections.Counter(f'{u["w"]}x{u["h"]}' for u in uniq.values()).most_common(8))
    print('unique by cat:', collections.Counter(u['cat'] for u in uniq.values()).most_common())

def texconv_batch(cfg, inputs, outdir, args, chunk=400):
    os.makedirs(outdir, exist_ok=True)
    for i in range(0, len(inputs), chunk):
        lst = W(cfg, 'flist.txt')
        with open(lst, 'w', newline='\r\n') as fl: fl.write('\n'.join(inputs[i:i + chunk]) + '\n')
        run([cfg['texconv'], '-nologo', '-y'] + args + ['-o', outdir, '-flist', lst])
        print(f'  texconv {min(i + chunk, len(inputs))}/{len(inputs)}', flush=True)

def phase_decode(cfg, limit):
    ix = json.load(open(W(cfg, 'index.json')))
    todo = [W(cfg, 'src', s + '.dds') for s in ix['uniq'] if not os.path.exists(W(cfg, 'png', s + '.png'))]
    print(f'decode {len(todo)}'); texconv_batch(cfg, todo, W(cfg, 'png'), ['-ft', 'png', '-f', 'R8G8B8A8_UNORM'])

def phase_up(cfg, limit):
    sys.path.insert(0, site.getusersitepackages())
    import numpy as np, torch, torch.nn.functional as F
    from PIL import Image
    from spandrel import ModelLoader
    dev = 'cuda' if torch.cuda.is_available() else 'cpu'
    print('device', dev, torch.__version__, flush=True)
    model = ModelLoader().load_from_file(cfg['model']).eval().to(dev)
    half = dev == 'cuda' and cfg.get('half', True)
    if half: model.model.half()
    S = 2; assert model.scale == 2
    ix = json.load(open(W(cfg, 'index.json'))); os.makedirs(W(cfg, 'up'), exist_ok=True)
    logp = W(cfg, 'up_log.tsv'); new_log = not os.path.exists(logp); log = open(logp, 'a')
    if new_log: log.write('sha\tname\tsize\tstatus\tpsnr_raw\tpsnr_bp\tsec\n')
    done = {l.split('\t')[0] for l in open(logp) if l.strip()}
    todo = [s for s in ix['uniq'] if s not in done]
    if limit: todo = todo[:limit]
    t0 = time.time(); stats = collections.Counter()

    def up_bicubic(x, mode):  # pad, upsample, crop so borders follow the texture's wrap / clamp behaviour
        p = 4; xp = F.pad(x, (p, p, p, p), mode=mode)
        u = F.interpolate(xp, scale_factor=S, mode='bicubic', align_corners=False)
        return u[:, :, p * S:-p * S, p * S:-p * S]

    TILE, OV = cfg.get('tile', 1024), 32
    def sr_model(xp):  # model on a padded input; inputs bigger than TILE (2048 maps) go through in overlapping tiles
        H, Wd = xp.shape[2:]
        if max(H, Wd) <= TILE + 2 * OV: return model(xp.half() if half else xp).float()
        out = torch.empty(1, 3, H * S, Wd * S, device=xp.device)
        for y0 in range(0, H, TILE):
            for x0 in range(0, Wd, TILE):
                y1, x1 = min(y0 + TILE, H), min(x0 + TILE, Wd)
                ya, xa, yb, xb = max(y0 - OV, 0), max(x0 - OV, 0), min(y1 + OV, H), min(x1 + OV, Wd)
                t = xp[:, :, ya:yb, xa:xb]
                r = model(t.half() if half else t).float()
                out[:, :, y0 * S:y1 * S, x0 * S:x1 * S] = r[:, :, (y0 - ya) * S:(y1 - ya) * S, (x0 - xa) * S:(x1 - xa) * S]
        return out

    for i, s in enumerate(todo):
        u = ix['uniq'][s]; t1 = time.time()
        if not is_colour_name(u['name']):
            log.write(f'{s}\t{u["name"]}\t{u["w"]}x{u["h"]}\tskip_name\t\t\t\n'); stats['skip_name'] += 1; continue
        arr = np.asarray(Image.open(W(cfg, 'png', s + '.png')).convert('RGBA')).astype(np.float32) / 255.0
        rgb_np = arr[..., :3]; m = rgb_np.reshape(-1, 3).mean(0); sd = rgb_np.reshape(-1, 3).std(0)
        if m[2] > 0.7 and abs(m[0] - 0.5) < 0.12 and abs(m[1] - 0.5) < 0.12 and rgb_np[..., 2].min() > 0.35:
            log.write(f'{s}\t{u["name"]}\t{u["w"]}x{u["h"]}\tskip_normal\t\t\t\n'); stats['skip_normal'] += 1; continue
        if sd.max() < 1.5 / 255:
            log.write(f'{s}\t{u["name"]}\t{u["w"]}x{u["h"]}\tskip_flat\t\t\t\n'); stats['skip_flat'] += 1; continue
        mode = 'circular' if u['cat'] in ('field', 'vehicle') else 'replicate'
        rgb = torch.from_numpy(rgb_np.copy()).permute(2, 0, 1)[None].to(dev)
        P = 8; rgbp = F.pad(rgb, (P, P, P, P), mode=mode)
        with torch.inference_mode():
            sr = sr_model(rgbp).clamp(0, 1)[:, :, P * S:-P * S, P * S:-P * S]
            raw = F.mse_loss(F.avg_pool2d(sr, S), rgb).item()
            for _ in range(3): sr = (sr + up_bicubic(rgb - F.avg_pool2d(sr, S), mode)).clamp(0, 1)
            bp = F.mse_loss(F.avg_pool2d(sr, S), rgb).item()
        out = (sr[0].permute(1, 2, 0).cpu().numpy() * 255).round().clip(0, 255).astype(np.uint8)
        a8 = (arr[..., 3] * 255).round().astype(np.uint8)
        im = Image.fromarray(out, 'RGB')
        if a8.min() < 255 and 'grunge' in u['name'].lower():
            # grunge overlays keep a detailed dirt pattern in alpha: upscale it with the model too (grey -> RGB -> mean)
            al = torch.from_numpy(arr[..., 3].copy())[None, None].to(dev)
            alp = F.pad(al.repeat(1, 3, 1, 1), (P, P, P, P), mode=mode)
            with torch.inference_mode():
                sa = sr_model(alp).clamp(0, 1)[:, :, P * S:-P * S, P * S:-P * S].mean(1, keepdim=True)
                for _ in range(3): sa = (sa + up_bicubic(al - F.avg_pool2d(sa, S), mode)).clamp(0, 1)
            im.putalpha(Image.fromarray((sa[0, 0].cpu().numpy() * 255).round().clip(0, 255).astype(np.uint8)))
        elif a8.min() < 255: im.putalpha(Image.fromarray(a8).resize((u['w'] * S, u['h'] * S), Image.LANCZOS))
        else: im.putalpha(255)
        im.save(W(cfg, 'up', s + '.png'), compress_level=1)
        ps = lambda e: 99.0 if e <= 0 else 10 * np.log10(1 / e)
        log.write(f'{s}\t{u["name"]}\t{u["w"]}x{u["h"]}\tok\t{ps(raw):.2f}\t{ps(bp):.2f}\t{time.time() - t1:.2f}\n'); stats['ok'] += 1
        if i % 200 == 0:
            log.flush(); el = time.time() - t0
            print(f'up {i}/{len(todo)} {dict(stats)} {el:.0f}s, eta {el / (i + 1) * (len(todo) - i - 1) / 60:.0f} min', flush=True)
    log.close(); print('up done', dict(stats), f'{time.time() - t0:.0f}s')

def phase_encode(cfg, limit):
    ix = json.load(open(W(cfg, 'index.json'))); groups = collections.defaultdict(list)
    for s, u in ix['uniq'].items():
        if os.path.exists(W(cfg, 'up', s + '.png')) and not os.path.exists(W(cfg, 'dds', s + '.dds')):
            groups[u['mips'] + 1].append(W(cfg, 'up', s + '.png'))
    for m, lst in sorted(groups.items()):
        print(f'encode mips={m}: {len(lst)}', flush=True)
        texconv_batch(cfg, lst, W(cfg, 'dds'), ['-f', 'BC7_UNORM', '-m', str(m)] + cfg.get('bc7_args', []))

def phase_pack(cfg, limit):
    ix = json.load(open(W(cfg, 'index.json'))); toc = load_toc(cfg['toc'])
    cpk = open(cfg['cpk'], 'rb'); total = 0; rep = []; nfiles = 0; nent = 0; ngr = 0; umap = {}
    excl = set(cfg.get('exclude_files', []))  # files another mod ships: their upscaled entries go to upscaled_map.json only
    # optional grain_all.py results (photo grain on wood / ground / stone + their normals) override the plain upscale
    gix = json.load(open(W(cfg, 'grain_index.json'))) if cfg.get('use_grain', True) and os.path.exists(W(cfg, 'grain_index.json')) else {}
    # optional hand-tuned results (e.g. carriage.py: 4x + oriented wood grain) override both
    six = json.load(open(W(cfg, 'special_index.json'))) if os.path.exists(W(cfg, 'special_index.json')) else {}
    # optional cavity.py results (baked crevice occlusion + deeper normals on Grand Trad stone) override everything
    cix = json.load(open(W(cfg, 'cavity_index.json'))) if cfg.get('use_cavity', True) and os.path.exists(W(cfg, 'cavity_index.json')) else {}
    # optional best_source.py results (low-res copies of an atlas get the big sibling's shipped version) override everything
    bix = json.load(open(W(cfg, 'best_index.json'))) if cfg.get('use_best', True) and os.path.exists(W(cfg, 'best_index.json')) else {}
    # --only REGEX | @cavity | @best: repack just these files (the upscaled map is merged, not replaced)
    only = (lambda f: f in cix) if cfg.get('_only') == '@cavity' else (lambda f: f in bix) if cfg.get('_only') == '@best' else (
        re.compile(cfg['_only']).search if cfg.get('_only') else None)
    for f in sorted(set(ix['files']) | set(gix) | set(six) | set(cix) | set(bix)):
        if only and not only(f): continue
        size, off = toc[f]; cpk.seek(off); ents = read_tex(cpk.read(size)); new = {}
        for k, s in ix['files'].get(f, []):
            p = W(cfg, 'dds', s + '.dds')
            if os.path.exists(p): new[k] = (s, p, 'up')
        for k, s in gix.get(f, []):
            p = W(cfg, 'grain', s + '.dds')
            if os.path.exists(p): new[k] = (s, p, 'grain')
        for k, s in six.get(f, []):
            p = W(cfg, 'special', s + '.dds')
            if os.path.exists(p): new[k] = (s, p, 'special')
        for k, s in cix.get(f, []):
            p = W(cfg, 'cavity', s + '.dds')
            if os.path.exists(p): new[k] = (s, p, 'cavity')
        for k, s, p, _ in bix.get(f, []):
            if os.path.exists(p): new[k] = (s, p, 'best')
        nrep = 0
        for k, (s, p, kind) in new.items():
            d = open(p, 'rb').read(); inf = dds_info(d); oi = dds_info(ents[k][1])
            ok = (1, 2, 4, 8) if kind == 'best' else (1, 2, 4)
            if not inf or not oi or inf['fmt'] != oi['fmt'] or inf['w'] not in [oi['w'] * r for r in ok]: continue
            if inf['w'] * oi['h'] != inf['h'] * oi['w']: continue
            d += b'\0' * (-len(d) % 32)  # the game's own DDS entries are zero-padded to 32 bytes
            ents[k][1] = d; nrep += 1; umap[s] = p; ngr += kind == 'grain'
        if not nrep or f in excl: continue
        blob = write_tex(ents); dst = os.path.join(cfg['out'], *f.split('/'))
        os.makedirs(os.path.dirname(dst), exist_ok=True); open(dst, 'wb').write(blob)
        total += len(blob); nfiles += 1; nent += nrep; rep.append(f'{f}\t{len(ents)}\t{nrep}\t{len(blob)}')
    if only:  # partial repack: keep the full map, update only what was rewritten
        full = json.load(open(W(cfg, 'upscaled_map.json'))) if os.path.exists(W(cfg, 'upscaled_map.json')) else {}
        full.update(umap); umap = full
        open(W(cfg, 'pack_report_only.tsv'), 'w').write('file\tentries\treplaced\tbytes\n' + '\n'.join(rep) + '\n')
    else:
        open(W(cfg, 'pack_report.tsv'), 'w').write('file\tentries\treplaced\tbytes\n' + '\n'.join(rep) + '\n')
    json.dump(umap, open(W(cfg, 'upscaled_map.json'), 'w'))
    print(f'packed {nfiles} files, {nent} entries replaced ({ngr} with grain), {total / 1e9:.2f} GB')

PHASES = dict(index=phase_index, decode=phase_decode, up=phase_up, encode=phase_encode, pack=phase_pack)
if __name__ == '__main__':
    cfg = json.load(open(sys.argv[1])); ph = sys.argv[2]
    limit = int(sys.argv[sys.argv.index('--limit') + 1]) if '--limit' in sys.argv else 0
    if '--only' in sys.argv: cfg['_only'] = sys.argv[sys.argv.index('--only') + 1]
    for p in (PHASES if ph == 'all' else [ph]):
        t = time.time(); print(f'== {p}', flush=True); PHASES[p](cfg, limit); print(f'== {p} done in {time.time() - t:.0f}s', flush=True)
