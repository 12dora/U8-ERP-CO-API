using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // --selftest 的只读部分：出入库调整单 ia_adjust、存货调价单 inventory_price_adjust 的种类登记（只开读取）、列表列与 SQL
    // 占位符、按存货查找、读线程池与权限规则；固定资产报表 fa_changes、fa_depreciation 的参数校验、SQL 占位符与游标。不连库。
    internal static class IaSaFaSelfTest
    {
        public static void Run()
        {
            CheckKinds();
            CheckIaList();
            CheckPriceList();
            CheckWiring();
            CheckFaParse();
            CheckFaSql();
        }

        static void CheckKinds()
        {
            VoucherKind ia = Kinds.Find(IaAdjustRead.KindName);
            VoucherKind ip = Kinds.Find(InvPriceAdjustRead.KindName);
            Expect("ia kind", ia != null && ip != null);
            Expect("ia kind cols", All(ia.HeadTable == "JustInVouch", ia.IdColumn == "id", ia.BodyFk == "cJVCode",
                ia.LineIdColumn == "AutoID", ip.HeadTable == "SA_InvPriceJustMain", ip.BodyFk == "id"));
            VoucherKind[] both = new VoucherKind[] { ia, ip };
            for (int i = 0; i < both.Length; i++)
            {
                VoucherKind k = both[i];
                Expect("ia read only " + k.Name, All(!k.Creatable, !k.Updatable, !k.Deletable, !k.Verifiable, !k.Closable,
                    !k.Workflow, k.Sources.Length == 0, k.SubId == "SA"));
            }
            Expect("ia type name", All(IaAdjustRead.TypeName("20") == "入库调整单", IaAdjustRead.TypeName("21") == "出库调整单",
                IaAdjustRead.TypeName("66") == ""));
        }

        static void CheckIaList()
        {
            ListKind kind = ListKinds.Find(IaAdjustRead.KindName);
            Expect("ia list kind", kind != null && !kind.HasBodyUfts && kind.Has(ListKind.Apply) && !kind.Has(ListKind.Cond));
            Expect("ia list flags", All(kind.VerifiedSql().Contains("b.posted = b.n"), kind.ClosedSql() == null,
                kind.Has(ListKind.Wh), !kind.Has(ListKind.Cus), !kind.Has(ListKind.Ven)));
            string keys = string.Join(",", ListSql.FullKeys(kind));
            Expect("ia list keys", keys.EndsWith(",ufts,vouch_type,rd_flag,rd_code,auto,bus_type,unit_code,vendor_code,handler,"
                + "memo,line_count,posted_lines,amount,created_at,modified_at", StringComparison.Ordinal));
            ListArgs args = ListArgs.Vouchers(Body("{\"type\":\"ia_adjust\",\"changed_since\":\"123\",\"after\":5,\"limit\":20,"
                + "\"filter\":{\"code\":\"0000000001\",\"date_from\":\"2026-08-01\",\"date_to\":\"2026-08-31\","
                + "\"wh_code\":\"01\",\"maker\":\"op001\",\"verified\":true}}"));
            List<object> ps = new List<object>();
            string sql = ListSql.Vouchers(args, ps, null);
            Expect("ia list params", All(Count(sql, '?') == ps.Count, ps.Count == 8, (int)ps[0] == 21, (int)ps[1] == 5));
            Expect("ia list sql", All(sql.Contains(" FROM JustInVouch h OUTER APPLY (SELECT COUNT(*) AS n"),
                sql.Contains("d.cJVCode = h.cJVCode) b WHERE h.id > ?"), sql.Contains("h.ufts > CONVERT(binary(8), CONVERT(bigint, ?))"),
                !sql.Contains("d2."), sql.EndsWith(" ORDER BY h.id", StringComparison.Ordinal)));
            ListRefused("ia cus", "{\"type\":\"ia_adjust\",\"filter\":{\"cus_code\":\"C1\"}}");
            ListRefused("ia closed", "{\"type\":\"ia_adjust\",\"filter\":{\"closed\":true}}");
        }

        static void CheckPriceList()
        {
            ListKind kind = ListKinds.Find(InvPriceAdjustRead.KindName);
            Expect("price list kind", kind != null && !kind.HasBodyUfts && !kind.Has(ListKind.Apply) && !kind.Has(ListKind.Wh));
            ListArgs args = ListArgs.Vouchers(Body("{\"type\":\"inventory_price_adjust\",\"keys_only\":true,\"changed_since\":\"9\"}"));
            List<object> ps = new List<object>();
            string sql = ListSql.Vouchers(args, ps, null);
            Expect("price keys sql", All(sql.StartsWith("SELECT TOP (?) h.id AS id, h.ccode AS code, ", StringComparison.Ordinal),
                sql.Contains(" FROM SA_InvPriceJustMain h WHERE h.id > ?"), Count(sql, '?') == ps.Count, ps.Count == 3));
            ListRefused("price wh", "{\"type\":\"inventory_price_adjust\",\"filter\":{\"wh_code\":\"01\"}}");
        }

        static void CheckWiring()
        {
            string[] names = new string[] { IaAdjustRead.KindName, InvPriceAdjustRead.KindName };
            for (int i = 0; i < names.Length; i++)
            {
                VoucherKind kind = Kinds.Find(names[i]);
                PermRule rule = PermRegistry.ForKey("voucher:" + names[i]);
                Expect("ia rule " + names[i], rule != null && rule.Auths.Length > 0);
                Expect("ia list rule " + names[i], PermRegistry.Find(Item(Requests.ListPath, "{\"type\":\"" + names[i] + "\"}")) == rule);
                Expect("ia sql load " + names[i], RouteClass.IsSqlLoad(kind));
                Expect("ia search inv " + names[i], VoucherSearchSql.HasInventory(ListKinds.Find(names[i])));
                string[] plan = MetaFieldsVt.Plan(kind);
                Expect("ia vt " + names[i], plan != null && plan[0].Length > 0 && plan[2] == MetaFieldsVt.Card);
            }
            Expect("ia ids", All(Has("voucher:" + IaAdjustRead.KindName, "IA1001"), Has("voucher:" + IaAdjustRead.KindName, "IA02040301"),
                Has("voucher:" + InvPriceAdjustRead.KindName, "SA03120202"), Has(ReportsFaReq.ChangesRule, "FA1603"),
                Has(ReportsFaReq.DeprRule, "FA2403"), !Has(ReportsFaReq.DeprRule, "FA1501")));
            Expect("ia defines", VoucherSearchDefines.Supports(ListKinds.Find(IaAdjustRead.KindName))
                && !VoucherSearchDefines.Supports(ListKinds.Find(InvPriceAdjustRead.KindName)));
            string[] paths = new string[] { ReportsFaReq.ChangesPath, ReportsFaReq.DeprPath };
            string[] rules = new string[] { ReportsFaReq.ChangesRule, ReportsFaReq.DeprRule };
            for (int i = 0; i < paths.Length; i++)
            {
                WorkItem item = Item(paths[i], "{}");
                Expect("fa route " + paths[i], All(PermRegistry.IsRead(paths[i]), RouteClass.IsSqlRead(item), !WriteGate.IsWrite(item),
                    PermRegistry.Find(item) == PermRegistry.ForKey(rules[i]), PermRegistry.ForKey(rules[i]) != null));
            }
            Expect("fa names", Array.IndexOf(Reports.Names, ReportsFaReq.ChangesName) >= 0
                && Array.IndexOf(Reports.Names, ReportsFaReq.DeprName) >= 0 && Reports.SubOf(ReportsFaReq.DeprName, null) == "SA");
        }

        static void CheckFaParse()
        {
            ReportArgs a = ReportsReq.Parse(ReportsFaReq.ChangesName, Body("{\"fiscal_year\":2026,\"period\":4,\"card\":\"00021\","
                + "\"code\":\"00005\",\"change_type\":1,\"limit\":5}"));
            FaReportArgs f = ReportsFaReq.Parse(a, Body("{\"period\":4,\"card\":\"00021\",\"code\":\"00005\",\"change_type\":1,\"limit\":5}"));
            Expect("fa parse changes", All(a.FiscalYear == 2026, a.Limit == 5, f.Period == 4, f.Card == "00021", f.Code == "00005",
                f.ChangeType == 1));
            ReportArgs d = new ReportArgs();
            d.Name = ReportsFaReq.DeprName;
            FaReportArgs g = ReportsFaReq.Parse(d, Body("{\"nonzero\":true}"));
            Expect("fa parse depr", All(d.Limit == 200, g.Period == 0, g.Card == "", g.NonZero));
            Refused("fa period", ReportsFaReq.DeprName, "{\"period\":13}");
            Refused("fa card", ReportsFaReq.DeprName, "{\"card\":\"a%\"}");
            Refused("fa nonzero", ReportsFaReq.DeprName, "{\"nonzero\":1}");
            Refused("fa limit", ReportsFaReq.ChangesName, "{\"limit\":1001}");
            Refused("fa type", ReportsFaReq.ChangesName, "{\"change_type\":\"1\"}");
        }

        static void CheckFaSql()
        {
            ReportArgs a = new ReportArgs();
            a.Name = ReportsFaReq.ChangesName;
            a.FiscalYear = 2026;
            a.Limit = 10;
            a.After = Reports.Cursor("00003");
            FaReportArgs f = new FaReportArgs();
            f.Period = 4;
            f.Card = "00021";
            List<object> ps = new List<object>();
            string sql = ReportsFa.ChangesQuery(a, f, ps);
            Expect("fa changes sql", All(Count(sql, '?') == ps.Count, ps.Count == 5, (int)ps[0] == 11, (int)ps[1] == 2026,
                (string)ps[4] == "00003", sql.EndsWith(" ORDER BY v.sNum", StringComparison.Ordinal)));
            a.Name = ReportsFaReq.DeprName;
            a.After = Reports.Cursor("00021", "7");
            f.NonZero = true;
            List<object> dp = new List<object>();
            string dsql = ReportsFa.DeprQuery(a, f, dp);
            Expect("fa depr sql", All(Count(dsql, '?') == dp.Count, dp.Count == 7, (int)dp[6] == 7,
                dsql.Contains("WHEN 12 THEN t.dblDeprT12 END"), dsql.EndsWith(" ORDER BY t.sCardNum, l.iPeriod", StringComparison.Ordinal)));
            a.After = Reports.Cursor("00021", "13");
            bool refused = false;
            try
            {
                ReportsFa.DeprQuery(a, f, new List<object>());
            }
            catch (BridgeException ex)
            {
                refused = ex.Status == 400;
            }
            Expect("fa depr cursor", refused);
        }

        static void Refused(string name, string report, string json)
        {
            ReportArgs a = new ReportArgs();
            a.Name = report;
            try
            {
                ReportsFaReq.Parse(a, Body(json));
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void ListRefused(string name, string json)
        {
            try
            {
                ListArgs.Vouchers(Body(json));
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static WorkItem Item(string path, string json)
        {
            WorkItem item = new WorkItem();
            item.Path = path;
            item.Body = Body(json);
            return item;
        }

        static bool Has(string key, string auth)
        {
            PermRule rule = PermRegistry.ForKey(key);
            return rule != null && Array.IndexOf(rule.Auths, auth) >= 0;
        }

        static bool All(params bool[] checks)
        {
            return Array.IndexOf(checks, false) < 0;
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

        static Dictionary<string, object> Body(string json)
        {
            return Json.Parse(Encoding.UTF8.GetBytes(json));
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
