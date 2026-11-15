using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 启用年度 GL_mend 各期间的采购期初记账 / 结账标志（bflag_PU）：只取闸门要看的几项。
    internal sealed class OpeningFlags
    {
        // 有第 0 期（期初）这一行。
        public bool HasZero;
        // 第 0 期 bflag_PU=1（期初已记账）。
        public bool Zero;
        // 第 1 到 12 期有 bflag_PU=1 的（期初记账前不应有）。
        // 启用月份起（且不早于第 1 期）有 bflag_PU=1 的（采购已结账的月份）。
        public bool ClosedFromStart;
    }

    // 采购期初记账 / 取消记账（openings/post module=pu）。U8 没有可无界面调用的组件，桥按 U8 界面执行的 SQL 写（实测核对）：
    // 记账（菜单「采购期初记账」PU0206）是 GL_mend 启用年度、启用月份之前各期（含第 0 期）bflag_PU=1；
    // 取消记账同 U8，把这几期改回 0。都在请求连接的一个事务里：UPDLOCK, HOLDLOCK 读标志 → 闸门 → 改 → 再读 → 提交；
    // 提交后在新连接上回读。预演（rollback）照样走到提交点，由提交钩子回滚。只对配置为测试账套的账套开放（TestAccountGate）。
    // 存货核算（module=ia）交给 OpeningIa。
    internal static class OpeningPost
    {
        const string FlagsSql = "SELECT CONVERT(varchar(4), iperiod) p, CONVERT(varchar(1), CONVERT(int, ISNULL(bflag_PU,0))) f"
            + " FROM GL_mend WITH (UPDLOCK, HOLDLOCK) WHERE iyear=?";
        const string ZeroSql = "SELECT CONVERT(varchar(1), CONVERT(int, ISNULL(bflag_PU,0))) f FROM GL_mend WHERE iyear=? AND iperiod=0";
        const string PostSql = "UPDATE GL_mend SET bflag_PU=1 WHERE iyear=? AND iperiod<?";
        const string UnpostSql = "UPDATE GL_mend SET bflag_PU=0 WHERE iyear=? AND iperiod<?";
        const string InvoiceSql = "SELECT TOP 1 'x' FROM PurBillVouch WITH (UPDLOCK, HOLDLOCK)";
        // U8：存货核算已期初记账时，采购不能取消期初记账。
        const string IaPostedSql = "SELECT TOP 1 'x' FROM GL_mend WITH (UPDLOCK, HOLDLOCK) WHERE iyear=? AND iperiod=0 AND ISNULL(bflag_IA,0)=1";

        public static ApiResult Run(WorkContext ctx)
        {
            OpeningAsk ask = OpeningPostReq.Parse(ctx.Item.Body);
            // 入队后再查一次测试账套名单（登录前已查过）。
            TestAccountGate.Require(ctx.Item, TestAccountGate.OpeningPostText);
            PermCheck.RequireRule(PermCheck.Of(ctx), PermRegistry.ForKey(OpeningPostReq.RuleKey(ask)));
            if (ask.Module == "ia")
            {
                return OpeningIa.Run(ctx, ask);
            }
            string start = ReportsOpening.StartDate(ctx, ask.Module);
            if (start.Length == 0)
            {
                throw Refuse("采购管理未启用");
            }
            int year = ReportsOpening.YearOf(start, 0);
            int month = int.Parse(start.Substring(5, 2), CultureInfo.InvariantCulture);
            CoRows.Note(ctx.Item, (ask.Post ? "采购期初记账 " : "取消采购期初记账 ") + start);
            Tran(ctx, ask, year, month, start);
            Reread(ctx, ask, year);
            return ApiResult.Ok(Body(ask, year, start));
        }

        static void Tran(WorkContext ctx, OpeningAsk ask, int year, int month, string start)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                OpeningFlags flags = Load(conn, year, month);
                Must(Refusal(ask.Post, flags, year));
                if (!ask.Post)
                {
                    UnpostChecks(conn, year);
                }
                GlSql.Exec(conn, ask.Post ? PostSql : UnpostSql, new object[] { year, month });
                if (Load(conn, year, month).Zero != ask.Post)
                {
                    throw new BridgeException(409, "u8_rejected", "期初记账标志没有改过来，已回滚");
                }
                Preview(ask, year, start);
                ctx.Item.TranAfter = CoTrans.Count(conn);
                CoTrans.CommitSeen(conn);
                open = false;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
        }

        // 预演：提交钩子会回滚，这里交出操作后（事务里）的状态，detail.opening 与真做成功的响应同形（去掉 ok）。
        static void Preview(OpeningAsk ask, int year, string start)
        {
            if (!DryRun.Active)
            {
                return;
            }
            Dictionary<string, object> body = Body(ask, year, start);
            body.Remove("ok");
            DryRun.Set("opening", body);
        }

        static OpeningFlags Load(object conn, int year, int month)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, FlagsSql, new object[] { year }, 100);
            List<int[]> periods = new List<int[]>();
            foreach (Dictionary<string, object> row in rows)
            {
                int period;
                if (!int.TryParse(CoRows.Col(row, "p"), NumberStyles.Integer, CultureInfo.InvariantCulture, out period))
                {
                    continue;
                }
                periods.Add(new int[] { period, CoRows.Col(row, "f") == "1" ? 1 : 0 });
            }
            return Flags(periods, month);
        }

        // periods：{ 期间, bflag_PU }。纯函数，--selftest 用（OpeningPostSelfTest）。
        internal static OpeningFlags Flags(List<int[]> periods, int month)
        {
            OpeningFlags f = new OpeningFlags();
            foreach (int[] p in periods)
            {
                bool on = p[1] == 1;
                if (p[0] == 0)
                {
                    f.HasZero = true;
                    f.Zero = on;
                }
                f.ClosedFromStart |= on && p[0] >= 1 && p[0] >= month;
            }
            return f;
        }

        // 闸门（发票检查之外）：返回拒绝原因，可以做时返回 null。纯函数，--selftest 用。
        internal static string Refusal(bool post, OpeningFlags f, int year)
        {
            if (!f.HasZero)
            {
                return "GL_mend 没有 " + year.ToString(CultureInfo.InvariantCulture) + " 年的期初期间（第 0 期），请在 U8 客户端处理";
            }
            if (post)
            {
                if (f.Zero)
                {
                    return "采购期初已记账";
                }
                // 启用月份之前各期的标志不是结账（启用模块时 U8 就会置上），只看启用月份起的各期。
                return f.ClosedFromStart ? "采购已有月份结账，不能期初记账" : null;
            }
            if (!f.Zero)
            {
                return "采购期初未记账";
            }
            return f.ClosedFromStart ? "采购已有月份结账，不能取消期初记账" : null;
        }

        // 已提交。新连接回读第 0 期；读不出来或不是目标状态都是 504 outcome_unknown（已提交，先核对，不要重投）。
        static void Reread(WorkContext ctx, OpeningAsk ask, int year)
        {
            object conn = null;
            string flag;
            try
            {
                conn = ctx.OpenFresh();
                flag = Rows.Scalar(conn, ZeroSql, new object[] { year });
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "期初记账回读 " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "已提交但未能回读采购期初记账标志，请先用 reports/opening_balance 核对");
            }
            finally
            {
                AdoXml.Close(conn);
            }
            bool posted = flag != null && flag.Trim() == "1";
            if (posted != ask.Post)
            {
                throw new BridgeException(504, "outcome_unknown", "已提交，但回读时采购期初记账标志不是预期状态；请先用 reports/opening_balance 核对，不要直接重试");
            }
        }

        static Dictionary<string, object> Body(OpeningAsk ask, int year, string start)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["module"] = ask.Module;
            body["action"] = ask.ActionText();
            body["posted"] = ask.Post;
            body["opening_year"] = year;
            body["start_date"] = start;
            return body;
        }

        static void Must(string refusal)
        {
            if (refusal != null)
            {
                throw Refuse(refusal);
            }
        }

        static void UnpostChecks(object conn, int year)
        {
            if (Rows.Scalar(conn, IaPostedSql, new object[] { year }) != null)
            {
                throw Refuse("存货核算已期初记账，不能取消采购期初记账");
            }
            if (Rows.Scalar(conn, InvoiceSql, new object[0]) != null)
            {
                throw Refuse("已有采购发票，不能取消期初记账");
            }
        }

        static BridgeException Refuse(string message)
        {
            return new BridgeException(409, "state_mismatch", message);
        }
    }
}
