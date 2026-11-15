using System.Text;

namespace U8Co
{
    // 总账基础档案（币种、凭证类别）修改、删除前查引用。表名列名是常量，列名按 U8 表结构。
    // 引用表只列主要的档案、单据和设置，U8 自己可能还会拒绝更多，原文 409 带回。
    internal static class ArcGlRefs
    {
        // 币种（按名称 cexch_name 引用）：标签、表、列成组。
        static readonly string[] CurrencyRefs = new string[]
        {
            "客户档案", "Customer", "cCusExch_name", "供应商档案", "Vendor", "cVenExch_name", "汇率", "exch", "cexch_name",
            "会计科目", "code", "cexch_name", "本单位开户银行", "Bank", "cCurrencyName", "凭证", "GL_accvouch", "cexch_name",
            "销售订单", "SO_SOMain", "cexch_name", "采购订单", "PO_Pomain", "cexch_name", "发货单", "DispatchList", "cexch_name",
            "销售发票", "SaleBillVouch", "cexch_name", "采购发票", "PurBillVouch", "cexch_name",
            "收付款单", "Ap_CloseBill", "cexch_name", "应收应付单", "Ap_Vouch", "cexch_name", "往来明细", "Ap_Detail", "cexch_name",
            "到货单", "PU_ArrivalVouch", "cexch_name", "报价单", "SA_QuoMain", "cexch_name", "委外订单", "OM_MOMain", "cexch_name",
            "采购入库单", "RdRecord01", "cExch_Name"
        };

        // 凭证类别（按类别字 csign 引用），不分年度：任一年度的凭证用过都算。
        static readonly string[] SignRefs = new string[]
        {
            "凭证", "GL_accvouch", "csign", "凭证草稿", "GL_VouchDraft", "csign", "常用凭证", "GL_bfreq", "csign",
            "自动转账定义", "GL_bautotran", "csign", "出纳", "GL_CashTable", "csign", "应付明细", "Ap_Detail", "csign",
            "应收明细", "Ar_Detail", "csign", "固定资产凭证", "fa_ZWVouchers", "csign", "限制科目", "dsigns", "csign"
        };

        static readonly string CurrencySql = Compile(CurrencyRefs, null);
        // 删除时汇率随币种一起删（ArcCurrencySql），不算引用。
        static readonly string CurrencyDelSql = Compile(CurrencyRefs, "exch");
        static readonly string SignSql = Compile(SignRefs, null);

        // 引用该币种的第一类档案或单据的名称；没有引用返回 null。
        internal static string Currency(object conn, string name)
        {
            return Rows.Scalar(conn, CurrencySql, Same(name, CurrencyRefs.Length / 3));
        }

        // 不算汇率的引用（删除用）。
        internal static string CurrencyBesidesRates(object conn, string name)
        {
            return Rows.Scalar(conn, CurrencyDelSql, Same(name, CurrencyRefs.Length / 3 - 1));
        }

        internal static string Sign(object conn, string sign)
        {
            return Rows.Scalar(conn, SignSql, Same(sign, SignRefs.Length / 3));
        }

        static object[] Same(string code, int n)
        {
            object[] args = new object[n];
            for (int i = 0; i < n; i++)
            {
                args[i] = code;
            }
            return args;
        }

        // 给自检用：每条引用 SQL 的 ? 个数与参数个数一致。
        internal static bool ArgsMatch()
        {
            int n = CurrencyRefs.Length / 3;
            return Marks(CurrencySql) == n && Marks(CurrencyDelSql) == n - 1 && Marks(SignSql) == SignRefs.Length / 3;
        }

        static int Marks(string sql)
        {
            int marks = 0;
            foreach (char c in sql)
            {
                if (c == '?')
                {
                    marks++;
                }
            }
            return marks;
        }

        // SELECT TOP 1 u.x FROM (SELECT TOP 1 N'<标签>' AS x FROM <表> WHERE <列>=? UNION ALL …) u
        static string Compile(string[] r, string skipTable)
        {
            StringBuilder sb = new StringBuilder("SELECT TOP 1 u.x FROM (");
            bool first = true;
            for (int i = 0; i + 2 < r.Length; i += 3)
            {
                if (r[i + 1] == skipTable)
                {
                    continue;
                }
                if (!first)
                {
                    sb.Append(" UNION ALL ");
                }
                first = false;
                sb.Append("SELECT TOP 1 N'").Append(r[i]).Append("' AS x FROM ").Append(r[i + 1])
                    .Append(" WHERE ").Append(r[i + 2]).Append("=?");
            }
            return sb.Append(") u").ToString();
        }
    }
}
