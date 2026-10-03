using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 对方科目的键：存货、存货分类、往来单位、往来单位分类、销售 / 采购类型、地区、币种、税率。
    internal sealed class OppKeys
    {
        public string Inv = "";
        public string InvClass = "";
        public string Dw = "";
        public string DwClass = "";
        public string BizType = "";
        public string District = "";
        public string Currency = "";
        public decimal TaxRate;
    }

    // 对方科目（同 U8）：先查「对方科目设置」AP_OppCodeSet（cFlag = AR / AP、会计年度），
    // 行上填了的键都要与单据一致（分类、地区按编码前缀，存货分类 1002 对 100201），没填的键不限；
    // 取最具体的一行：先比第一个填了的键在「存货 → 存货分类 → 往来单位 → 往来单位分类 → 销售 / 采购类型 → 地区 → 币种」里的位置，
    // 再比分类编码长度（未覆盖：U8 组合键的优先级没能从元数据核对）。同样具体的几行给出不同科目 → 409，不去猜。
    // 没有匹配行再用「基本科目设置」Ap_InputCode（cNote_f：销售收入 xssrkm、应交增值税 xssjkm、采购 cgkm、采购税金 cgsjkm）。
    internal static class ArapVoucherAcct
    {
        public const string SaleIncome = "cSaIncomeCode";
        public const string SaleTax = "cYjzzsCode";
        public const string BuyCost = "cPuCode";
        public const string BuyTax = "cPuTaxCode";

        const string OppSql = "select isnull(cInvCode,N'') as inv, isnull(cInvCCode,N'') as invc, isnull(cCusVendCode,N'') as dw, "
            + "isnull(cCusVendCCode,N'') as dwc, isnull(cSaPuTypeCode,N'') as biz, isnull(cDistrictCode,N'') as dist, "
            + "isnull(cexch_name,N'') as cur, convert(varchar(40), iTax) as tax, {col} as v from AP_OppCodeSet "
            + "where cFlag=? and iyear=? and isnull({col},N'')<>N''";

        // 列名只来自上面四个常量；值只进参数。找不到返回 null。
        public static string Find(object conn, string flag, int year, string column, OppKeys keys)
        {
            string sql = OppSql.Replace("{col}", Column(column));
            List<Dictionary<string, object>> rows = Rows.Query(conn, sql, new object[] { flag, year }, 5001);
            int bestRank = int.MaxValue;
            int bestLen = -1;
            string best = null;
            bool clash = false;
            foreach (Dictionary<string, object> row in rows)
            {
                int len;
                int rank = Rank(row, keys, out len);
                if (rank < 0)
                {
                    continue;
                }
                string value = CoRows.Col(row, "v");
                if (rank < bestRank || (rank == bestRank && len > bestLen))
                {
                    bestRank = rank;
                    bestLen = len;
                    best = value;
                    clash = false;
                }
                else if (rank == bestRank && len == bestLen && !value.Equals(best, StringComparison.OrdinalIgnoreCase))
                {
                    clash = true;
                }
            }
            if (clash)
            {
                throw ArapVoucherDoc.Refuse("对方科目设置有多行同样匹配、科目不同（" + column + "），请在 U8 里调整后再制单");
            }
            return best ?? Basic(conn, flag, year, column, keys.Currency);
        }

        static string Column(string column)
        {
            if (column == SaleIncome || column == SaleTax || column == BuyCost || column == BuyTax)
            {
                return column;
            }
            throw new BridgeException(500, "internal", "对方科目列无效");
        }

        // 行不匹配返回 -1；匹配时返回第一个填了的键的位置（全空是 7），len 是该键的编码长度。
        static int Rank(Dictionary<string, object> row, OppKeys keys, out int len)
        {
            len = 0;
            string[] have = new string[]
            {
                CoRows.Col(row, "inv"), CoRows.Col(row, "invc"), CoRows.Col(row, "dw"), CoRows.Col(row, "dwc"),
                CoRows.Col(row, "biz"), CoRows.Col(row, "dist"), CoRows.Col(row, "cur")
            };
            string[] want = new string[] { keys.Inv, keys.InvClass, keys.Dw, keys.DwClass, keys.BizType, keys.District, keys.Currency };
            bool[] prefix = new bool[] { false, true, false, true, false, true, false };
            int rank = -1;
            for (int i = 0; i < have.Length; i++)
            {
                if (have[i].Length == 0)
                {
                    continue;
                }
                if (!Hit(have[i], want[i], prefix[i]))
                {
                    return -1;
                }
                if (rank < 0)
                {
                    rank = i;
                    len = have[i].Length;
                }
            }
            string tax = CoRows.Col(row, "tax");
            if (tax.Length > 0 && WriteoffSql.Num(tax) != keys.TaxRate)
            {
                return -1;
            }
            return rank < 0 ? have.Length : rank;
        }

        static bool Hit(string have, string want, bool prefix)
        {
            if (want.Length == 0)
            {
                return false;
            }
            if (prefix)
            {
                return want.StartsWith(have, StringComparison.OrdinalIgnoreCase);
            }
            return want.Equals(have, StringComparison.OrdinalIgnoreCase);
        }

        // 基本科目设置：应收 cFlag='R'（cArCode），应付 'P'（cApCode）；按会计年度，多行时取币种（cArCodeName / cApCodeName）一致的。
        static string Basic(object conn, string flag, int year, string column, string currency)
        {
            string note = BasicName(column);
            bool ar = flag == "AR";
            string sql = "select isnull(" + (ar ? "cArCode" : "cApCode") + ",N'') as v, isnull(" + (ar ? "cArCodeName" : "cApCodeName")
                + ",N'') as cur from Ap_InputCode where iyear=? and cFlag=? and cNote_f=?";
            List<Dictionary<string, object>> rows = Rows.Query(conn, sql, new object[] { year, ar ? "R" : "P", note }, 50);
            string only = null;
            int count = 0;
            foreach (Dictionary<string, object> row in rows)
            {
                string value = CoRows.Col(row, "v");
                if (value.Length == 0)
                {
                    continue;
                }
                if (CoRows.Col(row, "cur") == currency)
                {
                    return value;
                }
                only = value;
                count++;
            }
            return count == 1 ? only : null;
        }

        static string BasicName(string column)
        {
            switch (column)
            {
                case SaleIncome:
                    return "xssrkm";
                case SaleTax:
                    return "xssjkm";
                case BuyCost:
                    return "cgkm";
                default:
                    return "cgsjkm";
            }
        }

        // 缺科目时的 409 文案。
        public static string Missing(string column)
        {
            switch (column)
            {
                case SaleIncome:
                    return "销售发票制单：账套未设置销售收入科目（对方科目设置、基本科目设置都没有）";
                case SaleTax:
                    return "制单：账套未设置销项税金科目（对方科目设置、基本科目设置都没有）";
                case BuyCost:
                    return "采购发票制单暂不支持：账套未设置采购科目";
                default:
                    return "制单：账套未设置采购税金科目（对方科目设置、基本科目设置都没有）";
            }
        }

        // 往来单位的分类、地区（客户 / 供应商档案）。
        public static OppKeys Partner(object conn, string flag, string dw, string currency)
        {
            OppKeys keys = new OppKeys();
            keys.Dw = dw;
            keys.Currency = currency;
            string sql = flag == "AP"
                ? "select isnull(cVCCode,N'') as c, isnull(cDCCode,N'') as d from Vendor where cVenCode=?"
                : "select isnull(cCCCode,N'') as c, isnull(cDCCode,N'') as d from Customer where cCusCode=?";
            Dictionary<string, object> row = Rows.One(conn, sql, new object[] { dw });
            keys.DwClass = CoRows.Col(row, "c");
            keys.District = CoRows.Col(row, "d");
            return keys;
        }

        public static OppKeys Line(OppKeys partner, string inv, string invClass, decimal taxRate)
        {
            OppKeys keys = new OppKeys();
            keys.Inv = inv;
            keys.InvClass = invClass;
            keys.Dw = partner.Dw;
            keys.DwClass = partner.DwClass;
            keys.BizType = partner.BizType;
            keys.District = partner.District;
            keys.Currency = partner.Currency;
            keys.TaxRate = taxRate;
            return keys;
        }

        public static string Need(object conn, string flag, int year, string column, OppKeys keys)
        {
            string code = Find(conn, flag, year, column, keys);
            if (code == null || code.Length == 0)
            {
                throw ArapVoucherDoc.Refuse(Missing(column));
            }
            return code;
        }

        internal static string Text(decimal value)
        {
            return value.ToString("0.00", CultureInfo.InvariantCulture);
        }
    }
}
