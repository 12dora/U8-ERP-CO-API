namespace U8Co
{
    // 取消结账的检查（请求连接、结账事务里，只读的 SELECT）。返回拒绝原因，可以取消返回 null。表名、列名只来自这里的常量。
    internal static class PeriodReopenChecks
    {
        // 下一年度第 0 期总账标志为 1，而且下一年度已有凭证（年度结转或新年度已经开始做账）。
        const string NextYearSql = "SELECT TOP 1 'x' FROM GL_mend m WHERE m.iyear=? AND m.iperiod=0 AND ISNULL(m.bflag,0)=1"
            + " AND EXISTS (SELECT 1 FROM GL_accvouch v WHERE v.iyear=m.iyear)";

        public static string Reopen(object conn, int mod, int year, int period)
        {
            if (mod == PeriodModules.Gl)
            {
                return Gl(conn, year, period);
            }
            // 库存：最后一个已结账的月份可以取消，快照由 PeriodStock.Reopen 删；同期存货核算已结账时
            // 由 PeriodGate.Order 拒绝（U8 先结库存再结存货核算，取消反过来）。
            // 存货核算：有数据的月份由 PeriodIa 执行取消结账脚本，不到这里；这里只管没有数据、只改标志的月份。
            if (mod == PeriodModules.Ia)
            {
                return Stock(conn, year, period);
            }
            return null;
        }

        // 总账选项「反结账须输入账套主管口令」开着时不做。12 期：下一年度的期间已结账由顺序闸门挡住（LaterClosed）；
        // 下一年度都没结账、但第 0 期已置标志并且已有凭证（多半做过年度结转）时也不做，U8 里先处理下一年度。
        static string Gl(object conn, int year, int period)
        {
            if (PeriodCloseChecks.Option(conn, "GL", "bConselClos"))
            {
                return "反结账须输入账套主管口令，接口不支持；请在 U8 总账里取消结账";
            }
            if (period == 12 && PeriodCloseChecks.Has(conn, NextYearSql, year + 1))
            {
                return "下一年度已有凭证（可能已做年度结转），不能取消 12 期结账；请在 U8 里处理";
            }
            return null;
        }

        // 本月没有存货核算数据、只改标志的取消结账：该月有库存单据，或下月已有存货核算数据（IA_Summary / IA_Subsidiary）时不做——
        // 本月没有结账脚本写下的数据，取消结账脚本会把下月的数据当成结账结果删掉。
        // 不看 ST_MonthAccount：库存结账自己写这张快照（有结存的月份都有行），取消库存结账时自己删。
        static string Stock(object conn, int year, int period)
        {
            const string busy = "本月没有存货核算数据，但该月有出入库单据或下月已有存货核算数据，接口无法自动取消；"
                + "请在 U8 里取消存货核算结账";
            if (PeriodCloseChecks.Busy(conn, PeriodModules.Ia, year, period) != null)
            {
                return busy;
            }
            int[] next = PeriodCloseChecks.Shift(year, period, 1);
            if (PeriodCloseChecks.IaRows(conn, "IA_Summary", next[0], next[1])
                || PeriodCloseChecks.IaRows(conn, "IA_Subsidiary", next[0], next[1]))
            {
                return busy;
            }
            return null;
        }
    }
}
