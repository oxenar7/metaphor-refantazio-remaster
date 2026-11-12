"""List the TOC of a CRI CPK archive (read-only). Usage: python -I cpk_toc.py <file.cpk> <out.tsv>"""
import struct, sys, collections

def read_utf(buf):
    assert buf[:4] == b"@UTF", buf[:4]
    size = struct.unpack(">I", buf[4:8])[0]
    t = buf[8:8 + size]
    _, rows_off, str_off, data_off, name_off, ncols, row_len, nrows = struct.unpack(">HHIIIHHI", t[:24])
    def cstr(off):
        s = str_off + off
        e = t.index(b"\0", s)
        return t[s:e].decode("utf-8", "replace")
    fmts = {0: ">B", 1: ">b", 2: ">H", 3: ">h", 4: ">I", 5: ">i", 6: ">Q", 7: ">q", 8: ">f", 9: ">d"}
    def read_val(typ, pos):
        if typ in fmts:
            f = fmts[typ]; n = struct.calcsize(f)
            return struct.unpack(f, t[pos:pos + n])[0], n
        if typ == 0xA:
            return cstr(struct.unpack(">I", t[pos:pos + 4])[0]), 4
        if typ == 0xB:
            o, l = struct.unpack(">II", t[pos:pos + 8])
            return t[data_off + o:data_off + o + l], 8
        raise ValueError(f"type {typ:#x}")
    cols, pos = [], 24
    for _ in range(ncols):
        flags = t[pos]; nm = cstr(struct.unpack(">I", t[pos + 1:pos + 5])[0]); pos += 5
        storage, typ = flags & 0xF0, flags & 0x0F
        const = None
        if storage == 0x30:
            const, n = read_val(typ, pos); pos += n
        cols.append((nm, storage, typ, const))
    rows = []
    for r in range(nrows):
        p = rows_off + r * row_len; row = {}
        for nm, storage, typ, const in cols:
            if storage == 0x50:
                v, n = read_val(typ, p); p += n; row[nm] = v
            elif storage == 0x30:
                row[nm] = const
            else:
                row[nm] = None
        rows.append(row)
    return rows

def main(path, out):
    with open(path, "rb") as f:
        f.seek(0x10); hdr = read_utf(f.read(0x800))[0]
        toc_off, toc_size = hdr.get("TocOffset"), hdr.get("TocSize")
        print({k: hdr.get(k) for k in ("ContentOffset", "TocOffset", "TocSize", "ItocOffset", "EtocOffset", "Files", "Align", "Version", "Revision", "Sorted")})
        if not toc_off:
            print("No TOC (ID-only archive)"); return
        f.seek(toc_off + 0x10); tbuf = f.read(toc_size)
    if tbuf[:4] != b"@UTF":
        print("TOC appears encrypted, first bytes:", tbuf[:8].hex()); return
    rows = read_utf(tbuf)
    base = min(toc_off, hdr.get("ContentOffset") or toc_off)
    ext_count, ext_size, dir_tex = collections.Counter(), collections.Counter(), collections.Counter()
    with open(out, "w", encoding="utf-8") as o:
        o.write("dir\tname\tsize\textract\toffset\n")
        for r in rows:
            d, n = r.get("DirName") or "", r.get("FileName") or ""
            ext = n.rsplit(".", 1)[-1].lower() if "." in n else "(none)"
            ext_count[ext] += 1; ext_size[ext] += r.get("ExtractSize") or r.get("FileSize") or 0
            top = d.split("/")[0] if d else "(root)"
            if ext in ("dds", "tex", "tmx", "gnf", "txd", "btx", "gtf"):
                dir_tex[top] += 1
            o.write(f"{d}\t{n}\t{r.get('FileSize')}\t{r.get('ExtractSize')}\t{(r.get('FileOffset') or 0) + base}\n")
    print(f"files: {len(rows)}")
    print("by extension (count, extracted MB):")
    for e, c in ext_count.most_common(30):
        print(f"  {e:10s} {c:7d} {ext_size[e] / 2**20:10.1f}")
    print("texture-like files by top dir:", dict(dir_tex.most_common(20)))

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
