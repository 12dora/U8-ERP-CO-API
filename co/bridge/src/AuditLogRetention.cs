using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text.RegularExpressions;
using System.Threading;

namespace U8Co
{
    // 审计日志按日文件的保留：config.json 的 auditLogRetentionDays（缺省 30 天，0 不清理）。
    internal static class AuditLogRetention
    {
        public const string ConfigKey = "auditLogRetentionDays";
        public const int DefaultDays = 30;
        public const int MaxDays = 3650;
        static readonly Regex NamePattern = new Regex(
            "^u8co-(\\d{8})\\.log(\\.gz)?$", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
        static readonly object Gate = new object();
        static Thread _thread;

        public static void Load(BridgeConfig cfg, Dictionary<string, object> map)
        {
            cfg.AuditLogRetentionDays = BridgeConfig.OptionalInt(
                map, ConfigKey, DefaultDays, 0, MaxDays);
        }

        public static string Describe(BridgeConfig cfg)
        {
            int days = cfg == null ? DefaultDays : cfg.AuditLogRetentionDays;
            string text = "审计日志保留天数：" + days.ToString(CultureInfo.InvariantCulture);
            return days == 0 ? text + "（不清理）" : text;
        }

        public static void Start(BridgeConfig cfg)
        {
            try
            {
                StartThread(cfg);
            }
            catch (Exception)
            {
            }
        }

        static void StartThread(BridgeConfig cfg)
        {
            if (cfg == null || (cfg.AuditLogRetentionDays == 0 && cfg.AuditLogCompressAfterDays == 0))
            {
                return;
            }
            lock (Gate)
            {
                if (_thread != null)
                {
                    return;
                }
                Thread thread = new Thread(new ThreadStart(delegate { Loop(cfg); }));
                thread.IsBackground = true;
                thread.Name = "u8co-log-retention";
                thread.Start();
                _thread = thread;
            }
        }

        static void Loop(BridgeConfig cfg)
        {
            WaitAndSweep(cfg, TimeSpan.FromSeconds(60));
            TimeSpan every = TimeSpan.FromHours(6);
            while (true)
            {
                WaitAndSweep(cfg, every);
            }
        }

        static void WaitAndSweep(BridgeConfig cfg, TimeSpan wait)
        {
            try
            {
                Thread.Sleep(wait);
                Sweep(cfg);
            }
            catch (Exception)
            {
            }
        }

        static void Sweep(BridgeConfig cfg)
        {
            if (!SafeChain(cfg.AuditLog))
            {
                return;
            }
            NoteCompressed(cfg);
            NotePruned(cfg);
        }

        static void NoteCompressed(BridgeConfig cfg)
        {
            if (cfg.AuditLogCompressAfterDays <= 0)
            {
                return;
            }
            long before;
            long after;
            int conflicts;
            int count = AuditLogCompression.Compress(
                cfg.AuditLog, cfg.AuditLogCompressAfterDays, DateTime.Now,
                out before, out after, out conflicts);
            if (conflicts >= 1)
            {
                Dictionary<string, object> conflictFields = new Dictionary<string, object>();
                conflictFields["count"] = conflicts;
                AuditEvent.Write("audit_log_compress_conflict", conflictFields);
            }
            if (count < 1)
            {
                return;
            }
            Dictionary<string, object> fields = new Dictionary<string, object>();
            fields["count"] = count;
            fields["bytes_before"] = before;
            fields["bytes_after"] = after;
            fields["days"] = cfg.AuditLogCompressAfterDays;
            AuditEvent.Write("audit_log_compressed", fields);
        }

        static void NotePruned(BridgeConfig cfg)
        {
            if (cfg.AuditLogRetentionDays <= 0)
            {
                return;
            }
            long bytes;
            int count = Prune(cfg.AuditLog, cfg.AuditLogRetentionDays, DateTime.Now, out bytes);
            if (count < 1)
            {
                return;
            }
            Dictionary<string, object> fields = new Dictionary<string, object>();
            fields["count"] = count;
            fields["bytes"] = bytes;
            fields["days"] = cfg.AuditLogRetentionDays;
            AuditEvent.Write("audit_log_pruned", fields);
        }

        static bool SafeChain(string dir)
        {
            string full;
            string root;
            if (!NormPair(dir, out full, out root))
            {
                return false;
            }
            if (!IsUnder(full, root))
            {
                return false;
            }
            if (IsReparse(root))
            {
                return false;
            }
            string rest = "";
            if (full.Length > root.Length)
            {
                rest = full.Substring(root.Length);
            }
            string[] parts = rest.Split(new char[] { '\\', '/' }, StringSplitOptions.RemoveEmptyEntries);
            string current = root;
            for (int i = 0; i < parts.Length; i++)
            {
                current = Path.Combine(current, parts[i]);
                if (IsReparse(current))
                {
                    return false;
                }
            }
            return true;
        }

        static bool NormPair(string dir, out string full, out string root)
        {
            full = null;
            root = null;
            if (dir == null || dir.Length == 0)
            {
                return false;
            }
            try
            {
                full = TrimDir(Path.GetFullPath(dir));
                root = TrimDir(Path.GetFullPath(Paths.Root));
                return true;
            }
            catch (Exception)
            {
                return false;
            }
        }

        static bool IsUnder(string full, string root)
        {
            if (string.Equals(full, root, StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }
            return full.StartsWith(root + "\\", StringComparison.OrdinalIgnoreCase);
        }

        static bool IsReparse(string dir)
        {
            try
            {
                return (File.GetAttributes(dir) & FileAttributes.ReparsePoint) != 0;
            }
            catch (Exception)
            {
                return true;
            }
        }

        static string TrimDir(string dir)
        {
            return dir.TrimEnd('\\', '/');
        }

        internal static int Prune(string dir, int days, DateTime today, out long bytes)
        {
            bytes = 0;
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
            return DeleteDue(files, today.Date.AddDays(-days), ref bytes);
        }

        static int DeleteDue(string[] files, DateTime cutoff, ref long bytes)
        {
            int count = 0;
            for (int i = 0; i < files.Length; i++)
            {
                long size;
                if (TryDelete(files[i], cutoff, out size))
                {
                    count++;
                    bytes += size;
                }
            }
            return count;
        }

        static bool TryDelete(string path, DateTime cutoff, out long size)
        {
            size = 0;
            try
            {
                if (!Due(path, cutoff))
                {
                    return false;
                }
                size = new FileInfo(path).Length;
                File.Delete(path);
                return true;
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
    }
}
