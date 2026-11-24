using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Text;
using System.Web.Script.Serialization;

namespace U8Co
{
    // --selftest 的审计日志压缩：配置键范围、按日 gzip 与校验、残留 .gz/.tmp。不连 U8；临时目录跑完删掉。
    internal static class AuditLogCompressionSelfTest
    {
        const string Key = AuditLogCompression.ConfigKey;
        const string RangeMsg = "auditLogCompressAfterDays 必须是 0 到 3650 的整数";
        static readonly DateTime Today = new DateTime(2026, 11, 16);
        static readonly string[] Kept = new string[]
        {
            "u8co-20261109.log", "u8co-20261116.log", "u8co-20261201.log",
            "u8co-2026111.log", "other.log", "u8co-20261301.log"
        };

        public static void Run()
        {
            CheckConfig();
            CheckFiles();
        }

        static void CheckConfig()
        {
            BridgeConfig cfg = new BridgeConfig();
            Expect("field default", cfg.AuditLogCompressAfterDays == 0);
            AuditLogCompression.Load(cfg, Map("{}"));
            Expect("missing key", cfg.AuditLogCompressAfterDays == 0);
            AuditLogCompression.Load(cfg, Map("{\"" + Key + "\":0}"));
            Expect("zero", cfg.AuditLogCompressAfterDays == 0);
            AuditLogCompression.Load(cfg, Map("{\"" + Key + "\":7}"));
            Expect("seven", cfg.AuditLogCompressAfterDays == 7);
            Expect("describe", AuditLogCompression.Describe(cfg) == "审计日志压缩：7 天后");
            AuditLogCompression.Load(cfg, Map("{\"" + Key + "\":3650}"));
            Expect("max", cfg.AuditLogCompressAfterDays == 3650);
            BridgeConfig.CheckKeys(Map("{\"" + Key + "\":7}"));
            cfg.AuditLogCompressAfterDays = 0;
            Expect("describe off", AuditLogCompression.Describe(cfg) == "审计日志压缩：不压缩");
            Bad("-1", "{\"" + Key + "\":-1}");
            Bad("over", "{\"" + Key + "\":3651}");
            Bad("string", "{\"" + Key + "\":\"7\"}");
            Bad("bool", "{\"" + Key + "\":true}");
        }

