using System;
using System.Collections.Generic;

namespace U8Co
{
    // 月末结账检查 close_check 的一项检查：属于哪个模块、结果里的 item 键、SQL、SQL 里 ? 的取值顺序。
    // Args 每个字符一个参数：f 本月 1 日、n 下月 1 日、y 会计年度、p 期间（yyyy-MM-dd 文本）。
    internal sealed class AcctCheck
    {
        public readonly string Module;
        public readonly string Item;
        public readonly string Sql;
        public readonly string Args;

        public AcctCheck(string module, string item, string sql, string args)
        {
            Module = module;
            Item = item;
            Sql = sql;
            Args = args;
        }
    }

    // close_check 的 SQL：每项检查一条语句，返回（个数 n，最多 5 个示例单号 code）。
    // 条件同结账时的检查：采购、销售、总账、往来在 PeriodCloseSql，库存单据在 PeriodStockSql.Unaudited，
    // 存货核算未记账在 ReportsAcctIaSql。全是常量，调用方的值只进 ? 参数。
    internal static class ReportsAcctCheckSql
    {
        internal const int Samples = 5;
        const string StockHead = "SELECT TOP 1 'x' FROM ";

        // GL_mend 里各模块的结账标志（总账是 bflag，没有 bflag_GL）。参数：年度、期间。
        internal const string ClosedSql = "SELECT CONVERT(int, ISNULL(bflag_SA,0)) SA, CONVERT(int, ISNULL(bflag_PU,0)) PU,"
            + " CONVERT(int, ISNULL(bflag_ST,0)) ST, CONVERT(int, ISNULL(bflag_IA,0)) IA, CONVERT(int, ISNULL(bflag,0)) GL,"
            + " CONVERT(int, ISNULL(bflag_AR,0)) AR, CONVERT(int, ISNULL(bflag_AP,0)) AP, CONVERT(int, ISNULL(bflag_CA,0)) CA,"
            + " CONVERT(int, ISNULL(bflag_FA,0)) FA FROM GL_mend WHERE iyear=? AND iperiod=?";

        internal static readonly string[] Modules = new string[] { "SA", "PU", "ST", "IA", "GL", "AR", "AP", "CA", "FA" };

        // 结果里的检查项，按这个顺序列出。
        internal static readonly AcctCheck[] All = Build();

        static AcctCheck[] Build()
        {
            return new AcctCheck[]
            {
                new AcctCheck("SA", "unaudited_dispatch", Simple("cDLCode", PeriodCloseSql.DispatchFrom), "fn"),
                new AcctCheck("SA", "unaudited_sale_invoice", Simple("cSBVCode", PeriodCloseSql.SaleInvoiceFrom), "fn"),
                new AcctCheck("PU", "unaudited_purchase_invoice", Simple("cPBVCode", PeriodCloseSql.PuInvoiceFrom), "n"),
                Stock("unverified_purchase_in", "RdRecord01"),
                Stock("unverified_product_in", "rdrecord10"),
                Stock("unverified_material_out", "rdrecord11"),
                Stock("unverified_sale_out", "rdrecord32"),
                Stock("unverified_other_in", "RdRecord08"),
                Stock("unverified_other_out", "RdRecord09"),
                new AcctCheck("GL", "unposted_vouchers", Unposted(), "yp"),
                new AcctCheck("IA", "unbooked_ia", Unbooked(), "fnfnfnfnfnfn"),
                new AcctCheck("AR", "unvouchered_ar", Unvouched("Ar_Detail"), "py"),
                new AcctCheck("AP", "unvouchered_ap", Unvouched("Ap_Detail"), "py"),
                new AcctCheck("GL", "pl_not_transferred", ProfitOpen(), "ypyp")
            };
        }

        // 一张单据一行的表：个数和按单号排的前几个示例。fromWhere 是 FROM 之后的部分。
        static string Simple(string codeColumn, string fromWhere)
        {
            return "SELECT TOP (" + Samples + ") " + codeColumn + " code, COUNT(*) OVER() n FROM " + fromWhere
                + " ORDER BY " + codeColumn;
        }

        // 按单号分组后的个数和示例；inner 返回一列 code。
        static string Grouped(string inner)
        {
            return "SELECT TOP (" + Samples + ") q.code code, COUNT(*) OVER() n FROM (" + inner + ") q ORDER BY q.code";
        }

        // 库存单据：条件取自 PeriodStockSql.Unaudited（结账窗体逐条查的那份），表头 cCode 是单号，范围是本月。
        static AcctCheck Stock(string item, string table)
        {
            return new AcctCheck("ST", item, Simple("cCode", StockFrom(table)), "fn");
        }

        // PeriodStockSql.Unaudited 里该表的「FROM 之后」部分；找不到时抛出（自检会发现）。
        internal static string StockFrom(string table)
        {
            foreach (string[] spec in PeriodStockSql.Unaudited)
            {
                if (spec[1] == "m" && spec[2].StartsWith(StockHead + table + " WHERE", StringComparison.OrdinalIgnoreCase))
                {
                    return spec[2].Substring(StockHead.Length);
                }
            }
            throw new InvalidOperationException("PeriodStockSql.Unaudited 里没有 " + table);
        }

        // 未记账凭证按（类别字、凭证号）算一张，单号写成「记-0001」。
        static string Unposted()
        {
            return Grouped("SELECT csign + N'-' + RIGHT(N'0000' + CONVERT(nvarchar(10), ino_id), 4) code FROM GL_accvouch WHERE "
                + PeriodCloseSql.UnpostedWhere + " GROUP BY isignseq, csign, ino_id");
        }

        // 存货核算未记账的库存单据，按（单据类型、单号）算一张。
        static string Unbooked()
        {
            return Grouped("SELECT u.code code FROM (" + ReportsAcctIaSql.UnbookedUnion + ") u GROUP BY u.typ, u.code");
        }

        // 应收 / 应付本月审核登记、还没制单的原始往来明细，按单据号算一张。
        static string Unvouched(string table)
        {
            return Grouped("SELECT cCode code FROM " + table + " WHERE " + PeriodCloseSql.UnvouchedWhere + " GROUP BY cCode");
        }

        // 本期没有期间损益结转凭证，损益类末级科目的借贷还不平：示例是科目编码。
        static string ProfitOpen()
        {
            return Grouped("SELECT p.ccode code FROM (SELECT c.ccode FROM " + PeriodCloseSql.ProfitFrom
                + ") p WHERE NOT EXISTS (SELECT 1 FROM " + PeriodCloseSql.CarryFrom + ")");
        }

        // 按 Args 里的字符取参数值。
        internal static object[] ArgsOf(AcctCheck check, int year, int period)
        {
            List<object> args = new List<object>();
            for (int i = 0; i < check.Args.Length; i++)
            {
                args.Add(ArgOf(check.Args[i], year, period));
            }
            return args.ToArray();
        }

        static object ArgOf(char kind, int year, int period)
        {
            switch (kind)
            {
                case 'f':
                    return ReportsAcctParse.FirstDay(year, period);
                case 'n':
                    return ReportsAcctParse.NextDay(year, period);
                case 'y':
                    return year;
                default:
                    return period;
            }
        }
    }
}
