using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 月末结账的单据检查（请求连接、结账事务里，都是只读的 SELECT）。期间按自然月算：[本月 1 日, 下月 1 日)。
    // 返回拒绝原因，可以结账返回 null。表名、列名只来自这里的常量，调用方的值只进参数。
    internal static class PeriodCloseChecks
    {
        const string Range = " >=CONVERT(date, ?, 23) AND {0}<CONVERT(date, ?, 23)";
        // 采购：U8 采购结账窗体查「日期在本月及以前、未复核的采购发票」（cVerifier 是采购复核人）。
        const string PuInvoiceSql = "SELECT TOP 1 'x' FROM PurBillVouch WHERE ISNULL(cVerifier,N'')=N''"
            + " AND dPBVDate<CONVERT(date, ?, 23)";
        // 库存单据：采购入库、其他入库、其他出库、产成品入库、材料出库、销售出库（表头 dDate）。
        static readonly string[] StockTables = new string[]
        {
            "RdRecord01", "RdRecord08", "RdRecord09", "rdrecord10", "rdrecord11", "rdrecord32"
        };
        const string IaRowsSql = "SELECT TOP 1 'x' FROM {0} WHERE iYear=? AND iMonth=?";
        // 在结账事务里加范围锁：检查之后、提交之前别的连接插不进本期未记账的凭证。
        const string UnpostedSql = "SELECT TOP 1 'x' FROM GL_accvouch WITH (UPDLOCK, HOLDLOCK)"
            + " WHERE iyear=? AND iperiod=? AND ISNULL(ibook,0)=0 AND (iflag IS NULL OR iflag<>1)";
        // 损益类末级科目本期借贷不平（未作废的凭证）。
        const string ProfitSql = "SELECT TOP 1 c.ccode FROM GL_accvouch v JOIN code c ON c.iyear=v.iyear AND c.ccode=v.ccode"
            + " WHERE v.iyear=? AND v.iperiod=? AND c.bend=1 AND c.cclass=N'损益' AND (v.iflag IS NULL OR v.iflag<>1)"
            + " GROUP BY c.ccode HAVING ROUND(SUM(ISNULL(v.md,0)) - SUM(ISNULL(v.mc,0)), 2)<>0";
        const string CarrySql = "SELECT TOP 1 'x' FROM GL_accvouch WHERE iyear=? AND iperiod=? AND coutsign=N'期间损益'"
            + " AND (iflag IS NULL OR iflag<>1)";
        // 应收、应付：本月审核登记的原始往来明细（cProcStyle=cVouchType）还没制单。
        const string UnvouchedSql = "SELECT TOP 1 'x' FROM {0} WHERE iPeriod=? AND YEAR(dRegDate)=?"
            + " AND cProcStyle=cVouchType AND ISNULL(cPZid,N'')=N''";
        const string OptionSql = "SELECT TOP 1 cValue FROM AccInformation WHERE cSysID=? AND cName=?";

        public static string Close(object conn, int mod, int year, int period)
        {
            string from = Day(year, period);
            string to = NextDay(year, period);
            switch (mod)
            {
                case 0:
                    return Has(conn, PuInvoiceSql, to) ? "有日期在本月及以前、还没复核的采购发票，请先复核" : null;
                case 1:
                    return Sale(conn, from, to);
                case 2:
                    return Stock(conn, year, period);
                case 3:
                    return Busy(conn, mod, year, period);
                case 4:
                case 5:
                    return Arap(conn, mod, year, period);
                default:
                    return Gl(conn, year, period);
            }
        }

        static string Sale(object conn, string from, string to)
        {
            if (Has(conn, Dated("SELECT TOP 1 'x' FROM DispatchList WHERE ISNULL(cVerifier,N'')=N'' AND dDate", "dDate"), from, to))
            {
                return "本月有未审核的发货单（退货单），请先审核";
            }
            if (Has(conn, Dated("SELECT TOP 1 'x' FROM SaleBillVouch WHERE ISNULL(cChecker,N'')=N'' AND dDate", "dDate"), from, to))
            {
                return "本月有未复核的销售发票，请先复核";
            }
            return null;
        }

        // 库存（真实月结，快照由 PeriodStock 写）：先挡接口不支持的库存选项，再照 U8 库存结账窗体查未审核的单据。
        // 上月库存已结账由顺序闸门查（PeriodGate.Close），同期采购、销售已结账由 PeriodGate.Order 查。
        // 不查的（见 docs/limitations.md）：货位结存差异、其他出库的预算超额、质检人设置、期初单据的日期下限。
        static string Stock(object conn, int year, int period)
        {
            string refusal = StockOptions(conn) ?? StockOpening(conn, year, period);
            if (refusal != null)
            {
                return refusal;
            }
            string from = Day(year, period);
            string to = NextDay(year, period);
            List<string> names = new List<string>();
            foreach (string[] spec in PeriodStockSql.Unaudited)
            {
                if (StockPending(conn, spec, from, to))
                {
                    names.Add(spec[0]);
                }
            }
            return names.Count == 0 ? null : UnauditedText(names);
        }

        static bool StockPending(object conn, string[] spec, string from, string to)
        {
            if (spec[1] == "m")
            {
                return Has(conn, spec[2], from, to);
            }
            return spec[1] == "u" ? Has(conn, spec[2], to) : Has(conn, spec[2]);
        }

        static string StockOptions(object conn)
        {
            string item = Rows.Scalar(conn, PeriodStockSql.DefineSetSql, new object[0]);
            if (item != null)
            {
                return DefineSetText(item);
            }
            if (Has(conn, PeriodStockSql.QualitySql))
            {
                return "账套有保质期管理的存货（Inventory.bInvQuality=1），U8 结账时按最新入库改写月结快照的保质期信息，"
                    + "接口暂不支持；请在 U8 里做库存月末结账";
            }
            return null;
        }

        // 库存期初的两种特殊情况，实测前先拒绝（PeriodStockSql.QcCrossMonthSql / QcBeforeFirstSql）。
        static string StockOpening(object conn, int year, int period)
        {
            string to = NextDay(year, period);
            if (Has(conn, PeriodStockSql.QcCrossMonthSql(), to, to))
            {
                return "有已审核的库存期初单据，审核日期与单据日期不在同一个月，U8 结账对这种期初另有算法，接口暂不支持；"
                    + "请在 U8 里做库存月末结账";
            }
            if (Has(conn, PeriodStockSql.QcBeforeFirstSql(), year * 100 + period))
            {
                return "首次库存结账，有已审核的库存期初单据日期早于账套第一个会计期间，U8 结账对这种期初另有算法，接口暂不支持；"
                    + "请在 U8 里做库存月末结账";
            }
            return null;
        }

        // 纯函数，--selftest 用。
        internal static string DefineSetText(string item)
        {
            string what = item.Trim().Equals("cdepcode", StringComparison.OrdinalIgnoreCase) ? "部门" : "收发类别、业务类型";
            return "库存选项「月结按" + what + "汇总」（ST_DefineSet " + item.Trim() + "）已启用，接口暂不支持；请在 U8 里做库存月末结账";
        }

        // 纯函数，--selftest 用。
        internal static string UnauditedText(List<string> names)
        {
            return "还有未审核的单据：" + string.Join("、", names.ToArray()) + "（U8 库存月末结账要求先审核），请先审核";
        }

        // 存货核算没有数据（IA_Subsidiary / IA_Summary 该月无行）的月份只改标志：该月有库存单据（还没记账）或有存货核算数据时
        // 返回拒绝原因。有数据的月份不到这里，由 PeriodIa 执行结账脚本。存货核算取消结账也用（PeriodReopenChecks）。
        internal static string Busy(object conn, int mod, int year, int period)
        {
            const string busy = "该月有出入库单据但存货核算还没有记账数据，请先正常单据记账（ia/post）、期末处理（ia/period_end）再结账";
            string from = Day(year, period);
            string to = NextDay(year, period);
            foreach (string table in StockTables)
            {
                if (Has(conn, Dated("SELECT TOP 1 'x' FROM " + table + " WHERE dDate", "dDate"), from, to))
                {
                    return busy;
                }
            }
            if (mod == 3 && (IaRows(conn, "IA_Subsidiary", year, period) || IaRows(conn, "IA_Summary", year, period)))
            {
                return busy;
            }
            return null;
        }

        // 应收 / 应付：{ cFlag, 简称, 往来单据的叫法, 未审核发票的查询（含日期列）, 发票的日期列, 发票的叫法, 往来明细表 }。
        static string[] ArapSpec(bool ap)
        {
            if (ap)
            {
                return new string[]
                {
                    "AP", "应付", "应付单或付款单", "SELECT TOP 1 'x' FROM PurBillVouch WHERE ISNULL(cPBVVerifier,N'')=N'' AND dPBVDate",
                    "dPBVDate", "采购发票", "Ap_Detail"
                };
            }
            return new string[]
            {
                "AR", "应收", "应收单或收款单", "SELECT TOP 1 'x' FROM SaleBillVouch WHERE ISNULL(cVerifier,N'')=N'' AND dDate",
                "dDate", "销售发票", "Ar_Detail"
            };
        }

        static string Arap(object conn, int mod, int year, int period)
        {
            string[] spec = ArapSpec(mod == 5);
            string refusal = ArapUnaudited(conn, spec, Day(year, period), NextDay(year, period));
            if (refusal != null)
            {
                return refusal;
            }
            // 选项 bZDMonth「月末结账前全部制单」。
            string unvouched = string.Format(CultureInfo.InvariantCulture, UnvouchedSql, spec[6]);
            if (Option(conn, spec[0], "bZDMonth") && Has(conn, unvouched, period, year))
            {
                return spec[1] + "选项要求月末结账前全部制单：本月还有未制单的单据";
            }
            return null;
        }

        static string ArapUnaudited(object conn, string[] spec, string from, string to)
        {
            string bills = Dated("SELECT TOP 1 'x' FROM Ap_Vouch WHERE cFlag=? AND ISNULL(cCheckMan,N'')=N'' AND dVouchDate",
                "dVouchDate");
            string receipts = Dated("SELECT TOP 1 'x' FROM Ap_CloseBill WHERE cFlag=? AND ISNULL(cCheckMan,N'')=N''"
                + " AND dVouchDate", "dVouchDate");
            if (Has(conn, bills, spec[0], from, to) || Has(conn, receipts, spec[0], from, to))
            {
                return "本月有未审核的" + spec[2] + "，请先审核";
            }
            if (Has(conn, Dated(spec[3], spec[4]), from, to))
            {
                return "本月有" + spec[1] + "未审核的" + spec[5] + "，请先审核";
            }
            return null;
        }

        static string Gl(object conn, int year, int period)
        {
            if (Has(conn, UnpostedSql, year, period))
            {
                return "本期还有未记账的凭证，请先记账";
            }
            if (Has(conn, ProfitSql, year, period) && !Has(conn, CarrySql, year, period))
            {
                return "请先做期间损益结转";
            }
            return null;
        }

        internal static bool IaRows(object conn, string table, int year, int period)
        {
            return Has(conn, string.Format(CultureInfo.InvariantCulture, IaRowsSql, table), year, period);
        }

        // AccInformation 的布尔选项；没有这一项按 false。
        internal static bool Option(object conn, string sys, string name)
        {
            string value = Rows.Scalar(conn, OptionSql, new object[] { sys, name });
            string text = value == null ? "" : value.Trim();
            return text == "1" || text.Equals("true", StringComparison.OrdinalIgnoreCase);
        }

        // head 以日期列结尾，补上 [from, to) 的范围（两个参数）。
        static string Dated(string head, string column)
        {
            return head + string.Format(CultureInfo.InvariantCulture, Range, column);
        }

        internal static bool Has(object conn, string sql, params object[] args)
        {
            return Rows.Scalar(conn, sql, args) != null;
        }

        internal static string Day(int year, int period)
        {
            return new DateTime(year, period, 1).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        }

        internal static string NextDay(int year, int period)
        {
            return new DateTime(year, period, 1).AddMonths(1).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        }

        // (year, period) 前后 months 个月的 { 年度, 期间 }。纯函数，--selftest 用。
        internal static int[] Shift(int year, int period, int months)
        {
            DateTime day = new DateTime(year, period, 1).AddMonths(months);
            return new int[] { day.Year, day.Month };
        }
    }
}
