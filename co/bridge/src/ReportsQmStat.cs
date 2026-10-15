using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 一个月里某指标的检验行数、数值行的个数与合计、不良行数。
    internal sealed class QmMonth
    {
        public int N;
        public int NumericN;
        public decimal Sum;
        public int Bad;
    }

    // 不良批次的一条记录。Seq 是读入顺序，同日同单号时保持稳定。
    internal sealed class QmBadBatch
    {
        public int Seq;
        public string Date = "";
        public string Code = "";
        public string Batch = "";
        public string Value = "";
    }

    // 一个（存货、指标）的检验行汇总。数值统计在 C# 里按行算（不依赖 SQL 的字符串转换）。
    internal sealed class QmGroup
    {
        public string InvCode = "";
        public string InvName = "";
        public string GuideCode = "";
        public string GuideName = "";
        public string GuideUnit = "";
        public int Lines;
        public int BadLines;
        public int Seq;
        public readonly List<decimal> Values = new List<decimal>();
        public readonly Dictionary<string, int> Standards = new Dictionary<string, int>(StringComparer.Ordinal);
        public readonly SortedDictionary<string, QmMonth> Months =
            new SortedDictionary<string, QmMonth>(StringComparer.Ordinal);
        public readonly List<QmBadBatch> Bad = new List<QmBadBatch>();
    }

    // 检验指标统计的纯逻辑：小数解析、不良判断、月份与标准值归并、均值与标准差。
    internal static class ReportsQmStat
    {
        // 数值的绝对值上限：更大的当作文本（条码、编号一类的串），也免得合计溢出。
        internal const decimal MaxAbs = 1000000000000m;
        internal const int BadBatchMax = 10;
        const NumberStyles Plain = NumberStyles.AllowLeadingSign | NumberStyles.AllowDecimalPoint;

        // 去掉首尾空白后能解析成小数（只收正负号、数字和小数点，不收千分位和指数）。
        internal static bool TryNumber(string text, out decimal value)
        {
            value = 0m;
            string t = text == null ? "" : text.Trim();
            if (t.Length == 0 || !decimal.TryParse(t, Plain, CultureInfo.InvariantCulture, out value))
            {
                return false;
            }
            return value <= MaxAbs && value >= -MaxAbs;
        }

        // 单项判定 CTARGETQJUG 含「不良」或「不合格」。
        internal static bool IsBad(string judge)
        {
            string t = judge == null ? "" : judge;
            return t.IndexOf("不良", StringComparison.Ordinal) >= 0 || t.IndexOf("不合格", StringComparison.Ordinal) >= 0;
        }

        // 日期文本（yyyy-MM-dd 或带时间）的前 10 位；不是日期时原样返回。
        internal static string DayOf(string date)
        {
            string t = date == null ? "" : date.Trim();
            return t.Length > 10 ? t.Substring(0, 10) : t;
        }

        internal static string MonthOf(string date)
        {
            string t = DayOf(date);
            return t.Length >= 7 ? t.Substring(0, 7) : t;
        }

        // 加一行检验明细：行数、标准值计数、数值、不良和月份。row 是检验行查询的一行（列名见 ReportsQmIndicatorsSql）。
        internal static void Add(QmGroup g, Dictionary<string, object> row)
        {
            string date = GlSql.Col(row, "dt");
            string value = GlSql.Col(row, "val");
            g.Lines++;
            QmMonth month = MonthOfGroup(g, MonthOf(date));
            month.N++;
            CountStandard(g, GlSql.Col(row, "std"));
            decimal number;
            if (TryNumber(value, out number))
            {
                g.Values.Add(number);
                month.NumericN++;
                month.Sum += number;
            }
            if (IsBad(GlSql.Col(row, "jug")))
            {
                g.BadLines++;
                month.Bad++;
                g.Bad.Add(NewBad(g.Seq++, row));
            }
        }

        static void CountStandard(QmGroup g, string standard)
        {
            string std = standard == null ? "" : standard.Trim();
            if (std.Length == 0)
            {
                return;
            }
            int count;
            g.Standards.TryGetValue(std, out count);
            g.Standards[std] = count + 1;
        }

        static QmBadBatch NewBad(int seq, Dictionary<string, object> row)
        {
            QmBadBatch bad = new QmBadBatch();
            bad.Seq = seq;
            bad.Date = DayOf(GlSql.Col(row, "dt"));
            bad.Code = GlSql.Col(row, "code");
            bad.Batch = GlSql.Col(row, "batch");
            bad.Value = GlSql.Col(row, "val").Trim();
            return bad;
        }

        static QmMonth MonthOfGroup(QmGroup g, string key)
        {
            QmMonth month;
            if (!g.Months.TryGetValue(key, out month))
            {
                month = new QmMonth();
                g.Months.Add(key, month);
            }
            return month;
        }

        // 出现次数最多的标准值；次数相同取字典序最小的，没有时返回 null。
        internal static string TopStandard(Dictionary<string, int> counts)
        {
            string best = null;
            int bestCount = 0;
            foreach (KeyValuePair<string, int> pair in counts)
            {
                bool better = pair.Value > bestCount
                    || (pair.Value == bestCount && best != null && string.CompareOrdinal(pair.Key, best) < 0);
                if (better)
                {
                    best = pair.Key;
                    bestCount = pair.Value;
                }
            }
            return best;
        }

        internal static decimal Mean(List<decimal> values)
        {
            decimal sum = 0m;
            for (int i = 0; i < values.Count; i++)
            {
                sum += values[i];
            }
            return values.Count == 0 ? 0m : sum / values.Count;
        }

        // 样本标准差（n−1）。少于 2 个数值时为 null。
        internal static object Stdev(List<decimal> values)
        {
            if (values.Count < 2)
            {
                return null;
            }
            decimal mean = Mean(values);
            double squares = 0;
            for (int i = 0; i < values.Count; i++)
            {
                double d = (double)(values[i] - mean);
                squares += d * d;
            }
            double sd = Math.Sqrt(squares / (values.Count - 1));
            return decimal.Round((decimal)sd, 6, MidpointRounding.AwayFromZero);
        }

        // 六位小数；没有数值时为 null。
        internal static object Round6(decimal value, int count)
        {
            return count == 0 ? null : (object)decimal.Round(value, 6, MidpointRounding.AwayFromZero);
        }

        internal static object Min(List<decimal> values)
        {
            return Extreme(values, true);
        }

        internal static object Max(List<decimal> values)
        {
            return Extreme(values, false);
        }

        static object Extreme(List<decimal> values, bool low)
        {
            if (values.Count == 0)
            {
                return null;
            }
            decimal best = values[0];
            for (int i = 1; i < values.Count; i++)
            {
                if (low ? values[i] < best : values[i] > best)
                {
                    best = values[i];
                }
            }
            return decimal.Round(best, 6, MidpointRounding.AwayFromZero);
        }

        // 最近的不良批次（日期、单号新的在前），最多 BadBatchMax 条。
        internal static List<QmBadBatch> NewestBad(List<QmBadBatch> bad)
        {
            List<QmBadBatch> sorted = new List<QmBadBatch>(bad);
            sorted.Sort(delegate(QmBadBatch x, QmBadBatch y)
            {
                int c = string.CompareOrdinal(y.Date, x.Date);
                if (c == 0)
                {
                    c = string.CompareOrdinal(y.Code, x.Code);
                }
                return c != 0 ? c : y.Seq.CompareTo(x.Seq);
            });
            if (sorted.Count > BadBatchMax)
            {
                sorted.RemoveRange(BadBatchMax, sorted.Count - BadBatchMax);
            }
            return sorted;
        }

        // 按存货编码、指标编码的字典序（ordinal）排序。
        internal static void SortGroups(List<QmGroup> groups)
        {
            groups.Sort(delegate(QmGroup x, QmGroup y)
            {
                int c = string.CompareOrdinal(x.InvCode, y.InvCode);
                return c != 0 ? c : string.CompareOrdinal(x.GuideCode, y.GuideCode);
            });
        }
    }
}
