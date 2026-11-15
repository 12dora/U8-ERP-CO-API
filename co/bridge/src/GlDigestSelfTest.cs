using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // --selftest 的凭证摘要部分（gl/vouchers/digest）：请求校验、SQL 占位符个数、指纹向量、路由登记。不连库。
    internal static class GlDigestSelfTest
    {
        // SHA-256("100.0000|100.0000|2|9001|张三|李四|||0|0")。
        const string Vector = "ccf3a661cdcf7b87ccc6bfe9892067c5266651e790c34ed8886566f88311be93";

        public static void Run()
        {
            CheckParse();
            CheckBad();
            CheckSql();
            CheckFingerprint();
            CheckRoutes();
        }

        static void CheckParse()
        {
            GlDigestReq d = GlDigest.Parse(new Dictionary<string, object>());
            Expect("defaults", d.Year == 0 && d.Periods == null && d.Closed == 1);
            Expect("defaults page", d.After == null && d.Limit == GlDigest.DefaultLimit && !d.KeysOnly);
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["fiscal_year"] = 2026;
            body["periods"] = new object[] { 9, 8L, 12 };
            body["after"] = "9.3.120";
            body["limit"] = 500;
            body["keys_only"] = true;
            GlDigestReq r = GlDigest.Parse(body);
            Expect("fields", r.Year == 2026 && r.Limit == 500 && r.KeysOnly);
            Expect("periods sorted", Join(r.Periods, ",") == "8,9,12");
            Expect("after", Join(r.After, ".") == "9.3.120");
            Expect("closed 0", GlDigest.Parse(One("closed_periods", 0)).Closed == 0);
        }

        static void CheckBad()
        {
            Bad("year", One("fiscal_year", 1899));
            Bad("year str", One("fiscal_year", "2026"));
            Bad("periods empty", One("periods", new object[0]));
            Bad("periods 13", One("periods", new object[] { 13 }));
            Bad("periods 0", One("periods", new object[] { 0 }));
            Bad("periods dup", One("periods", new object[] { 9, 9 }));
            Bad("periods str", One("periods", "9"));
            Bad("closed 13", One("closed_periods", 13));
            Bad("limit 501", One("limit", 501));
            Bad("limit 0", One("limit", 0));
            Bad("after", One("after", "9.3"));
            Bad("keys_only", One("keys_only", "true"));
            Dictionary<string, object> both = One("periods", new object[] { 9 });
            both["closed_periods"] = 1;
            Bad("both", both);
        }

        static void CheckSql()
        {
            GlDigestReq req = GlDigest.Parse(One("after", "9.3.120"));
            List<object> args = new List<object>();
            string sql = GlDigestSql.PageSql(req, 2026, new int[] { 8, 9 }, args).ToString() + GlDigestSql.Tail;
            Expect("page marks", Marks(sql) == args.Count && args.Count == 9);
            Expect("page top", (int)args[0] == req.Limit + 1 && (int)args[1] == 2026 && (int)args[3] == 9);
            // 按聚集索引 (iperiod, isignseq, ino_id) 分组，csign 取 MAX，才能顺序聚合、取够一页即停。
            Expect("page group", sql.EndsWith(" GROUP BY iperiod, isignseq, ino_id ORDER BY iperiod, isignseq, ino_id", StringComparison.Ordinal)
                && sql.IndexOf("MAX(ISNULL(csign,'')) csign", StringComparison.Ordinal) > 0);
            List<object> wm = new List<object>();
            StringBuilder mark = new StringBuilder(GlDigestSql.MarkHead);
            GlDigestSql.Scope(mark, wm, 2026, new int[0]);
            Expect("empty scope", mark.ToString().EndsWith(" AND 1=0", StringComparison.Ordinal) && wm.Count == 1);
            Dictionary<string, object> last = new Dictionary<string, object>();
            last["iperiod"] = "9";
            last["isignseq"] = "3";
            last["ino_id"] = "120";
            Expect("next", GlDigestSql.Next(last) == "9.3.120");
        }

        static void CheckFingerprint()
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["dsum"] = "100.0000";
            row["csum"] = "100.0000";
            row["n"] = "2";
            row["maxid"] = "9001";
            row["maker"] = "张三";
            row["checker"] = "李四";
            row["posted"] = "0";
            row["flag"] = "0";
            Expect("vector", GlDigestSql.Fingerprint(row) == Vector);
            row["checker"] = "";
            Expect("changes", GlDigestSql.Fingerprint(row) != Vector);
        }

        static void CheckRoutes()
        {
            WorkItem item = new WorkItem();
            item.Config = new BridgeConfig();
            item.Path = GlDigest.Path;
            item.Body = new Dictionary<string, object>();
            item.Date = "2026-09-30";
            Expect("sql read", RouteClass.IsSqlRead(item));
            Expect("perm read", PermRegistry.IsRead(GlDigest.Path) && PermRegistry.Find(item) != null);
            Expect("not write", !WriteGate.IsWrite(item));
        }

        static string Join(int[] values, string sep)
        {
            StringBuilder text = new StringBuilder();
            for (int i = 0; i < values.Length; i++)
            {
                text.Append(i == 0 ? "" : sep).Append(values[i].ToString(CultureInfo.InvariantCulture));
            }
            return text.ToString();
        }

        static int Marks(string sql)
        {
            int count = 0;
            for (int i = 0; i < sql.Length; i++)
            {
                if (sql[i] == '?')
                {
                    count++;
                }
            }
            return count;
        }

        static Dictionary<string, object> One(string key, object value)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body[key] = value;
            return body;
        }

        static void Bad(string name, Dictionary<string, object> body)
        {
            try
            {
                GlDigest.Parse(body);
            }
            catch (BridgeException ex)
            {
                Expect(name + " 400", ex.Status == 400);
                return;
            }
            throw new InvalidOperationException("gldigest bad " + name);
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("gldigest " + name);
            }
        }
    }
}
