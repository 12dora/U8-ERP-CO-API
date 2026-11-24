using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Web.Script.Serialization;

namespace U8Co
{
    // --selftest 的审计日志保留：配置键范围与按日文件名清理。不连 U8；临时目录跑完删掉。
    internal static class AuditLogRetentionSelfTest
    {
        const string Key = AuditLogRetention.ConfigKey;
        const string RangeMsg = "auditLogRetentionDays 必须是 0 到 3650 的整数";

        public static void Run()
        {
            CheckConfig();
            CheckPrune();
            CheckReparse();
        }

        static void CheckConfig()
        {
            BridgeConfig cfg = new BridgeConfig();
            Expect("field default", cfg.AuditLogRetentionDays == 30);
            AuditLogRetention.Load(cfg, Map("{}"));
            Expect("missing key", cfg.AuditLogRetentionDays == 30);
            AuditLogRetention.Load(cfg, Map("{\"" + Key + "\":0}"));
            Expect("zero", cfg.AuditLogRetentionDays == 0);
            AuditLogRetention.Load(cfg, Map("{\"" + Key + "\":3650}"));
            Expect("max", cfg.AuditLogRetentionDays == 3650);
            BridgeConfig.CheckKeys(Map("{\"" + Key + "\":30}"));
            Expect("describe", AuditLogRetention.Describe(cfg).IndexOf("3650") >= 0);
            cfg.AuditLogRetentionDays = 0;
            Expect("describe off", AuditLogRetention.Describe(cfg).IndexOf("不清理") >= 0);
            Bad("-1", "{\"" + Key + "\":-1}");
            Bad("over", "{\"" + Key + "\":3651}");
            Bad("string", "{\"" + Key + "\":\"30\"}");
            Bad("bool", "{\"" + Key + "\":true}");
        }

        static void CheckPrune()
        {
            string dir = Path.Combine(Path.GetTempPath(), "u8co-logret-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(dir);
            try
            {
                Seed(dir);
                long bytes;
                int count = AuditLogRetention.Prune(dir, 30, new DateTime(2026, 11, 16), out bytes);
                Expect("count", count == 4);
                Expect("left", Left(dir) == "u8co-2025010.log,u8co-20250103.log,u8co-20261017.log,"
                    + "u8co-20261017.log.gz,u8co-20261116.log,u8co-20261201.log,u8co-20261301.log,write-quota.json");
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

        static void CheckReparse()
        {
            string root = Path.Combine(Path.GetTempPath(), "u8co-logret-j-" + Guid.NewGuid().ToString("N"));
            string target = Path.Combine(root, "real");
            string link = Path.Combine(root, "junc");
            Directory.CreateDirectory(target);
            Touch(target, "u8co-20250101.log");
            try
            {
                if (!MakeJunction(link, target))
                {
                    return;
                }
                long bytes;
                int count = AuditLogRetention.Prune(link, 30, new DateTime(2026, 11, 16), out bytes);
                Expect("reparse count", count == 0);
                Expect("reparse kept", File.Exists(Path.Combine(target, "u8co-20250101.log")));
            }
            finally
            {
                DropJunction(link);
                try
                {
                    Directory.Delete(target, true);
                }
                catch (Exception)
                {
                }
                try
                {
                    Directory.Delete(root);
                }
                catch (Exception)
                {
                }
            }
        }

        static bool MakeJunction(string link, string target)
        {
            try
            {
                ProcessStartInfo info = new ProcessStartInfo();
                info.FileName = "cmd.exe";
                info.Arguments = "/c mklink /J \"" + link + "\" \"" + target + "\"";
                info.UseShellExecute = false;
                info.CreateNoWindow = true;
                info.RedirectStandardOutput = true;
                info.RedirectStandardError = true;
                using (Process proc = Process.Start(info))
                {
                    if (proc == null)
                    {
                        return false;
                    }
                    proc.WaitForExit();
                    if (proc.ExitCode != 0)
                    {
                        return false;
                    }
                }
                return (File.GetAttributes(link) & FileAttributes.ReparsePoint) != 0;
            }
            catch (Exception)
            {
                return false;
            }
        }

        static void DropJunction(string link)
        {
            try
            {
                if ((File.GetAttributes(link) & FileAttributes.ReparsePoint) != 0)
                {
                    Directory.Delete(link);
                }
            }
            catch (Exception)
            {
            }
        }

        static void Seed(string dir)
        {
            Touch(dir, "u8co-20261016.log");
            Touch(dir, "u8co-20261017.log");
            Touch(dir, "u8co-20261001.log.gz");
            Touch(dir, "u8co-20261017.log.gz");
            Touch(dir, "u8co-20250101.log");
            Touch(dir, "u8co-20261116.log");
            Touch(dir, "u8co-20261201.log");
            Touch(dir, "u8co-20261301.log");
            Touch(dir, "write-quota.json");
            Touch(dir, "U8CO-20250102.LOG");
            Touch(dir, "u8co-2025010.log");
            Directory.CreateDirectory(Path.Combine(dir, "u8co-20250103.log"));
        }

        static void Touch(string dir, string name)
        {
            File.WriteAllText(Path.Combine(dir, name), "x");
        }

        static string Left(string dir)
        {
            string[] entries = Directory.GetFileSystemEntries(dir);
            string[] names = new string[entries.Length];
            for (int i = 0; i < entries.Length; i++)
            {
                names[i] = Path.GetFileName(entries[i]);
            }
            Array.Sort(names, StringComparer.Ordinal);
            return string.Join(",", names);
        }

        static void Bad(string name, string json)
        {
            try
            {
                AuditLogRetention.Load(new BridgeConfig(), Map(json));
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
