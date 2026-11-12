"""Build the release data for the installer from a finished mod build.

For every .TEX the mod ships, the entries that differ from the game's own file become "blobs" (deduplicated by
content, raw-deflate compressed, packed into part files below the host's per-file limit). The manifest says how to
rebuild each file from the player's own base.cpk plus those blobs, with SHA-1 checks of the base and of the result.
No unmodified game data ends up in the parts.

usage: python -I make_release.py <base.cpk> <out dir> <version> <download base url> <mod CPK dir> [<mod CPK dir> ...]
       (each <mod CPK dir> is a folder whose sub-paths mirror base.cpk, e.g. .../MFEssentials/CPK/Upscale)
"""
import sys, os, struct, hashlib, zlib, gzip, json, time, collections
from multiprocessing import Pool

PART_MAX = 1_400_000_000          # below GameBanana's ~1.46 GB per-file limit (GitHub allows 2 GB)
CAT_ORDER = ['field', 'vehicle', 'archetype', 'item', 'weapon', 'character', 'enemy']


def read_utf(buf):
    assert buf[:4] == b'@UTF', buf[:4]
    size = struct.unpack('>I', buf[4:8])[0]; t = buf[8:8 + size]
    _, rows_off, str_off, data_off, _, ncols, row_len, nrows = struct.unpack('>HHIIIHHI', t[:24])
    def cstr(off):
        s = str_off + off; return t[s:t.index(b'\0', s)].decode('utf-8', 'replace')
    fmts = {0: '>B', 1: '>b', 2: '>H', 3: '>h', 4: '>I', 5: '>i', 6: '>Q', 7: '>q', 8: '>f', 9: '>d'}
    def val(typ, pos):
        if typ in fmts:
            n = struct.calcsize(fmts[typ]); return struct.unpack(fmts[typ], t[pos:pos + n])[0], n
        if typ == 0xA: return cstr(struct.unpack('>I', t[pos:pos + 4])[0]), 4
        if typ == 0xB:
            o, l = struct.unpack('>II', t[pos:pos + 8]); return t[data_off + o:data_off + o + l], 8
        raise ValueError(typ)
    cols, pos = [], 24
    for _ in range(ncols):
        flags = t[pos]; nm = cstr(struct.unpack('>I', t[pos + 1:pos + 5])[0]); pos += 5
        const = None
        if flags & 0xF0 == 0x30: const, n = val(flags & 0xF, pos); pos += n
        cols.append((nm, flags & 0xF0, flags & 0xF, const))
    rows = []
    for r in range(nrows):
        p = rows_off + r * row_len; row = {}
        for nm, st, typ, const in cols:
            if st == 0x50: row[nm], n = val(typ, p); p += n
            elif st == 0x30: row[nm] = const
        rows.append(row)
    return rows


def load_toc(cpk):
    with open(cpk, 'rb') as f:
        f.seek(0x10); hdr = read_utf(f.read(0x800))[0]
        toc_off, toc_size = hdr['TocOffset'], hdr['TocSize']
        f.seek(toc_off + 0x10); rows = read_utf(f.read(toc_size))
    base = min(toc_off, hdr.get('ContentOffset') or toc_off); toc = {}
    for r in rows:
        p = (r.get('DirName') or '') + '/' + r['FileName']
        toc[p.lstrip('/')] = (base + r['FileOffset'], r['FileSize'], r.get('ExtractSize') or r['FileSize'])
    return toc


def parse_tex(b):
    n = struct.unpack('>I', b[:4])[0]; p = 4; out = []
    for _ in range(n):
        sz = struct.unpack('>I', b[p + 256:p + 260])[0]
        out.append((p, b[p:p + 256], p + 260, sz)); p += 260 + sz
    if p != len(b): raise ValueError('trailing bytes')
    return out


def analyse(job):
    """One mod file -> its rebuild recipe (no data, only hashes and where the new entries live in the mod file)."""
    out_path, src, cpk, cand = job
    mod = open(src, 'rb').read(); me = parse_tex(mod)
    with open(cpk, 'rb') as f:
        best = None
        for base_path, (off, size, ext) in cand:
            if size != ext: continue                       # compressed in the CPK: not handled
            f.seek(off); base = f.read(size)
            try: be = parse_tex(base)
            except Exception: continue
            if len(be) != len(me) or any(base[a[0]:a[0] + 256] != mod[b[0]:b[0] + 256] for a, b in zip(be, me)): continue
            same = sum(base[a[2]:a[2] + a[3]] == mod[b[2]:b[2] + b[3]] for a, b in zip(be, me))
            if best is None or same > best[0]: best = (same, base_path, base, be)
    if best is None: return (out_path, None)
    _, base_path, base, be = best
    ents = []
    for k, (a, b) in enumerate(zip(be, me)):
        d = mod[b[2]:b[2] + b[3]]
        if base[a[2]:a[2] + a[3]] != d: ents.append((k, hashlib.sha1(d).hexdigest(), b[2], b[3]))
    return (out_path, dict(base=base_path, base_size=len(base), base_sha1=hashlib.sha1(base).hexdigest(),
                           out_size=len(mod), out_sha1=hashlib.sha1(mod).hexdigest(), n=len(me), ents=ents, src=src))


