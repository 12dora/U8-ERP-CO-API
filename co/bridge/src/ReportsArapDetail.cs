using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 往来明细账 arap_detail：Ar_Detail / Ap_Detail（与 arap_balance 同源，iFlag<3，不含应收票据、现金），
    // 按往来单位列出 date_from 的期初、区间内逐张单据的借贷和滚动余额。余额方向同 arap_balance：应收借减贷，应付贷减借。
    // 一行是同一天、同一单据、同一处理方式（cProcStyle）、同一对方单据的明细合计（发票的多行合成一行）。
    // 核销行（9P）：期初、区间借贷、期末和滚动余额一律含核销行（与 arap_balance 对得上）；include_writeoff 只决定是否列出这些行，
    // 缺省不列。不列时本页的滚动余额仍按全部行（含核销行）逐行累计：先按不含核销行的条件取一页，再把游标到本页最后一行之间的
    // 全部行（含核销行）按同一顺序取出来累计，只输出非核销行。区间内核销行多于 HiddenMax 时 400，请缩小范围或列出核销行。
    // 表名、列名只来自下面的固定表；编码、科目前缀、日期只进参数。
    internal static class ReportsArapDetail
    {
        // side → { 明细表, 名称表, 编码列, 名称列, 明细行余额表达式, 分组行余额表达式 }。
        static readonly Dictionary<string, string[]> Sides = BuildSides();
        // basis → { 口径日期列, 另一个日期列 }。
        static readonly Dictionary<string, string[]> Dates = BuildDates();
        // 单据类型名称（cVouchType）；表外的类型 doc_type_name 为 null。
        static readonly Dictionary<string, string> ArTypes = BuildTypes("ar");
        static readonly Dictionary<string, string> ApTypes = BuildTypes("ap");

        const int HiddenMax = 20000;
        const string WriteoffFilter = " AND ISNULL(d.cProcStyle, N'')<>N'9P'";

        // 往来单位汇总：期初（date_from 之前）、区间借方、区间贷方。绑定顺序：from, from, from, to, {FILTER}。
        const string SumSql = "SELECT t.cDwCode partner, n.{NAME} pname, CONVERT(decimal(18,2), t.ob) ob,"
            + " CONVERT(decimal(18,2), t.pd) pd, CONVERT(decimal(18,2), t.pc) pc"
            + " FROM (SELECT d.cDwCode, SUM(CASE WHEN d.{DATE}<CONVERT(date, ?, 23) THEN {SIGNED} ELSE 0 END) ob,"
            + " SUM(CASE WHEN d.{DATE}>=CONVERT(date, ?, 23) THEN ISNULL(d.iDAmount,0) ELSE 0 END) pd,"
            + " SUM(CASE WHEN d.{DATE}>=CONVERT(date, ?, 23) THEN ISNULL(d.iCAmount,0) ELSE 0 END) pc"
            + " FROM {TABLE} d WHERE d.iFlag<3 AND d.{DATE}<DATEADD(day, 1, CONVERT(date, ?, 23)){FILTER}"
            + " GROUP BY d.cDwCode) t LEFT JOIN {NTABLE} n ON n.{CODE}=t.cDwCode ORDER BY t.cDwCode";

        // 区间内按单据合并的行。绑定顺序：from, to, {FILTER}。fid 是组内最小 Auto_ID，与 dt 一起定行序和游标。
        const string GroupSql = "SELECT d.cDwCode, CONVERT(char(10), d.{DATE}, 23) dt, MIN(d.Auto_ID) fid,"
            + " d.cVouchType vt, d.cVouchID vid, ISNULL(d.cProcStyle, N'') ps, ISNULL(d.cCoVouchType, N'') cvt,"
            + " ISNULL(d.cCoVouchID, N'') cvid, CONVERT(char(10), MIN(d.{ODATE}), 23) odt,"
            + " SUM(ISNULL(d.iDAmount,0)) dm, SUM(ISNULL(d.iCAmount,0)) cm, MAX(d.cDigest) digest,"
            + " MAX(d.cDeptCode) dept, MAX(d.cPerson) psn, CASE WHEN MIN(d.cCode)=MAX(d.cCode) THEN MIN(d.cCode) END acc"
            + " FROM {TABLE} d WHERE d.iFlag<3 AND d.{DATE}>=CONVERT(date, ?, 23)"
            + " AND d.{DATE}<DATEADD(day, 1, CONVERT(date, ?, 23)){FILTER}"
            + " GROUP BY d.cDwCode, CONVERT(char(10), d.{DATE}, 23), d.cVouchType, d.cVouchID, ISNULL(d.cProcStyle, N''),"
            + " ISNULL(d.cCoVouchType, N''), ISNULL(d.cCoVouchID, N'')";

        const string PageSql = "SELECT TOP (?) g.cDwCode partner, g.dt, g.fid, g.vt, g.vid, g.ps, g.cvt, g.cvid, g.odt,"
            + " CONVERT(decimal(18,2), g.dm) dm, CONVERT(decimal(18,2), g.cm) cm, g.digest, g.dept, g.psn, g.acc"
            + " FROM ({GROUP}) g WHERE 1=1";

        // 游标所在往来单位在本页之前的区间发生额（含游标那一行）。
        const string CarrySql = "SELECT CONVERT(decimal(18,2), ISNULL(SUM({GSIGNED}),0)) carry FROM ({GROUP}) g"
            + " WHERE g.cDwCode=? AND (g.dt<? OR (g.dt=? AND g.fid<=?))";

        static Dictionary<string, string[]> BuildSides()
        {
            Dictionary<string, string[]> map = new Dictionary<string, string[]>(StringComparer.Ordinal);
            map.Add("ar", new string[]
            {
                "Ar_Detail", "Customer", "cCusCode", "cCusName", "ISNULL(d.iDAmount,0)-ISNULL(d.iCAmount,0)", "g.dm-g.cm"
            });
            map.Add("ap", new string[]
            {
                "Ap_Detail", "Vendor", "cVenCode", "cVenName", "ISNULL(d.iCAmount,0)-ISNULL(d.iDAmount,0)", "g.cm-g.dm"
            });
            return map;
        }

        static Dictionary<string, string[]> BuildDates()
        {
            Dictionary<string, string[]> map = new Dictionary<string, string[]>(StringComparer.Ordinal);
            map.Add("register", new string[] { "dRegDate", "dVouchDate" });
            map.Add("document", new string[] { "dVouchDate", "dRegDate" });
            return map;
        }

        // 常见类型：应收 26/27 销售发票、R0 应收单、48 收款单、49 付款单（退款）；
        // 应付 01/02 采购发票、P0 应付单、49 付款单、48 收款单（退款）。
        static Dictionary<string, string> BuildTypes(string side)
        {
            Dictionary<string, string> map = new Dictionary<string, string>(StringComparer.Ordinal);
            map.Add("48", "收款单");
            map.Add("49", "付款单");
            if (side == "ar")
            {
                map.Add("26", "销售专用发票");
                map.Add("27", "销售普通发票");
                map.Add("R0", "应收单");
                return map;
            }
            map.Add("01", "采购专用发票");
            map.Add("02", "采购普通发票");
            map.Add("P0", "应付单");
            return map;
        }

        public static ApiResult Detail(WorkContext ctx, ReportArgs a)
        {
            DetailArgs d = ReportsDetailReq.Parse(a.Name, ctx.Item.Body);
            if (d.DateTo.Length == 0)
            {
                d.DateTo = (ctx.Item.Date ?? "").Trim();
                ReportsDetailReq.CheckOrder(d.DateFrom, d.DateTo);
            }
            string[] after = Reports.Uncursor(a.After, 3);
            List<Dictionary<string, object>> sums = Summary(ctx, d);
            List<Dictionary<string, object>> rows = Page(ctx, d, after, !d.Writeoff);
            Dictionary<string, decimal> opening = new Dictionary<string, decimal>(StringComparer.Ordinal);
            List<object> partners = new List<object>(sums.Count);
            for (int i = 0; i < sums.Count; i++)
            {
                partners.Add(PartnerItem(sums[i], d.Side, opening));
            }
            decimal carry = after == null ? 0m : Carry(ctx, d, after);
            List<Dictionary<string, object>> walk = d.Writeoff ? rows : Walk(ctx, d, after, rows);
            List<object> items = Items(walk, d, opening, after == null ? null : after[0], carry);
            Dictionary<string, object> body = Reports.Body();
            body["side"] = d.Side;
            body["basis"] = d.Basis;
            body["date_from"] = d.DateFrom;
            body["date_to"] = d.DateTo;
            body["partners"] = partners;
            body["items"] = items;
            body["next"] = rows.Count > d.Limit ? Next(rows[d.Limit - 1]) : null;
            return ApiResult.Ok(body);
        }

        static string Fill(string sql, DetailArgs d)
        {
            string[] side = Sides[d.Side];
            string[] dates = Dates[d.Basis];
            return sql.Replace("{TABLE}", side[0]).Replace("{NTABLE}", side[1]).Replace("{CODE}", side[2])
                .Replace("{NAME}", side[3]).Replace("{SIGNED}", side[4]).Replace("{GSIGNED}", side[5])
                .Replace("{DATE}", dates[0]).Replace("{ODATE}", dates[1]);
        }

        // 明细条件：往来单位、科目前缀（含 / 排除）、部门、业务员、数据权限；hide 为 true 时去掉核销行（只用于取一页要输出的行）。
        // 每条查询各拼一次（参数各自追加）。
        static string Filter(WorkContext ctx, DetailArgs d, List<object> args, bool hide)
        {
            StringBuilder sql = new StringBuilder(" AND d.cDwCode IN (");
            for (int i = 0; i < d.Partners.Length; i++)
            {
                sql.Append(i == 0 ? "?" : ", ?");
                args.Add(d.Partners[i]);
            }
            sql.Append(")");
            Reports.LikeAny(sql, args, "d.cCode", d.Accounts, false);
            Reports.LikeAny(sql, args, "d.cCode", d.Excludes, true);
            if (d.Dept.Length > 0)
            {
                sql.Append(" AND d.cDeptCode=?");
                args.Add(d.Dept);
            }
            if (d.Person.Length > 0)
            {
                sql.Append(" AND d.cPerson=?");
                args.Add(d.Person);
            }
            if (hide)
            {
                sql.Append(WriteoffFilter);
            }
            // 数据权限：客户或供应商，同 arap_balance。
            PermHook.Where(sql, args, ctx, "d");
            return sql.ToString();
        }

        static List<Dictionary<string, object>> Summary(WorkContext ctx, DetailArgs d)
        {
            List<object> args = new List<object>(new object[] { d.DateFrom, d.DateFrom, d.DateFrom, d.DateTo });
            string sql = Fill(SumSql, d).Replace("{FILTER}", Filter(ctx, d, args, false));
            return Rows.Query(ctx.Conn, sql, args.ToArray(), d.Partners.Length);
        }

        static string Group(WorkContext ctx, DetailArgs d, List<object> args, bool hide)
        {
            args.Add(d.DateFrom);
            args.Add(d.DateTo);
            return Fill(GroupSql, d).Replace("{FILTER}", Filter(ctx, d, args, hide));
        }

        static readonly string[] KeyCols = new string[] { "g.cDwCode", "g.dt", "g.fid" };

        static object[] KeyVals(string partner, string dt, string fid)
        {
            return new object[] { partner, dt, ReportsDetailReq.CursorInt(fid) };
        }

        static List<Dictionary<string, object>> Page(WorkContext ctx, DetailArgs d, string[] after, bool hide)
        {
            List<object> args = new List<object>();
            args.Add(d.Limit + 1);
            StringBuilder sql = new StringBuilder(PageSql.Replace("{GROUP}", Group(ctx, d, args, hide)));
            if (after != null)
            {
                sql.Append(" AND ").Append(ReportsGlDetail.Greater(KeyCols, KeyVals(after[0], after[1], after[2]), args));
            }
            sql.Append(" ORDER BY g.cDwCode, g.dt, g.fid");
            return Rows.Query(ctx.Conn, sql.ToString(), args.ToArray(), d.Limit + 1);
        }

        // 不列核销行时：游标之后、到本页最后一行（含）为止的全部行（含核销行），同一顺序，供滚动余额逐行累计。
        static List<Dictionary<string, object>> Walk(WorkContext ctx, DetailArgs d, string[] after,
            List<Dictionary<string, object>> rows)
        {
            if (rows.Count == 0)
            {
                return rows;
            }
            Dictionary<string, object> last = rows[Math.Min(rows.Count, d.Limit) - 1];
            List<object> args = new List<object>();
            args.Add(HiddenMax + d.Limit + 1);
            StringBuilder sql = new StringBuilder(PageSql.Replace("{GROUP}", Group(ctx, d, args, false)));
            if (after != null)
            {
                sql.Append(" AND ").Append(ReportsGlDetail.Greater(KeyCols, KeyVals(after[0], after[1], after[2]), args));
            }
            object[] end = KeyVals(GlSql.Col(last, "partner"), GlSql.Col(last, "dt"), GlSql.Col(last, "fid"));
            sql.Append(" AND NOT ").Append(ReportsGlDetail.Greater(KeyCols, end, args));
            sql.Append(" ORDER BY g.cDwCode, g.dt, g.fid");
            List<Dictionary<string, object>> all = Rows.Query(ctx.Conn, sql.ToString(), args.ToArray(), HiddenMax + d.Limit + 1);
            if (all.Count > HiddenMax + d.Limit)
            {
                throw new BridgeException(400, "bad_request", "区间内核销行过多，请缩小日期或往来单位范围，或设 include_writeoff=true");
            }
            return all;
        }

        static decimal Carry(WorkContext ctx, DetailArgs d, string[] after)
        {
            List<object> args = new List<object>();
            string sql = Fill(CarrySql, d).Replace("{GROUP}", Group(ctx, d, args, false));
            args.Add(after[0]);
            args.Add(after[1]);
            args.Add(after[1]);
            args.Add(ReportsDetailReq.CursorInt(after[2]));
            Dictionary<string, object> row = Rows.One(ctx.Conn, sql, args.ToArray());
            return GlSql.Money(row, "carry");
        }

        static Dictionary<string, object> PartnerItem(Dictionary<string, object> row, string side, Dictionary<string, decimal> opening)
        {
            Dictionary<string, object> item = ReportsArap.Partner(row);
            decimal open = GlSql.Money(row, "ob");
            decimal debit = GlSql.Money(row, "pd");
            decimal credit = GlSql.Money(row, "pc");
            opening[GlSql.Col(row, "partner")] = open;
            item["opening"] = open;
            item["debit"] = debit;
            item["credit"] = credit;
            item["closing"] = open + (side == "ar" ? debit - credit : credit - debit);
            return item;
        }

        // 滚动余额：每个往来单位从期初起算；游标所在单位另加本页之前的区间发生额（含核销行）。
        // rows 是 Page（列核销行时）或 Walk（不列时，含核销行、正好截到本页最后一行）的结果；不列时核销行只累计、不输出。
        static List<object> Items(List<Dictionary<string, object>> rows, DetailArgs d, Dictionary<string, decimal> opening,
            string carryPartner, decimal carry)
        {
            List<object> items = new List<object>();
            string current = null;
            decimal balance = 0m;
            int max = d.Writeoff ? d.Limit : rows.Count;
            for (int i = 0; i < max && i < rows.Count; i++)
            {
                string partner = GlSql.Col(rows[i], "partner");
                if (partner != current)
                {
                    current = partner;
                    decimal open;
                    opening.TryGetValue(partner, out open);
                    balance = open + (partner == carryPartner ? carry : 0m);
                }
                Dictionary<string, object> item = Row(rows[i], d);
                decimal debit = (decimal)item["debit"];
                decimal credit = (decimal)item["credit"];
                balance += d.Side == "ar" ? debit - credit : credit - debit;
                item["balance"] = balance;
                if (d.Writeoff || !string.Equals((string)item["proc_style"], "9P", StringComparison.OrdinalIgnoreCase))
                {
                    items.Add(item);
                }
            }
            return items;
        }

        // date 是口径日期；reg_date、doc_date 是登记日期和单据日期（非口径的那个取组内最早）。
        static Dictionary<string, object> Row(Dictionary<string, object> row, DetailArgs d)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            string type = GlSql.Col(row, "vt");
            string name;
            (d.Side == "ar" ? ArTypes : ApTypes).TryGetValue(type, out name);
            bool register = d.Basis == "register";
            item["partner"] = GlSql.Col(row, "partner");
            item["date"] = GlSql.Col(row, "dt");
            item["reg_date"] = Reports.Text(row, register ? "dt" : "odt");
            item["doc_date"] = Reports.Text(row, register ? "odt" : "dt");
            item["doc_type"] = type;
            item["doc_type_name"] = name;
            item["doc_code"] = GlSql.Col(row, "vid");
            item["proc_style"] = Reports.Text(row, "ps");
            item["co_doc_type"] = Reports.Text(row, "cvt");
            item["co_doc_code"] = Reports.Text(row, "cvid");
            item["digest"] = Reports.Text(row, "digest");
            item["account"] = Reports.Text(row, "acc");
            item["dept"] = Reports.Text(row, "dept");
            item["person"] = Reports.Text(row, "psn");
            item["debit"] = GlSql.Money(row, "dm");
            item["credit"] = GlSql.Money(row, "cm");
            return item;
        }

        static string Next(Dictionary<string, object> row)
        {
            return Reports.Cursor(GlSql.Col(row, "partner"), GlSql.Col(row, "dt"), GlSql.Col(row, "fid"));
        }
    }
}
