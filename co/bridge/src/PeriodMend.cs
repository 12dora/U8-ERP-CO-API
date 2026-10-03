using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // GL_mend 第 1 到 12 期的结账标志（各年度、各模块），以及月末结账的顺序闸门。全是纯函数，--selftest 用（PeriodCloseSelfTest）。
    // 行：{ 年度, 期间, 采购, 销售, 库存, 存货, 应收, 应付, 总账 }，标志 1 已结账、0 未结账（空值按未结账）。
    internal sealed class PeriodMend
    {
        readonly Dictionary<int, int[]> _flags = new Dictionary<int, int[]>();
        public int MinYear;
        public int MaxYear;

        public static PeriodMend From(List<int[]> rows)
        {
            PeriodMend m = new PeriodMend();
            foreach (int[] row in rows)
            {
                if (row.Length < 2 + PeriodModules.Count || row[1] < 1 || row[1] > 12)
                {
                    continue;
                }
                int[] flags = new int[PeriodModules.Count];
                for (int i = 0; i < flags.Length; i++)
                {
                    flags[i] = row[2 + i] == 1 ? 1 : 0;
                }
                m._flags[Key(row[0], row[1])] = flags;
                m.Span(row[0]);
            }
            return m;
        }

        void Span(int year)
        {
            if (MinYear == 0 || year < MinYear)
            {
                MinYear = year;
            }
            if (year > MaxYear)
            {
                MaxYear = year;
            }
        }

        static int Key(int year, int period)
        {
            return year * 100 + period;
        }

        public bool Has(int year, int period)
        {
            return _flags.ContainsKey(Key(year, period));
        }

        public bool Closed(int mod, int year, int period)
        {
            int[] flags;
            return _flags.TryGetValue(Key(year, period), out flags) && flags[mod] == 1;
        }

        public void Set(int mod, int year, int period, bool closed)
        {
            int[] flags;
            if (_flags.TryGetValue(Key(year, period), out flags))
            {
                flags[mod] = closed ? 1 : 0;
            }
        }

        // 该年度第一个未结账期间；全年已结账或没有这一年为 0。
        public int FirstOpen(int mod, int year)
        {
            for (int p = 1; p <= 12; p++)
            {
                if (Has(year, p) && !Closed(mod, year, p))
                {
                    return p;
                }
            }
            return 0;
        }

        // (year, period) 之后（同年更晚的期间、以后各年度）还有已结账的期间。
        public bool LaterClosed(int mod, int year, int period)
        {
            for (int y = year; y <= MaxYear; y++)
            {
                for (int p = y == year ? period + 1 : 1; p <= 12; p++)
                {
                    if (Closed(mod, y, p))
                    {
                        return true;
                    }
                }
            }
            return false;
        }

        // 从最早的年度到 (year, period)（含）为止未结账的期间 { 年度, 期间 }，按时间先后。
        public List<int[]> OpenUpTo(int mod, int year, int period)
        {
            List<int[]> list = new List<int[]>();
            for (int y = MinYear; y > 0 && y <= year; y++)
            {
                int last = y == year ? period : 12;
                for (int p = 1; p <= last; p++)
                {
                    if (Has(y, p) && !Closed(mod, y, p))
                    {
                        list.Add(new int[] { y, p });
                    }
                }
            }
            return list;
        }
    }

    // 月末结账的顺序闸门：返回拒绝原因（不含模块和期间的前缀），可以做时返回 null。
    internal static class PeriodGate
    {
        public static string Missing(PeriodMend m, int year, int period)
        {
            if (m.Has(year, period))
            {
                return null;
            }
            return "GL_mend 没有 " + N(year) + " 年第 " + N(period) + " 期，请在 U8 客户端处理";
        }

        // 结账：本期未结账；是该年度第一个未结账期间；1 月时上年度（GL_mend 有这一年的话）12 期已结账。
        public static string Close(PeriodMend m, int mod, int year, int period)
        {
            if (m.Closed(mod, year, period))
            {
                return "该期间已结账";
            }
            int first = m.FirstOpen(mod, year);
            if (first != period)
            {
                return "上一期间还没结账（" + N(year) + " 年第一个未结账期间是 " + N(first) + " 期）";
            }
            if (period == 1 && m.Has(year - 1, 12) && !m.Closed(mod, year - 1, 12))
            {
                return "上一期间还没结账（" + N(year - 1) + " 年 12 期未结账）";
            }
            return null;
        }

        // 取消结账：本期已结账，而且是最后一个已结账的期间（之后没有已结账的期间）。
        public static string Reopen(PeriodMend m, int mod, int year, int period)
        {
            if (!m.Closed(mod, year, period))
            {
                return "该期间还没结账";
            }
            if (m.LaterClosed(mod, year, period))
            {
                return "只能取消最后一个已结账的期间（" + N(year) + " 年 " + N(period) + " 期之后还有已结账的期间）";
            }
            return null;
        }

        // 同期其他模块：结账时前置模块（已启用的）必须已结账；取消结账时依赖它的模块（已启用的）必须未结账。
        public static string Order(PeriodMend m, int mod, int year, int period, bool[] started, bool close)
        {
            if (close)
            {
                foreach (int other in PeriodModules.Prereqs(mod))
                {
                    if (started[other] && !m.Closed(other, year, period))
                    {
                        return PeriodModules.Title(other) + "本期还没结账，请先结账（" + PeriodModules.OrderText + "）";
                    }
                }
                return null;
            }
            foreach (int other in PeriodModules.Dependents(mod))
            {
                if (started[other] && m.Closed(other, year, period))
                {
                    return PeriodModules.Title(other) + "本期已结账，不能取消" + PeriodModules.Title(mod) + "结账";
                }
            }
            return null;
        }

        // 拒绝原因的前缀：「总账 2026 年 9 期：」。
        public static string At(int mod, int year, int period)
        {
            return PeriodModules.Title(mod) + " " + N(year) + " 年 " + N(period) + " 期：";
        }

        internal static string N(int value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }
    }
}
