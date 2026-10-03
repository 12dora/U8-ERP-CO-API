using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 坏账发生 / 收回在事务里过完公共闸门之后的状态：会计期间，以及 U8 要改的那一行坏账准备参数（Ar_BadPara 的 max(autoid) 行）。
    internal sealed class BadOpen
    {
        public string Date;
        public int Year;
        public int Period;
        public int ParaId;
        public int Style;
        public decimal RemainBefore;
        public string Accounter;
    }

    // 应收坏账处理（arap/bad_debt）的分派：action = occur（坏账发生 9G，ArapBadOccur）/ recover（坏账收回 9H，ArapBadRecover）/
    // provision（计提坏账准备 9F，ArapBadProvision）。三者都是第二级写入：只对测试账套开放（入队后再查一次 TestAccountGate），
    // 功能权限按动作（PermRegistry.BadDebtKey）。U8 没有可无界面调用的组件，桥按 U8 界面执行的 SQL 写（sql/arap/bad_*.sql，实测核对），
    // 在请求连接的一个事务里执行，预演由提交钩子回滚。这里另有坏账发生、收回共用的闸门、脚本执行和提交。
    internal static class ArapBad
    {
        internal const int ScriptSeconds = 90;
        // 预演响应 detail 的键（与 ArapBadProvision.DryKey 相同）。
        internal const string DryKey = "bad_debt";
        const int AccounterMax = 20;

        // U8 坏账发生 / 收回改的是 max(autoid) 那一行（不按年度）；桥另要求它就是登记日期所在年度的那一行。
        const string ParaSql = "select top 1 convert(varchar(20), autoid) as id, convert(varchar(10), iYear) as y, "
            + "convert(varchar(10), iJtStyle) as style, convert(varchar(40), convert(decimal(28,2), isnull(iRemainAmount,0))) as remain "
            + "from Ar_BadPara with (UPDLOCK, HOLDLOCK) order by autoid desc";

        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "缺少 U8 登录");
            }
            ArapBadAsk ask = ArapBadReq.Parse(ctx.Item.Body);
            ask.DryRun = ctx.Item.DryRun;
            // 入队后再查一次测试账套名单（登录前已查过）。
            TestAccountGate.Require(ctx.Item, ArapBadReq.TestOnly);
            PermCheck.RequireRule(PermCheck.Of(ctx), PermRegistry.ForKey(PermRegistry.BadDebtKey(ask.Action)));
            switch (ask.Action)
            {
                case ArapBadReq.Occur:
                    return ArapBadOccur.Run(ctx, ask);
                case ArapBadReq.Recover:
                    return ArapBadRecover.Run(ctx, ask);
                default:
                    return ArapBadProvision.Run(ctx, ask);
            }
        }

        // 事务里的公共闸门：登记日期所在会计期间（UA_Period）、不早于应收启用日期、应收未结账；坏账准备参数。都在脚本之前查，不符 409。
        internal static BadOpen Open(WorkContext ctx, ArapBadAsk ask)
        {
            object conn = ctx.Conn;
            BadOpen open = new BadOpen();
            open.Date = Day(ask.Date);
            int[] found = WriteoffSql.PeriodOf(conn, ctx.Item.Acc, open.Date);
            if (found == null)
            {
                throw State("日期 " + open.Date + " 不在 U8 的会计期间里");
            }
            DateTime start;
            if (WriteoffSql.StartDate(conn, "AR", out start) && ask.Date < start)
            {
                throw State("日期早于应收系统启用日期 " + Day(start));
            }
            open.Year = found[0];
            open.Period = found[1];
            if (WriteoffSql.Closed(conn, "AR", open.Year, open.Period))
            {
                throw State(ClosedText(open.Year, open.Period));
            }
            Dictionary<string, object> para = Rows.One(conn, ParaSql, new object[0]);
            string why = ParaRefusal(para, open.Year);
            if (why != null)
            {
                throw State(why);
            }
            open.ParaId = CoRows.AsId(CoRows.Col(para, "id"));
            open.Style = CoRows.AsId(CoRows.Col(para, "style"));
            open.RemainBefore = WriteoffSql.Num(CoRows.Col(para, "remain"));
            open.Accounter = Accounter(ctx);
            return open;
        }

        internal static string ClosedText(int year, int period)
        {
            return "应收 " + year.ToString(CultureInfo.InvariantCulture) + " 年 " + period.ToString(CultureInfo.InvariantCulture)
                + " 月已结账";
        }

        // 坏账准备参数行（max(autoid)）不合用时的原因，合用返回 null。纯函数，--selftest 用。
        internal static string ParaRefusal(Dictionary<string, object> para, int year)
        {
            string unset = "未设置 " + year.ToString(CultureInfo.InvariantCulture) + " 年坏账准备参数（应收款管理 › 设置 › 坏账准备）";
            if (para == null || CoRows.AsId(CoRows.Col(para, "id")) <= 0)
            {
                return unset;
            }
            string y = CoRows.Col(para, "y");
            if (y != year.ToString(CultureInfo.InvariantCulture))
            {
                return unset + "；最新一行坏账准备参数是 " + (y.Length == 0 ? "未知" : y) + " 年的";
            }
            int style = CoRows.AsId(CoRows.Col(para, "style"));
            if (style < 1 || style > 3)
            {
                return "不支持的坏账计提方法（Ar_BadPara.iJtStyle = " + CoRows.Col(para, "style") + "）";
            }
            return null;
        }

        static string Accounter(WorkContext ctx)
        {
            string name = (ctx.OperatorName ?? "").Trim();
            if (name.Length == 0)
            {
                throw new BridgeException(500, "internal", "读不到登录操作员的姓名");
            }
            if (name.Length > AccounterMax)
            {
                throw State("操作员姓名超过 20 个字符，U8 不能记录处理人；请换一个操作员");
            }
            return name;
        }

        // 执行一段脚本：拒绝按 texts 换成中文（英文原文进审计备注），超时换成本功能的说明。
        internal static ScriptResult Script(WorkContext ctx, string name, string key, string title, Dictionary<int, string> texts)
        {
            try
            {
                return SqlScript.RunPrepared(ctx.Conn, new string[] { name }, ScriptSeconds, key);
            }
            catch (ScriptRefusal ex)
            {
                CoRows.Note(ctx.Item, title + "脚本 " + ex.Number.ToString(CultureInfo.InvariantCulture) + " " + ex.Message);
                throw Refused(ex, texts);
            }
            catch (BridgeException ex)
            {
                if (ex.Code == "ia_timeout")
                {
                    throw new BridgeException(503, "ia_timeout", title + "超时，已回滚；可稍后重试");
                }
                throw;
            }
        }

        // 公共编号（两段脚本相同）→ 中文 409，再查各自的 texts；50101（参数缺失）和没登记的编号 500。
        // {0} 是英文提示最后一个冒号后的部分（单号、处理号）。
        internal static BridgeException Refused(ScriptRefusal refusal, Dictionary<int, string> texts)
        {
            int number = refusal == null ? 0 : refusal.Number;
            string tail = ArapExGainSql.TailOf(refusal == null ? "" : refusal.Message);
            string text = Common(number);
            if (text == null && (texts == null || !texts.TryGetValue(number, out text)))
            {
                return new BridgeException(500, "internal", "坏账处理脚本拒绝 " + number.ToString(CultureInfo.InvariantCulture));
            }
            return State(string.Format(CultureInfo.InvariantCulture, text, tail.Length > 0 ? "：" + tail : ""));
        }

        static string Common(int number)
        {
            switch (number)
            {
                case 50102:
                    return "登记日期所在期间应收已结账{0}";
                case 50103:
                    return "坏账准备参数在处理过程中被修改，已回滚，请重试{0}";
                case 50104:
                    return "处理号已被占用，请检查 Ap_CancelNo（HZ / AR）{0}";
                default:
                    return null;
            }
        }

        // 提交前核对事务层数（脚本或 U8 组件自行提交、回滚过就说不清写没写进去，504），然后提交（预演由提交钩子回滚）。
        internal static void Finish(WorkContext ctx, string title)
        {
            object conn = ctx.Conn;
            ctx.Item.TranAfter = CoTrans.Count(conn);
            int before;
            int after;
            if (!int.TryParse(ctx.Item.TranBefore, out before) || !int.TryParse(ctx.Item.TranAfter, out after)
                || !IaRun.TranIntact(before, after))
            {
                CoRows.Note(ctx.Item, title + "事务层数 " + ctx.Item.TranBefore + " → " + ctx.Item.TranAfter);
                throw new BridgeException(504, "outcome_unknown", title + "中事务被提前结束，结果未知；请先在 U8 里核对往来明细和坏账准备");
            }
            CoTrans.CommitSeen(conn);
        }

        // 该处理号在往来明细上的行数（9G / 9H）。
        internal static int CountRows(object conn, string style, string no)
        {
            string sql = "select convert(varchar(20), count(*)) from Ar_Detail where cProcStyle=? and cCancelNo=? and cFlag=N'AR'";
            return CoRows.AsId(Rows.Scalar(conn, sql, new object[] { style, no }));
        }

        // 坏账准备参数那一行当前的余额（事务里核对、提交后回读用）。
        internal static decimal ParaRemain(object conn, int id)
        {
            string sql = "select convert(varchar(40), convert(decimal(28,2), isnull(iRemainAmount,0))) from Ar_BadPara where autoid=?";
            return WriteoffSql.Num(Rows.Scalar(conn, sql, new object[] { id }));
        }

        // 坏账发生、收回响应的公共部分。
        internal static Dictionary<string, object> Body(WorkContext ctx, ArapBadAsk ask, BadOpen open, string style, bool dry)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["acc"] = ctx == null || ctx.Item == null ? "" : ctx.Item.Acc;
            body["action"] = ask.Action;
            body["date"] = open.Date;
            body["fiscal_year"] = open.Year;
            body["period"] = open.Period;
            body["style"] = style;
            body["style_name"] = style == ArapBadRule.OccurStyle ? "坏账发生" : "坏账收回";
            body["customer"] = ask.Customer;
            body["dry_run"] = dry;
            return body;
        }

        internal static BridgeException State(string message)
        {
            return new BridgeException(409, "state_mismatch", message);
        }

        internal static string Day(DateTime day)
        {
            return day.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        }
    }
}
