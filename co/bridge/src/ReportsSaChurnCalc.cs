using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 客户变动里一个客户在两个窗口的汇总（SQL 按客户汇总好，分类在代码里做）。
    internal sealed class ChurnRow
    {
        public string Code = "";
        public string Name;
        public string Person;
        // 全部历史（到 date_to）里第一张、最后一张发票的日期。
        public string First;
        public string Last;
        public decimal RevCur;
        public decimal RevBase;
        // 窗口内的发票行数（0 表示窗口内没有发票）和有发票的自然月数。
        public int LinesCur;
        public int LinesBase;
        public int MonthsCur;
        public int MonthsBase;
        // 月均收入下降额（只用于 declining 排序）。
        public decimal Drop;
    }

    // 当前窗口 [CurFrom, CurTo] 和基线窗口 [BaseFrom, BaseTo]（date_from 之前 lookback_months 个月），及各自的月数。
    internal sealed class ChurnWindows
    {
        public DateTime CurFrom;
        public DateTime CurTo;
        public DateTime BaseFrom;
        public DateTime BaseTo;
        public decimal CurMonths;
        public decimal BaseMonths;
    }

    internal sealed class ChurnLists
    {
        public List<ChurnRow> New = new List<ChurnRow>();
        public List<ChurnRow> Lost = new List<ChurnRow>();
        public List<ChurnRow> Declining = new List<ChurnRow>();
        // 当前窗口里有发票的客户数（含新客户和下降客户）。
        public int Active;
    }

    // 客户变动的分类和窗口计算，全是纯函数（自检直接调用）。
    // new：当前窗口有发票且收入为正，date_from 之前从没有过发票；lost：基线窗口收入为正、当前窗口没有发票；
    // declining：两个窗口都有发票，基线收入为正，且当前月均收入 < 基线月均收入 × (1 − decline_pct / 100)。
    // 月均 = 窗口收入 ÷ 窗口月数；窗口恰好是整月（月初到月末）时月数是整数，否则按天数 ÷ 30.4375 折算。
    internal static class SaChurnCalc
    {
        const decimal DaysPerMonth = 30.4375m;

        internal static DateTime Day(string text)
        {
            return DateTime.ParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture);
        }

        internal static string Text(DateTime day)
        {
            return day.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        }

        internal static ChurnWindows Windows(string from, string to, int lookback)
        {
            ChurnWindows w = new ChurnWindows();
            w.CurFrom = Day(from);
            w.CurTo = Day(to);
            w.BaseFrom = w.CurFrom.AddMonths(-lookback);
            w.BaseTo = w.CurFrom.AddDays(-1);
            w.CurMonths = Months(w.CurFrom, w.CurTo);
            w.BaseMonths = Months(w.BaseFrom, w.BaseTo);
            return w;
        }

        // [from, to] 含两端的月数：正好是整月就取整数，否则按天数折算。
        internal static decimal Months(DateTime from, DateTime to)
        {
            DateTime end = to.AddDays(1);
            int whole = (end.Year - from.Year) * 12 + end.Month - from.Month;
            if (whole > 0 && from.AddMonths(whole) == end)
            {
                return whole;
            }
            int days = (end - from).Days;
            return days <= 0 ? 0m : decimal.Round(days / DaysPerMonth, 6, MidpointRounding.AwayFromZero);
        }

        internal static ChurnLists Classify(List<ChurnRow> rows, ChurnWindows w, string dateFrom, int pct, bool byCode = false)
        {
            ChurnLists lists = new ChurnLists();
            for (int i = 0; i < rows.Count; i++)
            {
                ChurnRow r = rows[i];
                if (r.LinesCur > 0)
                {
                    lists.Active++;
                }
                if (r.LinesCur > 0 && r.RevCur > 0m && string.CompareOrdinal(r.First, dateFrom) >= 0)
                {
                    lists.New.Add(r);
                }
                else if (r.LinesBase > 0 && r.LinesCur == 0 && r.RevBase > 0m)
                {
                    lists.Lost.Add(r);
                }
                else if (Declined(r, w, pct))
                {
                    lists.Declining.Add(r);
                }
            }
            Sort(lists, byCode);
            return lists;
        }

        static bool Declined(ChurnRow r, ChurnWindows w, int pct)
        {
            if (r.LinesCur == 0 || r.LinesBase == 0 || r.RevBase <= 0m || w.CurMonths <= 0m || w.BaseMonths <= 0m)
            {
                return false;
            }
            decimal baseAvg = r.RevBase / w.BaseMonths;
            decimal curAvg = r.RevCur / w.CurMonths;
            r.Drop = baseAvg - curAvg;
            return curAvg < baseAvg * (100 - pct) / 100m;
        }

        // 新客户按当前收入、流失客户按基线收入、下降客户按月均下降额，从大到小；同额按客户编码。
        // byCode：字段权限会遮收入时只按客户编码排，清单的先后（和 limit 的截断）不泄露收入高低。
        static void Sort(ChurnLists lists, bool byCode)
        {
            if (byCode)
            {
                lists.New.Sort(CompareCode);
                lists.Lost.Sort(CompareCode);
                lists.Declining.Sort(CompareCode);
                return;
            }
            lists.New.Sort(delegate(ChurnRow a, ChurnRow b) { return Compare(b.RevCur, a.RevCur, a, b); });
            lists.Lost.Sort(delegate(ChurnRow a, ChurnRow b) { return Compare(b.RevBase, a.RevBase, a, b); });
            lists.Declining.Sort(delegate(ChurnRow a, ChurnRow b) { return Compare(b.Drop, a.Drop, a, b); });
        }

        static int CompareCode(ChurnRow a, ChurnRow b)
        {
            return string.CompareOrdinal(a.Code, b.Code);
        }

        static int Compare(decimal left, decimal right, ChurnRow a, ChurnRow b)
        {
            int c = left.CompareTo(right);
            return c != 0 ? c : string.CompareOrdinal(a.Code, b.Code);
        }

        internal static List<object> Items(List<ChurnRow> rows, int limit)
        {
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count && i < limit; i++)
            {
                items.Add(Item(rows[i]));
            }
            return items;
        }

        static Dictionary<string, object> Item(ChurnRow r)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["customer_code"] = r.Code;
            item["customer_name"] = r.Name;
            item["person_name"] = r.Person;
            item["first_date"] = r.First;
            item["last_date"] = r.Last;
            item["revenue_current"] = SaCalc.Money(r.RevCur);
            item["revenue_baseline"] = SaCalc.Money(r.RevBase);
            item["months_current"] = r.MonthsCur;
            item["months_baseline"] = r.MonthsBase;
            return item;
        }

        internal static Dictionary<string, object> Counts(ChurnLists lists)
        {
            Dictionary<string, object> counts = new Dictionary<string, object>();
            counts["new"] = lists.New.Count;
            counts["lost"] = lists.Lost.Count;
            counts["declining"] = lists.Declining.Count;
            counts["active"] = lists.Active;
            return counts;
        }

        internal static Dictionary<string, object> WindowsBody(ChurnWindows w)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            map["current_from"] = Text(w.CurFrom);
            map["current_to"] = Text(w.CurTo);
            map["baseline_from"] = Text(w.BaseFrom);
            map["baseline_to"] = Text(w.BaseTo);
            return map;
        }
    }
}
