using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 客户信用 customer_credit：客户档案上的信用额度、信用期限、信用等级，加上 U8 信用余额表的各项占用
    // （ReportsCreditSql）。额度检查公式取销售选项 cCrCheckFunction（「1+1+1+…」第 1、3、5、7、9 位依次是
    // 订单、发货单、发票、应收账款余额、代垫费用单；第 11 位起是合同结算单和出口三项，本报表不算，打开时列在 unsupported）。
    // used 只合计公式里打开的项（应收余额还要应收系统已启用），available = credit_line − used，只对受控客户（bCredit）给出。
    // U8 按信用单位（cCusCreditCompany）合并占用；这里按客户逐个列出，并给出 credit_company。
    internal static class ReportsCredit
    {
        static readonly string[] Parts = new string[] { "order", "dispatch", "invoice", "ar", "expense" };
        static readonly string[] Columns = new string[] { "so_amt", "dl_amt", "bl_amt", "ar_amt", "ex_amt" };
        static readonly string[] Others = new string[] { "contract", "export_order", "export_consignment", "export_invoice" };

        public static ApiResult Credit(WorkContext ctx, ReportArgs a)
        {
            StockReportArgs s = ReportsStockReq.Parse(a, ctx.Item.Body);
            Dictionary<string, object> opt = Rows.One(ctx.Conn, ReportsCreditSql.Options, new object[0]);
            bool save = !string.Equals(GlSql.Col(opt, "point"), "false", StringComparison.OrdinalIgnoreCase);
            bool arOn = GlSql.Col(opt, "ar").Length > 0;
            bool[] formula = Formula(GlSql.Col(opt, "formula"));
            List<object> ps = new List<object>();
            ps.Add(save ? 1 : 0);
            ps.Add(a.Limit + 1);
            StringBuilder filter = new StringBuilder();
            if (s.Customers.Length > 0)
            {
                filter.Append(" AND c.cCusCode IN (").Append(Marks(s.Customers.Length)).Append(")");
                ps.AddRange(s.Customers);
            }
            if (s.ControlledOnly)
            {
                filter.Append(" AND ISNULL(c.bCredit, 0)=1");
            }
            string[] after = Reports.Uncursor(a.After, 1);
            if (after != null)
            {
                filter.Append(" AND c.cCusCode>?");
                ps.Add(after[0]);
            }
            // 数据权限：客户。
            PermHook.Where(filter, ps, ctx, "c");
            string sql = ReportsCreditSql.Main.Replace("{FILTER}", filter.ToString());
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql, ps.ToArray(), a.Limit + 1);
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count && i < a.Limit; i++)
            {
                items.Add(Item(rows[i], formula, arOn));
            }
            string next = rows.Count > a.Limit ? Reports.Cursor(GlSql.Col(rows[a.Limit - 1], "code")) : null;
            Dictionary<string, object> body = ReportsStockSql.Page(items, next);
            body["credit_control"] = GlSql.Bit(opt, "credit");
            body["check_point"] = save ? "save" : "verify";
            body["balance_table"] = GlSql.Bit(opt, "bal");
            body["ar_enabled"] = arOn;
            body["formula"] = Flags(formula);
            body["unsupported"] = Unsupported(formula);
            return ApiResult.Ok(body);
        }

        // U8 取第 1、3、5…17 位（SUBSTRING(@f, 2k+1, 1)），是「1」才算。缺省或位数不够时按不算。
        static bool[] Formula(string text)
        {
            bool[] flags = new bool[Parts.Length + Others.Length];
            for (int k = 0; k < flags.Length; k++)
            {
                int at = 2 * k;
                flags[k] = at < text.Length && text[at] == '1';
            }
            return flags;
        }

        static Dictionary<string, object> Flags(bool[] formula)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            for (int k = 0; k < Parts.Length; k++)
            {
                map[Parts[k]] = formula[k];
            }
            for (int k = 0; k < Others.Length; k++)
            {
                map[Others[k]] = formula[Parts.Length + k];
            }
            return map;
        }

        static List<object> Unsupported(bool[] formula)
        {
            List<object> list = new List<object>();
            for (int k = 0; k < Others.Length; k++)
            {
                if (formula[Parts.Length + k])
                {
                    list.Add(Others[k]);
                }
            }
            return list;
        }

        static Dictionary<string, object> Item(Dictionary<string, object> row, bool[] formula, bool arOn)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            bool controlled = GlSql.Bit(row, "ctl");
            decimal line = GlSql.Money(row, "line");
            item["code"] = GlSql.Col(row, "code");
            item["name"] = Reports.Text(row, "name");
            item["controlled"] = controlled;
            item["credit_line"] = line;
            item["credit_days"] = GlSql.Int(row, "days");
            item["credit_days_controlled"] = GlSql.Bit(row, "ctl_days");
            item["credit_grade"] = Reports.Text(row, "grade");
            item["credit_company"] = Reports.Text(row, "company");
            decimal used = 0m;
            for (int k = 0; k < Parts.Length; k++)
            {
                decimal amount = GlSql.Money(row, Columns[k]);
                item[Parts[k]] = amount;
                if (formula[k] && (Parts[k] != "ar" || arOn))
                {
                    used += amount;
                }
            }
            item["used"] = used;
            item["available"] = controlled ? (object)(line - used) : null;
            return item;
        }

        static string Marks(int count)
        {
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < count; i++)
            {
                sb.Append(i == 0 ? "?" : ", ?");
            }
            return sb.ToString();
        }
    }
}
