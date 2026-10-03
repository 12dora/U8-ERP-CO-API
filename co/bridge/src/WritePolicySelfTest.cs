using System;
using System.IO;
using System.Text;

namespace U8Co
{
    // --selftest 的写入策略部分（WritePolicy、WritePolicyEval 第 1 到 6 步）：解析与未知键拒绝、缺省拒绝、通配、冻结、时段、
    // 操作员、行数与金额上限、无效内容保留上一份、文件缺失不放行。不连库、不建 COM。
    internal static class WritePolicySelfTest
    {
        // 2026-10-05 是星期一。
        static readonly DateTime Monday10 = new DateTime(2026, 10, 5, 10, 0, 0, DateTimeKind.Local);

        // 单引号写的 JSON，用前换成双引号。
        const string Sample = "{'version':1,'comment':'演练','reloadSeconds':2,"
            + "'freeze':{'global':false,'accounts':['802'],'reason':'月末结账','comment':'c'},"
            + "'windows':[{'days':'1-5','start':'09:00','end':'18:00','comment':'c'}],"
            + "'denyDates':['2026-10-30'],"
            + "'license':{'maxConcurrentLogins':1,'holdWritesWhen':['near','full']},"
            + "'defaults':{'writesPerMinute':10,'writesPerDay':200,'maxLines':200,'maxAmount':0},"
            + "'accounts':{'comment':'c',"
            + "'801':{'comment':'c','operators':{'allow':[],'deny':['svc01']},"
            + "'quotas':{'writesPerMinute':8,'maxLines':80,'maxAmount':5000000},"
            + "'allow':[{'type':'sale_order','ops':['create'],'comment':'c'},{'type':'*','ops':['verify']},"
            + "{'type':'ar_receipt','ops':['*']}]},"
            + "'802':{'allow':[{'type':'*','ops':['*']}]},"
            + "'803':{'operators':{'allow':['Demo01']},'allow':[{'type':'*','ops':['*']}]},"
            + "'998':{}}}";

        public static void Run()
        {
            try
            {
                CheckParse();
                CheckRejects();
                CheckAllow();
                CheckFreezeAndWindow();
                CheckOperators();
                CheckLimits();
                CheckReload();
                CheckMissing();
                CheckSameLength();
                CheckHealth();
            }
            finally
            {
                WritePolicy.ResetForTest();
            }
        }

        static string Q(string text)
        {
            return text.Replace('\'', '"');
        }

        static WritePolicySnapshot Snap()
        {
            return WritePolicySnapshot.Parse(Q(Sample), DateTime.UtcNow);
        }

        static WriteRequestInfo Req(string acc, string type, string op)
        {
            WriteRequestInfo info = new WriteRequestInfo();
            info.Acc = acc;
            info.Type = type;
            info.Op = op;
            info.Operator = "op001";
            info.Path = "/u8co/v1/vouchers/create";
            return info;
        }

        static void CheckParse()
        {
            WritePolicySnapshot s = Snap();
            Expect("parse version", s.Version == 1 && s.ReloadSeconds == 2);
            Expect("parse freeze", !s.FreezeGlobal && s.FreezeReason == "月末结账");
            Expect("parse license", s.License.MaxConcurrentLogins == 1 && s.License.Holds("full"));
            Expect("license ok state", !s.License.Holds("ok"));
            WriteQuotaLimits q = s.Quota("801");
            Expect("quota override", q.PerMinute == 8 && q.PerDay == 200);
            Expect("quota override limits", q.MaxLines == 80 && q.MaxAmount == 5000000m);
            Expect("quota defaults", s.Quota("999").PerMinute == 10 && s.Quota("998").MaxLines == 200);
            WritePolicySnapshot bare = WritePolicySnapshot.Parse("{\"version\":1}", DateTime.UtcNow);
            Expect("bare policy", bare.Windows == null && !bare.UnlistedAllow);
            Expect("bare limits", bare.Quota("801").PerDay == 0 && bare.License.MaxConcurrentLogins == 0);
            Expect("bare window", bare.WindowOpen(Monday10));
        }

