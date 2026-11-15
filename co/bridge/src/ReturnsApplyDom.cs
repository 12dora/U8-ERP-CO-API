using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 退货申请单表体的数量、金额与可退数量（新增 SaleGenApply、修改 ReturnsApplyEdit 共用）。
    // 卡片上数量、金额都是负数：API 收正数，桥写负数；iAppQuantity（申请数量）与 iQuantity 同写（同 U8，两列相同）。
    internal static class ReturnsApplyDom
    {
        // 按比例缩放的列（数量 6 位、金额 2 位）。
        static readonly string[] Nums = new string[] { "inum", "iappnum" };
        static readonly string[] Money = new string[] {
            "imoney", "itax", "isum", "inatmoney", "inattax", "inatsum", "idiscount", "inatdiscount"
        };

        // 原发货行可申请数量 = |iQuantity| − |iRetQuantity| − 其他未关闭申请行尚未退货的数量（|iQuantity| − |fretqty|）。
        // except 是要排除的申请行（修改时为本次改数量的各行，新增为空）。SaVoucherService 自行提交、不能跨调用持锁，所以不加锁读。
        const string DlSql = "select convert(varchar(40), isnull(iQuantity,0)) as q, convert(varchar(40), isnull(iRetQuantity,0)) as r "
            + "from DispatchLists where iDLsID=?";
        const string PendSql = "select a.AutoID as id, convert(varchar(40), isnull(a.iQuantity,0)) as q, "
            + "convert(varchar(40), isnull(a.fretqty,0)) as f from SA_ReturnsApplyDetail a "
            + "inner join SA_ReturnsApplyMain m on m.ID=a.ID "
            + "where a.iDLsID=? and isnull(m.cCloser,'')='' and isnull(a.cSCloser,'')=''";

        internal static decimal Room(object conn, int dlLine, ICollection<int> except)
        {
            Dictionary<string, object> row = Rows.One(conn, DlSql, new object[] { dlLine });
            if (row == null)
            {
                throw new BridgeException(409, "state_mismatch", "原发货单行不存在");
            }
            List<Dictionary<string, object>> pend = Rows.Query(conn, PendSql, new object[] { dlLine }, 5000);
            decimal room = Math.Abs(PuInv.Num(CoRows.Col(row, "q"))) - Math.Abs(PuInv.Num(CoRows.Col(row, "r")));
            for (int i = 0; i < pend.Count; i++)
            {
                if (except != null && except.Contains(CoRows.AsId(CoRows.Col(pend[i], "id"))))
                {
                    continue;
                }
                room = room - (Math.Abs(PuInv.Num(CoRows.Col(pend[i], "q"))) - Math.Abs(PuInv.Num(CoRows.Col(pend[i], "f"))));
            }
            return room < 0m ? 0m : room;
        }

        // qty 是同一发货行在本次请求里的合计。
        internal static void RequireRoom(object conn, int dlLine, ICollection<int> except, decimal qty)
        {
            if (qty > Room(conn, dlLine, except) + 0.000001m)
            {
                throw new BridgeException(409, "state_mismatch", "超过可退货数量");
            }
        }

        // 行上数量写成 −qty，辅数量、金额按 ratio 缩放（ratio 可为负：从蓝字发货行取数时）。
        internal static void SetQty(object body, object row, decimal qty, decimal ratio, List<string> schema)
        {
            string neg = Text(-qty, 6);
            DomRows.Set(body, row, "iquantity", neg, schema);
            if (Has(schema, "iappquantity"))
            {
                DomRows.Set(body, row, "iappquantity", neg, schema);
            }
            Scale(body, row, Nums, ratio, 6, schema);
            Scale(body, row, Money, ratio, 2, schema);
        }

        static void Scale(object body, object row, string[] names, decimal ratio, int places, List<string> schema)
        {
            for (int i = 0; i < names.Length; i++)
            {
                decimal value;
                if (!Has(schema, names[i]) || !TryDec(DomRows.Get(row, names[i]), out value))
                {
                    continue;
                }
                DomRows.Set(body, row, names[i], Text(Math.Round(value * ratio, places, MidpointRounding.AwayFromZero), places),
                    schema);
            }
        }

        // 单行 BodyCheck：先数量，再单价键（有含税单价用它，否则无税单价）。数量 U8 拒绝 409 u8_rejected 原文带回；
        // 单价键拒绝只记审计，保留按比例算出的金额。
        internal static void Recalc(WorkContext ctx, object co, object[] doms, object row)
        {
            string msg = SaleCalc.Check(co, doms[0], doms[1], row, "iquantity", false);
            if (msg.Length > 0)
            {
                CoRows.Note(ctx.Item, "BodyCheck iquantity " + msg);
                throw new BridgeException(409, "u8_rejected", msg);
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

        internal static decimal Dec(string text)
        {
            decimal value;
            return TryDec(text, out value) ? value : 0m;
        }

        internal static string Text(decimal value, int places)
        {
            return value.ToString(places == 2 ? "0.00" : "0.######", CultureInfo.InvariantCulture);
        }

        internal static bool Has(List<string> schema, string name)
        {
            for (int i = 0; schema != null && i < schema.Count; i++)
            {
                if (string.Equals(schema[i], name, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }

        static bool TryDec(string text, out decimal value)
        {
            value = 0m;
            if (text == null || text.Trim().Length == 0)
            {
                return false;
            }
            return decimal.TryParse(text.Trim(), NumberStyles.Any, CultureInfo.InvariantCulture, out value);
        }
    }
}
