using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 删除采购结算单（vouchers/delete，type=purchase_settle，id 是 PSVID）。闸门见 PuSettleGate.ForDelete。
    // 测试账套实测：Info_PU.Init + VoucherCO_PU.Init(5, …, "", "普通采购", 0, "", pt)、bOutTrans，
    // GetVoucherDataById(h, b, "PSVID=<id>", id, "") 引用 {0,1,4}，Delete(h, b) 引用 {0,1}，返回空串为成功；
    // 在 CoTrans 里（实测可回滚）。提交前在同一事务里核对：表头、表体都没了，关联发票行和表头的结算日期 dSDate 已清空，
    // @@TRANCOUNT 没变；对不上回滚 409。提交后在新连接上确认表头已不在。
    internal static class PuSettleDel
    {
        const string LeftSql = "select (select count(*) from PurSettleVouch where PSVID=?)"
            + " + (select count(*) from PurSettleVouchs where PSVID=?) as n";
        const string StampLeftSql = "select (select count(*) from PurBillVouchs b where b.PBVID=? and b.dSDate is not null"
            + " and not exists (select 1 from PurSettleVouchs s where s.iBsID=b.ID))"
            + " + (select count(*) from PurBillVouch h where h.PBVID=? and h.dSDate is not null"
            + " and not exists (select 1 from PurSettleVouchs s join PurBillVouchs b2 on b2.ID=s.iBsID"
            + " where b2.PBVID=h.PBVID)) as n";

        const string ApSql = "select convert(varchar(20), h.PBVID) from PurBillVouch h with (updlock, holdlock)"
            + " where h.PBVID=? and isnull(h.cPBVVerifier, N'')<>N''";

        public static ApiResult Delete(WorkContext ctx, VoucherKind kind, int psvid)
        {
            SettleDoc doc = PuSettleGate.ForDelete(ctx, psvid);
            // 预演：登记关联的发票（结算日期回退）。
            for (int i = 0; i < doc.Invoices.Count; i++)
            {
                DocMark.Touched("purchase_invoice", doc.Invoices[i]);
            }
            object info = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                ctx.PuInitType = CoRows.Col(doc.Head, "cPTCode");
                PuInv.Open(ctx, 5, "", true, out info, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                Read(ctx, co, doms, psvid);
                CoRows.RequireHead(doms[0], kind.IdColumn, psvid);
                Tran(ctx, co, doms, doc);
                return Gone(ctx, kind, psvid);
            }
            finally
            {
                ComUtil.Final(doms[1]);
                ComUtil.Final(doms[0]);
                ComUtil.Final(co);
                ComUtil.Final(info);
            }
        }

        // 条件串照实测写 PSVID=<id>（id 是请求里已校验的整数，不是调用方文本）。
        static void Read(WorkContext ctx, object co, object[] doms, int psvid)
        {
            string id = psvid.ToString(CultureInfo.InvariantCulture);
            object[] args = new object[] { doms[0], doms[1], "PSVID=" + id, psvid, "" };
            object ret = ComUtil.CallRef(co, "GetVoucherDataById", args, new int[] { 0, 1, 4 });
            CoRows.Swap(doms, 0, args[0]);
            CoRows.Swap(doms, 1, args[1]);
            CoRows.Note(ctx.Item, "GetVoucherDataById " + Values.Text(ret).Trim());
        }

        static void Tran(WorkContext ctx, object co, object[] doms, SettleDoc doc)
        {
            bool open = false;
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                string before = CoTrans.Count(ctx.Conn);
                ctx.Item.TranBefore = before;
                object[] args = new object[] { doms[0], doms[1] };
                object ret = ComUtil.CallRef(co, "Delete", args, new int[] { 0, 1 });
                CoRows.Swap(doms, 0, args[0]);
                CoRows.Swap(doms, 1, args[1]);
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                string msg = Values.Text(ret).Trim();
                CoRows.Note(ctx.Item, "Delete " + (msg.Length == 0 ? "ok" : msg));
                if (msg.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", msg);
                }
                StockCall.AfterCheck(ctx, delegate(object conn) { RequireUndone(conn, doc, before); },
                    "PSVID " + doc.Psvid.ToString(CultureInfo.InvariantCulture));
                CoTrans.CommitSeen(ctx.Conn);
                open = false;
            }
            catch (Exception)
            {
                CoRows.CatchTran(ctx.Conn, ctx.Item, open);
                throw;
            }
        }

        // 事务里、提交前：结算单已不在；关联发票不再有结算行的表体行和表头，dSDate 都已清空；关联发票仍未应付审核。
        static void RequireUndone(object conn, SettleDoc doc, string before)
        {
            string now = CoTrans.Count(conn);
            int b0;
            int n0;
            // ADO 的 BeginTrans 到第一条语句才真正开事务：开头可能读到 0、U8 写过之后是 1（IaRun.TranIntact）。
            if (!int.TryParse(before, out b0) || !int.TryParse(now, out n0) || !IaRun.TranIntact(b0, n0))
            {
                throw new BridgeException(409, "u8_rejected", "U8 改变了事务层数（@@TRANCOUNT " + before + " → " + now + "）");
            }
            object[] key = new object[] { doc.Psvid, doc.Psvid };
            if (Count(Rows.Scalar(conn, Counted(LeftSql), key)) != 0)
            {
                throw new BridgeException(409, "u8_rejected", "U8 返回成功但结算单仍在");
            }
            for (int i = 0; i < doc.Invoices.Count; i++)
            {
                object[] inv = new object[] { doc.Invoices[i], doc.Invoices[i] };
                if (Count(Rows.Scalar(conn, Counted(StampLeftSql), inv)) != 0)
                {
                    throw new BridgeException(409, "u8_rejected", "U8 没有清除发票的结算日期");
                }
                // 闸门之后别人可能做了应付审核：提交前在本事务里再查一次（加更新锁，持到提交）。
                if (Rows.Scalar(conn, ApSql, new object[] { doc.Invoices[i] }) != null)
                {
                    throw new BridgeException(409, "state_mismatch", "发票已应付审核，请先取消应付审核");
                }
            }
        }

        // Rows.Scalar 只回字符串列：计数包一层 convert。
        static string Counted(string sql)
        {
            return "select convert(varchar(20), q.n) from (" + sql + ") q";
        }

        static int Count(string text)
        {
            int n;
            if (text != null && int.TryParse(text.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out n))
            {
                return n;
            }
            throw new BridgeException(409, "u8_rejected", "无法核对删除结果");
        }

        // 删除已提交；回读出错是 504，单据仍在是 U8 说成功但没删掉。
        static ApiResult Gone(WorkContext ctx, VoucherKind kind, int psvid)
        {
            object conn = null;
            string left;
            try
            {
                conn = ctx.OpenFresh();
                left = Rows.Scalar(conn, Counted(LeftSql), new object[] { psvid, psvid });
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "PuSettleDel " + ex.Message);
                throw new BridgeException(504, "outcome_unknown",
                    "已提交删除但未能回读确认，标识 " + psvid.ToString(CultureInfo.InvariantCulture));
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (left == null || left.Trim() != "0")
            {
                throw new BridgeException(409, "state_mismatch", "U8 返回成功但回读状态不符");
            }
            return StockMsg.Gone(kind, psvid);
        }
    }
}
