using System.Collections.Generic;

namespace U8Co
{
    // 同一张表头表分成两个单据类型（发货单 / 退货单，到货单 / 采购退货单）时，vouchers/load 只认本类型的单据：
    // 单据属于另一个类型（或两个类型都不属于）时 400，detail.reason 是 type_mismatch，属于另一个类型时 detail.type 给出它。
    // 判断用列表同一条类型条件（ListKinds 的 Cond），列表、搜索、读取因此口径一致；事件服务靠 detail.reason 把「离开本类型」和「被删除」分开。
    internal static class VoucherScope
    {
        public const string Reason = "type_mismatch";

        // 类型、同表的另一个类型、本类型的说明、另一个类型的说明。
        static readonly string[][] Pairs = new string[][]
        {
            new string[] { "dispatch", "sale_return", "发货单（蓝字）", "退货单（红字发货单）" },
            new string[] { "sale_return", "dispatch", "退货单（红字发货单）", "发货单（蓝字）" },
            new string[] { "arrival", "purchase_return", "到货单（蓝字）", "采购退货单（红字到货单）" },
            new string[] { "purchase_return", "arrival", "采购退货单（红字到货单）", "到货单（蓝字）" }
        };

        // 单据不属于本类型时返回要抛的 400；属于本类型、不存在（读取自己回 404）或本类型没有同表的另一个类型时返回 null。
        public static BridgeException Check(object conn, VoucherKind kind, int id)
        {
            string[] pair = Find(kind);
            if (pair == null)
            {
                return null;
            }
            Dictionary<string, object> row = Rows.One(conn, Sql(pair), new object[] { id });
            if (row == null || CoRows.Col(row, "own") == "1")
            {
                return null;
            }
            Dictionary<string, object> detail = new Dictionary<string, object>();
            detail["reason"] = Reason;
            if (CoRows.Col(row, "other") != "1")
            {
                return new BridgeException(400, "bad_request", "该单据不是" + pair[2]).WithDetail(detail);
            }
            detail["type"] = pair[1];
            return new BridgeException(400, "bad_request", "该单据是" + pair[3] + "，请用 type=" + pair[1]).WithDetail(detail);
        }

        // 一行：本类型条件、另一个类型条件各算一列；表头表、主键、条件都来自 ListKinds 的常量表。
        internal static string Sql(string[] pair)
        {
            ListKind own = ListKinds.Find(pair[0]);
            ListKind other = ListKinds.Find(pair[1]);
            return "SELECT CASE WHEN " + own[ListKind.Cond] + " THEN 1 ELSE 0 END AS own, CASE WHEN "
                + other[ListKind.Cond] + " THEN 1 ELSE 0 END AS other FROM " + own[ListKind.Head] + " h WHERE h."
                + own[ListKind.Id] + "=?";
        }

        // 自检用：全部类型对的查询。
        internal static List<string> AllSql()
        {
            List<string> all = new List<string>();
            for (int i = 0; i < Pairs.Length; i++)
            {
                all.Add(Sql(Pairs[i]));
            }
            return all;
        }

        static string[] Find(VoucherKind kind)
        {
            for (int i = 0; i < Pairs.Length; i++)
            {
                if (kind != null && Pairs[i][0] == kind.Name)
                {
                    return Pairs[i];
                }
            }
            return null;
        }
    }
}
