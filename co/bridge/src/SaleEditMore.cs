using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 生单来的销售单据修改：发货单、退货单（红字发货单，同一 Save 状态 1 的做法）、销售发票。
    // v1 只改未审核、没有下游的单据（闸门同删除，SaleEditMoreGate.cs）；可以删行但至少留一行原有明细；
    // 发货单可新增行（op=add，参照本单来源销售订单的行，SaleEditMoreAdd.cs），退货单、销售发票仍不能新增行；
    // 数量只能减少（退货单按绝对值：请求写正数，桥写负数）；来源关联、bneedbill、iCorID 不能改。
    // GetVoucherData 读入，按 editprop 标 M / D，数量变了按比例缩放金额再单行 BodyCheck（同生单），桥写修改人，
    // CoTrans 里先 UPDLOCK 读来源累计数作基准，Save(头, 体, (short)1, "")，提交前核对回写（SaleEditMoreBack.cs）。
    internal static partial class SaleEditMore
    {
        const string DispHead = "cmemo,ddate,cdepcode,cpersoncode,cshipaddress";
        const string DispLine = "iquantity,cwhcode,cbatch,cmemo";
        const string RetHead = "cmemo,ddate,cdepcode,cpersoncode";
        const string RetLine = "iquantity,cwhcode,cmemo";
        const string InvHead = "cmemo,ddate";
        const string InvLine = "iquantity,cmemo";
        const string DispLinesSql = "select iDLsID as id, isnull(iSOsID,0) as so, isnull(iCorID,0) as orig, cInvCode as inv, "
            + "isnull(irtnappid,0) as app, convert(varchar(40), isnull(iQuantity,0)) as q from DispatchLists where DLID=?";
        const string AppLineMsg = "参照退货申请单生成的退货行暂不支持修改数量或删除，请删除退货单后重新生成";
        const string InvLinesSql = "select AutoID as id, isnull(iDLsID,0) as dl, isnull(iSOsID,0) as so, cInvCode as inv, "
            + "convert(varchar(40), isnull(iQuantity,0)) as q from SaleBillVouchs where SBVID=?";

        internal static ApiResult Update(WorkContext ctx, VoucherKind kind, int id, Dictionary<string, object> head, object[] lines)
        {
            SaleEditPlan plan = Prepare(ctx.Conn, kind, id, head ?? new Dictionary<string, object>(), lines ?? new object[0]);
            // 预演：登记上游单据（来源的累计数量随改数量回写）。
            DocMark.Upstream(ctx.Conn, kind, id);
            object sys = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                SaSession.OpenSa(ctx.Conn, ctx.Session.Login, plan.Vt, out sys, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                string err = SaSession.ReadSa(co, doms, id, ctx.Item);
                if (err.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", err);
                }
                CoRows.RequireHead(doms[0], kind.IdColumn, id);
                Apply(ctx, co, doms, plan);
                SaveEdit(ctx, co, doms, plan);
            }
            finally
            {
                SaSession.Release(sys, co, doms);
            }
            return AfterSave(ctx, kind, id, plan.Code);
        }

        // lowerField 小写。表头另收 cdefine1–16，表体另收 cdefine22–37，发货单表体还收 cfree1–10（存货要启用该自由项）。
        internal static bool MetaAllowed(VoucherKind kind, bool head, string lowerField)
        {
            string low = lowerField == null ? "" : lowerField.ToLowerInvariant();
            string listed = ListOf(kind, head);
            if (listed.Length == 0 || low.Length == 0)
            {
                return false;
            }
            if (("," + listed + ",").IndexOf("," + low + ",", StringComparison.Ordinal) >= 0)
            {
                return true;
            }
            if (head)
            {
                return ArapReq.Span(low, "cdefine", 1, 16);
            }
            if (kind.Name == "dispatch" && ArapReq.Span(low, "cfree", 1, 10))
            {
                return true;
            }
            return ArapReq.Span(low, "cdefine", 22, 37);
        }

        static string ListOf(VoucherKind kind, bool head)
        {
            switch (kind == null ? "" : kind.Name)
            {
                case "dispatch":
                    return head ? DispHead : DispLine;
                case "sale_return":
                    return head ? RetHead : RetLine;
                case "sale_invoice":
                    return head ? InvHead : InvLine;
            }
            return "";
        }

        static SaleEditPlan Prepare(object conn, VoucherKind kind, int id, Dictionary<string, object> head, object[] lines)
        {
            if (head.Count == 0 && lines.Length == 0)
            {
                throw new BridgeException(400, "bad_request", "没有要修改的内容");
            }
            if (lines.Length > 200)
            {
                throw BridgeException.BadField("lines", "lines 不能超过 200 行");
            }
            SaleEditPlan plan = new SaleEditPlan();
            plan.Kind = kind;
            plan.Id = id;
            plan.Head = Texts(Clean(kind, true, head));
            Dictionary<string, object> snap = Gate(conn, plan);
            plan.Code = CoRows.Col(snap, "code");
            plan.Rows = ReadRows(conn, plan);
            if (plan.Invoice)
            {
                RefuseAdvance(conn, plan);
            }
            ReadOps(plan, lines);
            if (kind.Name == "dispatch")
            {
                CheckAdds(conn, plan);
                CheckFree(conn, plan);
            }
            return plan;
        }

        static Dictionary<string, object> Gate(object conn, SaleEditPlan plan)
        {
            if (plan.Kind.Name == "sale_invoice")
            {
                Dictionary<string, object> snap = SaleEdit.GateEditInvoice(conn, plan.Kind, plan.Id);
                plan.Vt = SaleEdit.InvoiceVtOf(snap);
                return snap;
            }
            plan.Vt = SaSession.SaVt(plan.Kind);
            return SaleEdit.GateEditDispatch(conn, plan.Kind, plan.Id);
        }

        static List<SaleEditRow> ReadRows(object conn, SaleEditPlan plan)
        {
            bool inv = plan.Invoice;
            List<Dictionary<string, object>> found = Rows.Query(conn, inv ? InvLinesSql : DispLinesSql, new object[] { plan.Id }, 500);
            List<SaleEditRow> rows = new List<SaleEditRow>();
            for (int i = 0; i < found.Count; i++)
            {
                SaleEditRow row = new SaleEditRow();
                row.LineId = CoRows.AsId(CoRows.Col(found[i], "id"));
                row.Src = CoRows.AsId(CoRows.Col(found[i], inv ? "dl" : (plan.Red ? "orig" : "so")));
                row.SoLine = CoRows.AsId(CoRows.Col(found[i], "so"));
                row.Inv = CoRows.Col(found[i], "inv");
                row.App = inv ? 0 : CoRows.AsId(CoRows.Col(found[i], "app"));
                row.Old = PuInv.Num(CoRows.Col(found[i], "q"));
                row.New = row.Old;
                rows.Add(row);
            }
            return rows;
        }

        static void ReadOps(SaleEditPlan plan, object[] lines)
        {
            Dictionary<int, SaleEditRow> byId = IndexRows(plan);
            HashSet<int> seen = new HashSet<int>();
            List<SaleEditRow> adds = new List<SaleEditRow>();
            int deletes = 0;
            for (int i = 0; i < lines.Length; i++)
            {
                Dictionary<string, object> map = lines[i] as Dictionary<string, object>;
                if (map == null)
                {
                    throw BridgeException.BadField(FieldPath.Item("lines", i), "表体行必须是对象");
                }
                try
                {
                    string op = Values.Text(Raw(map, "op")).Trim().ToLowerInvariant();
                    if (op == "add")
                    {
                        adds.Add(ReadAdd(plan, map, adds, i));
                        continue;
                    }
                    SaleEditRow row = PickRow(map, byId, seen);
                    if (op == "delete")
                    {
                        RefuseAppLine(row);
                        row.Deleted = true;
                        row.New = 0m;
                        deletes = deletes + 1;
                        continue;
                    }
                    ReadUpdate(plan, row, map);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
            if (deletes > 0 && plan.Rows.Count - deletes < 1)
            {
                throw BridgeException.BadField("lines", "不能删除全部明细");
            }
            plan.Rows.AddRange(adds);
        }

        static Dictionary<int, SaleEditRow> IndexRows(SaleEditPlan plan)
        {
            Dictionary<int, SaleEditRow> byId = new Dictionary<int, SaleEditRow>();
            for (int i = 0; i < plan.Rows.Count; i++)
            {
                byId[plan.Rows[i].LineId] = plan.Rows[i];
            }
            return byId;
        }

        static SaleEditRow PickRow(Dictionary<string, object> map, Dictionary<int, SaleEditRow> byId, HashSet<int> seen)
        {
            int lineId = CoRows.AsId(Raw(map, "line_id"));
            SaleEditRow row;
            if (lineId <= 0 || !byId.TryGetValue(lineId, out row))
            {
                throw BridgeException.BadField("lines.line_id", "明细行不存在");
            }
            if (!seen.Add(lineId))
            {
                throw BridgeException.BadField("lines.line_id", "明细行重复");
            }
            return row;
        }

        static void ReadUpdate(SaleEditPlan plan, SaleEditRow row, Dictionary<string, object> map)
        {
            Dictionary<string, object> fields = Clean(plan.Kind, false, map);
            object qty;
            if (fields.TryGetValue("iquantity", out qty))
            {
                fields.Remove("iquantity");
                row.Qty = true;
                row.New = NewQty(plan, row, qty);
                if (row.New != row.Old)
                {
                    RefuseAppLine(row);
                }
            }
            row.Touched = true;
            row.Fields = Texts(fields);
        }

        // 挂着退货申请单行（irtnappid）的退货行：U8 修改时申请行 fretqty 怎么回写未实测，改数量、删行都 409（未经实测）。
        static void RefuseAppLine(SaleEditRow row)
        {
            if (row.App > 0)
            {
                throw new BridgeException(409, "state_mismatch", AppLineMsg);
            }
        }

        // 退货单行是负数：比较绝对值，请求写正数，写回负数。
        static decimal NewQty(SaleEditPlan plan, SaleEditRow row, object value)
        {
            decimal qty;
            if (!StockUnits.Dec(Cell(value), out qty) || qty <= 0m)
            {
                throw BridgeException.BadField("lines.iquantity", "数量必须大于 0");
            }
            if (qty > Math.Abs(row.Old))
            {
                throw BridgeException.BadField("lines.iquantity", "修改只能减少数量");
            }
            return plan.Red ? -qty : qty;
        }

        // 小写键 → 原值。op、line_id 跳过；名单外 400「不能修改字段 x」；大小写不同的重复键 400。
        static Dictionary<string, object> Clean(VoucherKind kind, bool head, Dictionary<string, object> map)
        {
            Dictionary<string, object> clean = new Dictionary<string, object>();
            foreach (KeyValuePair<string, object> pair in map)
            {
                string low = pair.Key == null ? "" : pair.Key.ToLowerInvariant();
                if (!head && (low == "op" || low == "line_id"))
                {
                    continue;
                }
                if (!MetaAllowed(kind, head, low))
                {
                    throw BridgeException.BadField(FieldPath.Join(FieldPath.Side(head), pair.Key), "不能修改字段 " + pair.Key)
                        .WithHint(FieldPath.WritableHint);
                }
                if (clean.ContainsKey(low))
                {
                    throw BridgeException.BadField(FieldPath.Join(FieldPath.Side(head), pair.Key), "字段重复 " + pair.Key);
                }
                clean[low] = pair.Value;
            }
            return clean;
        }

        // JSON null：文本字段清空（写空串）；数量、日期不能清空，400。
        static Dictionary<string, string> Texts(Dictionary<string, object> clean)
        {
            Dictionary<string, string> texts = new Dictionary<string, string>();
            foreach (KeyValuePair<string, object> pair in clean)
            {
                string text = Cell(pair.Value);
                if (text == null && (pair.Key == "iquantity" || pair.Key == "ddate"))
                {
                    throw new BridgeException(400, "bad_request", "字段 " + pair.Key + " 不能为 null");
                }
                texts[pair.Key] = text ?? "";
            }
            return texts;
        }

        static string Cell(object value)
        {
            if (value == null || value is DBNull)
            {
                return null;
            }
            if (value is bool)
            {
                return (bool)value ? "1" : "0";
            }
            IFormattable fmt = value as IFormattable;
            if (fmt != null)
            {
                return fmt.ToString(null, CultureInfo.InvariantCulture);
            }
            return Convert.ToString(value, CultureInfo.InvariantCulture);
        }

        static object Raw(Dictionary<string, object> map, string name)
        {
            foreach (KeyValuePair<string, object> pair in map)
            {
                if (string.Equals(pair.Key, name, StringComparison.OrdinalIgnoreCase))
                {
                    return pair.Value;
                }
            }
            return null;
        }
    }

    // 一次修改的计划：登录前从 SQL 读出的行（旧数量带符号）和请求要做的变更。
    internal sealed class SaleEditPlan
    {
        internal VoucherKind Kind;
        internal int Id;
        internal int Vt;
        internal string Code;
        internal Dictionary<string, string> Head;
        internal List<SaleEditRow> Rows;

        internal bool Invoice
        {
            get { return Kind != null && Kind.Name == "sale_invoice"; }
        }

        internal bool Red
        {
            get { return SaleReturn.Is(Kind); }
        }
    }

    // Src：发货单是订单行 iSOsID，退货单是原发货行 iCorID，销售发票是发货行 iDLsID。SoLine：行上的订单行 iSOsID。
    // Added：发货单新增行（LineId 为 0，Old 为 0），At 是请求里的下标，OrderRow / SoCode / SoQty 取自订单行。
    internal sealed class SaleEditRow
    {
        internal bool Added;
        internal int At;
        internal string OrderRow;
        internal string SoCode;
        internal decimal SoQty;
        internal decimal SoFh;
        internal int LineId;
        internal int Src;
        internal int SoLine;
        // 退货单行挂的退货申请单行 irtnappid（其余类型为 0）。
        internal int App;
        internal string Inv;
        internal decimal Old;
        internal decimal New;
        internal bool Deleted;
        internal bool Touched;
        internal bool Qty;
        internal Dictionary<string, string> Fields;
    }
}
