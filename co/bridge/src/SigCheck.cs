using System;
using System.Collections.Generic;
using System.Threading;

namespace U8Co
{
    // COM 签名自检：ProgID → CLSID → 类型库（注册表 + LoadTypeLibEx(REGKIND_NONE)），核对 SigTable 里桥的调用方式。
    // 只读：不创建 U8 对象、不登录、不写注册表。任何一行出错只把这一行记成 unknown，不抛出。
    // 服务启动后在后台线程跑一次（不阻塞启动）；命令行 --check-signatures 在前台跑并打印 JSON。
    internal static class SigCheck
    {
        const int CliWaitMs = 120000;
        static readonly object Gate = new object();
        static string _summary = "pending";
        static Dictionary<string, object> _report;

        public static void StartBackground()
        {
            try
            {
                Thread t = new Thread(RunBackground);
                t.IsBackground = true;
                t.Name = "u8co-sigcheck";
                t.SetApartmentState(ApartmentState.STA);
                t.Start();
            }
            catch (Exception)
            {
                Store("unknown", null);
            }
        }

        // health 用：pending / ok / mismatch:<n> / unknown（自检本身失败）。不含细节。
        public static string HealthSummary()
        {
            lock (Gate)
            {
                return _summary;
            }
        }

        // meta 用：完整报告（按功能分组），自检未完成时只有 summary。
        public static Dictionary<string, object> MetaView()
        {
            lock (Gate)
            {
                if (_report != null)
                {
                    return _report;
                }
                Dictionary<string, object> body = new Dictionary<string, object>();
                body["summary"] = _summary;
                return body;
            }
        }

        static void Store(string summary, Dictionary<string, object> report)
        {
            lock (Gate)
            {
                _summary = summary;
                _report = report;
            }
        }

        static void RunBackground()
        {
            Dictionary<string, object> evt = new Dictionary<string, object>();
            try
            {
                List<SigEntry> entries = RunAll();
                Dictionary<string, object> report = SigReport.Build(entries, DateTime.UtcNow);
                string summary = (string)report["summary"];
                Store(summary, report);
                evt["summary"] = summary;
                evt["counts"] = report["counts"];
                evt["problems"] = SigReport.Problems(entries);
            }
            catch (Exception ex)
            {
                Store("unknown", null);
                evt["summary"] = "unknown";
                evt["error"] = ex.GetType().Name;
            }
            AuditEvent.Write("signature_check", evt);
        }

        // 在当前线程逐行核对。类型库与注册表查询结果在一次运行内缓存。
        internal static List<SigEntry> RunAll()
        {
            SigNeed[] table = SigTable.Build();
            List<SigEntry> entries = new List<SigEntry>(table.Length);
            Dictionary<string, SigComp> comps = new Dictionary<string, SigComp>(StringComparer.OrdinalIgnoreCase);
            using (SigTypeLib libs = new SigTypeLib())
            {
                for (int i = 0; i < table.Length; i++)
                {
                    entries.Add(CheckOne(table[i], libs, comps));
                }
            }
            return entries;
        }

        static SigEntry CheckOne(SigNeed need, SigTypeLib libs, Dictionary<string, SigComp> comps)
        {
            SigEntry entry = new SigEntry();
            entry.Need = need;
            try
            {
                CheckInto(need, libs, Comp(need.ProgId, comps), entry);
            }
            catch (Exception ex)
            {
                SigJudge.Set(entry, "unknown", "自检出错 " + ex.GetType().Name);
            }
            return entry;
        }

        static void CheckInto(SigNeed need, SigTypeLib libs, SigComp comp, SigEntry entry)
        {
            if (comp.Missing != null)
            {
                SigJudge.Set(entry, "missing", comp.Missing);
                return;
            }
            if (need.Member.Length == 0)
            {
                SigJudge.Set(entry, "ok", "");
                return;
            }
            string problem;
            List<SigFunc> members = libs.Members(comp, out problem);
            if (members == null)
            {
                string kind = comp.Managed ? "（.NET 组件，只确认了注册）" : "";
                SigJudge.Set(entry, "unknown", (problem ?? "类型库不可读") + kind);
                return;
            }
            SigJudge.Judge(need, members, entry);
        }

        static SigComp Comp(string progId, Dictionary<string, SigComp> comps)
        {
            SigComp comp;
            if (!comps.TryGetValue(progId, out comp))
            {
                comp = SigReg.Lookup(progId);
                comps[progId] = comp;
            }
            return comp;
        }

        // 命令行：打印 JSON（纯 ASCII）。有不匹配也返回 0；--strict 时 summary 不是 ok 返回 2。
        public static int RunCli(bool strict)
        {
            if (IntPtr.Size != 4)
            {
                Console.Error.WriteLine("必须使用 32 位进程");
                return 1;
            }
            CliRun run = new CliRun();
            Thread t = new Thread(run.Execute);
            t.IsBackground = true;
            t.SetApartmentState(ApartmentState.STA);
            t.Start();
            Dictionary<string, object> report = t.Join(CliWaitMs) ? run.Report : null;
            if (report == null)
            {
                report = new Dictionary<string, object>();
                report["summary"] = "unknown";
                report["error"] = run.Error ?? "自检超时";
            }
            Console.WriteLine(SigReport.AsciiJson(report));
            if (strict && (string)report["summary"] != "ok")
            {
                return 2;
            }
            return 0;
        }

        sealed class CliRun
        {
            public volatile Dictionary<string, object> Report;
            public volatile string Error;

            public void Execute()
            {
                try
                {
                    Report = SigReport.Build(RunAll(), DateTime.UtcNow);
                }
                catch (Exception ex)
                {
                    Error = "自检出错 " + ex.GetType().Name;
                }
            }
        }
    }
}