def pack_blob(job):
    src, off, ln = job
    with open(src, 'rb') as f:
        f.seek(off); d = f.read(ln)
    c = zlib.compressobj(6, zlib.DEFLATED, -15); z = c.compress(d) + c.flush()
    return (1, z) if len(z) < 0.98 * len(d) else (0, d)


def category(p):
    s = p.split('/'); return s[2] if len(s) > 3 and s[1] == 'model' else s[1]


def main():
    cpk, outdir, version, url = sys.argv[1:5]; roots = sys.argv[5:]
    os.makedirs(outdir, exist_ok=True); t0 = time.time()
    toc = load_toc(cpk)
    jobs, seen = [], {}
    for root in roots:
        for r, _, fs in os.walk(root):
            for x in fs:
                src = os.path.join(r, x); rel = os.path.relpath(src, root).replace('\\', '/')
                if rel in seen: sys.exit(f'{rel} is shipped by two mods ({seen[rel]} and {root})')
                seen[rel] = root
                cand = [c for c in (rel, 'COMMON/' + rel[5:] if rel.startswith('XBOX/') else None) if c and c in toc]
                if not cand: sys.exit(f'{rel}: not in base.cpk')
                jobs.append((rel, src, cpk, [(c, toc[c]) for c in cand]))
    print(len(jobs), 'mod files', flush=True)
    recs = {}
    with Pool(8) as pool:
        for i, (rel, rec) in enumerate(pool.imap_unordered(analyse, jobs, chunksize=4)):
            if rec is None: sys.exit(f'{rel}: no matching base file (entry names differ)')
            recs[rel] = rec
            if i % 500 == 0: print(f'  analysed {i}/{len(jobs)}  {time.time() - t0:.0f}s', flush=True)
    # unique blobs, ordered by the first category that needs them (field first) so partial installs need fewer parts
    order = sorted(recs, key=lambda p: (CAT_ORDER.index(category(p)) if category(p) in CAT_ORDER else 99, p))
    blobs, blist = {}, []
    for p in order:
        for k, h, off, ln in recs[p]['ents']:
            if h not in blobs: blobs[h] = len(blist); blist.append((h, recs[p]['src'], off, ln))
    raw_total = sum(b[3] for b in blist)
    print(f'{len(blist)} unique new entries, {raw_total / 1e9:.2f} GB raw', flush=True)
    parts, binfo, cur, cur_size = [], [None] * len(blist), None, 0
    def close_part():
        if cur: cur[1].close(); parts.append((cur[0], cur_size, cur[2].hexdigest()))
    with Pool(10) as pool:
        for i, (meth, data) in enumerate(pool.imap(pack_blob, [(s, o, l) for _, s, o, l in blist], chunksize=8)):
            if cur is None or cur_size + len(data) > PART_MAX:
                close_part()
                name = f'MetaphorRemaster-{version}.part{len(parts) + 1:02d}.bin'
                cur = (name, open(os.path.join(outdir, name), 'wb'), hashlib.sha256()); cur_size = 0
            cur[1].write(data); cur[2].update(data)
            binfo[i] = (len(parts), cur_size, len(data), blist[i][3], meth); cur_size += len(data)
            if i % 2000 == 0: print(f'  packed {i}/{len(blist)}  {time.time() - t0:.0f}s', flush=True)
        close_part()
    lines = ['METAPHOR-REMASTER-MANIFEST 1', f'V {version}', f'U {url}']
    for i, (n, s, h) in enumerate(parts): lines.append(f'P {i} {n} {s} {h}')
    for i, (h, *_ ) in enumerate(blist):
        pi, off, sl, rl, meth = binfo[i]; lines.append(f'B {i} {pi} {off} {sl} {rl} {meth} {h}')
    for p in sorted(recs):
        r = recs[p]; e = ';'.join(f'{k}={blobs[h]}' for k, h, _, _ in r['ents']) or '-'
        lines.append(f"F {p} {r['base']} {r['base_size']} {r['base_sha1']} {r['out_size']} {r['out_sha1']} {category(p)} {r['n']} {e}")
    with gzip.open(os.path.join(outdir, 'manifest.txt.gz'), 'wt', encoding='utf-8', newline='\n') as g: g.write('\n'.join(lines) + '\n')
    cats = collections.Counter(); cnt = collections.Counter()
    for p, r in recs.items(): cats[category(p)] += r['out_size']; cnt[category(p)] += 1
    summary = dict(version=version, files=len(recs), out_bytes=sum(r['out_size'] for r in recs.values()),
                   blobs=len(blist), raw_bytes=raw_total, packed_bytes=sum(s for _, s, _ in parts),
                   parts=[dict(name=n, size=s, sha256=h) for n, s, h in parts],
                   by_category={c: dict(files=cnt[c], out_bytes=cats[c]) for c in cats})
    json.dump(summary, open(os.path.join(outdir, 'release_summary.json'), 'w'), indent=1)
    print(json.dumps({k: v for k, v in summary.items() if k != 'parts'}, indent=1))
    print(f'{len(parts)} parts, done in {time.time() - t0:.0f}s')


if __name__ == '__main__':
    main()