        // 未知键（任何层级）、类型或取值不对：整份无效。
        static void CheckRejects()
        {
            string[] bad = new string[]
            {
                "", "[]", "{bad", "{}", "{'version':2}", "{'version':1,'extra':1}", "{'version':1,'comment':1}",
                "{'version':1,'reloadSeconds':0}", "{'version':1,'freeze':{'global':'yes'}}",
                "{'version':1,'freeze':{'accounts':['9a5']}}", "{'version':1,'freeze':{'why':'x'}}",
                "{'version':1,'windows':[]}", "{'version':1,'windows':[{'days':'1-5','start':'23:00','end':'07:00'}]}",
                "{'version':1,'windows':[{'days':'6-1','start':'07:00','end':'08:00'}]}",
                "{'version':1,'windows':[{'days':'1-5','start':'7:00','end':'08:00'}]}",
                "{'version':1,'windows':[{'days':'1-5','start':'07:00','end':'08:00','tz':'x'}]}",
                "{'version':1,'denyDates':['2026-13-01']}", "{'version':1,'license':{'holdWritesWhen':['ok']}}",
                "{'version':1,'defaults':{'maxLines':-1}}", "{'version':1,'defaults':{'maxAmount':'1'}}",
                "{'version':1,'defaults':{'perMinute':1}}", "{'version':1,'unlisted':'maybe'}",
                "{'version':1,'accounts':{'95':{}}}", "{'version':1,'accounts':{'801':{'allow':[{'type':'x'}]}}}",
                "{'version':1,'accounts':{'801':{'allow':[{'type':'x','ops':[]}]}}}",
                "{'version':1,'accounts':{'801':{'allow':[{'type':'x','ops':['sell']}]}}}",
                "{'version':1,'accounts':{'801':{'allow':[{'type':'Sale','ops':['create']}]}}}",
                "{'version':1,'accounts':{'801':{'operators':{'mode':'any'}}}}",
                "{'version':1,'accounts':{'801':{'quotas':{'maxLines':1,'x':1}}}}"
            };
            for (int i = 0; i < bad.Length; i++)
            {
                bool threw = false;
                try
                {
                    WritePolicySnapshot.Parse(Q(bad[i]), DateTime.UtcNow);
                }
                catch (InvalidOperationException)
                {
                    threw = true;
                }
                Expect("reject " + bad[i], threw);
            }
        }

        // 第 4 步：未列出的账套缺省拒绝；规则按类型与操作匹配，"*" 为通配；没有规则的账套全部拒绝。
        static void CheckAllow()
        {
            WritePolicySnapshot s = Snap();
            Code("allow so create", s, Req("801", "sale_order", "create"), null);
            Code("deny so delete", s, Req("801", "sale_order", "delete"), "write_not_allowed");
            Code("wildcard type", s, Req("801", "gl", "verify"), null);
            Code("wildcard op", s, Req("801", "ar_receipt", "writeoff"), null);
            Code("deny gl create", s, Req("801", "gl", "create"), "write_not_allowed");
            Code("no rules", s, Req("998", "sale_order", "create"), "write_not_allowed");
            Code("unlisted deny", s, Req("999", "sale_order", "create"), "write_not_allowed");
            BridgeException ex = WritePolicyEval.Check(s, Req("801", "gl", "create"), Monday10);
            Expect("deny status", ex.Status == 403 && ex.Message == "该账套不允许此写入"
                && (string)ex.Detail["type"] == "gl" && (string)ex.Detail["op"] == "create");
            WritePolicySnapshot open = WritePolicySnapshot.Parse(Q("{'version':1,'unlisted':'allow'}"), DateTime.UtcNow);
            Code("unlisted allow", open, Req("999", "periods", "close"), null);
        }

        // 第 2、3 步：冻结（全局或账套，带原因）先于时段；denyDates 整天、时段外都拒绝。
        static void CheckFreezeAndWindow()
        {
            WritePolicySnapshot s = Snap();
            BridgeException ex = WritePolicyEval.Check(s, Req("802", "sale_order", "create"), Monday10);
            Expect("freeze account", ex != null && ex.Status == 503 && ex.Code == "write_frozen" && ex.Message == "写入已冻结：月末结账");
            WritePolicySnapshot all = WritePolicySnapshot.Parse(Q("{'version':1,'freeze':{'global':true}}"), DateTime.UtcNow);
            ex = WritePolicyEval.Check(all, Req("801", "sale_order", "create"), Monday10);
            Expect("freeze global", ex != null && ex.Code == "write_frozen" && ex.Message == "写入已冻结");
            Code("freeze before window", s, Req("802", "sale_order", "create"), new DateTime(2026, 10, 4, 10, 0, 0), "write_frozen");
            Code("sunday", s, Req("801", "sale_order", "create"), new DateTime(2026, 10, 4, 10, 0, 0), "write_window");
            Code("before start", s, Req("801", "sale_order", "create"), new DateTime(2026, 10, 5, 8, 59, 59), "write_window");
            Code("at start", s, Req("801", "sale_order", "create"), new DateTime(2026, 10, 5, 9, 0, 0), null);
            Code("at end", s, Req("801", "sale_order", "create"), new DateTime(2026, 10, 5, 18, 0, 0), "write_window");
            Code("deny date", s, Req("801", "sale_order", "create"), new DateTime(2026, 10, 30, 10, 0, 0), "write_window");
            ex = WritePolicyEval.Check(s, Req("801", "sale_order", "create"), new DateTime(2026, 10, 30, 10, 0, 0));
            Expect("window status", ex.Status == 503 && ex.Message == "当前时段不允许写入");
            Code("window before allow", s, Req("999", "sale_order", "create"), new DateTime(2026, 10, 30, 10, 0, 0), "write_window");
        }

