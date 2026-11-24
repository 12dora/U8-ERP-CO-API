using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text.RegularExpressions;

namespace U8Co
{
    // 审计日志按日文件的压缩：config.json 的 auditLogCompressAfterDays（缺省 0 不压缩）。
    internal static class AuditLogCompression
    {
        public const string ConfigKey = "auditLogCompressAfterDays";
        public const int DefaultDays = 0;
        public const int MaxDays = 3650;
        static readonly Regex NamePattern = new Regex(
            "^u8co-(\\d{8})\\.log$", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
        static readonly Regex TmpPattern = new Regex(
            "^u8co-\\d{8}\\.log\\.gz\\.tmp$", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

        public static void Load(BridgeConfig cfg, Dictionary<string, object> map)
        {
            cfg.AuditLogCompressAfterDays = BridgeConfig.OptionalInt(
                map, ConfigKey, DefaultDays, 0, MaxDays);
        }

        public static string Describe(BridgeConfig cfg)
        {
            int days = cfg == null ? DefaultDays : cfg.AuditLogCompressAfterDays;
            if (days == 0)
            {
                return "审计日志压缩：不压缩";
            }
            return "审计日志压缩：" + days.ToString(CultureInfo.InvariantCulture) + " 天后";
        }

        internal static int Compress(string dir, int days, DateTime today,
            out long before, out long after, out int conflicts)
        {
            before = 0;
            after = 0;
            conflicts = 0;
            if (dir == null || dir.Length == 0 || days <= 0)
            {
                return 0;
            }
            string[] files;
            try
            {
                dir = TrimDir(dir);
                if (dir.Length == 0 || !Directory.Exists(dir))
                {
                    return 0;
                }
                if ((File.GetAttributes(dir) & FileAttributes.ReparsePoint) != 0)
                {
                    return 0;
                }
                files = Directory.GetFiles(dir);
            }
            catch (Exception)
            {
                return 0;
            }
            DropStale(files);
            return CompressDue(files, today.Date.AddDays(-days), ref before, ref after, ref conflicts);
        }

        static int CompressDue(string[] files, DateTime cutoff,
            ref long before, ref long after, ref int conflicts)
        {
            int count = 0;
            for (int i = 0; i < files.Length; i++)
            {
                long orig;
                long gz;
                bool conflict;
                if (TryOne(files[i], cutoff, out orig, out gz, out conflict))
                {
                    count++;
                    before += orig;
                    after += gz;
                }
                if (conflict)
                {
                    conflicts++;
                }
            }
            return count;
        }

        static bool TryOne(string path, DateTime cutoff, out long orig, out long gz, out bool conflict)
        {
            orig = 0;
            gz = 0;
            conflict = false;
            try
            {
                if (!Due(path, cutoff))
                {
                    return false;
                }
                return GzipReplace(path, out orig, out gz, out conflict);
            }
            catch (Exception)
            {
                return false;
            }
        }

        static bool Due(string path, DateTime cutoff)
        {
            FileAttributes attr = File.GetAttributes(path);
            if ((attr & FileAttributes.ReparsePoint) != 0 || (attr & FileAttributes.Directory) != 0)
            {
                return false;
            }
            Match m = NamePattern.Match(Path.GetFileName(path));
            if (!m.Success)
            {
                return false;
            }
            DateTime fileDate;
            if (!DateTime.TryParseExact(m.Groups[1].Value, "yyyyMMdd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out fileDate))
            {
                return false;
            }
            return fileDate < cutoff;
        }

        static bool GzipReplace(string path, out long orig, out long gz, out bool conflict)
        {
            orig = 0;
            gz = 0;
            conflict = false;
            string tmp = path + ".gz.tmp";
            try
            {
                DateTime mtime = File.GetLastWriteTimeUtc(path);
                long origLen = new FileInfo(path).Length;
                byte[] hash;
                WriteTmp(path, tmp, out orig, out hash);
                if (!Verified(tmp, orig, hash))
                {
                    Drop(tmp);
                    return false;
                }
                if (!Finish(path, tmp, mtime, origLen, hash, out conflict))
                {
                    return false;
                }
                gz = new FileInfo(path + ".gz").Length;
                return true;
            }
            catch (Exception)
            {
                Drop(tmp);
                return false;
            }
        }

        static void WriteTmp(string src, string tmp, out long length, out byte[] hash)
        {
            using (FileStream input = new FileStream(src, FileMode.Open, FileAccess.Read,
                FileShare.ReadWrite | FileShare.Delete))
            using (SHA256 sha = SHA256.Create())
            using (FileStream output = new FileStream(tmp, FileMode.Create, FileAccess.Write, FileShare.None))
            {
                using (GZipStream gzip = new GZipStream(output, CompressionMode.Compress, true))
                {
                    length = CopyHash(input, gzip, sha);
                    hash = (byte[])sha.Hash.Clone();
                }
                output.Flush(true);
            }
        }

        static bool Verified(string tmp, long length, byte[] hash)
        {
            using (FileStream input = new FileStream(tmp, FileMode.Open, FileAccess.Read, FileShare.Read))
            using (GZipStream gzip = new GZipStream(input, CompressionMode.Decompress))
            using (SHA256 sha = SHA256.Create())
            {
                long seen = CopyHash(gzip, Stream.Null, sha);
                return seen == length && Same(hash, sha.Hash);
            }
        }

        static long CopyHash(Stream src, Stream dest, SHA256 sha)
        {
            byte[] buf = new byte[81920];
            long total = 0;
            int n;
            while ((n = src.Read(buf, 0, buf.Length)) > 0)
            {
                sha.TransformBlock(buf, 0, n, buf, 0);
                dest.Write(buf, 0, n);
                total += n;
            }
            sha.TransformFinalBlock(buf, 0, 0);
            return total;
        }

        static bool Finish(string path, string tmp, DateTime mtime, long origLen, byte[] hash, out bool conflict)
        {
            conflict = false;
            string dest = path + ".gz";
            if (File.Exists(dest))
            {
                return FinishExisting(path, tmp, origLen, mtime, hash, out conflict);
            }
            if (!Unchanged(path, origLen, mtime))
            {
                Drop(tmp);
                return false;
            }
            File.Move(tmp, dest);
            File.SetLastWriteTimeUtc(dest, mtime);
            if (!Unchanged(path, origLen, mtime))
            {
                Drop(dest);
                return false;
            }
            File.Delete(path);
            return true;
        }

        static bool FinishExisting(string path, string tmp, long origLen, DateTime mtime, byte[] hash, out bool conflict)
        {
            conflict = false;
            string dest = path + ".gz";
            Drop(tmp);
            if (!SameGzip(dest, origLen, hash))
            {
                if (Unchanged(path, origLen, mtime))
                {
                    conflict = true;
                }
                return false;
            }
            if (!Unchanged(path, origLen, mtime))
            {
                return false;
            }
            File.Delete(path);
            return true;
        }

        static bool Unchanged(string path, long length, DateTime mtime)
        {
            try
            {
                FileInfo info = new FileInfo(path);
                return info.Length == length && info.LastWriteTimeUtc == mtime;
            }
            catch (Exception)
            {
                return false;
            }
        }

        static bool SameGzip(string gz, long length, byte[] hash)
        {
            try
            {
                return Verified(gz, length, hash);
            }
            catch (Exception)
            {
                return false;
            }
        }

        static void DropStale(string[] files)
        {
            for (int i = 0; i < files.Length; i++)
            {
                TryDropStale(files[i]);
            }
        }

        static void TryDropStale(string path)
        {
            try
            {
                FileAttributes attr = File.GetAttributes(path);
                if ((attr & FileAttributes.ReparsePoint) != 0)
                {
                    return;
                }
                if (TmpPattern.IsMatch(Path.GetFileName(path)))
                {
                    File.Delete(path);
                }
            }
            catch (Exception)
            {
            }
        }

        static void Drop(string path)
        {
            try
            {
                File.Delete(path);
            }
            catch (Exception)
            {
            }
        }

        static bool Same(byte[] a, byte[] b)
        {
            if (a == null || b == null || a.Length != b.Length)
            {
                return false;
            }
            for (int i = 0; i < a.Length; i++)
            {
                if (a[i] != b[i])
                {
                    return false;
                }
            }
            return true;
        }

        static string TrimDir(string dir)
        {
            return dir.TrimEnd('\\', '/');
        }
    }
}
