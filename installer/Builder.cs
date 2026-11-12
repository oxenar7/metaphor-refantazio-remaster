// Metaphor: ReFantazio Remaster - installer core.
// Rebuilds the mod's .TEX files from the player's own base.cpk plus the release's data parts.
// Compiled at install time by Install.ps1 (Add-Type, C# 5 / .NET Framework 4.x): no executable is shipped.
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Security.Cryptography;
using System.Text;

namespace MetaphorRemaster
{
    public class Part { public int Index; public string Name; public long Size; public string Sha256; }
    public class Blob { public int Part; public long Offset; public int Stored; public int Raw; public int Method; }
    public class FileRec
    {
        public string Out, Base, BaseSha1, OutSha1, Category;
        public long BaseSize, OutSize; public int Count; public int[] Keys; public int[] Blobs;
    }
    public class CpkEntry { public long Offset; public long Size; public long ExtractSize; }

    public class BuildResult
    {
        public int Built, AlreadyThere, GameDiffers, Failed;
        public List<string> Messages = new List<string>();
    }

    public class Installer
    {
        public string Version, BaseUrl;
        public List<Part> Parts = new List<Part>();
        public List<Blob> Blobs = new List<Blob>();
        public List<FileRec> Files = new List<FileRec>();

        public static Installer Load(string manifestPath)
        {
            Installer m = new Installer();
            using (FileStream fs = File.OpenRead(manifestPath))
            using (GZipStream gz = new GZipStream(fs, CompressionMode.Decompress))
            using (StreamReader rd = new StreamReader(gz, Encoding.UTF8))
            {
                string head = rd.ReadLine();
                if (head != "METAPHOR-REMASTER-MANIFEST 1") throw new Exception("Unknown manifest format: " + head);
                string line;
                while ((line = rd.ReadLine()) != null)
                {
                    if (line.Length == 0) continue;
                    string[] t = line.Split(' ');
                    switch (t[0])
                    {
                        case "V": m.Version = t[1]; break;
                        case "U": m.BaseUrl = t[1]; break;
                        case "P":
                            Part p = new Part(); p.Index = int.Parse(t[1]); p.Name = t[2]; p.Size = long.Parse(t[3]); p.Sha256 = t[4];
                            m.Parts.Add(p); break;
                        case "B":
                            Blob b = new Blob(); b.Part = int.Parse(t[2]); b.Offset = long.Parse(t[3]); b.Stored = int.Parse(t[4]);
                            b.Raw = int.Parse(t[5]); b.Method = int.Parse(t[6]);
                            if (int.Parse(t[1]) != m.Blobs.Count) throw new Exception("Manifest blobs out of order");
                            m.Blobs.Add(b); break;
                        case "F":
                            FileRec f = new FileRec(); f.Out = t[1]; f.Base = t[2]; f.BaseSize = long.Parse(t[3]); f.BaseSha1 = t[4];
                            f.OutSize = long.Parse(t[5]); f.OutSha1 = t[6]; f.Category = t[7]; f.Count = int.Parse(t[8]);
                            if (t[9] == "-") { f.Keys = new int[0]; f.Blobs = new int[0]; }
                            else
                            {
                                string[] kv = t[9].Split(';'); f.Keys = new int[kv.Length]; f.Blobs = new int[kv.Length];
                                for (int i = 0; i < kv.Length; i++)
                                {
                                    int e = kv[i].IndexOf('='); f.Keys[i] = int.Parse(kv[i].Substring(0, e)); f.Blobs[i] = int.Parse(kv[i].Substring(e + 1));
                                }
                            }
                            m.Files.Add(f); break;
                    }
                }
            }
            return m;
        }

        // ---- selection helpers (used by Install.ps1) ----
        public Dictionary<string, long> CategorySizes()
        {
            Dictionary<string, long> d = new Dictionary<string, long>();
            foreach (FileRec f in Files) { long v; d.TryGetValue(f.Category, out v); d[f.Category] = v + f.OutSize; }
            return d;
        }