        // 第 5 步：deny 优先；allow 非空时只放行名单里的（不区分大小写）；操作员为空不算在名单里。
        static void CheckOperators()
        {
            WritePolicySnapshot s = Snap();
            WriteRequestInfo info = Req("801", "sale_order", "create");
            info.Operator = "SVC01";
            BridgeException ex = WritePolicyEval.Check(s, info, Monday10);
            Expect("operator deny", ex != null && ex.Status == 403 && ex.Code == "operator_not_allowed"
                && ex.Message == "该操作员不能在此账套写入");
            info = Req("803", "sale_order", "create");
            info.Operator = " demo01 ";
            Code("operator allow", s, info, null);
            info.Operator = "demo02";
            Code("operator not listed", s, info, "operator_not_allowed");
            info.Operator = null;
            Code("operator empty", s, info, "operator_not_allowed");
            info = Req("801", "gl", "create");
            info.Operator = "svc01";
            Code("allow before operator", s, info, "write_not_allowed");
        }

        // 第 6 步：行数只看新增、修改、生单；金额只看收付款类单据，按绝对值。
        static void CheckLimits()
        {
            WritePolicySnapshot s = Snap();
            WriteRequestInfo info = Req("801", "sale_order", "create");
            info.Lines = 80;
            Code("lines at max", s, info, null);
            info.Lines = 81;
            BridgeException ex = WritePolicyEval.Check(s, info, Monday10);
            Expect("lines over", ex != null && ex.Status == 400 && ex.Code == "write_limit" && ex.Message == "行数超过上限"
                && ex.Field == "lines");
            info = Req("801", "gl", "verify");
            info.Lines = 500;
            Code("lines verify", s, info, null);
            info = Req("801", "ar_receipt", "create");
            info.Amount = 5000000m;
            Code("amount at max", s, info, null);
            info.Amount = -5000000.01m;
            ex = WritePolicyEval.Check(s, info, Monday10);
            Expect("amount over", ex != null && ex.Status == 400 && ex.Code == "write_limit" && ex.Message == "金额超过上限");
            info.Amount = null;
            info.AmountBad = true;
            ex = WritePolicyEval.Check(s, info, Monday10);
            Expect("amount bad closed", ex != null && ex.Status == 400 && ex.Code == "write_limit");
            info = Req("801", "sale_order", "create");
            info.Amount = 9000000m;
            info.AmountBad = true;
            Code("amount other type", s, info, null);
            info = Req("802", "ar_receipt", "create");
            info.Amount = 1000000000m;
            WritePolicySnapshot noLimit = WritePolicySnapshot.Parse(
                Q("{'version':1,'accounts':{'802':{'allow':[{'type':'*','ops':['*']}]}}}"), DateTime.UtcNow);
            Code("amount unlimited", noLimit, info, null);
            info.AmountBad = true;
            Code("amount bad unlimited", noLimit, info, null);
        }

        // 新内容无效：保留上一份有效快照，状态 invalid；再改成有效内容恢复 ok。
        static void CheckReload()
        {
            WritePolicy.ResetForTest();
            Expect("off allows", WritePolicyEval.Check(Req("999", "gl", "create"), Monday10) == null);
            WritePolicy.Apply(Q(Sample), DateTime.UtcNow);
            WritePolicySnapshot good = WritePolicy.Current;
            Expect("apply ok", WritePolicy.State == "ok" && good != null);
            WritePolicy.Apply(Q("{'version':1,'unknown':true}"), DateTime.UtcNow);
            Expect("keep last good", WritePolicy.State == "invalid" && object.ReferenceEquals(WritePolicy.Current, good));
            Expect("last good used", WritePolicyEval.Check(Req("801", "sale_order", "create"), Monday10) == null);
            WritePolicy.Apply(Q("{'version':1,'freeze':{'global':true}}"), DateTime.UtcNow);
            Expect("swap", WritePolicy.State == "ok" && WritePolicy.Current.FreezeGlobal);
        }

