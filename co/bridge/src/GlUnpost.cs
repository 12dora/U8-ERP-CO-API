using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 取消记账 gl/vouchers/unpost（第二级写入，只对测试账套开放）：撤销本年度最近一次记账，相当于 U8 客户端
    // 「恢复记账前状态 → 最近一次记账」。U8 的这个功能在 VB6 客户端里，没有可无界面调用的组件（总账 .NET 组件只有记账方向），
    // 桥按 U8 记账对各总账表的更新写逆运算（GlUnpostSql，结果与 U8「恢复记账前状态」实测一致）。
    // 范围只认 GL_mpostcond 本年度的行（U8 记账时写入，桥记账同样留下）；U8 没有按单张凭证取消记账，桥也不挑选。
    // 全在请求连接的一个事务里：第一条语句同记账锁 GL_mpostcond1，读范围并加锁、过闸门（GlUnpostCheck），
    // 冲回科目总账、辅助总账、多辅助总账，凭证改回未记账，自检（与已记账凭证重新汇总一致），清 GL_mpostcond 本年度，提交。
    // 预演（rollback）照样走到提交点，由提交钩子回滚。提交后在新连接上回读。
    internal static class GlUnpost
    {
        const string BookSql = "SELECT CONVERT(varchar(4), MAX(ISNULL(CONVERT(int, ibook),0))) b FROM GL_accvouch"
            + " WHERE iyear=? AND iperiod=? AND isignseq=? AND ino_id=?";
        const string RangeSql = "SELECT CONVERT(varchar(12), COUNT(*)) n FROM GL_mpostcond WHERE iyear=?";

        public static ApiResult Run(WorkContext ctx)
        {
            TestAccountGate.Require(ctx.Item, GlUnpostReq.TestOnly);
            GlUnpostAsk ask = GlUnpostReq.Parse(ctx.Item.Body, GlState.LoginYear(ctx));
            PermCheck.RequireRule(PermCheck.Of(ctx), PermRegistry.ForKey(GlUnpostReq.Rule));
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                Rows.Scalar(conn, GlUnpostSql.LockSql, new object[0]);
                GlUnpostCheck.Batch(conn, ask);
                CoRows.Note(ctx.Item, "取消记账 " + ask.Summary());
                GlUnpostCheck.Gates(conn, ask);
                Options(conn, ctx, ask);
                Reverse(conn, ask);
                GlUnpostCheck.After(conn, ask);
                GlSql.Exec(conn, GlUnpostSql.ClearSql, new object[] { ask.Year });
                Preview(ask);
                ctx.Item.TranAfter = CoTrans.Count(conn);
                CoTrans.CommitSeen(conn);
                open = false;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
            return Readback(ctx, ask);
        }

        // 总账选项「启用多币种核算」决定科目总账是否按币种分行；本币名称取 UFSYSTEM..UA_Account（同 U8 的 GetCurName）。
        static void Options(object conn, WorkContext ctx, GlUnpostAsk ask)
        {
            ask.Multi = GlState.Option(conn, "IsUseMultiCurrency", false);
            if (ask.Multi)
            {
                ask.Currency = Rows.Scalar(conn, GlUnpostSql.CurrencySql, new object[] { ctx.Item.Acc ?? "" }) ?? "";
            }
        }

        // 顺序同 U8 记账：科目总账、辅助总账、多辅助总账，最后凭证标志。
        static void Reverse(object conn, GlUnpostAsk ask)
        {
            int y = ask.Year;
            int p = ask.Period;
            object[] sum = new object[] { ask.Currency, y, p, y, p, y };
            GlSql.Exec(conn, GlUnpostSql.SumPeriod(ask.Multi), sum);
            GlSql.Exec(conn, GlUnpostSql.SumLater(ask.Multi), sum);
            object[] ass = new object[] { y, p, p, y };
            GlSql.Exec(conn, GlUnpostSql.AssPeriod(false), ass);
            GlSql.Exec(conn, GlUnpostSql.AssLater(false), ass);
            GlSql.Exec(conn, GlUnpostSql.AssPeriod(true), ass);
            GlSql.Exec(conn, GlUnpostSql.AssLater(true), ass);
            GlSql.Exec(conn, GlUnpostSql.VouchSql, new object[] { y, p });
        }

        // 预演：提交钩子会回滚，这里先交出事务内的结果（rollback）。
        static void Preview(GlUnpostAsk ask)
        {
            if (!DryRun.Active)
            {
                return;
            }
            Dictionary<string, object> detail = new Dictionary<string, object>();
            detail["iyear"] = ask.Year;
            detail["iperiod"] = ask.Period;
            detail["multi_currency"] = ask.Multi;
            detail["vouchers"] = Vouchers(ask);
            detail["count"] = ask.Batch.Count;
            DryRun.Set("unpost", detail);
        }

        static List<object> Vouchers(GlUnpostAsk ask)
        {
            List<object> list = new List<object>();
            foreach (GlUnpostItem item in ask.Batch)
            {
                Dictionary<string, object> one = new Dictionary<string, object>();
                one["sign"] = item.Sign;
                one["no"] = item.No;
                list.Add(one);
            }
            return list;
        }

        // 已提交。回读失败、或读到凭证仍是记账状态、范围没清掉，都是 504 outcome_unknown（消息写明已提交）。
        static ApiResult Readback(WorkContext ctx, GlUnpostAsk ask)
        {
            object fresh = null;
            try
            {
                fresh = ctx.OpenFresh();
                string range = Rows.Scalar(fresh, RangeSql, new object[] { ask.Year });
                bool done = range != null && range.Trim() == "0";
                foreach (GlUnpostItem item in ask.Batch)
                {
                    string book = Rows.Scalar(fresh, BookSql, new object[] { ask.Year, item.Period, item.Seq, item.No });
                    done = done && book != null && book.Trim() == "0";
                }
                if (!done)
                {
                    throw Unknown(ask);
                }
                Dictionary<string, object> body = new Dictionary<string, object>();
                body["ok"] = true;
                body["fiscal_year"] = ask.Year;
                body["period"] = ask.Period;
                body["count"] = ask.Batch.Count;
                body["vouchers"] = Vouchers(ask);
                return ApiResult.Ok(body);
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "GlUnpostReadback " + ex.Message);
                throw Unknown(ask);
            }
            finally
            {
                AdoXml.Close(fresh);
            }
        }

        static BridgeException Unknown(GlUnpostAsk ask)
        {
            return new BridgeException(504, "outcome_unknown", "取消记账已提交，但回读凭证状态不符或失败（" + ask.Summary()
                + "，共 " + ask.Batch.Count.ToString(CultureInfo.InvariantCulture) + " 张）；请先用 gl/vouchers/load 核对，不要直接重试");
        }
    }
}
