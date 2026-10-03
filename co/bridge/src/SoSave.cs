using System;
using System.Collections.Generic;

namespace U8Co
{
    internal static class SoSave
    {
        public static int SaveNew(object conn, object co, object[] doms, WorkItem item, out string savedCode)
        {
            bool open = false;
            savedCode = "";
            try
            {
                CoTrans.Begin(conn);
                open = true;
                item.TranBefore = CoTrans.Count(conn);
                Allocate(co, doms, item);
                string code = SoDom.Attr(doms[0], "cSOCode");
                savedCode = code;
                // VoucherState 0 = 销售新增。vNewID、DomConfig 是 VB6 ByRef 可选参数：传 Missing 会 DISP_E_TYPEMISMATCH，
                // 所以 vNewID 给真值、DomConfig 整个不传。
                object[] args = new object[] { doms[0], doms[1], (short)0, "" };
                object ret = ComUtil.CallRef(co, "Save", args, new int[] { 3 });
                item.TranAfter = CoTrans.Count(conn);
                string msg = ret == null ? "" : Convert.ToString(ret);
                CoRows.Note(item, "Save " + msg);
                if (msg.Length > 0)
                {
                    CoTrans.Rollback(conn);
                    open = false;
                    throw new BridgeException(409, "u8_rejected", msg);
                }
                int id = CoRows.AsId(args[3]);
                if (id <= 0)
                {
                    id = CoRows.AsId(SoDom.Attr(doms[0], "ID"));
                }
                DocMark.Created(conn, "sale_order", id, code);
                CoTrans.CommitSeen(conn);
                open = false;
                KeepCode(doms[0], code, item);
                return id;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, item, open);
                throw;
            }
        }

        // 成功提交之后再把单号写回 DOM。这里失败不能把已经保存的订单回滚掉。
        static void KeepCode(object dom, string code, WorkItem item)
        {
            try
            {
                if (code.Length == 0 || SoDom.Attr(dom, "cSOCode").Length > 0)
                {
                    return;
                }
                SoDom.SetAttr(dom, "cSOCode", code);
            }
            catch (Exception ex)
            {
                // 订单已经提交。这里只是回写 DOM，失败不能让调用方以为保存失败而重试。
                CoRows.Note(item, "KeepCode " + ex.Message);
            }
        }

        static void Allocate(object co, object[] doms, WorkItem item)
        {
            // GetVoucherNO 不给号时再试 GetVoucherTrueNO，仍没有则交给 Save。
            string no = AskNo(co, doms, item, "GetVoucherNO");
            if (no.Length == 0)
            {
                no = SoDom.Attr(doms[0], "cSOCode");
            }
            if (no.Length == 0)
            {
                no = AskNo(co, doms, item, "GetVoucherTrueNO");
            }
            if (no.Length == 0)
            {
                no = SoDom.Attr(doms[0], "cSOCode");
            }
            if (no.Length > 0)
            {
                SoDom.SetAttr(doms[0], "cSOCode", no);
            }
        }

        static string AskNo(object co, object[] doms, WorkItem item, string method)
        {
            // DomFormat 等尾部参数是 ByRef 可选：不传，传 Missing 会类型不匹配。
            object[] args = new object[] { doms[0], "", "" };
            object ret;
            try
            {
                ret = ComUtil.CallRef(co, method, args, new int[] { 0, 1, 2 });
            }
            catch (Exception ex)
            {
                CoRows.Note(item, method + " " + ex.Message);
                if (!object.ReferenceEquals(args[0], doms[0]))
                {
                    ComUtil.Final(args[0]);
                }
                return "";
            }
            CoRows.Swap(doms, 0, args[0]);
            string no = Values.Text(args[1]).Trim();
            CoRows.Note(item, method + " " + Values.Text(ret) + " " + Values.Text(args[2]).Trim() + " " + no);
            if (!Values.Flag(ret) || no.Length == 0)
            {
                return "";
            }
            return no;
        }