        // 配置了文件但文件不存在、或首次内容无效：Current 为 null，一律 503 write_policy_unavailable。
        static void CheckMissing()
        {
            string file = Path.Combine(Path.GetTempPath(), "u8co-wp-" + Guid.NewGuid().ToString("N") + ".json");
            try
            {
                WritePolicy.Init(file);
                Expect("missing state", WritePolicy.State == "missing" && WritePolicy.Current == null);
                BridgeException ex = WritePolicyEval.Check(Req("801", "sale_order", "create"), Monday10);
                Expect("missing closed", ex != null && ex.Status == 503 && ex.Code == "write_policy_unavailable"
                    && ex.Message == "写入策略不可用");
                File.WriteAllText(file, "{\"version\":1,\"bogus\":1}", Encoding.UTF8);
                WritePolicy.Init(file);
                Expect("invalid first", WritePolicy.State == "invalid" && WritePolicy.Current == null);
                ex = WritePolicyEval.Check(Req("801", "sale_order", "create"), Monday10);
                Expect("invalid closed", ex != null && ex.Code == "write_policy_unavailable");
                File.WriteAllText(file, Q(Sample), new UTF8Encoding(true));
                WritePolicy.Init(file);
                Expect("file ok", WritePolicy.State == "ok" && WritePolicy.Current != null
                    && WritePolicyEval.Check(Req("801", "sale_order", "create"), Monday10) == null);
            }
            finally
            {
                File.Delete(file);
            }
        }

        // 等长替换且保留修改时间（如 Copy-Item、scp -p）：按内容 SHA-256 判断，照样重载。
        static void CheckSameLength()
        {
            string file = Path.Combine(Path.GetTempPath(), "u8co-wp-" + Guid.NewGuid().ToString("N") + ".json");
            try
            {
                File.WriteAllText(file, Q("{'version':1,'freeze':{'accounts':['801']}}"), Encoding.UTF8);
                DateTime stamp = File.GetLastWriteTimeUtc(file);
                WritePolicy.Init(file);
                Expect("hash first", WritePolicy.Current != null && WritePolicy.Current.IsFrozen("801"));
                File.WriteAllText(file, Q("{'version':1,'freeze':{'accounts':['802']}}"), Encoding.UTF8);
                File.SetLastWriteTimeUtc(file, stamp);
                WritePolicy.PollForTest();
                Expect("hash swap", WritePolicy.Current.IsFrozen("802") && !WritePolicy.Current.IsFrozen("801"));
                WritePolicySnapshot same = WritePolicy.Current;
                File.SetLastWriteTimeUtc(file, stamp.AddMinutes(1));
                WritePolicy.PollForTest();
                Expect("hash same content", object.ReferenceEquals(WritePolicy.Current, same));
            }
            finally
            {
                WritePolicy.ResetForTest();
                File.Delete(file);
            }
        }

        static void CheckHealth()
        {
            Expect("health off", WritePolicy.HealthOf("off", null, Monday10).Count == 1);
            System.Collections.Generic.Dictionary<string, object> h = WritePolicy.HealthOf("ok", Snap(), Monday10);
            Expect("health ok", (string)h["state"] == "ok" && (int)h["version"] == 1 && (bool)h["window_open"]
                && h["loaded_at"] is string && h.ContainsKey("freeze"));
            h = WritePolicy.HealthOf("missing", null, Monday10);
            Expect("health missing", h["version"] == null && !(bool)h["window_open"]);
        }

        static void Code(string name, WritePolicySnapshot s, WriteRequestInfo info, string code)
        {
            Code(name, s, info, Monday10, code);
        }

        static void Code(string name, WritePolicySnapshot s, WriteRequestInfo info, DateTime now, string code)
        {
            BridgeException ex = WritePolicyEval.Check(s, info, now);
            string got = ex == null ? null : ex.Code;
            if (got != code)
            {
                throw new InvalidOperationException("write policy " + name + ": " + (got ?? "allowed") + " != " + (code ?? "allowed"));
            }
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("write policy " + name);
            }
        }
    }
}
