"""Extract / rebuild Metaphor .TEX containers.
TEX = u32be count, then count x { char name[256]; u32be size; DDS bytes[size] }

  extract <base.cpk> <cpk_toc.tsv> <out_dir> <cpk_path> [<cpk_path> ...]
      -> out_dir/<cpk_path>/NN__<entry name>.dds + manifest.json
  pack <entries_dir> <out.TEX>
      -> rebuild from manifest.json, using replacement DDS from entries_dir/replaced/ when present
"""
import sys, os, json, struct

def read_toc(toc):
    idx = {}
    with open(toc, encoding="utf-8") as t:
        next(t)
        for line in t:
            d, n, size, ext, off = line.rstrip("\n").split("\t")
            idx[f"{d}/{n}"] = (int(size), int(off))
    return idx

def safe(name):
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)

def extract(cpk, toc, out, paths):
    idx = read_toc(toc)
    with open(cpk, "rb") as f:
        for p in paths:
            size, off = idx[p]
            f.seek(off); blob = f.read(size)
            cnt = struct.unpack(">I", blob[:4])[0]
            pos, entries = 4, []
            d = os.path.join(out, *p.split("/"))
            os.makedirs(d, exist_ok=True)
            for i in range(cnt):
                name = blob[pos:pos + 256].split(b"\0")[0].decode("latin1")
                raw_name = blob[pos:pos + 256]
                esz = struct.unpack(">I", blob[pos + 256:pos + 260])[0]
                data = blob[pos + 260:pos + 260 + esz]
                fn = f"{i:02d}__{safe(name)}.dds"
                with open(os.path.join(d, fn), "wb") as o:
                    o.write(data)
                entries.append({"index": i, "name": name, "raw_name_hex": raw_name.hex(), "file": fn, "size": esz})
                pos += 260 + esz
            trailing = blob[pos:]
            json.dump({"cpk_path": p, "count": cnt, "entries": entries, "trailing_hex": trailing.hex()},
                      open(os.path.join(d, "manifest.json"), "w"), indent=1)
            print(f"{p}: {cnt} entries, trailing {len(trailing)} bytes")

def pack(entries_dir, out_path):
    m = json.load(open(os.path.join(entries_dir, "manifest.json")))
    rep_dir = os.path.join(entries_dir, "replaced")
    parts = [struct.pack(">I", m["count"])]
    replaced = 0
    for e in m["entries"]:
        src = os.path.join(rep_dir, e["file"])
        if os.path.exists(src):
            replaced += 1
        else:
            src = os.path.join(entries_dir, e["file"])
        data = open(src, "rb").read()
        parts.append(bytes.fromhex(e["raw_name_hex"]))
        parts.append(struct.pack(">I", len(data)))
        parts.append(data)
    parts.append(bytes.fromhex(m.get("trailing_hex", "")))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as o:
        o.write(b"".join(parts))
    print(f"packed {out_path}: {m['count']} entries, {replaced} replaced, {os.path.getsize(out_path)} bytes")

if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "extract":
        extract(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:])
    elif cmd == "pack":
        pack(sys.argv[2], sys.argv[3])