        public List<FileRec> Select(string[] skip)
        {
            HashSet<string> s = new HashSet<string>(skip ?? new string[0], StringComparer.OrdinalIgnoreCase);
            List<FileRec> r = new List<FileRec>();
            foreach (FileRec f in Files) if (!s.Contains(f.Category)) r.Add(f);
            return r;
        }

        public List<Part> PartsFor(List<FileRec> files)
        {
            bool[] need = new bool[Parts.Count];
            foreach (FileRec f in files) foreach (int b in f.Blobs) need[Blobs[b].Part] = true;
            List<Part> r = new List<Part>();
            for (int i = 0; i < Parts.Count; i++) if (need[i]) r.Add(Parts[i]);
            return r;
        }

        public static long SumOut(List<FileRec> files) { long s = 0; foreach (FileRec f in files) s += f.OutSize; return s; }

        // ---- data parts ----
        public static string Hex(byte[] h) { StringBuilder sb = new StringBuilder(); foreach (byte x in h) sb.Append(x.ToString("x2")); return sb.ToString(); }

        public static string Sha256File(string path, string label)
        {
            using (SHA256 sha = SHA256.Create())
            using (FileStream fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 20))
            {
                byte[] buf = new byte[1 << 22]; long done = 0, total = fs.Length; int n; DateTime last = DateTime.MinValue;
                while ((n = fs.Read(buf, 0, buf.Length)) > 0)
                {
                    sha.TransformBlock(buf, 0, n, null, 0); done += n;
                    if ((DateTime.Now - last).TotalMilliseconds > 500) { Console.Write("\r  checking " + label + "  " + (100 * done / Math.Max(1, total)) + "%   "); last = DateTime.Now; }
                }
                sha.TransformFinalBlock(buf, 0, 0); Console.Write("\r" + new string(' ', 70) + "\r");
                return Hex(sha.Hash);
            }
        }

        public void Download(Part p, string dataDir)
        {
            ServicePointManager.SecurityProtocol = (SecurityProtocolType)3072; // TLS 1.2
            string dest = Path.Combine(dataDir, p.Name), tmp = dest + ".download";
            string url = BaseUrl.TrimEnd('/') + "/" + p.Name;
            for (int attempt = 1; ; attempt++)
            {
                long have = File.Exists(tmp) ? new FileInfo(tmp).Length : 0;
                if (have > p.Size) { File.Delete(tmp); have = 0; }
                try
                {
                    if (have < p.Size)
                    {
                        HttpWebRequest rq = (HttpWebRequest)WebRequest.Create(url);
                        rq.UserAgent = "MetaphorRemasterInstaller/" + Version; rq.AllowAutoRedirect = true; rq.Timeout = 60000; rq.ReadWriteTimeout = 60000;
                        if (have > 0) rq.AddRange(have);
                        using (HttpWebResponse rs = (HttpWebResponse)rq.GetResponse())
                        {
                            bool partial = rs.StatusCode == HttpStatusCode.PartialContent;
                            if (have > 0 && !partial) have = 0; // server ignored the range: start over
                            using (Stream inp = rs.GetResponseStream())
                            using (FileStream o = new FileStream(tmp, have > 0 ? FileMode.Append : FileMode.Create, FileAccess.Write, FileShare.None, 1 << 20))
                            {
                                byte[] buf = new byte[1 << 20]; int n; DateTime t0 = DateTime.Now, last = DateTime.MinValue; long start = have;
                                while ((n = inp.Read(buf, 0, buf.Length)) > 0)
                                {
                                    o.Write(buf, 0, n); have += n;
                                    if ((DateTime.Now - last).TotalMilliseconds > 500)
                                    {
                                        double sec = Math.Max(0.5, (DateTime.Now - t0).TotalSeconds);
                                        Console.Write(string.Format("\r  {0}  {1:0.00} / {2:0.00} GB  {3:0.0} MB/s   ", p.Name, have / 1e9, p.Size / 1e9, (have - start) / 1e6 / sec));
                                        last = DateTime.Now;
                                    }
                                }
                            }
                        }
                        Console.WriteLine();
                    }
                    if (new FileInfo(tmp).Length != p.Size) throw new IOException("incomplete download");
                    string h = Sha256File(tmp, p.Name);
                    if (h != p.Sha256) { File.Delete(tmp); throw new IOException("checksum mismatch, downloading again"); }
                    if (File.Exists(dest)) File.Delete(dest);
                    File.Move(tmp, dest);
                    return;
                }
                catch (Exception e)
                {
                    Console.WriteLine();
                    if (attempt >= 6) throw new Exception("Download of " + p.Name + " failed: " + e.Message);
                    Console.WriteLine("  " + e.Message + " - retrying (" + attempt + "/5)...");
                    System.Threading.Thread.Sleep(3000 * attempt);
                }
            }
        }

