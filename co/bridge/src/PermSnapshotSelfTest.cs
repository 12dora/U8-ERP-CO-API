using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的权限快照 / 权限评估部分（PermSnapshot、PermEvaluate）：只测纯逻辑，不连库、不登录。
    // 全部 / 编码 / 空集合三种形态，指纹稳定且与集合顺序无关，评估只认 permEvaluateOperators（主管不例外），subject 不存在 404。
    // 由 PermSelfTest.Run 调用。
    internal static class PermSnapshotSelfTest
    {
        public static void Run()
        {
            CheckShapes();
            CheckSupervisor();
            CheckFingerprint();
            CheckCaller();
            CheckSubject();
            CheckWiring();
        }

        // 受控对象：有授权给编码，没有授权给空数组（不是全部）；不受控给 all；fitem 给大类 + 编码；超过内联上限标 live。
        static void CheckShapes()
        {
            PermContext p = Ctx("op1");
            p.On.Add(PermObj.Customer);
            p.On.Add(PermObj.Warehouse);
            p.On.Add(PermObj.Item);
            p.On.Add(PermObj.Inventory);
            p.Codes[PermObj.Customer] = Set("C02", "C01");
            p.Codes[PermObj.Warehouse] = Set();
            p.Codes[PermObj.Item] = Set("00" + PermContext.PairSep + "P1");
            Dictionary<string, object> data = PermSnapshot.ToJson(p)["data"] as Dictionary<string, object>;
            List<string> cus = Codes(data, PermObj.Customer);
            Expect("snap codes", string.Join(",", cus.ToArray()) == "C01,C02");
            Expect("snap codes not all", !Has(data, PermObj.Customer, "all"));
            Expect("snap empty", Codes(data, PermObj.Warehouse).Count == 0);
            Expect("snap empty not all", !Has(data, PermObj.Warehouse, "all"));
            Expect("snap missing set empty", Codes(data, PermObj.Inventory).Count == 0);
            Expect("snap missing not all", !Has(data, PermObj.Inventory, "all"));
            Expect("snap all", Has(data, PermObj.Vendor, "all") && Has(data, PermObj.Account, "all"));
            List<object> pairs = Entry(data, PermObj.Item)["pairs"] as List<object>;
            Dictionary<string, object> pair = pairs == null || pairs.Count != 1 ? null : pairs[0] as Dictionary<string, object>;
            Expect("snap pairs", pair != null && (string)pair["class"] == "00" && (string)pair["code"] == "P1");
            Expect("snap not live", !Has(data, PermObj.Customer, "live"));
            Expect("snap no user", !data.ContainsKey("user"));
            Expect("snap no gzauth", !data.ContainsKey("gzauth"));
            Expect("snap read objects", data.Count == PermObj.ReadObjects().Length && data.ContainsKey(PermObj.Sign));
            CheckLive(p);
        }

        // 编码超过 PermSql.MaxInline 个照样全部给出并标 live；数据权限管理员不受控。
        static void CheckLive(PermContext p)
        {
            HashSet<string> many = Set();
            for (int i = 0; i <= PermSql.MaxInline; i++)
            {
                many.Add("C" + i.ToString("D4", System.Globalization.CultureInfo.InvariantCulture));
            }
            p.Codes[PermObj.Customer] = many;
            Dictionary<string, object> data = PermSnapshot.ToJson(p)["data"] as Dictionary<string, object>;
            Expect("snap live", Has(data, PermObj.Customer, "live") && Codes(data, PermObj.Customer).Count == many.Count);
            p.DataAdmin.Add(PermObj.Warehouse);
            data = PermSnapshot.ToJson(p)["data"] as Dictionary<string, object>;
            Expect("snap data admin", Has(data, PermObj.Warehouse, "all"));
        }

        static void CheckSupervisor()
        {
            PermContext p = Ctx("op1");
            p.On.Add(PermObj.Customer);
            p.Codes[PermObj.Customer] = Set();
            p.Supervisor = true;
            Dictionary<string, object> body = PermSnapshot.ToJson(p);
            Dictionary<string, object> data = body["data"] as Dictionary<string, object>;
            bool all = data.Count > 0;
            foreach (string obj in data.Keys)
            {
                all = all && Has(data, obj, "all");
            }
            Expect("snap supervisor all", all && (bool)body["supervisor"]);
            Expect("snap ttl", (int)body["ttl_s"] == PermSnapshot.TtlSeconds && (bool)body["ok"]);
        }

        // 同样的权限、不同的读入顺序与操作员：指纹相同；改一个编码、一个功能 id、主管标志或字段权限：指纹不同。
        static void CheckFingerprint()
        {
            PermContext a = Ctx("op1");
            a.Funcs.Add("SA03010104");
            a.Funcs.Add("AS011Q");
            a.On.Add(PermObj.Customer);
            a.Codes[PermObj.Customer] = Set("C01", "C02");
            PermContext b = Ctx("op2");
            b.Funcs.Add("AS011Q");
            b.Funcs.Add("SA03010104");
            b.On.Add(PermObj.Customer);
            b.Codes[PermObj.Customer] = Set("C02", "C01");
            string fa = Fp(a);
            Expect("fp hex", fa.Length == 64 && fa == fa.ToLowerInvariant());
            Expect("fp stable", fa == Fp(a) && fa == Fp(b));
            b.Codes[PermObj.Customer].Add("C03");
            Expect("fp codes", fa != Fp(b));
            a.Funcs.Add("GL0202");
            string fb = Fp(a);
            Expect("fp funcs", fa != fb);
            a.Supervisor = true;
            Expect("fp supervisor", fb != Fp(a));
            // 字段权限进指纹：多一个拒绝字段，指纹就变。
            string fc = Fp(b);
            HashSet<string> denied = Set("iPrice");
            b.Columns["0303"] = denied;
            Expect("fp columns", fc != Fp(b));
            Expect("fp columns shape", ((Dictionary<string, object>)PermSnapshot.ToJson(b)["columns"]).ContainsKey("0303")
                && ((Dictionary<string, object>)PermSnapshot.ToJson(a)["columns"]).Count == 0);
            PermContext empty = Ctx("op1");
            empty.On.Add(PermObj.Customer);
            PermContext open = Ctx("op1");
            Expect("fp empty vs all", Fp(empty) != Fp(open));
        }

        // 评估只认名单（不分大小写）；名单为空、配置缺失、主管（admin）不在名单里一律 403。
        static void CheckCaller()
        {
            BridgeConfig cfg = new BridgeConfig();
            Expect("eval default empty", cfg.PermEvaluateOperators.Length == 0);
            ExpectStatus("eval empty list", 403, delegate { PermEvaluate.RequireCaller(cfg, "admin"); });
            ExpectStatus("eval null cfg", 403, delegate { PermEvaluate.RequireCaller(null, "SYS01"); });
            cfg.PermEvaluateOperators = new string[] { "SYS01" };
            PermEvaluate.RequireCaller(cfg, "sys01");
            ExpectStatus("eval not listed", 403, delegate { PermEvaluate.RequireCaller(cfg, "op001"); });
            ExpectStatus("eval supervisor not listed", 403, delegate { PermEvaluate.RequireCaller(cfg, "admin"); });
            Dictionary<string, object> map = new Dictionary<string, object>();
            map["permEvaluateOperators"] = new object[] { "SYS01" };
            BridgeConfig.CheckKeys(map);
            ConfigRules.CheckPermEvaluateOperators(new string[] { "SYS01" });
            bool bad = false;
            try
            {
                ConfigRules.CheckPermEvaluateOperators(new string[] { "a b" });
            }
            catch (InvalidOperationException)
            {
                bad = true;
            }
            Expect("eval bad config code", bad);
        }

        static void CheckSubject()
        {
            ExpectStatus("subject unknown", 404, delegate { PermEvaluate.RequireKnown(null); });
            ExpectStatus("subject disabled", 404, delegate { PermEvaluate.RequireKnown("1"); });
            PermEvaluate.RequireKnown("0");
            Dictionary<string, object> body = new Dictionary<string, object>();
            ExpectStatus("subject missing", 400, delegate { PermEvaluate.Parse(body); });
            body["subject"] = "a b";
            ExpectStatus("subject bad", 400, delegate { PermEvaluate.Parse(body); });
            body["subject"] = 7;
            ExpectStatus("subject not text", 400, delegate { PermEvaluate.Parse(body); });
            body["subject"] = "op-001";
            Expect("subject ok", PermEvaluate.Parse(body) == "op-001");
        }

        // 读路由登记：权限规则只要登录，读线程池，Dispatch 路由表。
        static void CheckWiring()
        {
            string[] paths = new string[] { PermSnapshot.Path, PermEvaluate.Path };
            List<string> routes = DispatchRoutes.Paths();
            for (int i = 0; i < paths.Length; i++)
            {
                WorkItem item = new WorkItem();
                item.Path = paths[i];
                PermRule rule = PermRegistry.Find(item);
                Expect("perm read " + paths[i], PermRegistry.IsRead(paths[i]) && !PermRegistry.PerItem(paths[i]));
                Expect("perm rule " + paths[i], rule != null && rule.Auths.Length == 0 && rule.Objs.Length == 0);
                Expect("perm sql read " + paths[i], RouteClass.IsSqlRead(item));
                Expect("perm routed " + paths[i], routes.Contains(paths[i]));
            }
            Expect("perm owns", PermSnapshot.Owns(PermEvaluate.Path) && !PermSnapshot.Owns(Requests.ListPath));
        }

        static PermContext Ctx(string op)
        {
            PermContext p = new PermContext();
            p.Acc = "998";
            p.Operator = op;
            p.Year = 2024;
            p.DateYear = 2024;
            p.AcctYear = 2024;
            return p;
        }

        static HashSet<string> Set(params string[] codes)
        {
            return new HashSet<string>(codes, StringComparer.OrdinalIgnoreCase);
        }

        static string Fp(PermContext p)
        {
            return (string)PermSnapshot.ToJson(p)["fingerprint"];
        }

        static Dictionary<string, object> Entry(Dictionary<string, object> data, string obj)
        {
            object raw;
            Dictionary<string, object> e = data != null && data.TryGetValue(obj, out raw) ? raw as Dictionary<string, object> : null;
            if (e == null)
            {
                throw new InvalidOperationException("perm snapshot missing " + obj);
            }
            return e;
        }

        static bool Has(Dictionary<string, object> data, string obj, string key)
        {
            object raw;
            return Entry(data, obj).TryGetValue(key, out raw) && raw is bool && (bool)raw;
        }

        static List<string> Codes(Dictionary<string, object> data, string obj)
        {
            List<string> codes = Entry(data, obj)["codes"] as List<string>;
            if (codes == null)
            {
                throw new InvalidOperationException("perm snapshot codes " + obj);
            }
            return codes;
        }

        static void ExpectStatus(string name, int status, Action call)
        {
            try
            {
                call();
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == status);
                return;
            }
            throw new InvalidOperationException("perm " + name);
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("perm " + name);
            }
        }
    }
}
