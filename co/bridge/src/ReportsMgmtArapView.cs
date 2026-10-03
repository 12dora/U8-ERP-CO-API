using System;
using System.Collections.Generic;

namespace U8Co
{
    // 经营管理往来账期的输出：单位行、others、totals 同一套字段（ReportsMgmtArap.Partner → JSON）。
    // collection_days_avg / collection_days_median：近 12 个月已核销单据从单据日期到第一次核销的天数，按单据金额加权
    // （平均保留一位小数；中位数取累计金额首次达到一半的那个天数）；没有已核销单据时为 null。应付同义（付款天数）。
    internal static class ReportsMgmtArapView
    {
        // 分段描述：not_due（账龄 <= 0）、每段 from–to、最后一段 to 为 null（同 arap_aging）。
        internal static List<object> BucketInfo(int[] days)
        {
            List<object> list = new List<object>(days.Length + 2);
            list.Add(Bucket("not_due", null, 0));
            int from = 1;
            for (int i = 0; i < days.Length; i++)
            {
                list.Add(Bucket(ReportsMgmtArapSql.Idx(from) + "-" + ReportsMgmtArapSql.Idx(days[i]), from, days[i]));
                from = days[i] + 1;
            }
            list.Add(Bucket(ReportsMgmtArapSql.Idx(from) + "+", from, null));
            return list;
        }

        static Dictionary<string, object> Bucket(string key, object from, object to)
        {
            Dictionary<string, object> b = new Dictionary<string, object>();
            b["key"] = key;
            b["from"] = from;
            b["to"] = to;
            return b;
        }

        internal static bool Empty(ReportsMgmtArap.Partner p)
        {
            if (p.Balance != 0m || p.Prepaid != 0m || p.Notes != 0m || p.Count != 0)
            {
                return false;
            }
            return Array.TrueForAll(p.Aging, delegate(decimal v) { return v == 0m; });
        }

        internal static void Merge(ReportsMgmtArap.Partner into, ReportsMgmtArap.Partner p)
        {
            into.Balance += p.Balance;
            into.Prepaid += p.Prepaid;
            into.Overdue += p.Overdue;
            into.Notes += p.Notes;
            into.Count += p.Count;
            into.Amount += p.Amount;
            into.Unpaid += p.Unpaid;
            for (int b = 0; b < into.Aging.Length; b++)
            {
                into.Aging[b] += p.Aging[b];
            }
            foreach (KeyValuePair<int, decimal[]> t in p.Terms)
            {
                decimal[] slot;
                if (!into.Terms.TryGetValue(t.Key, out slot))
                {
                    slot = new decimal[2];
                    into.Terms.Add(t.Key, slot);
                }
                slot[0] += t.Value[0];
                slot[1] += t.Value[1];
            }
            foreach (KeyValuePair<int, decimal> d in p.Days)
            {
                decimal sum;
                into.Days.TryGetValue(d.Key, out sum);
                into.Days[d.Key] = sum + d.Value;
            }
        }

        internal static Dictionary<string, object> Summary(ReportsMgmtArap.Partner p, int count)
        {
            Dictionary<string, object> item = Item(p, false);
            item["partners"] = count;
            return item;
        }

        internal static Dictionary<string, object> Item(ReportsMgmtArap.Partner p, bool named)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            if (named)
            {
                item["code"] = p.Code;
                item["name"] = p.Name;
            }
            item["balance"] = p.Balance;
            item["prepaid"] = p.Prepaid;
            item["overdue"] = p.Overdue;
            item["aging"] = new List<object>(Array.ConvertAll<decimal, object>(p.Aging, delegate(decimal v) { return (object)v; }));
            item["open_notes"] = p.Notes;
            item["invoice_count"] = p.Count;
            item["invoice_amount"] = p.Amount;
            item["paid_amount"] = p.Amount - p.Unpaid;
            item["unpaid_amount"] = p.Unpaid;
            item["terms"] = Terms(p.Terms);
            item["collection_days_avg"] = Average(p.Days);
            item["collection_days_median"] = Median(p.Days);
            return item;
        }

        static List<object> Terms(SortedDictionary<int, decimal[]> terms)
        {
            List<object> list = new List<object>(terms.Count);
            foreach (KeyValuePair<int, decimal[]> t in terms)
            {
                Dictionary<string, object> item = new Dictionary<string, object>();
                item["days"] = t.Key;
                item["count"] = (int)t.Value[0];
                item["amount"] = t.Value[1];
                list.Add(item);
            }
            return list;
        }

        internal static object Average(SortedDictionary<int, decimal> days)
        {
            decimal weight = 0m;
            decimal sum = 0m;
            foreach (KeyValuePair<int, decimal> d in days)
            {
                weight += d.Value;
                sum += d.Key * d.Value;
            }
            if (weight <= 0m)
            {
                return null;
            }
            return decimal.Round(sum / weight, 1, MidpointRounding.AwayFromZero);
        }

        internal static object Median(SortedDictionary<int, decimal> days)
        {
            decimal weight = 0m;
            foreach (decimal amount in days.Values)
            {
                weight += amount;
            }
            if (weight <= 0m)
            {
                return null;
            }
            decimal seen = 0m;
            foreach (KeyValuePair<int, decimal> d in days)
            {
                seen += d.Value;
                if (seen * 2m >= weight)
                {
                    return d.Key;
                }
            }
            return null;
        }
    }
}
