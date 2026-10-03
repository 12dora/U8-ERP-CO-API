using System;
using System.Collections.Generic;

namespace U8Co
{
    // K12 退货申请单修改、删除（VT 34）。都只收未审核、未关闭、没有下游退货单的单据（ReturnsApply.RequireEditable）。
    // 修改：GetVoucherData 读入，表头 editprop=M，改到的行 editprop=M（数量按绝对值：请求正数、桥写负数，金额同比例缩放后
    // 单行 BodyCheck），调用前重查原发货行可退数量，Save(头, 体, (short)1, "")，之后核对行数与行数量。只改已有行。
    // 删除：每行 editprop=D，Delete(头, 体)，之后新连接上确认表头已不在。保存、删除都由 U8 自行提交（ReturnsApplyTran）。
    internal static class ReturnsApplyEdit
    {
        const string LinesSql = "select AutoID, isnull(iDLsID,0) as dl, convert(varchar(40), isnull(iQuantity,0)) as q "
            + "from SA_ReturnsApplyDetail where ID=?";
        const string CountSql = "select convert(varchar(20), count(*)) from SA_ReturnsApplyDetail where ID=?";
        const string QtySql = "select convert(varchar(40), isnull(iQuantity,0)) from SA_ReturnsApplyDetail where AutoID=? and ID=?";

        public static ApiResult Delete(WorkContext ctx, VoucherKind kind, int id)
        {
            ReturnsApply.RequireEditable(ctx.Conn, id);
            object sys = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                SaSession.OpenSa(ctx.Conn, ctx.Session.Login, ReturnsApplyRead.SaVt, out sys, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                string err = SaSession.ReadSa(co, doms, id, ctx.Item);
                if (err.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", err);
                }
                CoRows.RequireHead(doms[0], "ID", id);
                ReturnsApplyTran.Delete(ctx, co, doms, "删除退货申请单 " + Id(id));
            }
            finally
            {
                SaSession.Release(sys, co, doms);
            }
            return ReturnsApply.Gone(ctx, kind, id);
        }

        public static ApiResult Update(WorkContext ctx, VoucherKind kind, int id, Dictionary<string, object> head, object[] lines)
        {
            Dictionary<string, object> snap = ReturnsApply.RequireEditable(ctx.Conn, id);
            List<EditLine> plan = Plan(ctx.Conn, id, lines);
            DocMark.Upstream(ctx.Conn, kind, id);
            object sys = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                SaSession.OpenSa(ctx.Conn, ctx.Session.Login, ReturnsApplyRead.SaVt, out sys, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                string err = SaSession.ReadSa(co, doms, id, ctx.Item);
                if (err.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", err);
                }
                CoRows.RequireHead(doms[0], "ID", id);
                FillHead(ctx, doms[0], head);
                FillLines(ctx, co, doms, plan);
                SaveTran(ctx, co, doms, id, plan);
            }
            finally
            {
                SaSession.Release(sys, co, doms);
            }
            return ReturnsApply.Saved(ctx, kind, id, CoRows.Col(snap, "code"));
        }

        static List<EditLine> Plan(object conn, int id, object[] lines)
        {
            Dictionary<int, Dictionary<string, object>> have = new Dictionary<int, Dictionary<string, object>>();
            List<Dictionary<string, object>> rows = Rows.Query(conn, LinesSql, new object[] { id }, 1000);
            for (int i = 0; rows != null && i < rows.Count; i++)
            {
                have[CoRows.AsId(CoRows.Col(rows[i], "AutoID"))] = rows[i];
            }
            List<EditLine> plan = new List<EditLine>();
            for (int i = 0; i < lines.Length; i++)
            {
                Dictionary<string, object> map = (Dictionary<string, object>)lines[i];
                int lineId = ReturnsApplyReq.IdOf(map, "line_id", i);
                Dictionary<string, object> row;
                if (!have.TryGetValue(lineId, out row))
                {
                    throw BridgeException.BadField("lines." + i + ".line_id", "明细行不存在");
                }
                EditLine line = new EditLine();
                line.LineId = lineId;
                line.DlLine = CoRows.AsId(CoRows.Col(row, "dl"));
                line.Fields = map;
                if (ReturnsApplyReq.Raw(map, "iquantity") != null)
                {
                    line.Qty = ReturnsApplyReq.Qty(map, "iquantity", i);
                }
                plan.Add(line);
            }
            RequireRooms(conn, plan);
            return plan;
        }

        // 按原发货行合计本次改了数量的各行，与排除这些行之后的可申请数量比较（同一发货行的其他行仍按原数量占用）。
        static void RequireRooms(object conn, List<EditLine> plan)
        {
            HashSet<int> edited = new HashSet<int>();
            Dictionary<int, decimal> sum = new Dictionary<int, decimal>();
            for (int i = 0; i < plan.Count; i++)
            {
                if (plan[i].Qty <= 0m || plan[i].DlLine <= 0)
                {
                    continue;
                }
                edited.Add(plan[i].LineId);
                decimal had;
                sum.TryGetValue(plan[i].DlLine, out had);
                sum[plan[i].DlLine] = had + plan[i].Qty;
            }
            foreach (KeyValuePair<int, decimal> pair in sum)
            {
                ReturnsApplyDom.RequireRoom(conn, pair.Key, edited, pair.Value);
            }
        }

