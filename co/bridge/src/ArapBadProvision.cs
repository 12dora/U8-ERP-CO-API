using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 一次计提坏账准备的结果（脚本计数与账龄区间）。
    internal sealed class ProvisionResult
    {
        public int Method;
        public decimal Base;
        public decimal Rate;
        public decimal Target;
        public decimal RemainBefore;
        public decimal Amount;
        public decimal RemainAfter;
        public string CancelNo = "";
        public int RowId;
        public List<Dictionary<string, object>> Buckets = new List<Dictionary<string, object>>();
    }

    // 应收 计提坏账准备（arap/bad_debt action=provision，U8 处理方式 9F，处理号 HZAR…）。只对测试账套开放：
    // 测试账套闸门、权限、锁由 arap/bad_debt 的分派（ArapBad）在调用前做完。
    // U8 没有可无界面调用的组件：桥按 U8「计提坏账准备」界面执行的 SQL 写（sql/arap/bad_provision.sql，实测核对），在请求连接的一个事务里：
    // 闸门（会计期间、应收启用日期、结账）→ 脚本（取当年坏账准备参数、按计提方法算目标余额、本次计提 = 目标 − 当前余额、编号、
    // 更新当年 Ar_BadPara）。不写往来明细（U8 同）；当年没有坏账准备参数行时拒绝，不替 U8 新建年度行。提交后在新连接上回读。
    internal static class ArapBadProvision
    {
        const int ScriptSeconds = 90;
        internal const string Action = "provision";
        // 预演响应 detail 的键。
        internal const string DryKey = "bad_debt";

        public static ApiResult Run(WorkContext ctx, ArapBadAsk ask)
        {
            if (ctx == null || ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "缺少 U8 登录");
            }
            if (ask == null)
            {
                throw new BridgeException(500, "internal", "缺少计提坏账准备的请求");
            }
            ProvisionArgs args = Gates(ctx, Day(ask.Date));
            ProvisionResult result = Tran(ctx, args);
            Reread(ctx, args, result);
            return ApiResult.Ok(Body(ctx, args, result, false));
        }

        // 会计期间（UA_Period）、应收启用日期、结账；都在脚本之前查，不符 409。
        static ProvisionArgs Gates(WorkContext ctx, string date)
        {
            object conn = ctx.Conn;
            int[] found = WriteoffSql.PeriodOf(conn, ctx.Item.Acc, date);
            if (found == null)
            {
                throw ArapWriteoffGate.State("计提日期 " + date + " 不在 U8 的会计期间里");
            }
            DateTime start;
            if (WriteoffSql.StartDate(conn, "AR", out start) && string.CompareOrdinal(date, Day(start)) < 0)
            {
                throw ArapWriteoffGate.State("计提日期早于应收系统启用日期 " + Day(start));
            }
            if (WriteoffSql.Closed(conn, "AR", found[0], found[1]))
            {
                throw ArapWriteoffGate.State("应收 " + found[0].ToString(CultureInfo.InvariantCulture) + " 年 "
                    + found[1].ToString(CultureInfo.InvariantCulture) + " 月已结账");
            }
            ProvisionArgs args = new ProvisionArgs();
            args.Date = date;
            args.Year = found[0];
            args.Period = found[1];
            args.YearStart = ArapBadProvisionSql.YearStart(conn, ctx.Item.Acc, found[0]);
            args.PayDays = ArapBadProvisionSql.PayDays(conn);
            return args;
        }

        static ProvisionResult Tran(WorkContext ctx, ProvisionArgs args)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                ProvisionResult result = null;
                ArapWriteoffCheck.Guard(conn, delegate { result = Write(ctx, args); });
                ArapBadProvisionSql.Drop(conn);
                if (DryRun.Active)
                {
                    DryRun.Set(DryKey, Body(ctx, args, result, true));
                }
                Finish(ctx);
                open = false;
                CoRows.Note(ctx.Item, "已计提坏账准备 " + result.CancelNo + " 金额 "
                    + result.Amount.ToString(CultureInfo.InvariantCulture));
                return result;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
        }

        // 提交前核对事务层数（脚本自行提交、回滚过就说不清写没写进去，504），然后提交（预演由提交钩子回滚）。
        static void Finish(WorkContext ctx)
        {
            object conn = ctx.Conn;
            ctx.Item.TranAfter = CoTrans.Count(conn);
            int before;
            int after;
            if (!int.TryParse(ctx.Item.TranBefore, out before) || !int.TryParse(ctx.Item.TranAfter, out after)
                || !IaRun.TranIntact(before, after))
            {
                CoRows.Note(ctx.Item, "计提坏账准备事务层数 " + ctx.Item.TranBefore + " → " + ctx.Item.TranAfter);
                throw new BridgeException(504, "outcome_unknown", "计提坏账准备中事务被提前结束，结果未知；请先在 U8 里核对坏账准备");
            }
            CoTrans.CommitSeen(conn);
        }

        static ProvisionResult Write(WorkContext ctx, ProvisionArgs args)
        {
            ArapBadProvisionSql.Prepare(ctx.Conn, args);
            ScriptResult script;
            try
            {
                script = SqlScript.RunPrepared(ctx.Conn, new string[] { ArapBadProvisionSql.Script }, ScriptSeconds, "cancel_no");
            }
            catch (ScriptRefusal ex)
            {
                CoRows.Note(ctx.Item, "计提坏账准备脚本 " + ex.Number.ToString(CultureInfo.InvariantCulture) + " " + ex.Message);
                throw ArapBadProvisionSql.Refused(ex, args.Year, args.Period);
            }
            catch (BridgeException ex)
            {
                if (ex.Code == "ia_timeout")
                {
                    throw new BridgeException(503, "ia_timeout", "计提坏账准备超时，已回滚；可稍后重试");
                }
                throw;
            }
            ProvisionResult result = Shape(script);
            if (result.CancelNo.Length == 0 || result.RowId <= 0)
            {
                throw new BridgeException(500, "internal", "计提坏账准备脚本没有返回处理号，已回滚");
            }
            return result;
        }

        // 脚本计数 → 结果；账龄法另带区间清单。纯函数，--selftest 用。
        internal static ProvisionResult Shape(ScriptResult script)
        {
            ProvisionResult result = new ProvisionResult();
            result.Method = CoRows.AsId(Count(script, "style"));
            result.Base = WriteoffSql.Num(Count(script, "base"));
            result.Rate = ArapBadProvisionSql.Trim(WriteoffSql.Num(Count(script, "rate")));
            result.Target = WriteoffSql.Num(Count(script, "target"));
            result.RemainBefore = WriteoffSql.Num(Count(script, "remain_before"));
            result.Amount = WriteoffSql.Num(Count(script, "jt"));
            result.RemainAfter = WriteoffSql.Num(Count(script, "remain_after"));
            result.CancelNo = Count(script, "cancel_no");
            result.RowId = CoRows.AsId(Count(script, "row_id"));
            if (result.Method == 2 && script.Rows != null)
            {
                foreach (Dictionary<string, object> row in script.Rows)
                {
                    result.Buckets.Add(Bucket(row));
                }
            }
            return result;
        }

        // 一个账龄区间：from / to 是账龄天数（含两端，to 为 null 表示以上），rate 是百分比，amount = balance × rate / 100。
        internal static Dictionary<string, object> Bucket(Dictionary<string, object> row)
        {
            Dictionary<string, object> one = new Dictionary<string, object>();
            one["from"] = CoRows.AsId(CoRows.Col(row, "bucket_from"));
            string to = CoRows.Col(row, "bucket_to");
            one["to"] = to.Length == 0 ? null : (object)CoRows.AsId(to);
            one["balance"] = WriteoffSql.Num(CoRows.Col(row, "balance"));
            one["rate"] = ArapBadProvisionSql.Trim(WriteoffSql.Num(CoRows.Col(row, "rate")));
            one["amount"] = WriteoffSql.Num(CoRows.Col(row, "amount"));
            return one;
        }

        static string Count(ScriptResult script, string key)
        {
            string text;
            return script != null && script.Counts.TryGetValue(key, out text) ? (text ?? "").Trim() : "";
        }

        // 已提交。新连接（不加 NOLOCK）确认当年那一行的处理号和余额；读不出来或不符都是 504 outcome_unknown（不要重投）。
        static void Reread(WorkContext ctx, ProvisionArgs args, ProvisionResult result)
        {
            object conn = null;
            Dictionary<string, object> row;
            try
            {
                conn = ctx.OpenFresh();
                row = ArapBadProvisionSql.Reread(conn, result.RowId, args.Year);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "ArapBadProvision " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "坏账准备已计提但未能回读，处理号 " + result.CancelNo);
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (!Matches(row, result))
            {
                throw new BridgeException(504, "outcome_unknown", "坏账准备已计提，但回读时处理号 " + result.CancelNo
                    + " 与坏账准备余额不符；请先在 U8 里核对，不要直接重试");
            }
        }

        // 回读的行：处理号相同、余额等于计提后余额。纯函数，--selftest 用。
        internal static bool Matches(Dictionary<string, object> row, ProvisionResult result)
        {
            return row != null && CoRows.Col(row, "no") == result.CancelNo
                && WriteoffSql.Num(CoRows.Col(row, "remain")) == result.RemainAfter;
        }

        // 响应：账龄法给 buckets，另两种给 rate（Ar_BadPara.nJtRate，小数）；base 对账龄法是各区间余额合计。
        internal static Dictionary<string, object> Body(WorkContext ctx, ProvisionArgs args, ProvisionResult result, bool dry)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["acc"] = ctx == null || ctx.Item == null ? "" : ctx.Item.Acc;
            body["action"] = Action;
            body["date"] = args.Date;
            body["fiscal_year"] = args.Year;
            body["period"] = args.Period;
            body["cancel_no"] = result.CancelNo;
            body["style"] = ArapBadProvisionSql.Style;
            body["style_name"] = ArapBadProvisionSql.StyleName;
            body["method"] = result.Method;
            body["method_name"] = ArapBadProvisionSql.MethodName(result.Method);
            body["base"] = result.Base;
            if (result.Method == 2)
            {
                body["buckets"] = result.Buckets;
            }
            else
            {
                body["rate"] = result.Rate;
            }
            body["target"] = result.Target;
            body["remain_before"] = result.RemainBefore;
            body["amount"] = result.Amount;
            body["remain_after"] = result.RemainAfter;
            body["dry_run"] = dry;
            return body;
        }

        static string Day(DateTime day)
        {
            return day.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        }
    }
}
