using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 把修改计划写进 GetVoucherData 读出的 DOM。未改的行 editprop 清空，改的行 M、删的行 D，新增行 A（发货单），表头 M。
    internal static partial class SaleEditMore
    {
        // 按数量比例缩放的列（同退货单生单 SaleReturnLine.RetScaled）：金额 2 位，件数和未签收数 6 位。
        static readonly string[] Scaled = new string[] {
            "inum", "imoney", "itax", "isum", "inatmoney", "inattax", "inatsum", "idiscount", "inatdiscount",
            "funsignquantity", "funsignnum"
        };

        static void Apply(WorkContext ctx, object co, object[] doms, SaleEditPlan plan)
        {
            ApplyHead(doms[0], plan.Head);
            BlankEdit(doms[1]);
            string key = plan.Invoice ? "autoid" : "idlsid";
            for (int i = 0; i < plan.Rows.Count; i++)
            {
                SaleEditRow row = plan.Rows[i];
                if (row.Deleted)
                {
                    DomRows.Set(doms[1], FindRow(doms[1], key, row.LineId), "editprop", "D");
                }
                else if (row.Touched)
                {
                    ApplyLine(ctx, co, doms, key, row);
                }
            }
            AddLines(ctx, co, doms, plan);
            if (plan.Invoice)
            {
                PinInvoice(ctx, doms);
            }
            Stamp(ctx, doms[0]);
        }

        static void ApplyHead(object dom, Dictionary<string, string> head)
        {
            if (head == null || head.Count == 0)
            {
                return;
            }
            List<string> schema = DomRows.Schema(dom);
            object row = HeadRow(dom);
            foreach (KeyValuePair<string, string> pair in head)
            {
                DomRows.Set(dom, row, Known(schema, pair.Key, "head"), pair.Value, schema);
            }
        }

        static void ApplyLine(WorkContext ctx, object co, object[] doms, string key, SaleEditRow row)
        {
            List<string> schema = DomRows.Schema(doms[1]);
            object node = FindRow(doms[1], key, row.LineId);
            foreach (KeyValuePair<string, string> pair in row.Fields)
            {
                DomRows.Set(doms[1], node, Known(schema, pair.Key, "lines"), pair.Value, schema);
            }
            if (row.Qty && row.New != row.Old)
            {
                ScaleTo(doms[1], node, row.New, schema);
                Money(ctx, co, doms, node);
                node = FindRow(doms[1], key, row.LineId);
            }
            DomRows.Set(doms[1], node, "editprop", "M", schema);
        }

        // 行上原数量 → 新数量（同号）：数量写准，金额、件数按比例。之后 BodyCheck 再按 U8 的规则算一遍。
        static void ScaleTo(object body, object row, decimal qty, List<string> schema)
        {
            decimal full = PuInv.Num(DomRows.Get(row, "iquantity"));
            DomRows.Set(body, row, "iquantity", Num(qty), schema);
            if (full == 0m)
            {
                return;
            }
            decimal ratio = Math.Abs(qty / full);
            for (int i = 0; i < Scaled.Length; i++)
            {
                decimal value;
                if (!StockUnits.Dec(DomRows.Get(row, Scaled[i]), out value))
                {
                    continue;
                }
                bool money = Scaled[i].StartsWith("i", StringComparison.Ordinal) && Scaled[i] != "inum";
                decimal scaled = Math.Round(value * ratio, money ? 2 : 6, MidpointRounding.AwayFromZero);
                DomRows.Set(body, row, Scaled[i], scaled.ToString(money ? "0.00" : "0.######",
                    CultureInfo.InvariantCulture), schema);
            }
        }

        // 同退货单生单 ReturnMoney：单行 BodyCheck 先数量，再单价键。U8 拒绝只记审计，保留按比例算出的金额。
        static void Money(WorkContext ctx, object co, object[] doms, object row)
        {
            string msg = SaleCalc.Check(co, doms[0], doms[1], row, "iquantity", false);
            if (msg.Length > 0)
            {
                CoRows.Note(ctx.Item, "BodyCheck iquantity " + msg);
            }
            string price = DomRows.Get(row, "itaxunitprice").Trim().Length > 0 ? "itaxunitprice" : "iunitprice";
            if (DomRows.Get(row, price).Trim().Length == 0)
            {
                return;
            }
            msg = SaleCalc.Check(co, doms[0], doms[1], row, price, false);
            if (msg.Length > 0)
            {
                CoRows.Note(ctx.Item, "BodyCheck " + price + " " + msg);
            }
        }

        // U8 的 Save 不写修改人（与销售订单相同）。schema 里没有的戳记不设。
        static void Stamp(WorkContext ctx, object dom)
        {
            List<string> schema = DomRows.Schema(dom);
            object row = HeadRow(dom);
            DomRows.Set(dom, row, "editprop", "M", schema);
            string user = ctx.Session.OperatorName ?? "";
            if (user.Length == 0)
            {
                user = ctx.Item.Operator ?? "";
            }
            string date = ctx.Item.Date == null ? "" : ctx.Item.Date.Trim();
            if (date.Length == 0)
            {
                date = DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
            }
            SetIf(dom, row, schema, "cmodifier", user);
            SetIf(dom, row, schema, "dmoddate", date);
            SetIf(dom, row, schema, "dmodifysystime", DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture));
        }

        static void SetIf(object dom, object row, List<string> schema, string name, string value)
        {
            string actual = Match(schema, name);
            if (actual.Length > 0)
            {
                DomRows.Set(dom, row, actual, value, schema);
            }
        }

        static void BlankEdit(object body)
        {
            List<object> rows = DomRows.RowsOf(body);
            for (int i = 0; i < rows.Count; i++)
            {
                DomRows.Set(body, rows[i], "editprop", "");
            }
        }

        static object HeadRow(object dom)
        {
            List<object> rows = DomRows.RowsOf(dom);
            if (rows.Count < 1)
            {
                throw new BridgeException(500, "internal", "模板没有字段");
            }
            return rows[0];
        }

        // U8 按操作员数据权限装入的 DOM 里可能没有这一行。
        static object FindRow(object body, string key, int lineId)
        {
            List<object> rows = DomRows.RowsOf(body);
            for (int i = 0; i < rows.Count; i++)
            {
                if (CoRows.AsId(DomRows.Get(rows[i], key)) == lineId)
                {
                    return rows[i];
                }
            }
            throw new BridgeException(409, "state_mismatch", "U8 读入的单据里没有明细行 " + lineId.ToString(CultureInfo.InvariantCulture));
        }

        // 白名单里的名字必须在该行集 schema 里，否则 400「未知字段 x」。返回 schema 的写法。
        static string Known(List<string> schema, string name, string at)
        {
            string actual = Match(schema, name);
            if (actual.Length == 0)
            {
                throw BridgeException.BadField(FieldPath.Join(at, name), "未知字段 " + name);
            }
            return actual;
        }

        static string Match(List<string> schema, string name)
        {
            if (schema == null)
            {
                return "";
            }
            for (int i = 0; i < schema.Count; i++)
            {
                if (string.Equals(schema[i], name, StringComparison.OrdinalIgnoreCase))
                {
                    return schema[i];
                }
            }
            return "";
        }

        static string Num(decimal value)
        {
            return value.ToString("0.######", CultureInfo.InvariantCulture);
        }
    }
}