        public static void Stamp(WorkContext ctx, object sys, object dom, string card)
        {
            string user = ctx.Session.OperatorName ?? "";
            if (user.Length == 0)
            {
                user = ctx.Item.Operator ?? "";
            }
            object[] args = new object[] { ctx.Conn, card, user, false, "" };
            object ret;
            try
            {
                ret = ComUtil.CallRef(sys, "getDefaltVTID", args, new int[] { 0, 1, 2, 3, 4 });
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "getDefaltVTID " + ex.Message);
                CoRows.ReleaseIfNew(ctx.Conn, args[0]);
                return;
            }
            CoRows.ReleaseIfNew(ctx.Conn, args[0]);
            string vtid = Values.Text(ret).Trim();
            if (vtid.Length == 0)
            {
                vtid = Values.Text(args[4]).Trim();
            }
            // 制单人由桥写成登录操作员的姓名（调用方不能自带）；U8 Save 不填会报「制单人不能为空」。
            SoDom.SetAttr(dom, "cMaker", user);
            CoRows.Note(ctx.Item, "ivtid=" + vtid);
            if (vtid.Length > 0)
            {
                SoDom.SetAttr(dom, "iVTid", vtid);
            }
        }

        public static string RunDelete(object conn, object co, object[] doms, int id, WorkItem item)
        {
            return RunDelete(conn, co, doms, id, item, "ID");
        }

        // 销售订单主键列是 ID。发货单必须传 DLID，否则表头核对会把正常单据判成标识不一致。
        public static string RunDelete(object conn, object co, object[] doms, int id, WorkItem item, string idColumn)
        {
            return RunDelete(conn, co, doms, id, item, SoDeleteOpt.Of(idColumn, false));
        }

        // markBody：删除前把每一行 editprop 标成 D。销售发票不标则结算数量不回滚；发货单标了也无妨。
        public static string RunDelete(object conn, object co, object[] doms, int id, WorkItem item, SoDeleteOpt opt)
        {
            string read = SaSession.ReadSa(co, doms, id, item);
            if (read.Length > 0)
            {
                return read;
            }
            string column = opt.IdColumn == null || opt.IdColumn.Length == 0 ? "ID" : opt.IdColumn;
            CoRows.RequireHead(doms[0], column, id);
            if (opt.MarkBody)
            {
                MarkBody(doms[1]);
            }
            return InTran(conn, co, doms, item);
        }

        static void MarkBody(object body)
        {
            List<object> rows = DomRows.RowsOf(body);
            for (int i = 0; i < rows.Count; i++)
            {
                DomRows.Set(body, rows[i], "editprop", "D");
            }
        }

        public static void SaveModify(object conn, object co, object[] doms, WorkItem item)
        {
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                item.TranBefore = CoTrans.Count(conn);
                object[] args = new object[] { doms[0], doms[1], (short)1, "" };
                object ret = ComUtil.CallRef(co, "Save", args, new int[] { 3 });
                item.TranAfter = CoTrans.Count(conn);
                string msg = ret == null ? "" : Convert.ToString(ret);
                CoRows.Note(item, "Save " + msg);
                if (msg.Length > 0)
                {
                    CoTrans.Rollback(conn);
                    open = false;
                    throw new BridgeException(409, "u8_rejected", msg);
                }
                CoTrans.CommitSeen(conn);
                open = false;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, item, open);
                throw;
            }
        }

        // 发货单取号只走 GetVoucherNO。号在参数槽里，返回值不一定是布尔。
        public static int SaveDispatch(object conn, object co, object[] doms, WorkItem item, out string savedCode)
        {
            return SaveFresh(conn, co, doms, item, Fresh("cDLCode", "DLID", "没有生成发货单号", false, "dispatch"), out savedCode);
        }

        // 销售发票同样 GetVoucherNO(head, no, err) → cSBVCode，Save 第三参 (short)0。表头必须带空的 sbvid。
        public static int SaveInvoice(object conn, object co, object[] doms, WorkItem item, out string savedCode)
        {
            return SaveFresh(conn, co, doms, item, Fresh("cSBVCode", "", "没有生成发票号", true, "sale_invoice"), out savedCode);
        }

        static FreshSpec Fresh(string codeAttr, string idAttr, string emptyMsg, bool pinSbvid, string kindName)
        {
            FreshSpec spec = new FreshSpec();
            spec.KindName = kindName;
            spec.CodeAttr = codeAttr;
            spec.IdAttr = idAttr;
            spec.EmptyMsg = emptyMsg;
            spec.PinSbvid = pinSbvid;
            return spec;
        }

        static int SaveFresh(object conn, object co, object[] doms, WorkItem item, FreshSpec spec, out string savedCode)
        {
            bool open = false;
            savedCode = "";
            try
            {
                CoTrans.Begin(conn);
                open = true;
                item.TranBefore = CoTrans.Count(conn);
                string no = VoucherNo(co, doms, item, spec.CodeAttr);
                if (no.Length == 0)
                {
                    throw new BridgeException(409, "u8_rejected", spec.EmptyMsg);
                }
                SoDom.SetAttr(doms[0], spec.CodeAttr, no);
                savedCode = no;
                if (spec.PinSbvid)
                {
                    SoDom.SetAttr(doms[0], "sbvid", "");
                }
                object[] args = new object[] { doms[0], doms[1], (short)0, "" };
                object ret = ComUtil.CallRef(co, "Save", args, new int[] { 3 });
                item.TranAfter = CoTrans.Count(conn);
                string msg = ret == null ? "" : Convert.ToString(ret);
                CoRows.Note(item, "Save " + msg);
                if (msg.Length > 0)
                {
                    CoTrans.Rollback(conn);
                    open = false;
                    throw new BridgeException(409, "u8_rejected", msg);
                }
                int id = CoRows.AsId(args[3]);
                if (id <= 0 && spec.IdAttr.Length > 0)
                {
                    id = CoRows.AsId(SoDom.Attr(doms[0], spec.IdAttr));
                }
                DocMark.Created(conn, spec.KindName, id, no);
                CoTrans.CommitSeen(conn);
                open = false;
                return id;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, item, open);
                throw;
            }
        }

        // 退货单保存（SaleReturnSave.cs）也用它取号。
        internal static string VoucherNo(object co, object[] doms, WorkItem item, string codeAttr)
        {
            object[] args = new object[] { doms[0], "", "" };
            object ret;
            try
            {
                ret = ComUtil.CallRef(co, "GetVoucherNO", args, new int[] { 0, 1, 2 });
            }
            catch (Exception ex)
            {
                CoRows.Note(item, "GetVoucherNO " + ex.Message);
                if (!object.ReferenceEquals(args[0], doms[0]))
                {
                    ComUtil.Final(args[0]);
                }
                string text = ex.Message == null ? "" : ex.Message.Trim();
                if (text.Length == 0)
                {
                    return "";
                }
                throw new BridgeException(409, "u8_rejected", text);
            }
            CoRows.Swap(doms, 0, args[0]);
            string no = Values.Text(args[1]).Trim();
            string err = Values.Text(args[2]).Trim();
            CoRows.Note(item, "GetVoucherNO " + Values.Text(ret) + " " + err + " " + no);
            if (no.Length > 0)
            {
                return no;
            }
            string onDom = SoDom.Attr(doms[0], codeAttr);
            if (onDom.Length > 0)
            {
                return onDom;
            }
            if (err.Length > 0)
            {
                throw new BridgeException(409, "u8_rejected", err);
            }
            return "";
        }

        static string InTran(object conn, object co, object[] doms, WorkItem item)
        {
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                item.TranBefore = CoTrans.Count(conn);
                object[] args = new object[] { doms[0], doms[1] };
                object ret = ComUtil.CallRef(co, "Delete", args, new int[] { 0, 1 });
                CoRows.Swap(doms, 0, args[0]);
                CoRows.Swap(doms, 1, args[1]);
                item.TranAfter = CoTrans.Count(conn);
                string msg = ret == null ? "" : Convert.ToString(ret);
                CoRows.Note(item, "Delete " + (msg.Length == 0 ? "ok" : msg));
                if (msg.Length > 0)
                {
                    return Fail(conn, item, ref open, msg);
                }
                CoTrans.CommitSeen(conn);
                open = false;
                return "";
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, item, open);
                throw;
            }
        }

        static string Fail(object conn, WorkItem item, ref bool open, string msg)
        {
            item.TranAfter = CoTrans.Count(conn);
            CoTrans.Rollback(conn);
            open = false;
            return msg;
        }

        sealed class FreshSpec
        {
            internal string CodeAttr;
            internal string IdAttr;
            internal string EmptyMsg;
            internal bool PinSbvid;
            internal string KindName;
        }
    }

    internal sealed class SoDeleteOpt
    {
        internal string IdColumn;
        internal bool MarkBody;

        internal static SoDeleteOpt Of(string idColumn, bool markBody)
        {
            SoDeleteOpt opt = new SoDeleteOpt();
            opt.IdColumn = idColumn;
            opt.MarkBody = markBody;
            return opt;
        }
    }
}
