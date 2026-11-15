using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的读路由部分：archives/resolve 的请求校验、分档 SQL 的 ? 个数与参数一致；idempotency/get 的请求校验。不连库。
    internal static class ArcResolveSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckSql();
            CheckIdemGet();
            Expect("resolve read", PermRegistry.IsRead(ArcResolve.Path) && PermRegistry.PerItem(ArcResolve.Path));
            Expect("idem get not perm read", !PermRegistry.IsRead(IdemGet.Path) && !PermRegistry.PerItem(Requests.ArcRoot + "list"));
        }

        static void CheckParse()
        {
            ArcResolveReq req = ArcResolveReq.Parse(Body(Item("customer", "  示例贸易 "), null));
            Expect("resolve defaults", req.Limit == 5 && !req.IncludeDisabled && req.Items[0].Q == "示例贸易");
            Field("resolve no items", new Dictionary<string, object>(), "items");
            Field("resolve empty items", Body(null, null), "items");
            Field("resolve pair kind", Body(Item("customer_bank", "x"), null), "items.0.archive");
            Field("resolve fa_card", Body(Item(ArcFa.Name, "x"), null), "items.0.archive");
            Field("resolve unknown kind", Body(Item("nope", "x"), null), "items.0.archive");
            Field("resolve blank q", Body(Item("vendor", "   "), null), "items.0.q");
            Field("resolve long q", Body(Item("vendor", new string('a', 101)), null), "items.0.q");
            Dictionary<string, object> extra = Item("vendor", "x");
            extra["code"] = "V01";
            Field("resolve item key", Body(extra, null), "items.0.code");
            Field("resolve limit", Body(Item("vendor", "x"), 21), "limit");
            Dictionary<string, object> flag = Body(Item("vendor", "x"), 3);
            flag["include_disabled"] = "yes";
            Field("resolve include_disabled", flag, "include_disabled");
            Expect("resolve supports", ArcResolveReq.Supports(ArcKind.Find("inventory")) && ArcResolveReq.Supports(ArcKind.Find("project"))
                && !ArcResolveReq.Supports(ArcKind.Find("customer_address")) && !ArcResolveReq.Supports(ArcKind.Find(ArcExch.Name)));
        }

        // 每档、两种停用选项下，SQL 里 ? 的个数都等于参数个数（数据权限条件不加，快照为 null）。
        static void CheckSql()
        {
            string[] kinds = new string[] { "customer", "inventory", "department", "unit" };
            for (int k = 0; k < kinds.Length; k++)
            {
                ResolveSrc src = ArcResolveSrc.Of(null, ArcKind.Find(kinds[k]), null);
                for (int flag = 0; flag < 2; flag++)
                {
                    ResolveRun run = Run(src, flag == 1);
                    for (int tier = ArcResolveSql.TierCode; tier <= ArcResolveSql.TierContains; tier++)
                    {
                        if (!ArcResolveSql.Has(src, tier))
                        {
                            continue;
                        }
                        List<object> args = new List<object>();
                        string sql = ArcResolveSql.Build(run, tier, "a_b", args);
                        Expect("resolve sql " + kinds[k] + " " + tier, Marks(sql) == args.Count && sql.IndexOf("a_b", StringComparison.Ordinal) < 0);
                    }
                }
            }
            ResolveSrc unit = ArcResolveSrc.Of(null, ArcKind.Find("unit"), null);
            Expect("resolve unit tiers", !ArcResolveSql.Has(unit, ArcResolveSql.TierAbbr) && !ArcResolveSql.Has(unit, ArcResolveSql.TierMnem));
            List<object> like = new List<object>();
            ArcResolveSql.Build(Run(ArcResolveSrc.Of(null, ArcKind.Find("inventory"), null), false), ArcResolveSql.TierContains, "M6_", like);
            Expect("resolve like escaped", like.Contains("%M6\\_%"));
        }

        static void CheckIdemGet()
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["route"] = "/u8co/v1/vouchers/create";
            body[IdemReq.KeyField] = "k-1";
            IdemGetAsk ask = IdemGet.Parse(body);
            Expect("idem get caller", ask.Idem.Caller == IdemReq.DirectCaller && ask.Idem.Key == "k-1");
            IdemAsk same = new IdemAsk();
            same.Key = "k-1";
            same.Caller = IdemReq.DirectCaller;
            Expect("idem get store id", ask.Idem.StoreId("803", ask.Route) == same.StoreId("803", "/u8co/v1/vouchers/create"));
            // 全部写路由都收幂等键，读路由仍 400。
            body["route"] = "/u8co/v1/vouchers/update";
            Expect("idem get write route", IdemGet.Parse(body).Route == "/u8co/v1/vouchers/update");
            body["route"] = "/u8co/v1/vouchers/load";
            Field("idem get route", body, "route", true);
            body["route"] = "/u8co/v1/archives/create";
            body[IdemReq.KeyField] = "bad key";
            Field("idem get key", body, IdemReq.KeyField, true);
            body[IdemReq.KeyField] = "k-1";
            body[IdemReq.CallerField] = "";
            Field("idem get caller empty", body, IdemReq.CallerField, true);
            body[IdemReq.CallerField] = null;
            Field("idem get caller null", body, IdemReq.CallerField, true);
            Expect("idem get sub", IdemGet.SubOf(Requests.GlRoot + "create") == "GL" && IdemGet.SubOf(Requests.GlRoot + "post") == "GL"
                && IdemGet.SubOf("/u8co/v1/vouchers/create") == "AS" && IdemGet.SubOf(Requests.WriteoffPath) == "AS");
        }

        static ResolveRun Run(ResolveSrc src, bool withDisabled)
        {
            ResolveRun run = new ResolveRun();
            run.Src = src;
            run.Req = new ArcResolveReq();
            run.Req.IncludeDisabled = withDisabled;
            run.Date = "2026-01-31";
            return run;
        }

        static int Marks(string sql)
        {
            int n = 0;
            for (int i = 0; i < sql.Length; i++)
            {
                if (sql[i] == '?')
                {
                    n++;
                }
            }
            return n;
        }

        static Dictionary<string, object> Item(string archive, string q)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["archive"] = archive;
            item["q"] = q;
            return item;
        }

        static Dictionary<string, object> Body(Dictionary<string, object> item, object limit)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["items"] = item == null ? new object[0] : new object[] { item };
            if (limit != null)
            {
                body["limit"] = limit;
            }
            return body;
        }

        static void Field(string name, Dictionary<string, object> body, string field)
        {
            Field(name, body, field, false);
        }

        static void Field(string name, Dictionary<string, object> body, string field, bool idem)
        {
            try
            {
                if (idem)
                {
                    IdemGet.Parse(body);
                }
                else
                {
                    ArcResolveReq.Parse(body);
                }
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Field == field);
                return;
            }
            throw new InvalidOperationException(name);
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
