using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // --selftest 的降序分页部分：order 字段、游标编解码、vouchers/search 与 order_execution 降序的 SQL（? 个数与参数一致、续读条件、
    // 排序），以及 stock/current 的 inv_unit。不连库。
    internal static class DescPagingSelfTest
    {
        const string Date = "2026-01-31 08:30:00.123";

        public static void Run()
        {
            Order();
            Cursor();
            SearchSql();
            SearchArgs();
            ExecSql();
            Stock();
        }

        static void Order()
        {
            Expect("order default", !DescPaging.ParseOrder(new Dictionary<string, object>()));
            Expect("order asc", !DescPaging.ParseOrder(One("order", "asc")));
            Expect("order desc", DescPaging.ParseOrder(One("order", "desc")));
            Expect("order null", !DescPaging.ParseOrder(One("order", null)));
            Refused("order upper", delegate { DescPaging.ParseOrder(One("order", "DESC")); }, "order");
            Refused("order number", delegate { DescPaging.ParseOrder(One("order", 1)); }, "order");
        }

        static void Cursor()
        {
            DescCursor c = DescPaging.Parse(DescPaging.Encode(Date, 7, 9), 2);
            Expect("cursor roundtrip", c.Date == Date && c.Ids[0] == 7 && c.Ids[1] == 9);
            Expect("cursor null date", DescPaging.Parse(DescPaging.Encode("", 7), 1).Date == null);
            Expect("cursor none", DescPaging.Parse(null, 1) == null && DescPaging.Parse("", 2) == null);
            Refused("cursor count", delegate { DescPaging.Parse(DescPaging.Encode(Date, 7, 9), 1); }, "after");
            Refused("cursor garbage", delegate { DescPaging.Parse("!!!", 1); }, "after");
            Refused("cursor date", delegate { DescPaging.Parse(DescPaging.Encode("2026-13-01 00:00:00.000", 7), 1); }, "after");
            Refused("cursor date short", delegate { DescPaging.Parse(DescPaging.Encode("2026-01-31", 7), 1); }, "after");
            Refused("cursor date before 1753", delegate { DescPaging.Parse(DescPaging.Encode("1752-12-31 23:59:59.997", 7), 1); }, "after");
            Refused("cursor date after max", delegate { DescPaging.Parse(DescPaging.Encode("9999-12-31 23:59:59.998", 7), 1); }, "after");
            Expect("cursor date min", DescPaging.Parse(DescPaging.Encode("1753-01-01 00:00:00.000", 7), 1).Date != null);
            Expect("cursor date max", DescPaging.Parse(DescPaging.Encode("9999-12-31 23:59:59.997", 7), 1).Date != null);
            Refused("cursor id zero", delegate { DescPaging.Parse(DescPaging.Encode(Date, 0), 1); }, "after");
            Refused("cursor id text", delegate { DescPaging.Parse(Reports.Cursor(Date, "x"), 1); }, "after");
        }

        // 每种类型：降序无游标、有游标、游标日期为 NULL 三种 SQL 的 ? 个数等于参数个数；升序 SQL 不变。
        static void SearchSql()
        {
            foreach (string name in ListKinds.Names())
            {
                DescFirst(name);
                DescAfter(name);
                AscSame(name);
            }
        }

        static void DescFirst(string name)
        {
            ListKind kind = ListKinds.Find(name);
            string first = Sql(name, "desc", null, 2);
            Expect("desc first " + name, first.Contains("1 = 1") && first.Contains("AS cursor_date")
                && first.EndsWith(" ORDER BY " + kind[ListKind.Date] + " DESC, h." + kind[ListKind.Id] + " DESC",
                StringComparison.Ordinal));
        }

        static void DescAfter(string name)
        {
            ListKind kind = ListKinds.Find(name);
            string date = kind[ListKind.Date];
            string id = "h." + kind[ListKind.Id];
            List<object> ps = new List<object>();
            string next = Sql(name, "desc", DescPaging.Encode(Date, 7), 3, ps);
            Expect("desc cursor " + name, ps.Count == 4 && (int)ps[0] == 11 && (string)ps[1] == Date && (string)ps[2] == Date
                && (int)ps[3] == 7 && next.Contains("((" + date + " <= CONVERT(datetime, ?, 121) AND (" + date
                + " < CONVERT(datetime, ?, 121) OR " + id + " < ?)) OR " + date + " IS NULL)"));
            ps = new List<object>();
            string nulls = Sql(name, "desc", DescPaging.Encode("", 7), 4, ps);
            Expect("desc null date " + name, ps.Count == 2 && (int)ps[1] == 7
                && nulls.Contains("(" + date + " IS NULL AND " + id + " < ?)"));
        }

        static void AscSame(string name)
        {
            string id = "h." + ListKinds.Find(name)[ListKind.Id];
            string asc = Sql(name, null, null, 5);
            Expect("asc same " + name, asc.EndsWith(" ORDER BY " + id, StringComparison.Ordinal) && asc.Contains(id + " > ?")
                && !asc.Contains("cursor_date") && !asc.Contains("DESC"));
        }

        static string Sql(string type, string order, string after, int stage)
        {
            return Sql(type, order, after, stage, new List<object>());
        }

        static string Sql(string type, string order, string after, int stage, List<object> ps)
        {
            Dictionary<string, object> body = One("type", type);
            body["limit"] = 10;
            if (order != null)
            {
                body["order"] = order;
            }
            if (after != null)
            {
                body["after"] = after;
            }
            string sql = VoucherSearchSql.Build(VoucherSearchArgs.Parse(body), ps, null);
            Expect("params " + type + stage, Count(sql, '?') == ps.Count);
            return sql;
        }

        static void SearchArgs()
        {
            Dictionary<string, object> body = One("type", "sale_order");
            body["order"] = "desc";
            body["after"] = 7;
            Refused("desc int after", delegate { VoucherSearchArgs.Parse(body); }, "after");
            body["order"] = "asc";
            body["after"] = DescPaging.Encode(Date, 7);
            Refused("asc cursor after", delegate { VoucherSearchArgs.Parse(body); }, "after");
            body["order"] = "latest";
            body.Remove("after");
            Refused("search bad order", delegate { VoucherSearchArgs.Parse(body); }, "order");
            body["order"] = "desc";
            body["after"] = "!!!";
            Refused("desc bad cursor", delegate { VoucherSearchArgs.Parse(body); }, "after");
            body["after"] = new string('x', 513);
            Refused("desc long cursor", delegate { VoucherSearchArgs.Parse(body); }, "after");
        }

        static void ExecSql()
        {
            foreach (bool sale in new bool[] { true, false })
            {
                string[] c = ReportsOrderExec.Cols(sale);
                Dictionary<string, object> body = One("type", sale ? "sale_order" : "purchase_order");
                body["order"] = "desc";
                body["after"] = DescPaging.Encode(Date, 7, 9);
                ExecArgs a = ReportsTraceReq.ParseExec(body);
                List<object> ps = new List<object>();
                StringBuilder text = new StringBuilder();
                ReportsOrderExec.DescOrder(text, ps, a, c);
                string sql = text.ToString();
                string head = "h." + c[0];
                string line = "d." + c[5];
                Expect("exec params", Count(sql, '?') == ps.Count && ps.Count == 5 && (string)ps[0] == Date && (string)ps[1] == Date
                    && (int)ps[2] == 7 && (int)ps[3] == 7 && (int)ps[4] == 9);
                Expect("exec cursor", sql.StartsWith(" AND ((h." + c[2] + " <= CONVERT(datetime, ?, 121) AND (h." + c[2]
                    + " < CONVERT(datetime, ?, 121) OR (" + head + " < ? OR (" + head + " = ? AND " + line + " < ?)))) OR h."
                    + c[2] + " IS NULL)", StringComparison.Ordinal));
                Expect("exec order", sql.EndsWith(" ORDER BY h." + c[2] + " DESC, " + head + " DESC, " + line + " DESC",
                    StringComparison.Ordinal));
                body.Remove("after");
                ExecArgs first = ReportsTraceReq.ParseExec(body);
                text = new StringBuilder();
                ps = new List<object>();
                ReportsOrderExec.DescOrder(text, ps, first, c);
                Expect("exec first", ps.Count == 0 && text.ToString().StartsWith(" ORDER BY ", StringComparison.Ordinal));
            }
            Dictionary<string, object> asc = One("type", "sale_order");
            asc["after"] = DescPaging.Encode(Date, 7, 9);
            Expect("exec asc keeps text", !ReportsTraceReq.ParseExec(asc).Desc);
            asc["order"] = "desc";
            asc["after"] = Reports.Cursor("7", "9");
            Refused("exec asc cursor on desc", delegate { ReportsTraceReq.ParseExec(asc); }, "after");
            asc["order"] = "up";
            Refused("exec bad order", delegate { ReportsTraceReq.ParseExec(asc); }, "order");
        }

        static void Stock()
        {
            List<object> ps = new List<object>();
            string sql = ListStock.Build(ListArgs.Stock(One("wh", "W01")), ps, null);
            Expect("stock unit sql", sql.Contains("un.cComUnitName AS inv_unit")
                && sql.Contains("LEFT JOIN ComputationUnit un ON un.cComunitCode = i.cComUnitCode")
                && Count(sql, '?') == ps.Count);
            Expect("stock unit key", Array.IndexOf(ListStock.Keys, "inv_unit") == Array.IndexOf(ListStock.Keys, "inv_std") + 1);
        }

        static Dictionary<string, object> One(string key, object value)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body[key] = value;
            return body;
        }

        static void Refused(string name, Action run, string field)
        {
            try
            {
                run();
            }
            catch (BridgeException ex)
            {
                Expect("refuse " + name, ex.Status == 400 && ex.Field == field);
                return;
            }
            throw new InvalidOperationException("refuse " + name);
        }

        static int Count(string text, char c)
        {
            int n = 0;
            for (int i = 0; i < text.Length; i++)
            {
                if (text[i] == c)
                {
                    n++;
                }
            }
            return n;
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("desc paging " + name);
            }
        }
    }
}
