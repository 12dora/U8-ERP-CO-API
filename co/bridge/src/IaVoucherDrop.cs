using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 存货核算取消制单（ia/voucher/delete，第二级写入）：按外部业务号删除存货核算生成的凭证并清掉存货明细账上的凭证号
    // （U8「存货核算 → 凭证列表」里删除）。在请求连接的一个事务里：带锁找凭证 → 闸门（U8 删除凭证的拒绝原因：已记账、已审核、
    // 已出纳签字、所在期间已结账；另：制单系统是存货核算、没被别人打开、没被红字冲销、存货明细账的月份未结账、
    // 凭证没有引用采购结算 / 发票等桥不清除的单据）→ 删 GL_CashTable / GL_CodeRemark / GL_accvouch → 清除（同 U8 的清除 SQL）→
    // 核对再也没有引用 → 提交；提交后在新连接上回读。恢复记账后留下的没有回写的凭证也能删（清除时没有行可清）。
    internal static class IaVoucherDrop
    {
        const string FindSql = "select convert(varchar(6), iyear) as y, convert(varchar(4), iperiod) as p, csign as s, "
            + "convert(varchar(12), ino_id) as n from GL_accvouch with (UPDLOCK, HOLDLOCK) "
            + "where coutno_id=? group by iyear, iperiod, csign, ino_id";
        // U8 删除存货核算凭证时另清的单据（采购结算、应付、发票、产成品成本分配）：桥生成的凭证不会引用它们，引用了就是 U8 客户端生成的，桥不复现。
        const string ForeignSql = "select top 1 'x' from Ap_Detail where cPZid=? union all select top 1 'x' from PurBillVouchs where cClue=? "
            + "union all select top 1 'x' from Ap_CloseBill where cPzID=? union all select top 1 'x' from PurSettleVouch where bMakePz=? "
            + "union all select top 1 'x' from IA_DecReady where cPZID=?";

        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "缺少 U8 登录");
            }
            TestAccountGate.Require(ctx.Item, IaVoucherReq.DropTestOnly);
            IaVoucherDropAsk ask = IaVoucherReq.ParseDrop(ctx.Item.Body);
            PermCheck.RequireRule(PermCheck.Of(ctx), PermRegistry.ForKey(IaVoucherReq.RuleKey(true)));
            GlKey key = Tran(ctx, ask);
            return Reread(ctx, ask, key);
        }

        static GlKey Tran(WorkContext ctx, IaVoucherDropAsk ask)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                GlKey key = Find(conn, ask.PzId);
                Gate(conn, ask.PzId, key);
                int rows = Count(conn, "select convert(varchar(12), count(*)) from IA_Subsidiary where cPZID=?", ask.PzId);
                ArapVoucherBack.DeleteGl(conn, key, GlState.SignSeq(conn, key.Sign));
                IaVoucherBack.Clear(conn, ask.PzId);
                if (IaVoucherBack.Left(conn, ask.PzId) != 0)
                {
                    throw new BridgeException(409, "u8_rejected", "取消制单后仍有引用该凭证的行，已回滚");
                }
                Preview(ask, key, rows);
                ctx.Item.TranAfter = CoTrans.Count(conn);
                CoTrans.CommitSeen(conn);
                open = false;
                CoRows.Note(ctx.Item, "已取消存货核算制单 " + ask.PzId + " 凭证 " + key.Text());
                return key;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
        }

        static GlKey Find(object conn, string pzId)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, FindSql, new object[] { pzId }, 3);
            if (rows.Count == 0)
            {
                throw new BridgeException(404, "not_found", "凭证不存在：" + pzId);
            }
            if (rows.Count > 1)
            {
                throw IaVoucherLoad.Refuse("外部业务号 " + pzId + " 对应多张凭证，请在 U8 客户端处理");
            }
            Dictionary<string, object> row = rows[0];
            GlKey key = new GlKey();
            key.Year = CoRows.AsId(CoRows.Col(row, "y"));
            key.Period = CoRows.AsId(CoRows.Col(row, "p"));
            key.Sign = CoRows.Col(row, "s");
            key.No = CoRows.AsId(CoRows.Col(row, "n"));
            return key;
        }

        static void Gate(object conn, string pzId, GlKey key)
        {
            GlHead head = GlState.Need(conn, key, true);
            Must(!head.Mixed, "凭证各行状态不一致，请在 U8 客户端处理");
            Must(!head.Posted, "此凭证已记账，不能删除");
            Must(head.Checker.Length == 0, "此凭证已审核，不能删除");
            Must(head.Cashier.Length == 0, "此凭证已经出纳签字，不能删除");
            Must(head.Marks == 0, "凭证已做银行对账或往来两清");
            Must(head.OutSys == IaVoucherSave.System, "凭证不是存货核算生成的（制单系统 " + head.OutSys + "）");
            string closed = Rows.Scalar(conn, "select convert(varchar(1), isnull(bflag,0)) from GL_mend where iyear=? and iperiod=?",
                new object[] { key.Year, key.Period });
            Must(closed == null || closed.Trim() == "0", "此凭证所在期间已结账，不能删除");
            GlState.NotLocked(conn, key);
            GlState.NotReversed(conn, head);
            Must(Rows.Scalar(conn, ForeignSql, new object[] { pzId, pzId, pzId, pzId, pzId }) == null,
                "该凭证还引用了采购结算、应付或发票等单据（U8 客户端生成的），请在 U8 客户端删除");
            Types(conn, pzId);
            string month = Rows.Scalar(conn, "select top 1 convert(varchar(4), s.iYear) + N'-' + convert(varchar(2), s.iMonth) from IA_Subsidiary s "
                + "inner join GL_mend m on m.iyear=s.iYear and m.iperiod=s.iMonth where s.cPZID=? and isnull(m.bflag_IA,0)<>0", new object[] { pzId });
            Must(month == null, "存货核算已结账（单据记账月份 " + (month ?? "").Trim() + "）");
        }

        // 引用该凭证的存货明细账都是桥支持制单的单据类型；别的类型（U8 客户端生成的发票、结算、期初差异等凭证）有各自的回写，桥不复现。
        static void Types(object conn, string pzId)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, "select distinct isnull(cVouType,N'') t from IA_Subsidiary where cPZID=?",
                new object[] { pzId }, 50);
            foreach (Dictionary<string, object> row in rows)
            {
                string type = CoRows.Col(row, "t");
                Must(IaType.Find(type) != null, "该凭证含有桥不支持取消制单的单据类型（" + type + "），请在 U8 客户端删除");
            }
        }

        // 预演：提交钩子会回滚，这里交出要删的凭证键和外部业务号。
        static void Preview(IaVoucherDropAsk ask, GlKey key, int rows)
        {
            if (!DryRun.Active)
            {
                return;
            }
            Dictionary<string, object> gl = new Dictionary<string, object>();
            gl["pz_id"] = ask.PzId;
            gl["iyear"] = key.Year;
            gl["iperiod"] = key.Period;
            gl["csign"] = key.Sign;
            gl["ino_id"] = key.No;
            gl["rows"] = rows;
            gl["deleted"] = true;
            DryRun.Set("gl", gl);
        }

        static ApiResult Reread(WorkContext ctx, IaVoucherDropAsk ask, GlKey key)
        {
            object conn = null;
            int left;
            try
            {
                conn = ctx.OpenFresh();
                left = IaVoucherBack.Left(conn, ask.PzId);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "存货核算取消制单回读 " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "已提交但未能回读，外部业务号 " + ask.PzId);
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (left != 0)
            {
                throw new BridgeException(504, "outcome_unknown", "取消制单已提交（凭证 " + key.Text() + "），但回读时仍有引用外部业务号 "
                    + ask.PzId + " 的行；请先查询核对，不要直接重试");
            }
            Dictionary<string, object> voucher = new Dictionary<string, object>();
            voucher["year"] = key.Year;
            voucher["period"] = key.Period;
            voucher["sign"] = key.Sign;
            voucher["no"] = key.No;
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["acc"] = ctx.Item.Acc;
            body["pz_id"] = ask.PzId;
            body["deleted"] = true;
            body["voucher"] = voucher;
            return ApiResult.Ok(body);
        }

        static int Count(object conn, string sql, string arg)
        {
            return CoRows.AsId(Rows.Scalar(conn, sql, new object[] { arg }));
        }

        static void Must(bool ok, string message)
        {
            if (!ok)
            {
                throw IaVoucherLoad.Refuse(message);
            }
        }
    }
}