        // ---- CPK table of contents ----
        static uint U32(byte[] b, int p) { return (uint)(b[p] << 24 | b[p + 1] << 16 | b[p + 2] << 8 | b[p + 3]); }
        static ushort U16(byte[] b, int p) { return (ushort)(b[p] << 8 | b[p + 1]); }

        static List<Dictionary<string, object>> ReadUtf(byte[] buf)
        {
            if (buf[0] != '@' || buf[1] != 'U' || buf[2] != 'T' || buf[3] != 'F') throw new Exception("base.cpk: unexpected table (encrypted or not a CPK?)");
            int size = (int)U32(buf, 4); byte[] t = new byte[size]; Array.Copy(buf, 8, t, 0, size);
            int rowsOff = U16(t, 2), strOff = (int)U32(t, 4), dataOff = (int)U32(t, 8);
            int ncols = U16(t, 16), rowLen = U16(t, 18); int nrows = (int)U32(t, 20);
            Func<int, string> cstr = delegate(int off) { int s = strOff + off, e = s; while (t[e] != 0) e++; return Encoding.UTF8.GetString(t, s, e - s); };
            Func<int, int, object[]> val = delegate(int typ, int pos)
            {
                switch (typ)
                {
                    case 0: return new object[] { (long)t[pos], 1 };
                    case 1: return new object[] { (long)(sbyte)t[pos], 1 };
                    case 2: return new object[] { (long)U16(t, pos), 2 };
                    case 3: return new object[] { (long)(short)U16(t, pos), 2 };
                    case 4: return new object[] { (long)U32(t, pos), 4 };
                    case 5: return new object[] { (long)(int)U32(t, pos), 4 };
                    case 6: case 7: return new object[] { (long)((ulong)U32(t, pos) << 32 | U32(t, pos + 4)), 8 };
                    case 8: return new object[] { 0L, 4 };
                    case 9: return new object[] { 0L, 8 };
                    case 0xA: return new object[] { cstr((int)U32(t, pos)), 4 };
                    case 0xB: return new object[] { null, 8 };
                }
                throw new Exception("base.cpk: unknown column type " + typ);
            };
            List<string> names = new List<string>(); List<int> storage = new List<int>(), types = new List<int>(); List<object> consts = new List<object>();
            int p = 24;
            for (int c = 0; c < ncols; c++)
            {
                int flags = t[p]; names.Add(cstr((int)U32(t, p + 1))); p += 5;
                storage.Add(flags & 0xF0); types.Add(flags & 0xF); object k = null;
                if ((flags & 0xF0) == 0x30) { object[] v = val(flags & 0xF, p); k = v[0]; p += (int)v[1]; }
                consts.Add(k);
            }
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            for (int r = 0; r < nrows; r++)
            {
                int q = rowsOff + r * rowLen; Dictionary<string, object> row = new Dictionary<string, object>();
                for (int c = 0; c < ncols; c++)
                {
                    if (storage[c] == 0x50) { object[] v = val(types[c], q); row[names[c]] = v[0]; q += (int)v[1]; }
                    else if (storage[c] == 0x30) row[names[c]] = consts[c];
                }
                rows.Add(row);
            }
            return rows;
        }

