using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 成本核算期间数据 ca_inputs：成本管理（CA）每个期间录入或取数得到的计算输入——工时、人工费用、制造费用、折旧费用、
    // 在产品约当系数、自定义分配项目、完工产品产量——和成本计算、结账状态。只读，不调用 U8 组件。
    // 每张表一个 tables.<名称>：count 是该期间的全部行数，rows 最多 limit 行；行里是解析好的成本中心、成本核算对象
    // （存货编码、生产订单号）、成本项目，以及 U8 表上的列原名 → 数值（values，空值不输出）。
    // 读哪张表要有对应录入窗体的功能权限：指定 tables 时缺一张就 403；没带 tables 时只读有权限的表，其余列在 skipped 里。
    // 成本核算对象按存货数据权限过滤。period 缺省：会计年度是登录年度时取登录月份，否则取 12。
    // 工时日报按该会计期间的起止日期取（UA_Period，读不到按自然月并给 warnings），在产品固定约当按年度取（与 period 无关）。
    internal static class ReportsCaInputs
    {
        public static ApiResult Run(WorkContext ctx, ReportArgs a)
        {
            CaInputsArgs q = ReportsCaParse.Inputs(ctx.Item.Body);
            int year = a.FiscalYear;
            int period = q.Period > 0 ? q.Period : ReportsAcctParse.DefaultPeriod(ctx, year);
            List<object> skipped = new List<object>();
            List<CaTable> tables = Permit(PermCheck.Of(ctx), ReportsCaInputsDef.Select(q.Tables), q.Tables.Length > 0, skipped);
            string[] days = null;
            string warning = null;
            if (tables.Exists(delegate(CaTable t) { return t.Time == CaTable.Date; }))
            {
                string source;
                days = ReportsMgmtSales.Bounds(ctx, year, period, period, out source, out warning);
            }
            Dictionary<string, object> data = new Dictionary<string, object>();
            for (int i = 0; i < tables.Count; i++)
            {
                data[tables[i].Name] = Read(ctx, tables[i], new int[] { year, period, q.Limit }, days);
            }
            Dictionary<string, object> body = Reports.Body();
            body["fiscal_year"] = year;
            body["period"] = period;
            body["status"] = Status(ctx, year, period);
            body["tables"] = data;
            body["skipped"] = skipped;
            ReportsMgmtSales.AddWarning(body, warning);
            return ApiResult.Ok(body);
        }

        // 每张要读的表都要有对应录入窗体的功能权限。指定了表（explicitTables）时缺一张就 403；没指定时只留有权限的表，
        // 其余记进 skipped（{ table, reason: "no_permission" }），一张都读不了仍是 403。纯函数（给定权限），--selftest 用。
        internal static List<CaTable> Permit(PermContext p, List<CaTable> tables, bool explicitTables, List<object> skipped)
        {
            List<CaTable> allowed = new List<CaTable>();
            for (int i = 0; i < tables.Count; i++)
            {
                if (p.HasAny(tables[i].Auths))
                {
                    allowed.Add(tables[i]);
                    continue;
                }
                if (explicitTables)
                {
                    throw new BridgeException(403, "no_permission", "没有" + tables[i].Title + "查询权限");
                }
                Dictionary<string, object> one = new Dictionary<string, object>();
                one["table"] = tables[i].Name;
                one["reason"] = "no_permission";
                skipped.Add(one);
            }
            if (allowed.Count == 0)
            {
                throw new BridgeException(403, "no_permission", "没有成本核算期间数据查询权限");
            }
            return allowed;
        }

        // at 是 { 会计年度, 期间, limit }；days 是该会计期间的起始日和次日（只有工时日报用）。
        static Dictionary<string, object> Read(WorkContext ctx, CaTable t, int[] at, string[] days)
        {
            StringBuilder sql = new StringBuilder();
            List<object> ps = new List<object>();
            ReportsCaInputsSql.Compose(t, sql, ps, at[0], at[1], days);
            if (t.HasObject)
            {
                // 数据权限：存货（成本核算对象解析不到存货的行不过滤）。
                PermHook.Where(sql, ps, ctx, "r");
            }
            sql.Append(" ORDER BY ").Append(t.Order);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql.ToString(), ps.ToArray(), at[2]);
            return Table(t, rows);
        }

        // 一张表的结果：count 取第一行的总行数（没有行就是 0），rows 是各行。纯函数，--selftest 用。
        internal static Dictionary<string, object> Table(CaTable t, List<Dictionary<string, object>> rows)
        {
            List<object> list = new List<object>();
            for (int i = 0; i < rows.Count; i++)
            {
                list.Add(Row(t, rows[i]));
            }
            Dictionary<string, object> result = new Dictionary<string, object>();
            result["count"] = rows.Count == 0 ? 0 : GlSql.Int(rows[0], "ca_n");
            result["rows"] = list;
            return result;
        }

        // 一行：时间、成本中心 / 成本核算对象 / 成本项目、表上的数值列（values）。纯函数，--selftest 用。
        internal static Dictionary<string, object> Row(CaTable t, Dictionary<string, object> row)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            Time(item, t, row);
            if (t.HasObject)
            {
                Put(item, t.IsBase ? "base_object_id" : "object_id", row.ContainsKey("cPPID") ? (object)GlSql.Int(row, "cPPID") : null);
                Put(item, "batch", Reports.Text(row, "cBatch"));
                Put(item, "cost_center", Reports.Text(row, "ca_cc"));
                Put(item, "inv_code", Reports.Text(row, "ca_inv"));
                Put(item, "inv_name", Reports.Text(row, "ca_inv_name"));
                Put(item, "mo_code", Reports.Text(row, "ca_mo"));
            }
            else
            {
                Put(item, "cost_center", Reports.Text(row, "cDeptID"));
            }
            Put(item, "cost_center_name", Reports.Text(row, "ca_cc_name"));
            if (t.Shape == CaTable.Item)
            {
                Put(item, "item_code", Reports.Text(row, "cAmoID"));
                Put(item, "item_name", Reports.Text(row, "ca_item_name"));
            }
            if (t.TypeCol != null)
            {
                Put(item, "type", Reports.Text(row, t.TypeCol));
            }
            item["values"] = Values(t, row);
            return item;
        }

        static void Time(Dictionary<string, object> item, CaTable t, Dictionary<string, object> row)
        {
            if (t.Time == CaTable.Date)
            {
                string day = GlSql.Col(row, "dDate");
                item["date"] = day.Length > 10 ? day.Substring(0, 10) : day;
            }
            else if (t.Time == CaTable.Year)
            {
                item["year"] = GlSql.Int(row, "iYear");
            }
            else
            {
                item["period"] = ReportsAcctCost.PeriodText(GlSql.Int(row, "iPeriod"));
            }
        }

        static void Put(Dictionary<string, object> item, string key, object value)
        {
            if (value != null)
            {
                item[key] = value;
            }
        }

        // 表上的数值列：登记了 Measures 的取这些列，没登记的取除键列、内部标识外的全部数值列；文本列非空才输出。
        static Dictionary<string, object> Values(CaTable t, Dictionary<string, object> row)
        {
            Dictionary<string, object> values = new Dictionary<string, object>();
            string[] names = t.Measures ?? OtherColumns(row);
            for (int i = 0; i < names.Length; i++)
            {
                if (row.ContainsKey(names[i]))
                {
                    values[names[i]] = Reports.Qty(row, names[i]);
                }
            }
            for (int i = 0; i < t.Texts.Length; i++)
            {
                Put(values, t.Texts[i], Reports.Text(row, t.Texts[i]));
            }
            return values;
        }

        // 键列、内部标识（autoid、期初标志）和解析出的列（ca_ 开头）以外、能解析成数的列。
        static string[] OtherColumns(Dictionary<string, object> row)
        {
            List<string> names = new List<string>();
            foreach (string key in row.Keys)
            {
                decimal ignore;
                if (!Skipped(key) && decimal.TryParse(GlSql.Col(row, key), NumberStyles.Float, CultureInfo.InvariantCulture, out ignore))
                {
                    names.Add(key);
                }
            }
            return names.ToArray();
        }

        static readonly string[] Internal = new string[] { "cPPID", "cBatch", "iPeriod", "iYear", "autoid", "bQC" };

        static bool Skipped(string key)
        {
            if (key.StartsWith("ca_", StringComparison.Ordinal))
            {
                return true;
            }
            for (int i = 0; i < Internal.Length; i++)
            {
                if (string.Equals(key, Internal[i], StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }

        // 成本核算状态：calculated_through 是最近一次成本计算到的期间（yyyy-MM，没算过为 null），
        // closed 是该会计期间成本核算是否已结账（GL_mend.bflag_CA，没有该期间的行时为 false）。
        static Dictionary<string, object> Status(WorkContext ctx, int year, int period)
        {
            Dictionary<string, object> status = new Dictionary<string, object>();
            status["calculated_through"] = CalculatedText(Rows.Scalar(ctx.Conn, ReportsCaInputsSql.Calculated, new object[0]));
            Dictionary<string, object> row = Rows.One(ctx.Conn, ReportsAcctCheckSql.ClosedSql, new object[] { year, period });
            status["closed"] = row != null && GlSql.Int(row, "CA") != 0;
            return status;
        }

        // CA_CBSys.iCalculate（YYYYMM）→ "YYYY-MM"；空或不是 YYYYMM 返回 null。纯函数，--selftest 用。
        internal static object CalculatedText(string raw)
        {
            int value;
            if (!int.TryParse(raw, NumberStyles.Integer, CultureInfo.InvariantCulture, out value) || value < 190001 || value % 100 < 1
                || value % 100 > 12)
            {
                return null;
            }
            return ReportsAcctCost.PeriodText(value);
        }
    }
}
