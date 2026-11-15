using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 库龄分析的一行：（存货，仓库）或（不按仓库时）存货。Amount 为空表示没有成本。
    internal sealed class AgeLine
    {
        public string Inv = "";
        public string InvName;
        public string InvStd;
        public string Unit;
        // 不按仓库分行时为 null。
        public string Wh;
        public string WhName;
        public decimal Qty;
        public DateTime? LastIn;
        public DateTime? LastOut;
        public decimal? Amount;
        // 有成本的那部分数量（金额 / 它 = 加权单价）。
        public decimal CostQty;
        public int? Age;
        public bool Idle;
    }

    // 一组行的合计。Costed 是其中有成本的行数。
    internal sealed class AgeSum
    {
        public int Items;
        public int Costed;
        public decimal Amount;
    }

    internal sealed class AgeReport
    {
        public AgeSum[] Buckets;
        public readonly AgeSum Idle = new AgeSum();
        public readonly AgeSum Total = new AgeSum();
    }

    // 库龄分析的纯计算：查询行 → 行、合并、库龄与呆滞、排序、分段合计。不连库。
    internal static class ReportsStockAgeCalc
    {
        internal static AgeLine FromRow(Dictionary<string, object> row)
        {
            AgeLine line = new AgeLine();
            line.Inv = GlSql.Col(row, "inv");
            line.InvName = Reports.Text(row, "inv_name");
            line.InvStd = Reports.Text(row, "inv_std");
            line.Unit = Reports.Text(row, "unit");
            line.Wh = GlSql.Col(row, "wh");
            line.WhName = Reports.Text(row, "wh_name");
            line.Qty = Dec(row, "qty");
            line.LastIn = Day(GlSql.Col(row, "li"));
            line.LastOut = Day(GlSql.Col(row, "lo"));
            decimal? price = UnitPrice(row);
            if (price.HasValue)
            {
                line.Amount = decimal.Round(line.Qty * price.Value, 2, MidpointRounding.AwayFromZero);
                line.CostQty = line.Qty;
            }
            return line;
        }

        // 成本月份的结存单价：先取（存货，仓库）自己的金额 / 数量，数量为 0 时用该存货各仓合计兜底，都没有为 null。
        internal static decimal? UnitPrice(Dictionary<string, object> row)
        {
            decimal n = Dec(row, "ia_n");
            if (n != 0m)
            {
                return Dec(row, "ia_m") / n;
            }
            n = Dec(row, "iv_n");
            return n != 0m ? (decimal?)(Dec(row, "iv_m") / n) : null;
        }

        // 按存货合并相邻行（输入已按存货、仓库排序）：数量与金额相加，最后入库、最后出库取各仓里最晚的。
        internal static List<AgeLine> MergeByInv(List<AgeLine> lines)
        {
            List<AgeLine> merged = new List<AgeLine>();
            AgeLine cur = null;
            for (int i = 0; i < lines.Count; i++)
            {
                if (cur == null || cur.Inv != lines[i].Inv)
                {
                    cur = new AgeLine();
                    cur.Inv = lines[i].Inv;
                    cur.InvName = lines[i].InvName;
                    cur.InvStd = lines[i].InvStd;
                    cur.Unit = lines[i].Unit;
                    merged.Add(cur);
                }
                Absorb(cur, lines[i]);
            }
            return merged;
        }

        static void Absorb(AgeLine to, AgeLine from)
        {
            to.Qty += from.Qty;
            to.LastIn = Later(to.LastIn, from.LastIn);
            to.LastOut = Later(to.LastOut, from.LastOut);
            if (from.Amount.HasValue)
            {
                to.Amount = (to.Amount ?? 0m) + from.Amount.Value;
                to.CostQty += from.CostQty;
            }
        }

        static DateTime? Later(DateTime? a, DateTime? b)
        {
            if (!a.HasValue)
            {
                return b;
            }
            if (!b.HasValue)
            {
                return a;
            }
            return a.Value >= b.Value ? a : b;
        }

        // 库龄 = 登录日期 − 最后入库日期（从没有入库记录为 null）。
        // 呆滞：最后出库至今不少于 idleDays 天；从没出过库的按最后入库算（刚入库的不算呆滞），入库也没有记录的算呆滞。
        internal static void Rate(AgeLine line, DateTime asOf, int idleDays)
        {
            line.Age = line.LastIn.HasValue ? (int?)Days(asOf, line.LastIn.Value) : null;
            DateTime? since = line.LastOut ?? line.LastIn;
            line.Idle = !since.HasValue || Days(asOf, since.Value) >= idleDays;
        }

        static int Days(DateTime later, DateTime earlier)
        {
            int days = (int)(later.Date - earlier.Date).TotalDays;
            return days < 0 ? 0 : days;
        }

        // 无金额权限：清掉成本，之后的排序、合计都不带金额信息。
        internal static void DropAmounts(List<AgeLine> lines)
        {
            for (int i = 0; i < lines.Count; i++)
            {
                lines[i].Amount = null;
                lines[i].CostQty = 0m;
            }
        }

        // sort=amount：金额从大到小（没有成本的排最后），同额库龄大的在前；sort=age：库龄从大到小（没有入库记录的最前），同龄金额大的在前。
        // 最后按存货、仓库编码定序。
        internal static void Order(List<AgeLine> lines, string sort)
        {
            bool byAge = sort == "age";
            lines.Sort(delegate(AgeLine a, AgeLine b)
            {
                int c = byAge ? ByAge(a, b) : ByAmount(a, b);
                if (c == 0)
                {
                    c = string.CompareOrdinal(a.Inv, b.Inv);
                }
                return c != 0 ? c : string.CompareOrdinal(a.Wh ?? "", b.Wh ?? "");
            });
        }

        static int ByAmount(AgeLine a, AgeLine b)
        {
            int c = CompareAmount(a, b);
            return c != 0 ? c : CompareAge(a, b);
        }

        static int ByAge(AgeLine a, AgeLine b)
        {
            int c = CompareAge(a, b);
            return c != 0 ? c : CompareAmount(a, b);
        }

        static int CompareAmount(AgeLine a, AgeLine b)
        {
            if (!a.Amount.HasValue || !b.Amount.HasValue)
            {
                return a.Amount.HasValue ? -1 : (b.Amount.HasValue ? 1 : 0);
            }
            return b.Amount.Value.CompareTo(a.Amount.Value);
        }

        static int CompareAge(AgeLine a, AgeLine b)
        {
            int x = a.Age ?? int.MaxValue;
            int y = b.Age ?? int.MaxValue;
            return y.CompareTo(x);
        }

        // 库龄所在的分段序号：上界含本身（≤30 天为第 0 段）；没有入库记录的归最后一段。
        internal static int BucketOf(int[] bounds, int? age)
        {
            if (!age.HasValue)
            {
                return bounds.Length;
            }
            for (int i = 0; i < bounds.Length; i++)
            {
                if (age.Value <= bounds[i])
                {
                    return i;
                }
            }
            return bounds.Length;
        }

        internal static AgeReport Summarize(List<AgeLine> lines, int[] bounds)
        {
            AgeReport report = new AgeReport();
            report.Buckets = new AgeSum[bounds.Length + 1];
            for (int i = 0; i < report.Buckets.Length; i++)
            {
                report.Buckets[i] = new AgeSum();
            }
            for (int i = 0; i < lines.Count; i++)
            {
                AgeLine line = lines[i];
                Add(report.Total, line);
                Add(report.Buckets[BucketOf(bounds, line.Age)], line);
                if (line.Idle)
                {
                    Add(report.Idle, line);
                }
            }
            return report;
        }

        static void Add(AgeSum sum, AgeLine line)
        {
            sum.Items++;
            if (line.Amount.HasValue)
            {
                sum.Costed++;
                sum.Amount += line.Amount.Value;
            }
        }

        internal static decimal Dec(Dictionary<string, object> row, string name)
        {
            decimal value;
            decimal.TryParse(GlSql.Col(row, name), NumberStyles.Float, CultureInfo.InvariantCulture, out value);
            return value;
        }

        static DateTime? Day(string text)
        {
            DateTime parsed;
            if (text.Length == 10 && DateTime.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out parsed))
            {
                return parsed;
            }
            return null;
        }
    }
}
