using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 固定资产只读报表（读线程，只用 ctx.Conn），表名列名都是常量，调用方的值只进 ? 参数。
    // fa_changes：变动单 fa_Vouchers（一张一行，按变动单号 sNum 翻页）及其部门明细 fa_Vouchers_Detail。U8 每做一次变动，
    // 卡片新增一个版本（fa_Cards.lOptID = 变动单的 lOptID，变动前版本是 lPreOptID）；asset_name 取变动后那个版本的资产名称。
    // 年度按变动日期 dTransdate 的年份（会计期间按自然月划分的账套，同 ArcFa 的未覆盖项），期间是 iTransPeriod。
    // fiscal_year 是固定资产的业务年度（同 fa_Depr*.iyear、YEAR(dTransdate)），不是账套库 UFDATA_<账套>_<年> 的年份：
    // 一个账套库里常有多个业务年度的固定资产数据，按库年份查会是空表；缺省取登录日期的年份（Reports.Handle）。
    // fa_depreciation：fa_DeprTransactions 一张卡片一年一行、十二个月各一列，这里按 fa_DeprList 已计提的期间展开成
    // 卡片 × 期间（按卡片编号、期间翻页）；没计提的期间不出现。amount 是当月折旧，accumulated 是当月末累计折旧
    // （fa_card 的 accumulated_depreciation 在已计提期间取的就是它），month_value 是月初原值。
    // 两张报表都没有 rowversion，不支持增量；没有固定资产的账套返回空列表。
    // 按部门数据权限过滤卡片（ReportsFaPerm，部门开关打开时）：越权卡片的行不出现，翻页游标照常。
    internal static class ReportsFa
    {
        const int DeptCap = 50;

        const string ChangesSql = "SELECT TOP (?) v.sNum AS code, v.sCardNum AS card_code, c.name AS asset_name,"
            + " v.lOptID AS opt_id, v.lPreOptID AS pre_opt_id, v.iVoucherType AS change_type, v.sVoucherName AS change_name,"
            + " v.sBeforeVoucher AS before_value, v.sAfterVoucher AS after_value, CONVERT(nvarchar(1000), v.memReason) AS reason,"
            + " CONVERT(varchar(10), v.dTransdate, 23) AS change_date, v.iTransPeriod AS period, v.sOperatorVou AS operator,"
            + " v.sCurrencyVou AS currency, v.dblExchangeRateVou AS exchange_rate, v.sSiteAfter AS site_after,"
            + " v.sKeeperAfter AS keeper_after, v.bAct AS effective, v.sZWVoucherType AS gl_sign, v.sZWVoucherNum AS gl_num"
            + " FROM fa_Vouchers v OUTER APPLY (SELECT TOP 1 x.sAssetName AS name FROM fa_Cards x"
            + " WHERE x.sCardNum = v.sCardNum AND x.lOptID = v.lOptID ORDER BY x.sCardID DESC) c"
            + " WHERE YEAR(v.dTransdate) = ?";
        const string DeptSql = "SELECT d.sNum AS code, d.sDeptNum AS dept, dep.cDepName AS dept_name,"
            + " d.sBeforeVoucher AS before_value, d.sAfterVoucher AS after_value FROM fa_Vouchers_Detail d"
            + " LEFT JOIN Department dep ON dep.cDepCode = d.sDeptNum WHERE d.sNum IN (";
        const string PostedSql = "SELECT DISTINCT iPeriod AS p FROM fa_DeprList WHERE iyear = ? ORDER BY iPeriod";

        static readonly string[] ChangeTexts = new string[]
        {
            "code", "card_code", "asset_name", "change_name", "before_value", "after_value", "reason", "change_date", "operator",
            "currency", "site_after", "keeper_after", "gl_sign", "gl_num"
        };
        static readonly string[] DeprTexts = new string[] { "asset_num", "asset_name", "depr_date" };
        static readonly string[] DeprMoney = new string[] { "amount", "accumulated", "month_value" };

        public static ApiResult Changes(WorkContext ctx, ReportArgs a)
        {
            FaReportArgs f = ReportsFaReq.Parse(a, ctx.Item.Body);
            List<object> ps = new List<object>();
            string sql = ChangesQuery(a, f, ps, ReportsFaPerm.Of(ctx));
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql, ps.ToArray(), a.Limit + 1);
            List<Dictionary<string, object>> page = rows.GetRange(0, Math.Min(rows.Count, a.Limit));
            Dictionary<string, List<object>> depts = Depts(ctx.Conn, page);
            List<object> items = new List<object>();
            for (int i = 0; i < page.Count; i++)
            {
                Dictionary<string, object> item = ChangeItem(page[i]);
                List<object> list;
                item["depts"] = depts.TryGetValue(GlSql.Col(page[i], "code"), out list) ? list : new List<object>();
                items.Add(item);
            }
            string next = rows.Count > a.Limit ? Reports.Cursor(GlSql.Col(rows[a.Limit - 1], "code")) : null;
            Dictionary<string, object> body = ReportsStockSql.Page(items, next);
            body["fiscal_year"] = a.FiscalYear;
            return ApiResult.Ok(body);
        }

        internal static string ChangesQuery(ReportArgs a, FaReportArgs f, List<object> ps)
        {
            return ChangesQuery(a, f, ps, null);
        }

        // ? 的顺序：TOP、年度、期间、卡片、单号、类型、游标、部门数据权限（p 为 null 时没有）。
        internal static string ChangesQuery(ReportArgs a, FaReportArgs f, List<object> ps, PermContext p)
        {
            ps.Add(a.Limit + 1);
            ps.Add(a.FiscalYear);
            StringBuilder sql = new StringBuilder(ChangesSql);
            AddIf(sql, ps, f.Period > 0, " AND v.iTransPeriod = ?", f.Period);
            AddIf(sql, ps, f.Card.Length > 0, " AND v.sCardNum = ?", f.Card);
            AddIf(sql, ps, f.Code.Length > 0, " AND v.sNum = ?", f.Code);
            AddIf(sql, ps, f.ChangeType > 0, " AND v.iVoucherType = ?", f.ChangeType);
            string[] after = Reports.Uncursor(a.After, 1);
            AddIf(sql, ps, after != null, " AND v.sNum > ?", after == null ? null : after[0]);
            ReportsFaPerm.AppendCard(sql, ps, p, "v.sCardNum");
            ReportsFaPerm.AppendVoucher(sql, ps, p, "v.sNum");
            sql.Append(" ORDER BY v.sNum");
            return sql.ToString();
        }

        public static ApiResult Depreciation(WorkContext ctx, ReportArgs a)
        {
            FaReportArgs f = ReportsFaReq.Parse(a, ctx.Item.Body);
            List<object> ps = new List<object>();
            string sql = DeprQuery(a, f, ps, ReportsFaPerm.Of(ctx));
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql, ps.ToArray(), a.Limit + 1);
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count && i < a.Limit; i++)
            {
                items.Add(DeprItem(rows[i]));
            }
            string next = null;
            if (rows.Count > a.Limit)
            {
                Dictionary<string, object> last = rows[a.Limit - 1];
                next = Reports.Cursor(GlSql.Col(last, "card_code"), GlSql.Col(last, "period"));
            }
            Dictionary<string, object> body = ReportsStockSql.Page(items, next);
            body["fiscal_year"] = a.FiscalYear;
            body["posted_periods"] = Posted(ctx.Conn, a.FiscalYear);
            return ApiResult.Ok(body);
        }

        internal static string DeprQuery(ReportArgs a, FaReportArgs f, List<object> ps)
        {
            return DeprQuery(a, f, ps, null);
        }

        // ? 的顺序：TOP、年度、期间、卡片、游标（卡片、卡片、期间）、部门数据权限（p 为 null 时没有）。
        // 期间列 CASE 里的列名来自常量 1–12，期间只进参数。
        internal static string DeprQuery(ReportArgs a, FaReportArgs f, List<object> ps, PermContext p)
        {
            ps.Add(a.Limit + 1);
            ps.Add(a.FiscalYear);
            StringBuilder sql = new StringBuilder("SELECT TOP (?) t.sCardNum AS card_code, t.sDeprAssetNum AS asset_num,");
            sql.Append(" n.name AS asset_name, l.iPeriod AS period, CONVERT(varchar(10), l.dDate, 23) AS depr_date, ");
            sql.Append(Money(Pick("t.dblDepr"))).Append(" AS amount, ").Append(Money(Pick("t.dblDeprT"))).Append(" AS accumulated, ");
            sql.Append("CONVERT(decimal(20,6), ").Append(Pick("t.dblDeprRate")).Append(") AS rate, ");
            sql.Append(Money(Pick("t.dblMonthValue"))).Append(" AS month_value, ");
            sql.Append(Pick("t.lDeprMonths")).Append(" AS depr_months, ").Append(Pick("t.lUsedMonths")).Append(" AS used_months");
            sql.Append(" FROM fa_DeprTransactions t");
            sql.Append(" JOIN (SELECT iyear, iPeriod, MAX(dDate) AS dDate FROM fa_DeprList GROUP BY iyear, iPeriod) l ON l.iyear = t.iyear");
            sql.Append(" OUTER APPLY (SELECT TOP 1 x.sAssetName AS name FROM fa_Cards x WHERE x.sCardNum = t.sCardNum");
            sql.Append(" ORDER BY x.sCardID DESC) n WHERE t.iyear = ?");
            // 只列卡片在该期间存在的行：本年录入的卡片从录入期间起（iInputPeriod，年初结转的是 0），年中减少的卡片
            // 减少之后的期间各列为 NULL，跳过。
            sql.Append(" AND ISNULL(t.iInputPeriod, 0) <= l.iPeriod AND (").Append(Pick("t.dblDeprT")).Append(" IS NOT NULL OR ")
                .Append(Pick("t.dblMonthValue")).Append(" IS NOT NULL)");
            AddIf(sql, ps, f.Period > 0, " AND l.iPeriod = ?", f.Period);
            AddIf(sql, ps, f.Card.Length > 0, " AND t.sCardNum = ?", f.Card);
            if (f.NonZero)
            {
                sql.Append(" AND ISNULL(").Append(Pick("t.dblDepr")).Append(", 0) <> 0");
            }
            string[] after = Reports.Uncursor(a.After, 2);
            if (after != null)
            {
                sql.Append(" AND (t.sCardNum > ? OR (t.sCardNum = ? AND l.iPeriod > ?))");
                ps.Add(after[0]);
                ps.Add(after[0]);
                ps.Add(CursorPeriod(after[1]));
            }
            ReportsFaPerm.AppendCard(sql, ps, p, "t.sCardNum");
            sql.Append(" ORDER BY t.sCardNum, l.iPeriod");
            return sql.ToString();
        }

        // CASE l.iPeriod WHEN 1 THEN <列>1 … WHEN 12 THEN <列>12 END。
        static string Pick(string prefix)
        {
            StringBuilder sb = new StringBuilder("CASE l.iPeriod");
            for (int i = 1; i <= 12; i++)
            {
                string n = i.ToString(CultureInfo.InvariantCulture);
                sb.Append(" WHEN ").Append(n).Append(" THEN ").Append(prefix).Append(n);
            }
            return sb.Append(" END").ToString();
        }

        static string Money(string expr)
        {
            return "CONVERT(decimal(20,2), ROUND(" + expr + ", 2))";
        }

        static int CursorPeriod(string text)
        {
            int period;
            if (!int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out period) || period < 1 || period > 12)
            {
                throw GlReq.Bad("after 游标无效");
            }
            return period;
        }

        static void AddIf(StringBuilder sql, List<object> ps, bool on, string clause, object value)
        {
            if (on)
            {
                sql.Append(clause);
                ps.Add(value);
            }
        }

        static List<object> Posted(object conn, int year)
        {
            List<object> periods = new List<object>();
            foreach (Dictionary<string, object> row in Rows.Query(conn, PostedSql, new object[] { year }, 12))
            {
                periods.Add(GlSql.Int(row, "p"));
            }
            return periods;
        }

        static Dictionary<string, List<object>> Depts(object conn, List<Dictionary<string, object>> page)
        {
            Dictionary<string, List<object>> map = new Dictionary<string, List<object>>(StringComparer.Ordinal);
            if (page.Count == 0)
            {
                return map;
            }
            StringBuilder sql = new StringBuilder(DeptSql);
            object[] args = new object[page.Count];
            for (int i = 0; i < page.Count; i++)
            {
                sql.Append(i == 0 ? "?" : ",?");
                args[i] = GlSql.Col(page[i], "code");
            }
            sql.Append(") ORDER BY d.sNum, d.sID");
            foreach (Dictionary<string, object> row in Rows.Query(conn, sql.ToString(), args, page.Count * DeptCap))
            {
                string code = GlSql.Col(row, "code");
                if (!map.ContainsKey(code))
                {
                    map[code] = new List<object>();
                }
                Dictionary<string, object> dept = new Dictionary<string, object>();
                dept["dept_code"] = Reports.Text(row, "dept");
                dept["dept_name"] = Reports.Text(row, "dept_name");
                dept["before_value"] = Reports.Text(row, "before_value");
                dept["after_value"] = Reports.Text(row, "after_value");
                map[code].Add(dept);
            }
            return map;
        }

        static Dictionary<string, object> ChangeItem(Dictionary<string, object> row)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            for (int i = 0; i < ChangeTexts.Length; i++)
            {
                item[ChangeTexts[i]] = Reports.Text(row, ChangeTexts[i]);
            }
            item["opt_id"] = OptInt(row, "opt_id");
            item["pre_opt_id"] = OptInt(row, "pre_opt_id");
            item["change_type"] = OptInt(row, "change_type");
            item["period"] = OptInt(row, "period");
            item["exchange_rate"] = GlSql.Num(row, "exchange_rate");
            item["effective"] = GlSql.Bit(row, "effective");
            return item;
        }

        static Dictionary<string, object> DeprItem(Dictionary<string, object> row)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["card_code"] = Reports.Text(row, "card_code");
            item["period"] = GlSql.Int(row, "period");
            for (int i = 0; i < DeprTexts.Length; i++)
            {
                item[DeprTexts[i]] = Reports.Text(row, DeprTexts[i]);
            }
            for (int i = 0; i < DeprMoney.Length; i++)
            {
                item[DeprMoney[i]] = GlSql.Col(row, DeprMoney[i]).Length == 0 ? null : (object)GlSql.Money(row, DeprMoney[i]);
            }
            item["rate"] = GlSql.Num(row, "rate");
            item["depr_months"] = OptInt(row, "depr_months");
            item["used_months"] = OptInt(row, "used_months");
            return item;
        }

        static object OptInt(Dictionary<string, object> row, string name)
        {
            int value;
            string text = GlSql.Col(row, name);
            if (text.Length == 0 || !int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out value))
            {
                return null;
            }
            return value;
        }
    }
}
