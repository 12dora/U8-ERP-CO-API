using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 删除采购发票：未审核、未应付审核、未结算、未现付、非期初、无退货、无直运引用、没有应付明细、采购期间未结账，
    // 然后同一 Init + GetVoucherDataById(h, b, "", pbvid, "")，表体标 D，在 CoTrans 里 Delete(h, b) 引用 {0,1}；
    // 返回空串为成功，U8 回退 iSumBillQuantity（实测退到 0）/ iInvQTY，提交前核对。最后在新连接上确认单据已不在。
    internal static partial class PuInv
    {
        const string DelSql = "select h.cPBVCode, h.cPBVBillType, h.cVerifier, h.cPBVVerifier,"
            + " convert(varchar(10), h.dSDate, 23) as SDate, convert(varchar(5), isnull(h.bPayment,0)) as Paid,"
            + " convert(varchar(5), isnull(h.IsWfControlled,0)) as Wf, convert(varchar(5), isnull(h.bFirst,0)) as First,"
            + " convert(varchar(5), isnull(h.bOriginal,0)) as Orig, convert(varchar(10), h.dPBVDate, 23) as BillDate, h.cBusType"
            + " from PurBillVouch h where h.PBVID=?";
        const string SettleSql = "select top 1 convert(varchar(20), b.ID) from PurBillVouchs b where b.PBVID=?"
            + " and (b.dSDate is not null or isnull(b.cBAccounter,N'')<>N''"
            + " or exists (select 1 from PurSettleVouchs s where s.iBsID=b.ID))";
        const string ApSql = "select top 1 convert(varchar(20), d.iBVid) from Ap_Detail d"
            + " where d.cFlag='AP' and d.cVouchType=? and d.cVouchID=?";
        const string ReturnSql = "select top 1 convert(varchar(20), b.ID) from PurBillVouchs b"
            + " where b.PBVID=? and isnull(b.fretquantity,0)<>0";
        const string DirectSql = "select top 1 convert(varchar(20), s.AutoID) from SaleBillVouchs s"
            + " where s.iPBVsID in (select b.ID from PurBillVouchs b where b.PBVID=?)";
        const string RdQtySql = "select convert(varchar(20), RdsId) as RdsId, convert(varchar(40), sum(isnull(iPBVQuantity,0))) as Qty"
            + " from PurBillVouchs where PBVID=? and isnull(RdsId,0)<>0 group by RdsId";

        public static ApiResult Delete(WorkContext ctx, VoucherKind kind, int id)
        {
            Dictionary<string, object> row = Rows.One(ctx.Conn, DelSql, new object[] { id });
            if (row == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            string bill = BillKey(CoRows.Col(row, "cPBVBillType"));
            if (CoRows.Col(row, "cBusType") != "普通采购")
            {
                throw new BridgeException(409, "state_mismatch", "只支持删除普通采购的发票");
            }
            GateDelete(ctx.Conn, id, row);
            // 预演：登记来源采购入库单（累计开票回退）。
            DocMark.Upstream(ctx.Conn, kind, id);
            object info = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                PuSession.UseStored(ctx, kind, id);
                Open(ctx, 4, bill, PositiveOf(ctx.Conn, id), out info, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                PurchaseCo.ReadPu(co, doms, id, ctx.Item);
                CoRows.RequireHead(doms[0], kind.IdColumn, id);
                MarkDeleted(doms[1]);
                DeleteTran(ctx, co, doms, id);
                return Gone(ctx, kind, id);
            }
            finally
            {
                ComUtil.Final(doms[1]);
                ComUtil.Final(doms[0]);
                ComUtil.Final(co);
                ComUtil.Final(info);
            }
        }

        static void GateDelete(object conn, int id, Dictionary<string, object> row)
        {
            GateState(row);
            if (Rows.Scalar(conn, SettleSql, new object[] { id }) != null)
            {
                throw new BridgeException(409, "state_mismatch", "发票已结算");
            }
            if (Rows.Scalar(conn, ReturnSql, new object[] { id }) != null)
            {
                throw new BridgeException(409, "state_mismatch", "发票已有退货");
            }
            if (Rows.Scalar(conn, DirectSql, new object[] { id }) != null)
            {
                throw new BridgeException(409, "state_mismatch", "发票已被直运销售发票引用");
            }
            object[] key = new object[] { CoRows.Col(row, "cPBVBillType"), CoRows.Col(row, "cPBVCode") };
            if (Rows.Scalar(conn, ApSql, key) != null)
            {
                throw new BridgeException(409, "state_mismatch", "发票已有应付明细");
            }
            RequireOpen(conn, CoRows.Col(row, "BillDate"));
        }

        static void GateState(Dictionary<string, object> row)
        {
            if (CoRows.Col(row, "Wf") == "1")
            {
                throw new BridgeException(409, "workflow_enabled", "单据已启用审批流，不能直接删除");
            }
            if (CoRows.Col(row, "cVerifier").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核");
            }
            if (CoRows.Col(row, "cPBVVerifier").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "发票已应付审核");
            }
            if (CoRows.Col(row, "SDate").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "发票已结算");
            }
            if (CoRows.Col(row, "Paid") != "0")
            {
                throw new BridgeException(409, "state_mismatch", "发票已现付");
            }
            if (CoRows.Col(row, "First") != "0" || CoRows.Col(row, "Orig") != "0")
            {
                throw new BridgeException(409, "state_mismatch", "期初发票不能删除");
            }
        }

        // 表体每行标 D 再 Delete（销售发票的教训：不标时累计数量不回退）。
        static void MarkDeleted(object body)
        {
            List<object> rows = DomRows.RowsOf(body);
            for (int i = 0; i < rows.Count; i++)
            {
                try
                {
                    DomRows.Set(body, rows[i], "editprop", "D");
                }
                finally
                {
                    ComUtil.ReleaseOne(rows[i]);
                }
            }
        }

        static void DeleteTran(WorkContext ctx, object co, object[] doms, int id)
        {
            bool open = false;
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                List<decimal[]> undo = ReadUndo(ctx.Conn, id);
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
                StockCall.AfterCheck(ctx, delegate(object conn) { RequireUndone(conn, undo); },
                    "PBVID " + id.ToString(CultureInfo.InvariantCulture));
                CoTrans.CommitSeen(ctx.Conn);
                open = false;
            }
            catch (Exception)
            {
                CoRows.CatchTran(ctx.Conn, ctx.Item, open);
                throw;
            }
        }

        // 事务里、Delete 之前：每个入库行 {AutoID, 删除后应有的累计开票数量}（NULL 当 0）。
        static List<decimal[]> ReadUndo(object conn, int id)
        {
            List<decimal[]> list = new List<decimal[]>();
            List<Dictionary<string, object>> rows = Rows.Query(conn, RdQtySql, new object[] { id }, 500);
            for (int i = 0; rows != null && i < rows.Count; i++)
            {
                int rdsId = CoRows.AsId(CoRows.Col(rows[i], "RdsId"));
                decimal billed = Num(Rows.Scalar(conn, BilledSql, new object[] { rdsId }));
                list.Add(new decimal[] { rdsId, billed - Num(CoRows.Col(rows[i], "Qty")) });
            }
            return list;
        }

        // U8 没把入库行累计开票数量退回去就回滚。
        static void RequireUndone(object conn, List<decimal[]> undo)
        {
            for (int i = 0; i < undo.Count; i++)
            {
                decimal now = Num(Rows.Scalar(conn, BilledSql, new object[] { (int)undo[i][0] }));
                if (Math.Abs(now - undo[i][1]) > 0.000001m)
                {
                    throw new BridgeException(409, "u8_rejected", "U8 没有回退入库单累计开票数量");
                }
            }
        }

        // 删除已提交；回读出错是 504，单据仍在是 U8 说成功但没删掉。
        static ApiResult Gone(WorkContext ctx, VoucherKind kind, int id)
        {
            object conn = null;
            bool there = false;
            try
            {
                conn = ctx.OpenFresh();
                there = CoRows.HeadRow(conn, kind, id) != null;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "PuInvDelete " + ex.Message);
                throw new BridgeException(504, "outcome_unknown",
                    "已提交删除但未能回读确认，标识 " + id.ToString(CultureInfo.InvariantCulture));
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (there)
            {
                throw new BridgeException(409, "state_mismatch", "U8 返回成功但回读状态不符");
            }
            return StockMsg.Gone(kind, id);
        }
    }
}