        static long L(Dictionary<string, object> r, string k) { object v; return r.TryGetValue(k, out v) && v is long ? (long)v : 0; }

        public static Dictionary<string, CpkEntry> ReadToc(string cpk)
        {
            using (FileStream f = new FileStream(cpk, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            {
                byte[] h = new byte[0x800]; f.Seek(0x10, SeekOrigin.Begin); f.Read(h, 0, h.Length);
                Dictionary<string, object> hdr = ReadUtf(h)[0];
                long tocOff = L(hdr, "TocOffset"), tocSize = L(hdr, "TocSize"), content = L(hdr, "ContentOffset");
                if (tocOff == 0) throw new Exception("base.cpk has no file table");
                byte[] tb = new byte[tocSize]; f.Seek(tocOff + 0x10, SeekOrigin.Begin);
                int got = 0; while (got < tb.Length) { int n = f.Read(tb, got, tb.Length - got); if (n <= 0) break; got += n; }
                long baseOff = content > 0 ? Math.Min(tocOff, content) : tocOff;
                Dictionary<string, CpkEntry> toc = new Dictionary<string, CpkEntry>(StringComparer.Ordinal);
                foreach (Dictionary<string, object> r in ReadUtf(tb))
                {
                    object d, n; r.TryGetValue("DirName", out d); r.TryGetValue("FileName", out n);
                    string path = ((d as string ?? "") + "/" + (n as string ?? "")).TrimStart('/');
                    CpkEntry e = new CpkEntry(); e.Offset = baseOff + L(r, "FileOffset"); e.Size = L(r, "FileSize");
                    e.ExtractSize = L(r, "ExtractSize"); if (e.ExtractSize == 0) e.ExtractSize = e.Size;
                    toc[path] = e;
                }
                return toc;
            }
        }

        // ---- build ----
        static void ReadFully(Stream s, byte[] b, int len) { int got = 0; while (got < len) { int n = s.Read(b, got, len - got); if (n <= 0) throw new EndOfStreamException(); got += n; } }

        static string Sha1File(string path)
        {
            using (SHA1 sha = SHA1.Create()) using (FileStream fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 20)) return Hex(sha.ComputeHash(fs));
        }

        public BuildResult Build(string cpk, string dataDir, string outRoot, List<FileRec> files)
        {
            BuildResult res = new BuildResult();
            Dictionary<string, CpkEntry> toc = ReadToc(cpk);
            files = new List<FileRec>(files);
            files.Sort(delegate(FileRec a, FileRec b)
            {
                CpkEntry x, y; long ox = toc.TryGetValue(a.Base, out x) ? x.Offset : 0, oy = toc.TryGetValue(b.Base, out y) ? y.Offset : 0;
                return ox.CompareTo(oy);
            });
            Dictionary<int, FileStream> parts = new Dictionary<int, FileStream>();
            long totalBytes = SumOut(files), doneBytes = 0; DateTime t0 = DateTime.Now, last = DateTime.MinValue; int i = 0;
            try
            {
                using (FileStream cf = new FileStream(cpk, FileMode.Open, FileAccess.Read, FileShare.ReadWrite, 1 << 16))
                {
                    foreach (FileRec f in files)
                    {
                        i++; doneBytes += f.OutSize;
                        if ((DateTime.Now - last).TotalMilliseconds > 400 || i == files.Count)
                        {
                            Console.Write(string.Format("\r  [{0}/{1}]  {2:0.0} / {3:0.0} GB   ", i, files.Count, doneBytes / 1e9, totalBytes / 1e9));
                            last = DateTime.Now;
                        }
                        string dest = Path.Combine(outRoot, f.Out.Replace('/', Path.DirectorySeparatorChar));
                        if (File.Exists(dest) && new FileInfo(dest).Length == f.OutSize && Sha1File(dest) == f.OutSha1) { res.AlreadyThere++; continue; }
                        CpkEntry e;
                        if (!toc.TryGetValue(f.Base, out e) || e.Size != f.BaseSize || e.ExtractSize != e.Size)
                        { res.GameDiffers++; res.Messages.Add("game file differs: " + f.Base); continue; }
                        byte[] baseBytes = new byte[e.Size]; cf.Seek(e.Offset, SeekOrigin.Begin); ReadFully(cf, baseBytes, baseBytes.Length);
                        using (SHA1 sh = SHA1.Create()) if (Hex(sh.ComputeHash(baseBytes)) != f.BaseSha1) { res.GameDiffers++; res.Messages.Add("game file differs: " + f.Base); continue; }
                        Directory.CreateDirectory(Path.GetDirectoryName(dest));
                        string tmp = dest + ".tmp";
                        string outSha;
                        using (SHA1 sha = SHA1.Create())
                        {
                            using (FileStream o = new FileStream(tmp, FileMode.Create, FileAccess.Write, FileShare.None, 1 << 20))
                            using (CryptoStream cs = new CryptoStream(o, sha, CryptoStreamMode.Write))
                            {
                                int n = (int)U32(baseBytes, 0); if (n != f.Count) throw new Exception(f.Base + ": unexpected entry count");
                                cs.Write(baseBytes, 0, 4);
                                int p = 4, j = 0;
                                for (int k = 0; k < n; k++)
                                {
                                    int sz = (int)U32(baseBytes, p + 256);
                                    if (j < f.Keys.Length && f.Keys[j] == k)
                                    {
                                        byte[] d = ReadBlob(f.Blobs[j], dataDir, parts); j++;
                                        cs.Write(baseBytes, p, 256);
                                        byte[] len = { (byte)(d.Length >> 24), (byte)(d.Length >> 16), (byte)(d.Length >> 8), (byte)d.Length };
                                        cs.Write(len, 0, 4); cs.Write(d, 0, d.Length);
                                    }
                                    else cs.Write(baseBytes, p, 260 + sz);
                                    p += 260 + sz;
                                }
                                cs.FlushFinalBlock();
                                outSha = Hex(sha.Hash);
                            }
                        }
                        if (outSha != f.OutSha1 || new FileInfo(tmp).Length != f.OutSize)
                        {
                            File.Delete(tmp); res.Failed++; res.Messages.Add("checksum mismatch (damaged data part?): " + f.Out); continue;
                        }
                        if (File.Exists(dest)) File.Delete(dest);
                        File.Move(tmp, dest); res.Built++;
                    }
                }
            }
            finally { foreach (FileStream s in parts.Values) s.Dispose(); }
            Console.WriteLine();
            res.Messages.Add(string.Format("time: {0:0} min", (DateTime.Now - t0).TotalMinutes));
            return res;
        }

        byte[] ReadBlob(int id, string dataDir, Dictionary<int, FileStream> open)
        {
            Blob b = Blobs[id]; FileStream fs;
            if (!open.TryGetValue(b.Part, out fs))
            {
                fs = new FileStream(Path.Combine(dataDir, Parts[b.Part].Name), FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 16);
                open[b.Part] = fs;
            }
            byte[] stored = new byte[b.Stored]; fs.Seek(b.Offset, SeekOrigin.Begin); ReadFully(fs, stored, stored.Length);
            if (b.Method == 0) return stored;
            byte[] raw = new byte[b.Raw];
            using (DeflateStream ds = new DeflateStream(new MemoryStream(stored), CompressionMode.Decompress)) ReadFully(ds, raw, raw.Length);
            return raw;
        }
    }
}
