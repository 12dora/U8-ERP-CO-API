using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 取消制单（arap/voucher/delete）：按外部业务号删除应收（应付）生成的凭证并清掉单据上的凭证号。
    // U8 界面是「凭证查询」里删除（删凭证后清掉单据上的凭证号）；桥在请求连接的一个事务里：
    // 带锁找凭证 → 闸门（未记账、未审核、未出纳签字、没有两清对账、期间未结账、没被别人打开、没被红字冲销、是本系统生成的、
    // 应收应付两张往来明细里引用它的都是本方发票 / 收付款单 / 应收应付单的原始行、应收应付未结账）→ 删 GL_CashTable / GL_CodeRemark / GL_accvouch →
    // 清除语句 → 核对再也没有引用 → 提交；提交后在新连接上回读。
    internal static class ArapVoucherDrop
    {
        static readonly string[] BillTypes = new string[] { "26", "27", "01", "02", "48", "49", "R0", "P0" };
        // 凭证来源单据类型另认 50（票据处理凭证 coutsign PJ，由 ArapProcVoucherDrop.Owns 判定）；OnlyOriginal 仍只认 BillTypes。
        // 坏账：9F 计提凭证的来源是 9F（coutid = 处理号）；9G 可挂在 28、29 发票和 R1–R9 应收单上。
        static readonly string[] FindTypes = new string[] { "26", "27", "28", "29", "01", "02", "48", "49", "R0", "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "P0", "50", "9F" };

        const string FindSql = "select convert(varchar(6), iyear) as y, convert(varchar(4), iperiod) as p, csign as s, "
            + "convert(varchar(12), ino_id) as n, max(isnull(coutbillsign,N'')) as bt from GL_accvouch with (UPDLOCK, HOLDLOCK) "
            + "where coutno_id=? group by iyear, iperiod, csign, ino_id";

        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "缺少 U8 登录");
            }
            VoucherDropAsk ask = ArapVoucherReq.ParseDrop(ctx.Item.Body);
            PermRule rule = PermRegistry.ForKey(PermRegistry.ArapVoucherKey(ask.Flag, true));
            PermCheck.RequireRule(PermCheck.Of(ctx), rule);
            GlKey key = Tran(ctx, ask, rule);
            return Reread(ctx, ask, key);
        }

        static GlKey Tran(WorkContext ctx, VoucherDropAsk ask, PermRule rule)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                GlKey key = Find(conn, ask);
                Gate(conn, ask, key);
                // 处理凭证（ZZ / BZ / SY）：汇兑损益第二级、另一张往来明细的结账（ArapProcVoucherDrop）。
                ArapProcVoucherDrop.Gate(ctx, conn, ask.Flag, ask.PzId);
                Allowed(ctx, conn, ask, rule);
                ArapVoucherBack.DeleteGl(conn, key, GlState.SignSeq(conn, key.Sign));
                // 先清两张往来明细上的处理行（同 U8 只清凭证号三列），再跑原有的清除语句。
                ArapProcVoucherBack.Clear(conn, ask.PzId);
                ArapVoucherBack.Clear(conn, ask.Flag, ask.PzId);
                if (ArapVoucherBack.Left(conn, ask.PzId) + ArapProcVoucherNotes.Left(conn, ask.PzId) != 0)
                {
                    throw new BridgeException(409, "u8_rejected", "取消制单后仍有引用该凭证的行，已回滚");
                }
                Preview(ask, key);
                ctx.Item.TranAfter = CoTrans.Count(conn);
                CoTrans.CommitSeen(conn);
                open = false;
                CoRows.Note(ctx.Item, "已取消制单 " + ask.PzId + " 凭证 " + key.Text());
                return key;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
        }

        // 预演：提交钩子会回滚，这里交出要删的凭证键和外部业务号（rollback）。
        static void Preview(VoucherDropAsk ask, GlKey key)
        {
            if (!DryRun.Active)
            {
                return;
            }
            Dictionary<string, object> gl = new Dictionary<string, object>();
            gl["flag"] = ask.Flag;
            gl["pz_id"] = ask.PzId;
            gl["iyear"] = key.Year;
            gl["iperiod"] = key.Period;
            gl["csign"] = key.Sign;
            gl["ino_id"] = key.No;
            gl["deleted"] = true;
            DryRun.Set("gl", gl);
        }

        static GlKey Find(object conn, VoucherDropAsk ask)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, FindSql, new object[] { ask.PzId }, 3);
            if (rows.Count == 0)
            {
                throw new BridgeException(404, "not_found", "凭证不存在：" + ask.PzId);
            }
            if (rows.Count > 1)
            {
                throw ArapVoucherDoc.Refuse("外部业务号 " + ask.PzId + " 对应多张凭证，请在 U8 客户端处理");
            }
            Dictionary<string, object> row = rows[0];
            if (Array.IndexOf(FindTypes, CoRows.Col(row, "bt")) < 0)
            {
                throw ArapVoucherDoc.Refuse("只能取消销售发票、采购发票、收付款单、应收应付单、票据处理和坏账处理的制单（该凭证来源 " + CoRows.Col(row, "bt") + "）");
            }
            GlKey key = new GlKey();
            key.Year = CoRows.AsId(CoRows.Col(row, "y"));
            key.Period = CoRows.AsId(CoRows.Col(row, "p"));
            key.Sign = CoRows.Col(row, "s");
            key.No = CoRows.AsId(CoRows.Col(row, "n"));
            return key;
        }

        // U8 删除凭证的拒绝原因（资源文本）：已记账、已审核、已出纳签字、所在期间已结账。
        static void Gate(object conn, VoucherDropAsk ask, GlKey key)
        {
            GlHead head = GlState.Need(conn, key, true);
            Must(!head.Mixed, "凭证各行状态不一致，请在 U8 客户端处理");
            Must(!head.Posted, "此凭证已记账，不能删除");
            Must(head.Checker.Length == 0, "此凭证已审核，不能删除");
            Must(head.Cashier.Length == 0, "此凭证已经出纳签字，不能删除");
            Must(head.Marks == 0, "凭证已做银行对账或往来两清");
            Must(head.OutSys == ask.Flag, "凭证不是" + Side(ask.Flag) + "系统生成的（制单系统 " + head.OutSys + "）");
            string closed = Rows.Scalar(conn, "select convert(varchar(1), isnull(bflag,0)) from GL_mend where iyear=? and iperiod=?",
                new object[] { key.Year, key.Period });
            Must(closed == null || closed.Trim() == "0", "此凭证所在期间已结账，不能删除");
            GlState.NotLocked(conn, key);
            GlState.NotReversed(conn, head);
            Must(!WriteoffSql.Closed(conn, ask.Flag, key.Year, key.Period), Side(ask.Flag) + "已结账（凭证所在月份）");
            Must(OnlyOriginal(conn, ask) || ArapProcVoucherDrop.Owns(conn, ask.PzId),
                "该凭证不是发票 / 收付款单 / 应收应付单制单或处理制单（应收冲应付、应付冲应收、并账、汇兑损益、红票对冲、票据处理、坏账处理整批）生成的，请在 U8 客户端删除");
            string detail = WriteoffSql.Detail(ask.Flag);
            string sql = "select top 1 convert(varchar(10), d.iPeriod) from " + detail + " d inner join GL_mend m on m.iyear=year(d.dRegDate) "
                + "and m.iperiod=d.iPeriod where d.cPZid=? and isnull(m." + (ask.Flag == "AP" ? "bflag_AP" : "bflag_AR") + ",0)<>0";
            Must(Rows.Scalar(conn, sql, new object[] { ask.PzId }) == null, Side(ask.Flag) + "已结账（单据登记期间）");
        }

        // 来源核对：应收、应付两张往来明细里引用该外部业务号的每一行都必须是本方（cFlag=flag）上述 8 类单据的原始行
        // （cProcStyle = cVouchType）。核销（9P）、应收冲应付（9E，两张表都有行）、转账、并账、红票对冲、坏账等处理生成的凭证
        // 也带原单的 coutbillsign，U8 删它们时有各自的回写，桥不复现，一律拒绝。
        // 应收冲应付、应付冲应收、并账、汇兑损益的处理凭证另由 ArapProcVoucherDrop.Owns 放行（只清凭证号）。
        static bool OnlyOriginal(object conn, VoucherDropAsk ask)
        {
            string types = "N'" + string.Join("',N'", BillTypes) + "'";
            string cond = " where cPZid=? and not (isnull(cFlag,N'')=? and isnull(cProcStyle,N'')=isnull(cVouchType,N'') and cVouchType in (" + types + "))";
            string sql = "select top 1 'x' from Ar_Detail" + cond + " union all select top 1 'x' from Ap_Detail" + cond;
            return Rows.Scalar(conn, sql, new object[] { ask.PzId, ask.Flag, ask.PzId, ask.Flag }) == null;
        }

        // 数据权限按往来明细（往来单位 cDwCode、部门 cDeptCode、业务员 cPerson），同核销。
        static void Allowed(WorkContext ctx, object conn, VoucherDropAsk ask, PermRule rule)
        {
            string sql = "select d.cDwCode, d.cDeptCode, d.cPerson from " + WriteoffSql.Detail(ask.Flag) + " d where d.cPZid=?";
            PermContext p = PermCheck.Of(ctx);
            foreach (Dictionary<string, object> row in Rows.Query(conn, sql, new object[] { ask.PzId }, 1001))
            {
                if (!PermCheck.RowAllowed(p, rule, row))
                {
                    throw new BridgeException(403, "no_permission", PermCheck.DeniedData);
                }
            }
        }

        // 已提交。新连接确认再也没有引用；读不出来或还有引用都是 504 outcome_unknown（已提交，先核对，不要重投）。
        static ApiResult Reread(WorkContext ctx, VoucherDropAsk ask, GlKey key)
        {
            object conn = null;
            int left;
            try
            {
                conn = ctx.OpenFresh();
                left = ArapVoucherBack.Left(conn, ask.PzId);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "取消制单回读 " + ex.Message);
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
            body["flag"] = ask.Flag;
            body["pz_id"] = ask.PzId;
            body["deleted"] = true;
            body["voucher"] = voucher;
            return ApiResult.Ok(body);
        }

        static string Side(string flag)
        {
            return flag == "AP" ? "应付" : "应收";
        }

        static void Must(bool ok, string message)
        {
            if (!ok)
            {
                throw ArapVoucherDoc.Refuse(message);
            }
        }
    }
}
