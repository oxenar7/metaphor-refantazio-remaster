"""Check every TEX a mod ships against the game's original in base.cpk.
usage: python -I validate_mod.py <cpk> <cpk_toc.tsv> <mod CPK folder (holds COMMON/, XBOX/)>
Per file: same entry count and names; each entry is either unchanged or a valid DX10 DDS whose byte size matches
its width/height/mip count/format (BC7 or RGBA8). Reports size changes (2x upscales) and any problems.
"""
import sys, os, struct, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from upscale_all import read_tex, ename, dds_info, load_toc

BPB = {98: 16, 99: 16, 71: 8, 72: 8, 74: 16, 75: 16, 77: 16, 78: 16, 28: -4, 29: -4, 87: -4}  # block bytes; -4 = 4 bytes/pixel

def expected_size(inf):
    b = BPB.get(inf['fmt']); w, h = inf['w'], inf['h']; tot = 0
    if b is None: return None
    for _ in range(inf['mips']):
        tot += w * h * 4 if b < 0 else max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * b
        w, h = max(1, w // 2), max(1, h // 2)
    return tot + 148

def main():
    cpk_p, toc_p, root = sys.argv[1:4]
    toc = load_toc(toc_p); cpk = open(cpk_p, 'rb'); probs = []; stats = collections.Counter()
    for dp, _, fs in os.walk(root):
        for fn in fs:
            if not fn.upper().endswith('.TEX'): continue
            path = os.path.join(dp, fn); rel = os.path.relpath(path, root).replace('\\', '/')
            if rel not in toc: probs.append(f'{rel}: not in game TOC'); continue
            size, off = toc[rel]; cpk.seek(off)
            try: orig = read_tex(cpk.read(size)); new = read_tex(open(path, 'rb').read())
            except Exception as e: probs.append(f'{rel}: parse error {e}'); continue
            stats['files'] += 1
            if len(orig) != len(new): probs.append(f'{rel}: entry count {len(orig)} -> {len(new)}'); continue
            for (ro, do), (rn, dn) in zip(orig, new):
                if ro != rn: probs.append(f'{rel}: name changed {ename(ro)} -> {ename(rn)}'); continue
                if do == dn: stats['same'] += 1; continue
                io, inn = dds_info(do), dds_info(dn)
                if not inn: probs.append(f'{rel}/{ename(rn)}: not a DDS'); continue
                exp = expected_size(inn)
                if exp is not None and not exp <= len(dn) < exp + 32: probs.append(f'{rel}/{ename(rn)}: size {len(dn)} != expected {exp} ({inn})'); continue
                if io and inn['fmt'] != io['fmt']: stats[f'fmt {io["fmt"]}->{inn["fmt"]}'] += 1
                stats['changed'] += 1
                if io: stats[f'{io["w"]}->{inn["w"]}'] += 1
    print(dict(stats)); print(f'{len(probs)} problems')
    for p in probs[:40]: print('  ', p)

if __name__ == '__main__':
    main()
