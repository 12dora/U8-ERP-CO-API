using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的应收 / 应付处理记录部分（arap/process/list）：请求校验、摘要期间的选取、SQL 占位符个数与绑定顺序、
    // 路由登记（读线程池、读权限、不持写闸门）。只跑纯函数，不连库、不建 COM（PermHook 在没有请求上下文时不加条件）。
    internal static class ArapProcListSelfTest
    {
        public static void Run()
        {
            CheckList();
            CheckDigest();
            CheckBad();
            CheckPeriods();
            CheckSql();
            CheckFiscalYear();
            CheckPair();
            CheckRoutes();
        }

        static void CheckList()
        {
            ProcListArgs a = ArapProcListReq.Parse(Body("AR"));
            Expect("list defaults", Sig(a) == "AR|False|0|0|100|False|False");
            Dictionary<string, object> b = Body("AP");
            b["changed_since"] = "30025";
            b["after"] = 30100;
            b["keys_only"] = true;
            Expect("list incremental", Sig(ArapProcListReq.Parse(b)) == "AP|False|30025|30100|100|True|True");
            b["open_only"] = false;
            b["changed_since"] = 7;
            Expect("list open_only off", Sig(ArapProcListReq.Parse(b)) == "AP|False|7|30100|100|False|True");
        }

        // flag|digest|changed_since|after|limit|open_only|keys_only
        static string Sig(ProcListArgs a)
        {
            return string.Join("|", new object[] { a.Flag, a.Digest, a.Since, a.After, a.Limit, a.OpenOnly, a.KeysOnly });
        }

        static void CheckDigest()
        {
            Dictionary<string, object> b = Body("AR");
            b["digest"] = true;
            ProcListArgs a = ArapProcListReq.Parse(b);
            Expect("digest defaults", a.Digest && a.Limit == 500 && a.Periods == null && a.FiscalYear == 0 && a.Cursor == null);
            b["fiscal_year"] = 2026;
            b["periods"] = new object[] { 9, 8 };
            b["after"] = Reports.Cursor("9P", "HXAR0000000000001");
            a = ArapProcListReq.Parse(b);
            Expect("digest periods", a.FiscalYear == 2026 && a.Periods.Length == 2 && a.Periods[0] == 8 && a.Periods[1] == 9);
            Expect("digest cursor", a.Cursor[0] == "9P" && a.Cursor[1] == "HXAR0000000000001");
        }

        static void CheckBad()
        {
            Bad("flag", One("flag", "GL"));
            Bad("since text", With("changed_since", "12a"));
            Bad("since negative", With("changed_since", -1));
            Bad("since long", With("changed_since", "2147483648"));
            Bad("after text", With("after", "5"));
            Bad("limit", With("limit", 501));
            Bad("keys_only", With("keys_only", "true"));
            Bad("list fiscal_year", With("fiscal_year", 2026));
            Bad("list periods", With("periods", new object[] { 9 }));
            Bad("digest since", Digest("changed_since", "1"));
            Bad("digest keys_only", Digest("keys_only", true));
            Bad("digest open_only", Digest("open_only", true));
            Bad("digest after int", Digest("after", 5));
            Bad("digest after junk", Digest("after", "x"));
            Bad("digest year", Digest("fiscal_year", 1999));
            Bad("digest periods 13", Digest("periods", new object[] { 13 }));
            Bad("digest periods dup", Digest("periods", new object[] { 9, 9 }));
            Bad("digest periods empty", Digest("periods", new object[0]));
            Bad("digest periods text", Digest("periods", "9"));
        }

        // GL_mend：2026/8 已结账，9–12 未结账；2025/12 已结账。
        static void CheckPeriods()
        {
            List<int[]> states = new List<int[]>();
            states.Add(new int[] { 2025, 12, 1 });
            states.Add(new int[] { 2026, 8, 1 });
            for (int p = 9; p <= 12; p++)
            {
                states.Add(new int[] { 2026, p, 0 });
            }
            ProcListArgs a = ArapProcListReq.Parse(Digest("fiscal_year", 2025));
            Expect("periods year closed", ArapProcDigest.Periods(a, states, 2026).Count == 0);
            a = ArapProcListReq.Parse(Body("AR", true));
            List<int[]> open = ArapProcDigest.Periods(a, states, 2026);
            Expect("periods open", open.Count == 4 && open[0][0] == 2026 && open[0][1] == 9 && open[3][1] == 12);
            a = ArapProcListReq.Parse(Digest("periods", new object[] { 8 }));
            List<int[]> given = ArapProcDigest.Periods(a, states, 2026);
            Expect("periods given", given.Count == 1 && given[0][0] == 2026 && given[0][1] == 8);
        }

        static void CheckSql()
        {
            Dictionary<string, object> b = Body("AR");
            b["changed_since"] = "100";
            b["after"] = 250;
            List<object> args = new List<object>();
            string sql = ProcListSql.ListSql(null, ArapProcListReq.Parse(b), 30525, args);
            Expect("list marks", Marks(sql) == args.Count);
            Expect("list binds", Binds(args) == "101|AR|250|30525");
            Expect("list table", sql.IndexOf("FROM Ar_Detail d", StringComparison.Ordinal) > 0);
            Expect("list open", sql.IndexOf("ISNULL(m.bflag_AR,0)=0", StringComparison.Ordinal) > 0);
            Expect("list open fy", sql.IndexOf("m.iyear=" + ProcListSql.Fy, StringComparison.Ordinal) > 0);
            Expect("list fy col", sql.IndexOf(ProcListSql.Fy + ") as fiscal_year", StringComparison.Ordinal) > 0);
            List<int[]> periods = new List<int[]>();
            periods.Add(new int[] { 2025, 12 });
            periods.Add(new int[] { 2026, 9 });
            periods.Add(new int[] { 2026, 10 });
            b = Body("AP", true);
            b["after"] = Reports.Cursor("9P", "HXAP0000000000001");
            ProcListArgs a = ArapProcListReq.Parse(b);
            args = new List<object>();
            sql = ProcListSql.DigestSql(null, a, periods, args);
            Expect("digest marks", Marks(sql) == args.Count);
            Expect("digest binds", Binds(args)
                == "501|AP|2025-01-01|2026-01-01|12|2025|2025-10-01|2027-01-01|9|10|2026|9P|9P|HXAP0000000000001");
            Expect("digest fy", sql.IndexOf(ProcListSql.Fy + "=?", StringComparison.Ordinal) > 0
                && sql.IndexOf("MIN(" + ProcListSql.Fy + ")) as fiscal_year", StringComparison.Ordinal) > 0);
            Expect("digest table", sql.IndexOf("FROM Ap_Detail d", StringComparison.Ordinal) > 0);
            args = new List<object>();
            List<string[]> keys = new List<string[]>();
            keys.Add(new string[] { "9I", "YCFAP1" });
            keys.Add(new string[] { "9P", "HXAP9" });
            sql = ProcListSql.PartnerSql(null, a, periods, keys, args);
            Expect("partner marks", Marks(sql) == args.Count);
            Expect("partner binds", Binds(args)
                == "AP|2025-01-01|2026-01-01|12|2025|2025-10-01|2027-01-01|9|10|2026|9I|YCFAP1|9P|HXAP9");
        }

        // 登记日期下限：年度 Y 的行可能登记在 Y-1 年、月份大于期间（次年 1 月处理上年 12 月单据）。
        static void CheckFiscalYear()
        {
            Expect("fy lower p1", ProcListSql.LowerBound(2027, 1) == "2026-02-01");
            Expect("fy lower p11", ProcListSql.LowerBound(2027, 11) == "2026-12-01");
            Expect("fy lower p12", ProcListSql.LowerBound(2027, 12) == "2027-01-01");
            Expect("fy case", ProcListSql.Fy.IndexOf("d.iPeriod<MONTH(d.dRegDate) THEN YEAR(d.dRegDate)+1", StringComparison.Ordinal) > 0);
        }

        // 摘要行的往来单位：去重个数（含空串）不超过 2 直接取最小、最大值，超过返回 null 补查。
        static void CheckPair()
        {
            Expect("pair one", PairSig("C01", "C01", "1") == "C01");
            Expect("pair blank", PairSig("", "", "1") == "");
            Expect("pair blank and one", PairSig("", "C02", "2") == "C02");
            Expect("pair two", PairSig("C01", "C02", "2") == "C02,C01");
            Expect("pair case", PairSig("c01", "C01", "1") == "C01");
            Expect("pair more", ArapProcDigest.Pair(PairRow("C01", "C09", "3")) == null);
        }

        static string PairSig(string min, string max, string count)
        {
            return string.Join(",", ArapProcDigest.Pair(PairRow(min, max, count)).ToArray());
        }

        static Dictionary<string, object> PairRow(string min, string max, string count)
        {
            Dictionary<string, object> r = new Dictionary<string, object>();
            r["p_min"] = min;
            r["p_max"] = max;
            r["p_count"] = count;
            return r;
        }

        static string Binds(List<object> args)
        {
            return string.Join("|", args.ToArray());
        }

        static void CheckRoutes()
        {
            WorkItem item = new WorkItem();
            item.Config = new BridgeConfig();
            item.Path = ArapProcListReq.Path;
            item.Body = Body("AP");
            item.Date = "2026-09-30";
            Expect("route sql read", RouteClass.IsSqlRead(item) && !WriteGate.IsWrite(item));
            PermRule rule = PermRegistry.Find(item);
            Expect("route perm", PermRegistry.IsRead(ArapProcListReq.Path) && rule != null && rule.Key == "read:arap:process_list:ap");
            Expect("route perm ar", PermRegistry.ForKey(ArapProcListReq.KeyOf("AR")) != null);
            Expect("login year", ArapProcListReq.LoginYear(item) == 2026);
        }

        static Dictionary<string, object> Body(string flag)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["flag"] = flag;
            return body;
        }

        static Dictionary<string, object> Body(string flag, bool digest)
        {
            Dictionary<string, object> body = Body(flag);
            body["digest"] = digest;
            return body;
        }

        static Dictionary<string, object> One(string key, object value)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body[key] = value;
            return body;
        }

        static Dictionary<string, object> With(string key, object value)
        {
            Dictionary<string, object> body = Body("AR");
            body[key] = value;
            return body;
        }

        static Dictionary<string, object> Digest(string key, object value)
        {
            Dictionary<string, object> body = Body("AR", true);
            body[key] = value;
            return body;
        }

        static int Marks(string sql)
        {
            int count = 0;
            foreach (char c in sql)
            {
                if (c == '?')
                {
                    count++;
                }
            }
            return count;
        }

        static void Bad(string name, Dictionary<string, object> body)
        {
            try
            {
                ArapProcListReq.Parse(body);
            }
            catch (BridgeException ex)
            {
                Expect("proc list bad " + name, ex.Status == 400);
                return;
            }
            throw new InvalidOperationException("proc list bad " + name);
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException(name);
            }
        }
    }
}
