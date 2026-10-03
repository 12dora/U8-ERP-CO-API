using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 存货核算期初记账 / 取消期初记账（openings/post module=ia）。U8「期初余额」窗体的记账（取数 + AccSummary + AccQC）
    // 没有可无界面调用的组件（ClsQc.AccQC 只置标志、不取数），桥照 U8 界面的 SQL 重建成两段内嵌脚本 sql/ia/qc_keep.sql、
    // qc_recover.sql，在请求连接、请求事务里整批执行（同 IaRun，参数只经 #ia_args）。取消期初记账只删第 0 期的期初数据、
    // 清第 0 期标志，不是 U8 的「取消开账」；已有月份结账或已有日常数据时脚本拒绝。
    // 一个请求一个事务：脚本（含闸门）→ 核对计数 → 提交；提交后在新连接上回读第 0 期标志和期初汇总行数，不符 504。
    // 预演（rollback）照样走到提交点，由提交钩子回滚。只对配置为测试账套的账套开放（TestAccountGate，OpeningPost 已查）。
    internal static class OpeningIa
    {
        internal const string KeepScript = "sql/ia/qc_keep.sql";
        internal const string RecoverScript = "sql/ia/qc_recover.sql";
        // 各脚本最后一组计数里必有的键（缺了 SqlScript 报 500，事务回滚）。
        internal const string KeepKey = "summary_m0";
        internal const string RecoverKey = "summary_m0_left";
        internal const string FifoKey = "fifo_lines_qcass_skipped";
        internal const string FlagKey = "gl_mend_p0";

        const string StartSql = "SELECT TOP 1 LEFT(LTRIM(cValue), 10) v FROM AccInformation WHERE cSysID=N'IA' AND cName=N'dIAStartDate'";
        // 提交后的回读：启用年度第 0 期的 bflag_IA、期初（iMonth=0）汇总行数。
        const string FingerSql = "SELECT"
            + " (SELECT TOP 1 CONVERT(int, ISNULL(bflag_IA,0)) FROM GL_mend WHERE iyear=? AND iperiod=0) flag,"
            + " (SELECT COUNT(*) FROM IA_Summary WHERE iYear=? AND iMonth=0) m0";

        // 脚本拒绝编号 → 中文 409，与脚本里 THROW 的原文一致（OpeningIaSelfTest 逐条核对内嵌脚本）。
        internal static readonly Dictionary<int, string> Texts = BuildTexts();

        static Dictionary<int, string> BuildTexts()
        {
            Dictionary<int, string> map = new Dictionary<int, string>();
            map[50101] = "存货核算未启用";
            map[50102] = "期初年度不是存货核算启用年度";
            map[50103] = "总账期间表（GL_mend）没有启用年度的第 0 期，请在 U8 里检查会计期间";
            map[50104] = "存货核算期初已记账";
            map[50105] = "库存管理未启用，不能从库存期初结存取数";
            map[50106] = "库存管理与存货核算的启用日期不一致，不能期初取数";
            map[50111] = "存货核算未启用";
            map[50112] = "期初年度不是存货核算启用年度";
            map[50113] = "总账期间表（GL_mend）没有启用年度的第 0 期，请在 U8 里检查会计期间";
            map[50114] = "存货核算期初未记账";
            map[50115] = "存货核算已有月份结账，不能取消期初记账";
            map[50116] = "存货核算本年已有日常数据（汇总或明细），不能取消期初记账";
            return map;
        }

        public static ApiResult Run(WorkContext ctx, OpeningAsk ask)
        {
            string start = StartDate(ctx.Conn);
            if (start.Length == 0)
            {
                throw Refuse("存货核算未启用");
            }
            int year = ReportsOpening.YearOf(start, 0);
            string wrong = LoginRefusal(GlState.LoginYear(ctx), year, start);
            if (wrong != null)
            {
                throw BridgeException.BadField("date", wrong).WithHint("登录日期用存货核算启用日期 " + start);
            }
            IaArgs args = Args(year, start, IaRun.Accounter(ctx));
            CoRows.Note(ctx.Item, (ask.Post ? "存货核算期初记账 " : "取消存货核算期初记账 ") + start);
            Dictionary<string, object> counts = Tran(ctx, ask, args, start);
            Reread(ctx, ask, year, counts);
            return ApiResult.Ok(Body(ask, year, start, counts));
        }

        static string StartDate(object conn)
        {
            string text = Rows.Scalar(conn, StartSql, new object[0]);
            DateTime day;
            text = text == null ? "" : text.Trim();
            return DateTime.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out day) ? text : "";
        }

        // U8 在存货核算启用年度做期初记账：登录日期的年度不是启用年度时拒绝（返回原因，可以做时 null）。纯函数，--selftest 用。
        internal static string LoginRefusal(int loginYear, int year, string start)
        {
            if (loginYear == year)
            {
                return null;
            }
            return "存货核算期初记账要以启用年度登录：存货核算启用日期是 " + start + "，登录日期却在 "
                + loginYear.ToString(CultureInfo.InvariantCulture) + " 年";
        }

        // 脚本参数：启用年度、启用月份、记账日期（启用日期）、记账人（登录操作员姓名）。纯函数，--selftest 用。
        internal static IaArgs Args(int year, string start, string accounter)
        {
            IaArgs args = new IaArgs();
            args.Year = year;
            args.Month = int.Parse(start.Substring(5, 2), CultureInfo.InvariantCulture);
            args.KeepDate = start;
            args.Accounter = accounter;
            args.OnUncosted = "skip";
            return args;
        }

        static Dictionary<string, object> Tran(WorkContext ctx, OpeningAsk ask, IaArgs args, string start)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                Dictionary<string, object> counts = Script(ctx, ask, args);
                Settle(ask, counts);
                Preview(ask, args.Year, start, counts);
                ctx.Item.TranAfter = CoTrans.Count(conn);
                BridgeException broken = IaRun.AfterRollback(ctx.Item, true);
                if (broken != null)
                {
                    throw broken;
                }
                CoTrans.CommitSeen(conn);
                open = false;
                return counts;
            }
            catch (Exception)
            {
                IaRun.BeforeRollback(ctx.Item);
                bool wasOpen = open;
                CoRows.CatchTran(conn, ctx.Item, open);
                BridgeException unknown = IaRun.AfterRollback(ctx.Item, wasOpen);
                if (unknown != null)
                {
                    throw unknown;
                }
                throw;
            }
        }

        static Dictionary<string, object> Script(WorkContext ctx, OpeningAsk ask, IaArgs args)
        {
            string[] names = new string[] { ask.Post ? KeepScript : RecoverScript };
            try
            {
                return IaRun.Counts(IaRun.RunScripts(ctx, names, args, ask.Post ? KeepKey : RecoverKey).Counts);
            }
            catch (ScriptRefusal ex)
            {
                CoRows.Note(ctx.Item, "存货核算期初脚本 " + PeriodGate.N(ex.Number) + " " + ex.Message);
                throw Refusal(ex.Number);
            }
        }

        // 脚本拒绝编号 → 409 state_mismatch。纯函数，--selftest 用。
        internal static BridgeException Refusal(int number)
        {
            string text;
            if (!Texts.TryGetValue(number, out text))
            {
                text = "存货核算拒绝本次操作（编号 " + PeriodGate.N(number) + "）";
            }
            return Refuse(text);
        }

        // 提交前核对脚本的计数（事务里，抛出即回滚）：先进先出 / 后进先出的期初行桥不写计价辅助表，拒绝；
        // 第 0 期标志不是目标状态时 409 u8_rejected。
        static void Settle(OpeningAsk ask, Dictionary<string, object> counts)
        {
            if (ask.Post && Count(counts, FifoKey) > 0)
            {
                throw Refuse("有 " + PeriodGate.N((int)Count(counts, FifoKey))
                    + " 行期初在先进先出 / 后进先出计价的仓库或存货上，接口暂不支持（U8 要另写计价辅助表），已回滚；请在 U8 里做期初记账");
            }
            if (Count(counts, FlagKey) != (ask.Post ? 1 : 0))
            {
                throw new BridgeException(409, "u8_rejected", "存货核算期初记账标志没有改过来，已回滚");
            }
        }

        // 计数里的整数；没有或不是整数为 -1。
        internal static long Count(Dictionary<string, object> counts, string key)
        {
            object value;
            if (counts == null || !counts.TryGetValue(key, out value) || !(value is long))
            {
                return -1;
            }
            return (long)value;
        }

        // 预演：提交钩子会回滚，这里交出操作后（事务里）的状态，detail.opening 与真做成功的响应同形（去掉 ok）。
        static void Preview(OpeningAsk ask, int year, string start, Dictionary<string, object> counts)
        {
            if (!DryRun.Active)
            {
                return;
            }
            Dictionary<string, object> body = Body(ask, year, start, counts);
            body.Remove("ok");
            DryRun.Set("opening", body);
        }

        // 已提交。新连接回读；读不出来或与执行结果不符都是 504 outcome_unknown（已提交，先核对，不要重投）。
        static void Reread(WorkContext ctx, OpeningAsk ask, int year, Dictionary<string, object> counts)
        {
            object conn = null;
            Dictionary<string, object> row;
            try
            {
                conn = ctx.OpenFresh();
                row = Rows.One(conn, FingerSql, new object[] { year, year });
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "存货核算期初回读 " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "已提交但未能回读存货核算期初记账状态，请先用 reports/opening_balance 或在 U8 里核对");
            }
            finally
            {
                AdoXml.Close(conn);
            }
            string drift = Drift(ask.Post, counts, GlSql.Int(row, "flag"), GlSql.Int(row, "m0"));
            if (drift != null)
            {
                CoRows.Note(ctx.Item, "存货核算期初回读不符 " + drift);
                throw new BridgeException(504, "outcome_unknown",
                    "已提交，但回读时存货核算期初记账状态与执行结果不符；请先在 U8 里核对，不要直接重试");
            }
        }

        // 回读比对：记账后第 0 期标志为 1、期初汇总行数等于脚本的 summary_m0；取消后标志为 0、期初汇总为 0。
        // 一致返回 null，否则返回不符的项。纯函数，--selftest 用。
        internal static string Drift(bool post, Dictionary<string, object> counts, int flag, int summary)
        {
            if (flag != (post ? 1 : 0))
            {
                return "bflag_IA=" + PeriodGate.N(flag);
            }
            long want = post ? Count(counts, KeepKey) : 0;
            return summary == want ? null : "summary_m0=" + PeriodGate.N(summary);
        }

        internal static Dictionary<string, object> Body(OpeningAsk ask, int year, string start, Dictionary<string, object> counts)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["module"] = ask.Module;
            body["action"] = ask.ActionText();
            body["posted"] = ask.Post;
            body["opening_year"] = year;
            body["start_date"] = start;
            body["counts"] = counts;
            return body;
        }

        static BridgeException Refuse(string message)
        {
            return new BridgeException(409, "state_mismatch", message);
        }
    }
}
