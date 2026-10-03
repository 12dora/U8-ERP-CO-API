using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    internal static partial class SaleEdit
    {
        const string OutSql = "select top 1 convert(varchar(20), r.AutoID) from rdrecords32 r "
            + "where r.iDLsID<>0 and r.iDLsID in (select s.iDLsID from DispatchLists s where s.DLID=? and s.iDLsID<>0)";
        const string BillSql = "select top 1 convert(varchar(20), b.AutoID) from SaleBillVouchs b "
            + "where b.iDLsID<>0 and b.iDLsID in (select s.iDLsID from DispatchLists s where s.DLID=? and s.iDLsID<>0)";
        const string CloseHeadSql = "select cCloser as closer, dclosedate as closed_on, dclosesystime as closed_sys "
            + "from SO_SOMain where ID=?";
        const string CloseLineSql = "select iSOsID as line_id, cSCloser as closer from SO_SODetails "
            + "where ID=? order by iRowNo, iSOsID";
        const string LineStateSql = "select iSOsID, cSCloser from SO_SODetails where ID=?";
        const string ShapeSql = "select cVouchType, bReturnFlag, bFirst, iverifystate from DispatchList where DLID=?";
        static readonly string[] UnitAttrs = new string[] {
            "cUnitID", "iInvExchRate", "cGroupCode", "iGroupType", "inum"
        };
        static readonly string[] ClearedCols = new string[] {
            "iquantity", "inum",
            "iquotedprice", "iunitprice", "itaxunitprice", "inatunitprice", "kl", "kl2",
            "imoney", "itax", "isum", "inatmoney", "inattax", "inatsum", "idiscount", "inatdiscount",
            "cscloser", "dbclosedate", "dbclosesystime",
            "ifhquantity", "ifhnum", "ifhmoney", "ikpquantity", "ikpnum", "ikpmoney",
            "foutquantity", "foutnum", "fretquantity", "fveridispqty", "fveridispsum",
            "iexchsum", "imoneysum", "icostsum"
        };
        static readonly string[] ItemText = new string[] {
            "cinvname", "cinvstd", "cinvm_unit", "cinva_unit", "cinvaddcode",
            "citemcode", "citemname", "citem_class", "citem_cname",
            "cbsysbarcode", "ufts", "corufts"
        };
        // 克隆行上的客户存货、合同和来源。按行属性名大小写不敏感匹配，调用方传入的留下。
        static readonly string[] LinkCols = new string[] {
            "cCusInvCode", "cCusInvName", "cContractID", "cContractTagCode", "cContractRowGuid",
            "iPPartSeqID", "iPPartID", "cParentCode", "cChildCode", "fchildqty", "fchildrate",
            "cdemandcode", "cdetailsdemandcode", "cdemandmemo", "cdetailsdemandmemo", "iaoids",
            "cpreordercode", "cQuoCode", "iQuoID", "ippartid", "ippartqty", "gcsourceid", "gcsourceids"
        };

        public static ApiResult Update(WorkContext ctx, VoucherKind kind, int id, Dictionary<string, object> head, object[] lines)
        {
            if (kind == null || kind.Name != "sale_order")
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持修改");
            }
            if (head == null)
            {
                head = new Dictionary<string, object>();
            }
            if (lines == null)
            {
                lines = new object[0];
            }
            if (head.Count == 0 && lines.Length == 0)
            {
                throw new BridgeException(400, "bad_request", "没有要修改的内容");
            }
            if (lines.Length > 200)
            {
                throw new BridgeException(400, "bad_request", "表体行数必须在 1 到 200 之间");
            }
            Dictionary<string, object> snap = CoRows.HeadRow(ctx.Conn, kind, id);
            if (snap == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            VoucherLockGate.Refuse(ctx.Conn, kind.Name, id, ctx.OperatorName);
            SaleOrderCo.RefuseWritable(ctx.Conn, kind, id, snap, "单据正在审批，不能修改");
            object sys = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                SaSession.OpenSa(ctx.Conn, ctx.Session.Login, SaSession.SaVt(kind), out sys, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                string err = SaSession.ReadSa(co, doms, id, ctx.Item);
                if (err.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", err);
                }
                CoRows.RequireHead(doms[0], "ID", id);
                Apply(ctx, co, doms, head, lines);
                SoSave.SaveModify(ctx.Conn, co, doms, ctx.Item);
                return AfterUpdate(ctx, kind, id, CoRows.Col(snap, "code"));
            }
            finally
            {
                SaSession.Release(sys, co, doms);
            }
        }

        public static ApiResult Close(WorkContext ctx, VoucherKind kind, int id, string action, int[] lineIds)
        {
            if (kind == null || kind.Name != "sale_order")
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持关闭");
            }
            bool closing = WantClose(action);
            if (lineIds != null && lineIds.Length == 0)
            {
                throw new BridgeException(400, "bad_request", "没有要关闭的明细");
            }
            if (lineIds != null && lineIds.Length > 200)
            {
                throw new BridgeException(400, "bad_request", "表体行数必须在 1 到 200 之间");
            }
            Dictionary<string, object> snap = CoRows.HeadRow(ctx.Conn, kind, id);
            if (snap == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            GateClose(ctx.Conn, id, snap, closing, lineIds);
            RunClose(ctx, id, closing, lineIds);
            return ReadClosed(ctx, kind, id, action);
        }

        public static ApiResult Delete(WorkContext ctx, VoucherKind kind, int id)
        {
            // 预演：删除前登记上游单据（发货单 ← 订单、发票 ← 发货单 / 退货单、退货单 ← 发货单），它们的累计数量会回退。
            DocMark.Upstream(ctx.Conn, kind, id);
            if (kind != null && kind.Name == "sale_invoice")
            {
                return DeleteInvoice(ctx, kind, id);
            }
            if (SaleReturn.Is(kind))
            {
                return SaleReturn.Delete(ctx, kind, id);
            }
            return DeleteDispatch(ctx, kind, id);
        }

        static ApiResult DeleteDispatch(WorkContext ctx, VoucherKind kind, int id)
        {
            if (kind == null || kind.Name != "dispatch")
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持删除");
            }
            Dictionary<string, object> snap = CoRows.HeadRow(ctx.Conn, kind, id);
            if (snap == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            Dictionary<string, object> shape = Rows.One(ctx.Conn, ShapeSql, new object[] { id });
            if (shape == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            RefuseBlue(shape);
            snap["vstate"] = CoRows.Col(shape, "iverifystate");
            SaleOrderCo.RefuseRunning(ctx.Conn, kind, id, snap, "单据正在审批，不能删除");
            if (CoRows.Col(snap, "verifier").Length > 0 || CoRows.Col(snap, "verify_date").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核");
            }
            if (Rows.Scalar(ctx.Conn, OutSql, new object[] { id }) != null
                || Rows.Scalar(ctx.Conn, BillSql, new object[] { id }) != null)
            {
                throw new BridgeException(409, "state_mismatch", "单据已有下游单据");
            }
            return RunGone(ctx, kind, id, SaSession.SaVt(kind));
        }

        static ApiResult DeleteInvoice(WorkContext ctx, VoucherKind kind, int id)
        {
            Dictionary<string, object> snap = CoRows.HeadRow(ctx.Conn, kind, id);
            if (snap == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (CoRows.FlagOf(snap, "wf"))
            {
                throw new BridgeException(409, "workflow_enabled", "单据已启用审批流，不能删除");
            }
            if (CoRows.Col(snap, "verifier").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已复核");
            }
            if (CoRows.Col(snap, "ar_verifier").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已在应收系统审核");
            }
            // 红字发票（bReturnFlag=1）只放行参照退货单生成的，删除与回写核对在 SaleGenRedDel；修改照旧拒绝。
            if (CoRows.FlagOf(snap, "red"))
            {
                return SaleGen.DeleteRedInvoice(ctx, kind, id, snap);
            }
            SaleGen.RefuseRedLinked(ctx.Conn, id);
            RefuseStaleCheck(ctx.Conn, id);
            int vt = InvoiceVt(CoRows.Col(snap, "vouch_type"));
            // 先开票（有发货单 SBVID 指回本发票）：事务里核对 U8 生成的发货单也已删除（SrcLessSaDel）。
            if (SrcLessSaDel.IsAdvance(ctx.Conn, id))
            {
                return SrcLessSaDel.Delete(ctx, kind, id, vt);
            }
            return RunGone(ctx, kind, id, vt);
        }

        // 实测：发票复核后再弃复，U8 不回退发货单行的 fVeriBillQty；此后删除发票也不回退 iSettleQuantity。
        // 与触发器同一数量：发货行 iTB=0 比 iQuantity，否则比 TBQuantity，并留 0.000001 容差。只计已复核发票。
        const string StaleCheckSql = "select top 1 convert(varchar(20), l.iDLsID) from DispatchLists l "
            + "where l.iDLsID in (select s.iDLsID from SaleBillVouchs s where s.SBVID=? and isnull(s.iDLsID,0)<>0) "
            + "and isnull(l.fVeriBillQty,0) > isnull((select sum(case when isnull(d2.iTB,0)=0 then isnull(s2.iQuantity,0) "
            + "else isnull(s2.TBQuantity,0) end) from SaleBillVouchs s2 "
            + "inner join SaleBillVouch h2 on h2.SBVID=s2.SBVID inner join DispatchLists d2 on d2.iDLsID=s2.iDLsID "
            + "where s2.iDLsID=l.iDLsID and isnull(h2.cChecker,N'')<>N''),0) + 0.000001";

        static void RefuseStaleCheck(object conn, int id)
        {
            if (Rows.Scalar(conn, StaleCheckSql, new object[] { id }) != null)
            {
                throw new BridgeException(409, "state_mismatch",
                    "发票复核后又弃复，U8 未回退发货单的已复核开票数量，删除后累计开票数无法回退，请在 U8 客户端处理");
            }
        }

        static ApiResult RunGone(WorkContext ctx, VoucherKind kind, int id, int vt)
        {
            object sys = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                SaSession.OpenSa(ctx.Conn, ctx.Session.Login, vt, out sys, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                string err = SoSave.RunDelete(ctx.Conn, co, doms, id, ctx.Item, SoDeleteOpt.Of(kind.IdColumn, true));
                if (err.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", err);
                }
                return SaleOrderCo.Gone(ctx, kind, id);
            }
            finally
            {
                SaSession.Release(sys, co, doms);
            }
        }

        static int InvoiceVt(string text)
        {
            decimal n;
            if (!decimal.TryParse(text, NumberStyles.Number, CultureInfo.InvariantCulture, out n))
            {
                throw new BridgeException(400, "bad_request", "该发票类型不支持");
            }
            if (n == 26m)
            {
                return 0;
            }
            if (n == 27m)
            {
                return 2;
            }
            throw new BridgeException(400, "bad_request", "该发票类型不支持");
        }

        static void Apply(WorkContext ctx, object co, object[] doms, Dictionary<string, object> head, object[] lines)
        {
            bool rate = HeadRate(head);
            ApplyHead(doms[0], head);
            List<LineOp> ops = Parse(doms[1], lines);
            BlankEdit(doms[1]);
            EditBag bag = EditBagOf(ctx, co, doms);
            int rowNo = MaxRowNo(doms[1]);
            for (int i = 0; i < ops.Count; i++)
            {
                rowNo = ApplyOne(bag, ops[i], rowNo);
            }
            if (rate)
            {
                RecalcOpen(co, doms);
            }
            StampUser(ctx, doms[0]);
        }

        static ApiResult AfterUpdate(WorkContext ctx, VoucherKind kind, int id, string code)
        {
            try
            {
                return EditMsg.Saved(ctx, kind, id, null, 0);
            }
            catch (Exception ex)
            {
                BridgeException bridge = ex as BridgeException;
                if (bridge != null && bridge.Code == "outcome_unknown")
                {
                    throw;
                }
                CoRows.Note(ctx.Item, "Saved " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", Unknown(id, code));
            }
        }

        static string Unknown(int id, string code)
        {
            string msg = "已保存但未能确定单据标识";
            string no = code == null ? "" : code.Trim();
            if (no.Length > 0)
            {
                msg = msg + "，单号 " + no;
            }
            if (id > 0)
            {
                msg = msg + "，标识 " + id.ToString(CultureInfo.InvariantCulture);
            }
            return msg;
        }

        static EditBag EditBagOf(WorkContext ctx, object co, object[] doms)
        {
            EditBag bag = new EditBag();
            bag.Ctx = ctx;
            bag.Co = co;
            bag.Doms = doms;
            bag.Schema = Names(doms[1]);
            bag.HeadVals = HeadVals(doms[0]);
            return bag;
        }

        sealed class EditBag
        {
            internal WorkContext Ctx;
            internal object Co;
            internal object[] Doms;
            internal Dictionary<string, string> Schema;
            internal Dictionary<string, string> HeadVals;
        }
    }
}
