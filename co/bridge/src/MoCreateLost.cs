using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 生产订单新增拿不到新 MoId（调用异常、IPC 错误、Serialize 里没有）时，在新连接上找本次新建的订单。
    // 取调用前最大 MoId 之后本操作员建的全部订单（每行带类别编码和 mom_morder 的开工、完工日期），逐张比较：
    // 宽松匹配 = 有一行存货与请求的某一行相同；严格匹配 = 行数相同，每行行号、存货、数量（按存货数量小数位）、部门、
    // 类别、仓库、备注、开工、完工日期都相同，创建时间不早于调用前在同一查询里取的数据库时间，请求带了单号再比单号。
    // 只有恰好一张宽松匹配、且它严格匹配时才认；多张、没有或超出行数上限一律返回 0，由调用方报结果未知，不挑其中一张。
    internal static class MoCreateLost
    {
        const int RowsMax = 2000;
        const string LostSql = "select convert(varchar(20), o.MoId) as MoId, o.MoCode,"
            + " case when o.CreateTime >= convert(datetime, ?, 121) then '1' else '0' end as fresh,"
            + " convert(varchar(10), d.SortSeq) as SortSeq, d.InvCode, convert(varchar(40), d.Qty) as Qty,"
            + " d.MDeptCode, t.MotypeCode, d.WhCode, d.Remark,"
            + " convert(varchar(10), m.StartDate, 23) as StartDate, convert(varchar(10), m.DueDate, 23) as DueDate"
            + " from mom_order o join mom_orderdetail d on d.MoId=o.MoId"
            + " left join mom_motype t on t.MoTypeId=d.MoTypeId"
            + " left join mom_morder m on m.MoDId=d.MoDId"
            + " where o.MoId > ? and o.CreateUser = ?"
            + " order by o.MoId, d.SortSeq, d.MoDId";

        // loose 返回同存货的候选张数（行太多时按有候选算），调用方据此区分「多半没建成」和「结果未知」。
        public static int Find(object conn, MoCreateAsk ask, string user, out int loose)
        {
            loose = 1;
            object[] args = new object[] { ask.Since, ask.MaxBefore, user };
            List<Dictionary<string, object>> rows = Rows.Query(conn, LostSql, args, RowsMax + 1);
            if (rows.Count > RowsMax)
            {
                return 0;
            }
            List<int> order = new List<int>();
            Dictionary<int, List<Dictionary<string, object>>> byId = Group(rows, order);
            loose = 0;
            int hit = 0;
            for (int i = 0; i < order.Count; i++)
            {
                List<Dictionary<string, object>> lines = byId[order[i]];
                if (!Loose(lines, ask))
                {
                    continue;
                }
                loose++;
                if (Strict(lines, ask))
                {
                    hit = order[i];
                }
            }
            return loose == 1 ? hit : 0;
        }

        static Dictionary<int, List<Dictionary<string, object>>> Group(List<Dictionary<string, object>> rows, List<int> order)
        {
            Dictionary<int, List<Dictionary<string, object>>> byId = new Dictionary<int, List<Dictionary<string, object>>>();
            for (int i = 0; i < rows.Count; i++)
            {
                int id = CoRows.AsId(CoRows.Col(rows[i], "MoId"));
                List<Dictionary<string, object>> lines;
                if (!byId.TryGetValue(id, out lines))
                {
                    lines = new List<Dictionary<string, object>>();
                    byId[id] = lines;
                    order.Add(id);
                }
                lines.Add(rows[i]);
            }
            return byId;
        }

        static bool Loose(List<Dictionary<string, object>> rows, MoCreateAsk ask)
        {
            for (int r = 0; r < rows.Count; r++)
            {
                string inv = CoRows.Col(rows[r], "InvCode");
                for (int i = 0; i < ask.Lines.Count; i++)
                {
                    if (SameCode(inv, ask.Lines[i].InvCode))
                    {
                        return true;
                    }
                }
            }
            return false;
        }

        internal static bool Strict(List<Dictionary<string, object>> rows, MoCreateAsk ask)
        {
            if (rows.Count != ask.Lines.Count || CoRows.Col(rows[0], "fresh") != "1")
            {
                return false;
            }
            if (ask.Code.Length > 0 && !SameCode(CoRows.Col(rows[0], "MoCode"), ask.Code))
            {
                return false;
            }
            for (int i = 0; i < rows.Count; i++)
            {
                if (!SameKeys(rows[i], ask.Lines[i]) || !SameValues(rows[i], ask.Lines[i], ask.QtyDigits))
                {
                    return false;
                }
            }
            return true;
        }

        // 编码类（库的排序规则不分大小写，这里同样不分）。
        static bool SameKeys(Dictionary<string, object> row, MoLine line)
        {
            return CoRows.Col(row, "SortSeq") == line.Seq.ToString(CultureInfo.InvariantCulture)
                && SameCode(CoRows.Col(row, "InvCode"), line.InvCode)
                && SameCode(CoRows.Col(row, "MDeptCode"), line.Dept)
                && SameCode(CoRows.Col(row, "MotypeCode"), line.MoType)
                && (string.IsNullOrEmpty(line.Wh) || SameCode(CoRows.Col(row, "WhCode"), line.Wh));
        }

        static bool SameValues(Dictionary<string, object> row, MoLine line, int digits)
        {
            decimal qty;
            if (!decimal.TryParse(CoRows.Col(row, "Qty"), NumberStyles.Number, CultureInfo.InvariantCulture, out qty))
            {
                return false;
            }
            return decimal.Round(qty, digits) == decimal.Round(line.Qty, digits)
                && string.Equals(CoRows.Col(row, "Remark"), line.Remark ?? "", StringComparison.Ordinal)
                && CoRows.Col(row, "StartDate") == line.Start
                && CoRows.Col(row, "DueDate") == line.Due;
        }

        static bool SameCode(string a, string b)
        {
            return string.Equals((a ?? "").Trim(), (b ?? "").Trim(), StringComparison.OrdinalIgnoreCase);
        }
    }
}