        static void CheckFiles()
        {
            string dir = Path.Combine(Path.GetTempPath(), "u8co-loggz-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(dir);
            try
            {
                CheckCompress(dir);
                CheckLeftovers(dir);
                CheckConflict(dir);
                CheckZero(dir);
            }
            finally
            {
                try
                {
                    Directory.Delete(dir, true);
                }
                catch (Exception)
                {
                }
            }
        }

        static void CheckCompress(string dir)
        {
            byte[] original = Encoding.UTF8.GetBytes("20261108 审计日志\n第二行 UTF-8 中文\n第三行\n");
            string path = Path.Combine(dir, "u8co-20261108.log");
            File.WriteAllBytes(path, original);
            DateTime mtime = File.GetLastWriteTimeUtc(path);
            SeedKept(dir);
            long before;
            long after;
            int conflicts;
            int count = AuditLogCompression.Compress(dir, 7, Today, out before, out after, out conflicts);
            string gz = path + ".gz";
            Expect("count", count == 1);
            Expect("gone", !File.Exists(path) && File.Exists(gz));
            Expect("roundtrip", BytesEqual(original, Ungzip(gz)));
            double skew = Math.Abs((File.GetLastWriteTimeUtc(gz) - mtime).TotalSeconds);
            Expect("mtime", skew <= 2);
            Expect("before", before == original.Length);
            Expect("after", after == new FileInfo(gz).Length);
            ExpectKept(dir);
        }

        static void CheckLeftovers(string dir)
        {
            byte[] logBytes = Encoding.UTF8.GetBytes("20261101 应压缩\n残留替换\n");
            string logPath = Path.Combine(dir, "u8co-20261101.log");
            string gzPath = logPath + ".gz";
            string tmpPath = Path.Combine(dir, "u8co-20261102.log.gz.tmp");
            File.WriteAllBytes(logPath, logBytes);
            GzipTo(gzPath, logBytes);
            byte[] gzSaved = File.ReadAllBytes(gzPath);
            File.WriteAllBytes(tmpPath, Encoding.ASCII.GetBytes("CRASH-LEFTOVER"));
            long before;
            long after;
            int conflicts;
            int count = AuditLogCompression.Compress(dir, 7, Today, out before, out after, out conflicts);
            Expect("left count", count == 1 && conflicts == 0);
            Expect("left log gone", !File.Exists(logPath) && File.Exists(gzPath));
            Expect("left gz same", BytesEqual(gzSaved, File.ReadAllBytes(gzPath)));
            Expect("tmp gone", !File.Exists(tmpPath));
            Expect("left before", before == logBytes.Length);
            Expect("left after", after == gzSaved.Length);
        }

        static void CheckConflict(string dir)
        {
            byte[] logBytes = Encoding.UTF8.GetBytes("20261104 应压缩\n不同内容\n");
            byte[] other = Encoding.UTF8.GetBytes("另一份 gzip 内容\n");
            string logPath = Path.Combine(dir, "u8co-20261104.log");
            string gzPath = logPath + ".gz";
            File.WriteAllBytes(logPath, logBytes);
            GzipTo(gzPath, other);
            byte[] logSaved = File.ReadAllBytes(logPath);
            byte[] gzSaved = File.ReadAllBytes(gzPath);
            long before;
            long after;
            int conflicts;
            int count = AuditLogCompression.Compress(dir, 7, Today, out before, out after, out conflicts);
            Expect("diff count", count == 0 && conflicts == 1 && before == 0 && after == 0);
            Expect("diff log kept", File.Exists(logPath) && BytesEqual(logSaved, File.ReadAllBytes(logPath)));
            Expect("diff gz kept", File.Exists(gzPath) && BytesEqual(gzSaved, File.ReadAllBytes(gzPath)));
        }

        static void CheckZero(string dir)
        {
            string path = Path.Combine(dir, "u8co-20261103.log");
            byte[] data = Encoding.UTF8.GetBytes("零天不压缩\n第二行\n");
            File.WriteAllBytes(path, data);
            long before;
            long after;
            int conflicts;
            int count = AuditLogCompression.Compress(dir, 0, Today, out before, out after, out conflicts);
            Expect("zero count", count == 0 && before == 0 && after == 0 && conflicts == 0);
            Expect("zero kept", File.Exists(path) && !File.Exists(path + ".gz"));
            Expect("zero bytes", BytesEqual(data, File.ReadAllBytes(path)));
        }

        static void SeedKept(string dir)
        {
            Write(dir, Kept[0], "20261109 满 7 天保留\n第二行\n");
            Write(dir, Kept[1], "今天 20261116 不压\n第二行\n");
            Write(dir, Kept[2], "未来 20261201 不压\n第二行\n");
            Write(dir, Kept[3], "短日期名不匹配\n");
            Write(dir, Kept[4], "other.log 不匹配\n");
            Write(dir, Kept[5], "无效日期 20261301 不压\n");
        }

        static void ExpectKept(string dir)
        {
            for (int i = 0; i < Kept.Length; i++)
            {
                string path = Path.Combine(dir, Kept[i]);
                Expect("kept " + Kept[i], File.Exists(path) && !File.Exists(path + ".gz"));
            }
        }

        static void Write(string dir, string name, string text)
        {
            File.WriteAllBytes(Path.Combine(dir, name), Encoding.UTF8.GetBytes(text));
        }

        static void GzipTo(string dest, byte[] data)
        {
            using (FileStream output = new FileStream(dest, FileMode.Create, FileAccess.Write, FileShare.None))
            using (GZipStream gzip = new GZipStream(output, CompressionMode.Compress))
            {
                gzip.Write(data, 0, data.Length);
            }
        }

        static byte[] Ungzip(string path)
        {
            using (FileStream input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
            using (GZipStream gzip = new GZipStream(input, CompressionMode.Decompress))
            using (MemoryStream copy = new MemoryStream())
            {
                gzip.CopyTo(copy);
                return copy.ToArray();
            }
        }

        static bool BytesEqual(byte[] a, byte[] b)
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

        static void Bad(string name, string json)
        {
            try
            {
                AuditLogCompression.Load(new BridgeConfig(), Map(json));
            }
            catch (InvalidOperationException ex)
            {
                Expect(name + " msg", ex.Message == RangeMsg);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException(name);
            }
        }

        static Dictionary<string, object> Map(string json)
        {
            return (Dictionary<string, object>)new JavaScriptSerializer().DeserializeObject(json);
        }
    }
}
