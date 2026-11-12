"""Pass 8b: Grand Trad slots that ship a cavity version of a LOW-res copy (best_source.py kept them, because the big
sibling only had grain) -> add those big siblings (colour + paired normal / height, and big normal siblings) to
cavity_sel.json WITHOUT their own files (so cavity_index.json does not change: only the best-source mapping uses them),
then: cavity.py decode, cavity.py build --only <their names>, encode just the new ones here.

usage: python -I cavity_bigs.py <cfg.json> add | encode
"""
import sys, os, re, json, shutil, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from upscale_all import read_tex, ename, dds_info, load_toc, texconv_batch, W
from cavity import pair, sha12
from best_source import final_of

def phase_add(cfg):
    ix = json.load(open(W(cfg, 'index.json'))); toc = load_toc(cfg['toc']); cpk = open(cfg['cpk'], 'rb')
    pairs = json.load(open(W(cfg, 'best_pairs.json'))); excl = set(cfg.get('exclude_files', []))
    lay = tuple(json.load(open(W(cfg, n))) if os.path.exists(W(cfg, n)) else {} for n in ('cavity_index.json', 'special_index.json', 'grain_index.json'))
    sel = json.load(open(W(cfg, 'cavity_sel.json')))
    want = {}  # big sha -> kind
    for p in pairs:
        bp, bk = final_of(cfg, p['big'], lay=lay)
        if bk == 'cavity': continue
        if any(f not in excl and final_of(cfg, p['small'], f, k, lay)[1] == 'cavity' for f, k in p['slots']): want[p['big']] = p
    where = collections.defaultdict(list)
    for f, ps in ix['files'].items():
        for k, s in ps: where[s].append((f, k))
    added = collections.Counter(); names = set()
    for b, p in want.items():
        if p['kind'] == 'colour':
            if b in sel['colour'] or not where[b]: continue
            f, k = where[b][0]; size, off = toc[f]; cpk.seek(off); ents = read_tex(cpk.read(size))
            ns = hs = None
            for kk, kind in ((pair(ix['uniq'][b]['name'], ents, 'normal'), 'normal'), (pair(ix['uniq'][b]['name'], ents, 'height'), 'height')):
                if kk is None: continue
                d = ents[kk][1]; inf = dds_info(d); ss = sha12(d)
                if not inf or inf.get('arr', 1) != 1: continue
                q = W(cfg, 'cavity_src', ss + '.dds')
                if not os.path.exists(q): open(q, 'wb').write(d)
                sel[kind].setdefault(ss, dict(name=ename(ents[kk][0]), w=inf['w'], h=inf['h'], mips=inf['mips'], fmt=inf['fmt']))
                if kind == 'normal': ns = ss
                else: hs = ss
            u = ix['uniq'][b]; sel['colour'][b] = dict(name=u['name'], w=u['w'], h=u['h'], mips=u['mips'], normal=ns, height=hs, big_of_best=True)
            added['colour'] += 1; names.add(u['name'])
        else:
            if b in sel['normal']: continue
            src = W(cfg, 'best_src', b + '.dds'); d = open(src, 'rb').read(); inf = dds_info(d)
            shutil.copy(src, W(cfg, 'cavity_src', b + '.dds'))
            sel['normal'][b] = dict(name=p['big_name'], w=inf['w'], h=inf['h'], mips=inf['mips'], fmt=inf['fmt'], big_of_best=True); added['normal'] += 1
    json.dump(sel, open(W(cfg, 'cavity_sel.json'), 'w'), indent=0)
    rx = '^(' + '|'.join(re.escape(n) for n in sorted(names)) + ')$'
    open(W(cfg, 'cavity_bigs_only.txt'), 'w').write(rx)
    print('added', dict(added), 'of', len(want), 'wanted; --only regex in cavity_bigs_only.txt')

def phase_encode(cfg):
    from PIL import Image
    sel = json.load(open(W(cfg, 'cavity_sel.json'))); groups = collections.defaultdict(list)
    for s, u in {**sel['colour'], **sel['normal']}.items():
        if not u.get('big_of_best'): continue
        p = W(cfg, 'cavity_out', s + '.png')
        if not os.path.exists(p): continue
        w = Image.open(p).size[0]; m = u['mips'] + (1 if w == 2 * u['w'] else 2 if w == 4 * u['w'] else 0)
        groups[m].append(p)
    for m, lst in sorted(groups.items()):
        print(f'encode mips={m}: {len(lst)}', flush=True); texconv_batch(cfg, lst, W(cfg, 'cavity'), ['-f', 'BC7_UNORM', '-m', str(m)] + cfg.get('bc7_args', []))

if __name__ == '__main__':
    cfg = json.load(open(sys.argv[1])); dict(add=phase_add, encode=phase_encode)[sys.argv[2]](cfg)