        static void FillHead(WorkContext ctx, object dom, Dictionary<string, object> head)
        {
            List<object> rows = DomRows.RowsOf(dom);
            object row = rows[0];
            List<string> schema = DomRows.Schema(dom);
            foreach (KeyValuePair<string, object> pair in head)
            {
                if (!ReturnsApplyDom.Has(schema, pair.Key))
                {
                    throw BridgeException.BadField("head." + pair.Key, "不能设置字段 " + pair.Key);
                }
                DomRows.Set(dom, row, pair.Key, CellText(pair.Value, "head." + pair.Key), schema);
            }
            // 修改人、修改日期：schema 里有才写。
            if (ReturnsApplyDom.Has(schema, "cmodifier"))
            {
                DomRows.Set(dom, row, "cmodifier", ctx.OperatorName ?? "", schema);
            }
            if (ReturnsApplyDom.Has(schema, "dmoddate") && (ctx.Item.Date ?? "").Length > 0)
            {
                DomRows.Set(dom, row, "dmoddate", ctx.Item.Date, schema);
            }
            DomRows.Set(dom, row, "editprop", "M", schema);
        }

        static void FillLines(WorkContext ctx, object co, object[] doms, List<EditLine> plan)
        {
            List<string> schema = DomRows.Schema(doms[1]);
            for (int k = 0; k < plan.Count; k++)
            {
                int index = IndexOf(doms[1], plan[k].LineId);
                object row = DomRows.RowsOf(doms[1])[index];
                if (plan[k].Qty > 0m)
                {
                    decimal old = ReturnsApplyDom.Dec(DomRows.Get(row, "iquantity"));
                    decimal ratio = old == 0m ? 1m : -plan[k].Qty / old;
                    ReturnsApplyDom.SetQty(doms[1], row, plan[k].Qty, ratio, schema);
                    ReturnsApplyDom.Recalc(ctx, co, doms, row);
                    row = DomRows.RowsOf(doms[1])[index];
                }
                SetFields(doms[1], row, plan[k], schema);
                DomRows.Set(doms[1], row, "editprop", "M", schema);
            }
        }

        static void SetFields(object body, object row, EditLine line, List<string> schema)
        {
            foreach (KeyValuePair<string, object> pair in line.Fields)
            {
                string low = pair.Key.ToLowerInvariant();
                if (low == "op" || low == "line_id" || low == "iquantity")
                {
                    continue;
                }
                if (!ReturnsApplyDom.Has(schema, pair.Key))
                {
                    throw BridgeException.BadField("lines." + pair.Key, "未知字段 " + pair.Key);
                }
                DomRows.Set(body, row, pair.Key, CellText(pair.Value, "lines." + pair.Key), schema);
            }
        }

        static int IndexOf(object body, int lineId)
        {
            List<object> rows = DomRows.RowsOf(body);
            for (int i = 0; i < rows.Count; i++)
            {
                if (CoRows.AsId(DomRows.Get(rows[i], "autoid")) == lineId)
                {
                    return i;
                }
            }
            throw new BridgeException(409, "state_mismatch", "U8 读出的退货申请单缺少明细行");
        }

        // Save 由 SaVoucherService 自行提交（ReturnsApplyTran）：调用前不加锁重查可退数量、记下行数，之后核对（不符 504）。
        static void SaveTran(WorkContext ctx, object co, object[] doms, int id, List<EditLine> plan)
        {
            RequireRooms(ctx.Conn, plan);
            decimal count = PuInv.Num(Rows.Scalar(ctx.Conn, CountSql, new object[] { id }));
            ReturnsApplyTran.Save(ctx, co, doms, (short)1, "修改退货申请单 " + Id(id));
            StockCall.AfterCheck(ctx, delegate(object conn) { RequireSaved(conn, id, plan, count); }, "退货申请单 " + Id(id));
        }

        static string Id(int id)
        {
            return id.ToString(System.Globalization.CultureInfo.InvariantCulture);
        }

        // 只改已有行：保存后行数不变，改了数量的行等于请求（负数）。
        static void RequireSaved(object conn, int id, List<EditLine> plan, decimal count)
        {
            decimal now = PuInv.Num(Rows.Scalar(conn, CountSql, new object[] { id }));
            if (now != count)
            {
                throw new BridgeException(409, "u8_rejected", "U8 保存的退货申请单行数与修改前不一致");
            }
            for (int i = 0; i < plan.Count; i++)
            {
                if (plan[i].Qty <= 0m)
                {
                    continue;
                }
                decimal qty = PuInv.Num(Rows.Scalar(conn, QtySql, new object[] { plan[i].LineId, id }));
                if (Math.Abs(qty + plan[i].Qty) > 0.000001m)
                {
                    throw new BridgeException(409, "u8_rejected", "U8 保存的退货申请单数量与请求不一致");
                }
            }
        }

        static string CellText(object value, string field)
        {
            if (value is bool)
            {
                return (bool)value ? "1" : "0";
            }
            IFormattable fmt = value as IFormattable;
            if (value is string)
            {
                return (string)value;
            }
            if (fmt == null)
            {
                throw BridgeException.BadField(field, "字段值类型不正确");
            }
            return fmt.ToString(null, System.Globalization.CultureInfo.InvariantCulture);
        }

        sealed class EditLine
        {
            internal int LineId;
            internal int DlLine;
            internal decimal Qty;
            internal Dictionary<string, object> Fields;
        }
    }
}
