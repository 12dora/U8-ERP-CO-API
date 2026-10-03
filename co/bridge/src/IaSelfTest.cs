using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的存货核算记账、期末处理部分（ia/post、ia/period_end）：请求校验、测试账套名单、核算设置检查、
    // 拒绝编号的中文映射、回读比对、登记（写闸门、幂等、预演、锁键、权限）。只跑纯函数，不连库、不建 COM。
    internal static class IaSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckBad();
            CheckTestAccounts();
            CheckOptions();
            CheckRefusals();
            CheckUncosted();
            CheckLongWait();
            CheckCounts();
            CheckWiring();
        }

        static void CheckParse()
        {
            IaAsk ask = IaReq.Parse(IaReq.PostPath, Body(2026, 9, "post"));
            Expect("ia parse post", !ask.PeriodEnd && !ask.Undo && ask.Year == 2026 && ask.Period == 9 && ask.OnUncosted == IaReq.Refuse);
            Expect("ia audit post", IaReq.AuditAction(ask) == IaReq.PostAction && IaReq.RuleKey(ask) == "write:ia:post");
            Expect("ia scripts post", string.Join(",", IaRun.Scripts(ask)) == "sql/ia/post_tables.sql,sql/ia/post.sql");
            Expect("ia month key", IaReq.MonthKey(ask) == "2026-09");
            CheckParseMore();
        }

        static void CheckParseMore()
        {
            Dictionary<string, object> skip = Body(2026, 9, "post");
            skip["on_uncosted"] = "skip";
            Expect("ia parse skip", IaReq.Parse(IaReq.PostPath, skip).OnUncosted == IaReq.Skip);
            IaAsk ask = IaReq.Parse(IaReq.PostPath, Body(2026, 10, "unpost"));
            Expect("ia parse unpost", ask.Undo && ask.ActionText() == "unpost" && IaReq.AuditAction(ask) == IaReq.UnpostAction);
            Expect("ia rule unpost", IaReq.RuleKey(ask) == "write:ia:unpost");
            Expect("ia scripts unpost", string.Join(",", IaRun.Scripts(ask)) == "sql/ia/unpost.sql");
            CheckParseEnd();
        }

        static void CheckParseEnd()
        {
            IaAsk ask = IaReq.Parse(IaReq.PeriodEndPath, Body(2026, 9, "run"));
            Expect("ia parse run", ask.PeriodEnd && !ask.Undo && IaReq.AuditAction(ask) == IaReq.PeriodEndAction);
            Expect("ia scripts run", string.Join(",", IaRun.Scripts(ask)) == "sql/ia/period_end.sql");
            ask = IaReq.Parse(IaReq.PeriodEndPath, Body(2026, 9, "cancel"));
            Expect("ia parse cancel", ask.Undo && ask.ActionText() == "cancel"
                && IaReq.AuditAction(ask) == IaReq.PeriodEndCancelAction && IaReq.RuleKey(ask) == "write:ia:period_end");
            Expect("ia scripts cancel", string.Join(",", IaRun.Scripts(ask)) == "sql/ia/period_end_cancel.sql");
            Expect("ia default action", IaReq.DefaultAction(IaReq.PeriodEndPath) == IaReq.PeriodEndAction
                && IaReq.DefaultAction(IaReq.PostPath) == IaReq.PostAction);
        }

        static void CheckBad()
        {
            Bad("ia year", IaReq.PostPath, Body(26, 9, "post"), "fiscal_year");
            Bad("ia period zero", IaReq.PostPath, Body(2026, 0, "post"), "period");
            Bad("ia period thirteen", IaReq.PeriodEndPath, Body(2026, 13, "run"), "period");
            Bad("ia post action", IaReq.PostPath, Body(2026, 9, "run"), "action");
            Bad("ia period end action", IaReq.PeriodEndPath, Body(2026, 9, "post"), "action");
            Dictionary<string, object> text = Body(2026, 9, "post");
            text["period"] = "9";
            Bad("ia period text", IaReq.PostPath, text, "period");
            Dictionary<string, object> bad = Body(2026, 9, "post");
            bad["on_uncosted"] = "ignore";
            Bad("ia uncosted value", IaReq.PostPath, bad, "on_uncosted");
            Dictionary<string, object> undo = Body(2026, 9, "unpost");
            undo["on_uncosted"] = "skip";
            Bad("ia uncosted unpost", IaReq.PostPath, undo, "on_uncosted");
        }

        static void CheckTestAccounts()
        {
            TestAccountGateSelfTest.CheckGate("ia", IaReq.TestOnly, IaReq.RequireTestAccount);
        }

        static void CheckOptions()
        {
            Expect("ia options default", IaRun.UnsupportedReason("按仓库核算", "单到回冲", "发出商品", null) == null);
            Expect("ia options sale out", IaRun.UnsupportedReason("按仓库核算", "单到回冲", "销售出库单", "") == null);
            Expect("ia options blank", IaRun.UnsupportedReason(null, null, null, null) == null);
            Expect("ia options by inventory", Has(IaRun.UnsupportedReason("按存货核算", "单到回冲", "发出商品", null), "按存货核算"));
            Expect("ia options warehouse", Has(IaRun.UnsupportedReason("按仓库核算", "单到回冲", "发出商品", "03 "), "仓库 03 "));
            Expect("ia options estimate", Has(IaRun.UnsupportedReason("按仓库核算", "月初回冲", "发出商品", null), "月初回冲"));
            Expect("ia options sale type", Has(IaRun.UnsupportedReason("按仓库核算", "单到回冲", "其他", null), "接口暂不支持"));
            Expect("ia start same", IaRun.BeforeStart("2024-12-01", 2024, 12) == null);
            Expect("ia start later", IaRun.BeforeStart("2024-12-01", 2025, 1) == null);
            Expect("ia start before", Has(IaRun.BeforeStart("2024-12-01", 2024, 11), "2024-12"));
            Expect("ia start unknown", IaRun.BeforeStart("", 2020, 1) == null);
        }

        static void CheckRefusals()
        {
            int[] numbers = new int[]
            {
                50002, 50003, 50010, 50011, 50020, 50021, 50030, 50040, 50041, 50043, 50044, 50045, 50046,
                50050, 50051, 50052, 50060, 50061
            };
            foreach (int n in numbers)
            {
                BridgeException ex = IaRun.Refusal(n, "x", null);
                Expect("ia refusal " + n, ex.Status == 409 && ex.Code == "state_mismatch"
                    && ex.Message.IndexOf("编号", StringComparison.Ordinal) < 0 && ex.Message.IndexOf("IA ", StringComparison.Ordinal) < 0);
            }
            Expect("ia refusal prior", Has(IaRun.Refusal(50041, "prior IA month not closed: 2026-08", null).Message, "（2026-08）"));
            Expect("ia refusal unposted", Has(IaRun.Refusal(50043, "unposted verified costing lines: 12", null).Message, " 12 行"));
            Expect("ia refusal unaudited", Has(IaRun.Refusal(50044, "bcheckedpribooking: unaudited costing lines in month: 3", null).Message, " 3 行"));
            Expect("ia refusal tail bad", Has(IaRun.Refusal(50043, "no number", null).Message, "若干"));
            Expect("ia refusal unknown", Has(IaRun.Refusal(50099, "x", null).Message, "编号 50099"));
            BridgeException u8 = IaRun.Refusal(50000, "[Microsoft][SQL Server]MonthAcc: 存货 A01 没有单价: 请检查", null);
            Expect("ia refusal u8", u8.Code == "u8_rejected" && u8.Message == "U8 拒绝：存货 A01 没有单价: 请检查");
            Expect("ia refusal u8 bare", IaRun.Refusal(50000, "没有前缀", null).Message == "U8 拒绝：没有前缀");
        }

        static void CheckUncosted()
        {
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            rows.Add(Row("01", "A001 ", null, "25"));
            rows.Add(Row("02", "B002", "L1", "25"));
            BridgeException ex = IaRun.Refusal(50061, "outgoing cost undetermined", rows);
            Expect("ia uncosted status", ex.Status == 409 && ex.Code == "state_mismatch" && Has(ex.Message, "共 25 个")
                && Has(ex.Message, "例如 仓库 01 存货 A001、仓库 02 存货 B002 批号 L1 等；") && Has(ex.Message, "on_uncosted=skip"));
            Expect("ia uncosted detail", ex.Detail != null && (int)ex.Detail["uncosted_total"] == 25);
            List<object> items = (List<object>)ex.Detail["uncosted"];
            Dictionary<string, object> first = (Dictionary<string, object>)items[0];
            Expect("ia uncosted item", items.Count == 2 && (string)first["wh"] == "01" && (string)first["inv"] == "A001"
                && (string)first["batch"] == "");
            Expect("ia uncosted hint", Has(ex.Hint, "on_uncosted=skip"));
            ApiResult result = ApiResult.From(ex);
            Expect("ia uncosted body", result.Body.ContainsKey("detail") && result.Status == 409);
            Expect("ia uncosted empty", (int)IaRun.Refusal(50061, "x", null).Detail["uncosted_total"] == 0);
            CheckExamples(rows);
        }

        // 消息里最多举 5 个，没举全加「等」。
        static void CheckExamples(List<Dictionary<string, object>> rows)
        {
            Expect("ia uncosted none listed", Has(IaRun.Refusal(50061, "x", null).Message, "（未列出）"));
            for (int i = 0; i < 5; i++)
            {
                rows.Add(Row("0" + i, "C" + i, "", "7"));
            }
            string shown = IaRun.Refusal(50061, "x", rows).Message;
            Expect("ia uncosted five", shown.Split('、').Length == 5 && Has(shown, "共 7 个") && Has(shown, " 等；"));
            Expect("ia uncosted exact", !Has(IaRun.Examples(new List<object>(), 0), "等"));
        }

        // 长任务的 HTTP 等待、开始执行的最少剩余时间、请求级脚本预算（iaCommandSeconds，回滚预留 120 秒）。
        static void CheckLongWait()
        {
            BridgeConfig cfg = new BridgeConfig();
            Expect("ia wait default", IaReq.LongWait(cfg) == 960000 && IaReq.LongWait(null) == 960000);
            cfg.IaCommandSeconds = 300;
            Expect("ia wait config", IaReq.LongWait(cfg) == 360000);
            WorkItem item = new WorkItem();
            item.Config = new BridgeConfig();
            Expect("ia min start short", WorkItem.MinStartMs(item) == WorkItem.MinWriteStartMs);
            IaReq.Arm(item);
            Expect("ia arm short", item.IaDeadlineTicks == 0 && IaReq.SecondsLeft(item) == 900);
            Expect("ia script unarmed", IaReq.ScriptSeconds(item) == 780);
            item.WaitMs = IaReq.LongWait(item.Config);
            Expect("ia min start long", WorkItem.MinStartMs(item) == 30000 + 885000);
            IaReq.Arm(item);
            int left = IaReq.SecondsLeft(item);
            Expect("ia arm long", item.IaDeadlineTicks > 0 && left >= 898 && left <= 900);
            int script = IaReq.ScriptSeconds(item);
            Expect("ia script armed", script >= 778 && script <= 780);
            item.IaDeadlineTicks = DateTime.UtcNow.Ticks + 140L * TimeSpan.TicksPerSecond;
            Expect("ia script too late", IaReq.ScriptSeconds(item) == 0);
            item.IaDeadlineTicks = DateTime.UtcNow.Ticks - TimeSpan.TicksPerSecond;
            Expect("ia left floor", IaReq.SecondsLeft(item) == 0);
            Expect("ia watch ticks", IaReq.WatchTicks(780) == 1680L * TimeSpan.TicksPerSecond);
            item.DeadlineUtcTicks = DateTime.UtcNow.Ticks + 300L * TimeSpan.TicksPerSecond;
            IaReq.Arm(item);
            left = IaReq.SecondsLeft(item);
            Expect("ia arm capped", left >= 238 && left <= 240);
            item.IaWorkTicks = 100L * TimeSpan.TicksPerSecond;
            Expect("ia after script", IaReq.AfterScriptTicks(item) == 220L * TimeSpan.TicksPerSecond);
            CheckWatchdog();
        }

        // 看门狗：缺省 3 分钟，脚本期间放宽，做完恢复；没占槽时 Extend 什么也不做。
        static void CheckWatchdog()
        {
            Watchdog.Extend(IaReq.WatchTicks(780));
            Expect("ia watchdog idle", Watchdog.CurrentAllowance() == 0);
            // 自检专用槽（不占工作线程的槽位），用完 Leave。
            int slot = Watchdog.SelfTestSlot;
            long minute = 60L * TimeSpan.TicksPerSecond;
            Watchdog.Enter(slot);
            Expect("ia watchdog enter", Near(Watchdog.CurrentAllowance(), Watchdog.DefaultLimit, minute));
            Watchdog.Extend(IaReq.WatchTicks(780));
            Expect("ia watchdog extend", Near(Watchdog.CurrentAllowance(), 1680L * TimeSpan.TicksPerSecond, minute));
            Watchdog.Restore();
            Expect("ia watchdog restore", Near(Watchdog.CurrentAllowance(), Watchdog.DefaultLimit, minute));
            WorkItem item = new WorkItem();
            IaRun.BeforeRollback(item);
            Expect("ia rollback idle", Near(Watchdog.CurrentAllowance(), Watchdog.DefaultLimit, minute));
            item.IaStarted = true;
            item.IaWorkTicks = 600L * TimeSpan.TicksPerSecond;
            IaRun.BeforeRollback(item);
            Expect("ia rollback widen", Near(Watchdog.CurrentAllowance(), 720L * TimeSpan.TicksPerSecond, minute));
            Watchdog.Leave(slot);
            Expect("ia watchdog leave", Watchdog.CurrentAllowance() == 0);
            CheckTran();
        }

        // 事务层数：ADO 的事务到第一条语句才开（0 → 1 也算完好）；执行过脚本、层数不对才 504。
        static void CheckTran()
        {
            Expect("ia tran lazy", IaRun.TranIntact(0, 1) && IaRun.TranIntact(1, 1));
            Expect("ia tran broken", !IaRun.TranIntact(0, 0) && !IaRun.TranIntact(1, 0) && !IaRun.TranIntact(1, 2));
            WorkItem item = new WorkItem();
            item.TranBefore = "0";
            item.TranAfter = "0";
            Expect("ia tran not started", IaRun.AfterRollback(item, true) == null);
            item.IaStarted = true;
            BridgeException ex = IaRun.AfterRollback(item, true);
            Expect("ia tran unknown", ex != null && ex.Status == 504 && ex.Code == "outcome_unknown");
            Expect("ia tran closed", IaRun.AfterRollback(item, false) == null);
            item.TranAfter = "1";
            Expect("ia tran ok", IaRun.AfterRollback(item, true) == null);
            item.TranAfter = null;
            Expect("ia tran unread", IaRun.AfterRollback(item, true) == null);
        }

        static bool Near(long actual, long expected, long slack)
        {
            return actual <= expected && actual > expected - slack;
        }

        static void CheckCounts()
        {
            Dictionary<string, string> raw = new Dictionary<string, string>();
            raw["subsidiary_month"] = "120";
            raw["summary_iPeriod1"] = "0";
            raw["odd"] = "n/a";
            Dictionary<string, object> counts = IaRun.Counts(raw);
            Expect("ia counts int", (long)counts["subsidiary_month"] == 120 && (string)counts["odd"] == "n/a");
            Expect("ia counts null", IaRun.Counts(null).Count == 0);
            Expect("ia drift same", IaRun.Drift(counts, 120, 0, 0) == null);
            Expect("ia drift sub", IaRun.Drift(counts, 119, 0, 0) == "subsidiary_month=119");
            Expect("ia drift period", IaRun.Drift(counts, 120, 4, 0) == "summary_iPeriod1=4");
            Expect("ia drift flag", IaRun.Drift(counts, 120, 0, 1) == "bflag_IA=1");
            Expect("ia drift missing keys", IaRun.Drift(new Dictionary<string, object>(), 7, 9, 0) == null);
            IaAsk ask = IaReq.Parse(IaReq.PostPath, Body(2026, 9, "post"));
            Dictionary<string, object> body = IaRun.Body(ask, counts);
            Expect("ia body", (bool)body["ok"] && (string)body["action"] == "post" && (int)body["period"] == 9
                && (string)body["on_uncosted"] == "refuse" && object.ReferenceEquals(body["counts"], counts));
            ask = IaReq.Parse(IaReq.PeriodEndPath, Body(2026, 9, "run"));
            Expect("ia body run", !IaRun.Body(ask, counts).ContainsKey("on_uncosted") && IaRun.FinalKey(ask) == "summary_iPeriod1");
            CheckNothing();
        }

        // 没有要处理的单据：area / restore_rows 为 0 时响应带 message；最后的计数键。
        static void CheckNothing()
        {
            Dictionary<string, string> raw = new Dictionary<string, string>();
            raw["area"] = "0";
            raw["subsidiary_month"] = "5";
            IaAsk post = IaReq.Parse(IaReq.PostPath, Body(2026, 9, "post"));
            Expect("ia nothing post", Has((string)IaRun.Body(post, IaRun.Counts(raw))["message"], "没有可记账的单据"));
            Expect("ia final post", IaRun.FinalKey(post) == "subsidiary_month");
            raw["area"] = "3";
            Expect("ia something post", !IaRun.Body(post, IaRun.Counts(raw)).ContainsKey("message"));
            raw.Remove("area");
            raw["restore_rows"] = "0";
            IaAsk unpost = IaReq.Parse(IaReq.PostPath, Body(2026, 9, "unpost"));
            Expect("ia nothing unpost", Has(IaRun.Nothing(unpost, IaRun.Counts(raw)), "没有已记账的单据"));
            Expect("ia nothing unpost post", IaRun.Nothing(post, IaRun.Counts(raw)) == null);
            IaAsk cancel = IaReq.Parse(IaReq.PeriodEndPath, Body(2026, 9, "cancel"));
            Expect("ia nothing period end", IaRun.Nothing(cancel, IaRun.Counts(raw)) == null && IaRun.FinalKey(cancel) == "summary_iPeriod1");
        }

        static void CheckWiring()
        {
            string[] paths = new string[] { IaReq.PostPath, IaReq.PeriodEndPath };
            string[] actions = new string[] { IaReq.PostAction, IaReq.PeriodEndAction };
            string[] routes = new string[] { "ia/post", "ia/period_end" };
            for (int i = 0; i < paths.Length; i++)
            {
                string path = paths[i];
                Expect("ia write " + path, WriteGate.IsWrite(path) && IdemReq.Supports(path) && DryRunReq.Accepts(path));
                Expect("ia dry mode " + path, DryRunModes.Lookup(path, "", actions[i], "") == DryRunModes.Rollback);
                Expect("ia meta dry " + path, (string)MetaDryRun.Routes()[routes[i]] == DryRunModes.Rollback);
                Expect("ia not read " + path, !PermRegistry.IsRead(path));
            }
            foreach (string key in new string[] { "write:ia:post", "write:ia:unpost", "write:ia:period_end" })
            {
                Expect("ia perm " + key, PermRegistry.ForKey(key) != null);
            }
            WorkItem item = new WorkItem();
            item.Config = new BridgeConfig();
            item.Path = IaReq.PeriodEndPath;
            item.Body = Body(2026, 9, "run");
            string[] keys = DocLocks.KeysOf(item);
            Expect("ia locks", Array.IndexOf(keys, "ia:2026-09") >= 0 && Array.IndexOf(keys, "period:ia") >= 0
                && Array.IndexOf(keys, WriteGate.Key) >= 0);
            Expect("ia not sql read", !RouteClass.IsSqlRead(item));
            item.Body = Body(2026, 9, "bogus");
            Expect("ia locks bad body", Array.IndexOf(DocLocks.KeysOf(item), "period:ia") < 0);
        }

        static Dictionary<string, object> Row(string wh, string inv, string batch, string total)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["cWhDepCode"] = wh;
            row["cInvCode"] = inv;
            row["cBatchia"] = batch;
            row["total"] = total;
            return row;
        }

        static Dictionary<string, object> Body(int year, int period, string action)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["fiscal_year"] = year;
            body["period"] = period;
            body["action"] = action;
            return body;
        }

        static void Bad(string name, string path, Dictionary<string, object> body, string field)
        {
            try
            {
                IaReq.Parse(path, body);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Code == "bad_request" && ex.Field == field);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static bool Has(string text, string part)
        {
            return text != null && text.IndexOf(part, StringComparison.Ordinal) >= 0;
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException(name);
            }
        }
    }
}
