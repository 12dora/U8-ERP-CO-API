using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的客户开票单位新增 / 删除 / 设默认部分：请求解析、新增判重与幂等重放、默认行翻转与客户表头变化、
    // 删除与设默认的 expect / 默认行 / 不存在、INSERT / UPDATE / DELETE 生成、写后回读核对、响应体、锁键与权限登记。不连库。
    internal static class ArcCusInvoiceSelfTest
    {
        public static void Run()
        {
            CheckParseAdd();
            CheckParseExpect();
            CheckParseRejects();
            CheckRoutes();
            CheckAdd();
            CheckAddDefault();
            CheckAddFirst();
            CheckAddLegacy();
            CheckAddRejects();
            CheckDelete();
            CheckSetDefault();
            CheckSetLegacy();
            CheckSetRejects();
            CheckStatements();
            CheckMatches();
            CheckBodies();
            CheckLocksAndPerm();
        }

        static void CheckParseAdd()
        {
            ArcCusInvoiceAsk ask = ArcCusInvoice.Parse(ArcCusInvoice.AddOp, Body("C900001:C900002", null, null));
            Eq("add customer", ask.Customer, "C900001");
            Eq("add company", ask.Company, "C900002");
            Eq("add code", ask.Code, "C900001:C900002");
            Eq("add default off", ask.Default, false);
            Expect("add no ts", ask.ExpTs == null);
            ask = ArcCusInvoice.Parse(ArcCusInvoice.AddOp, Body("C900001:C900002", true, null));
            Eq("add default on", ask.Default, true);
            ask = ArcCusInvoice.Parse(ArcCusInvoice.AddOp, Body("C900001:C900002", false, null));
            Eq("add default explicit off", ask.Default, false);
        }

        static void CheckParseExpect()
        {
            ArcCusInvoiceAsk ask = ArcCusInvoice.Parse(ArcCusInvoice.DeleteOp, Body("C900001:C900002", null, Exp(false, null)));
            Eq("del exp default", ask.ExpDefault, false);
            Expect("del no ts", ask.ExpTs == null);
            ask = ArcCusInvoice.Parse(ArcCusInvoice.DefaultOp, Body("C900001:C900002", null, Exp(true, "7891234")));
            Eq("set exp default", ask.ExpDefault, true);
            Eq("set exp ts", ask.ExpTs, "7891234");
            Dictionary<string, object> onlyTs = new Dictionary<string, object>();
            onlyTs["customer_ufts"] = "55";
            ask = ArcCusInvoice.Parse(ArcCusInvoice.AddOp, Body("C900001:C900002", null, onlyTs));
            Eq("add exp ts", ask.ExpTs, "55");
        }

        static void CheckParseRejects()
        {
            string add = ArcCusInvoice.AddOp;
            string del = ArcCusInvoice.DeleteOp;
            string set = ArcCusInvoice.DefaultOp;
            Bad("no code", add, Body(null, null, null));
            Bad("empty code", add, Body("", null, null));
            Bad("no colon", add, Body("C900001", null, null));
            Bad("empty company", add, Body("C900001:", null, null));
            Bad("empty customer", add, Body(":C900002", null, null));
            Bad("space company", add, Body("C900001: C900002", null, null));
            Bad("long customer", add, Body(new string('C', 21) + ":C900002", null, null));
            Bad("long company", add, Body("C900001:" + new string('C', 21), null, null));
            Bad("too long pair", add, Body(new string('C', 21) + ":" + new string('D', 21), null, null));
            Bad("default type", add, Body("C900001:C900002", "yes", null));
            Bad("default number", add, Body("C900001:C900002", 1, null));
            Bad("add expect bdefault", add, Body("C900001:C900002", null, Exp(false, null)));
            Bad("expect not object", del, BodyRaw("C900001:C900002", "x"));
            Bad("del no expect", del, Body("C900001:C900002", null, null));
            Bad("set no expect", set, Body("C900001:C900002", null, null));
            Bad("del expect empty", del, Body("C900001:C900002", null, new Dictionary<string, object>()));
            Bad("del expect only ts", del, Body("C900001:C900002", null, Ts("5")));
            Bad("del expect default null", del, Body("C900001:C900002", null, Exp(null, null)));
            Bad("del expect default type", del, Body("C900001:C900002", null, ExpRaw("1", null)));
            Bad("del expect extra", del, Body("C900001:C900002", null, WithKey(Exp(false, null), "cinvoicecompany", "x")));
            Bad("ts not string", set, Body("C900001:C900002", null, ExpRaw(false, 5)));
            Bad("ts letters", set, Body("C900001:C900002", null, Exp(false, "12a")));
            Bad("ts empty", set, Body("C900001:C900002", null, Exp(false, "")));
            Bad("ts negative", set, Body("C900001:C900002", null, Exp(false, "-1")));
            Bad("ts long", set, Body("C900001:C900002", null, Exp(false, new string('9', 20))));
            Fail("unknown op", 500, "internal", delegate { ArcCusInvoice.Parse("customer_address", Body("C900001:C900002", null, null)); });
        }

        static void CheckRoutes()
        {
            Expect("is ops", ArcCusInvoice.Is("customer_invoice_company_add") && ArcCusInvoice.Is("customer_invoice_company_delete")
                && ArcCusInvoice.Is("customer_invoice_company"));
            Expect("is other", !ArcCusInvoice.Is("customer_address") && !ArcCusInvoice.Is(null) && !ArcCusInvoice.Is("update"));
            Expect("registered", ArcCustomerOps.Is(ArcCusInvoice.AddOp) && ArcCustomerOps.Is(ArcCusInvoice.DeleteOp)
                && ArcCustomerOps.Is(ArcCusInvoice.DefaultOp));
            Eq("add path", ArcCusInvoice.AddPath, Requests.ArcRoot + "customer_invoice_company_add");
            Eq("delete path", ArcCusInvoice.DeletePath, Requests.ArcRoot + "customer_invoice_company_delete");
            Eq("default path", ArcCusInvoice.DefaultPath, Requests.ArcRoot + "customer_invoice_company");
            Eq("add rule", ArcCusInvoice.AddRuleKey, "write:archive:customer:customer_invoice_company_add");
            Eq("delete rule", ArcCusInvoice.DeleteRuleKey, "write:archive:customer:customer_invoice_company_delete");
            Eq("default rule", ArcCusInvoice.DefaultRuleKey, "write:archive:customer:customer_invoice_company");
            ArcCustomerOps.Check(ArcCusInvoice.AddOp, Body("C900001:C900002", null, null));
            ArcCustomerOps.Check(ArcCusInvoice.DeleteOp, Body("C900001:C900002", null, Exp(false, null)));
            ArcCustomerOps.Check(ArcCusInvoice.DefaultOp, Body("C900001:C900002", null, Exp(false, null)));
            Eq("lock code", ArcCusInvoice.LockCode("C900001:C900002"), "C900001");
            Eq("lock code plain", ArcCustomerOps.LockCode("C900001"), "C900001");
        }

        // 新增一个非自身开票单位：只插一行、不碰别的行；新行排在最后、Id 未知；默认与表头不变。
        static void CheckAdd()
        {
            ArcCusInvoicePlan plan = ArcCusInvoicePlanner.MakeAdd(Ask("C900004"), Sample());
            Expect("insert changed", plan.Changed && plan.Insert != null && plan.Remove == null && plan.Dirty.Count == 0);
            Expect("insert rows", plan.Before.Count == 3 && plan.After.Count == 4 && plan.After[3] == plan.Insert);
            Expect("insert not default", plan.Insert.Id == 0 && !plan.Insert.Default && plan.After[0].Default);
            Expect("insert header kept", plan.Header == null);
            // 同一对已存在：重放，不写（编码不分大小写；没要求默认时已是默认的也算一致）。
            plan = ArcCusInvoicePlanner.MakeAdd(Ask("C900002"), Sample());
            Expect("replay", !plan.Changed && plan.Insert == null && plan.After.Count == 3);
            plan = ArcCusInvoicePlanner.MakeAdd(Ask("c900001"), Sample());
            Expect("replay case default", !plan.Changed);
            plan = ArcCusInvoicePlanner.MakeAdd(AskDefault("C900001"), Sample());
            Expect("replay want default", !plan.Changed);
        }

        // 新增并设默认：已有默认行取消、表头换成新默认。
        static void CheckAddDefault()
        {
            ArcCusInvoicePlan plan = ArcCusInvoicePlanner.MakeAdd(AskDefault("C900004"), Sample());
            Expect("def dirty", plan.Dirty.Count == 1 && plan.Dirty[0] == 0 && plan.Before[0].Default);
            Expect("def after", !plan.After[0].Default && plan.After[3].Default && plan.Insert.Default);
            Eq("def header", plan.Header, "C900004");
            plan = ArcCusInvoicePlanner.MakeAdd(Ask("C900004"), Sample());
            Expect("not default keeps", !plan.Insert.Default && Quiet(plan));
        }

        // 客户还没有行或没有默认行时，新行直接成为默认并写表头。
        static void CheckAddFirst()
        {
            ArcCusInvoicePlan plan = ArcCusInvoicePlanner.MakeAdd(Ask("C900004"), new List<ArcCusInvoiceRow>());
            Expect("first row default", plan.Insert != null && plan.Insert.Default && plan.After.Count == 1);
            Expect("first row dirty", plan.Dirty.Count == 0);
            Eq("first row header", plan.Header, "C900004");
            plan = ArcCusInvoicePlanner.MakeAdd(Ask("C900004"), NoDefault());
            Expect("no default heals", plan.Insert.Default && plan.Dirty.Count == 0);
            Eq("no default header", plan.Header, "C900004");
        }

        // 数据已不一致（多行默认）时，新增后恰好一个默认：default=true 时全部取消，否则只留第一个。
        static void CheckAddLegacy()
        {
            List<ArcCusInvoiceRow> rows = Sample();
            rows[2].Default = true;
            ArcCusInvoicePlan plan = ArcCusInvoicePlanner.MakeAdd(AskDefault("C900004"), rows);
            Expect("def dirty both", plan.Dirty.Count == 2 && !plan.After[0].Default && !plan.After[2].Default);
            plan = ArcCusInvoicePlanner.MakeAdd(Ask("C900004"), rows);
            Expect("legacy add keeps first", plan.After[0].Default && !plan.After[2].Default && !plan.Insert.Default);
            Expect("legacy add dirty", plan.Dirty.Count == 1 && plan.Dirty[0] == 2);
            Eq("legacy add header", plan.Header, "C900001");
        }

        static void CheckAddRejects()
        {
            Fail("taken not default", 409, "invoice_company_taken", delegate { ArcCusInvoicePlanner.MakeAdd(AskDefault("C900002"), Sample()); });
            Fail("taken case", 409, "invoice_company_taken", delegate { ArcCusInvoicePlanner.MakeAdd(AskDefault("c900003"), Sample()); });
        }

        static void CheckDelete()
        {
            ArcCusInvoicePlan plan = ArcCusInvoicePlanner.MakeDelete(Del("C900003", false), Sample());
            Expect("del changed", plan.Changed && plan.Remove != null && plan.Remove.Id == 13 && plan.Insert == null);
            Expect("del rows", plan.Before.Count == 3 && plan.After.Count == 2 && plan.Dirty.Count == 0 && plan.Header == null);
            plan = ArcCusInvoicePlanner.MakeDelete(Del("c900002", false), Sample());
            Expect("del case", plan.Remove.Id == 12);
            Fail("del default", 409, "invoice_company_is_default", delegate { ArcCusInvoicePlanner.MakeDelete(Del("C900001", true), Sample()); });
            Fail("del only default", 409, "invoice_company_is_default",
                delegate { ArcCusInvoicePlanner.MakeDelete(Del("C900001", true), Sample().GetRange(0, 1)); });
            Fail("del missing", 404, "invoice_company_not_found", delegate { ArcCusInvoicePlanner.MakeDelete(Del("C900009", false), Sample()); });
            Fail("del expect", 409, "state_mismatch", delegate { ArcCusInvoicePlanner.MakeDelete(Del("C900002", true), Sample()); });
            Fail("del expect default", 409, "state_mismatch", delegate { ArcCusInvoicePlanner.MakeDelete(Del("C900001", false), Sample()); });
            try
            {
                ArcCusInvoicePlanner.MakeDelete(Del("C900001", true), Sample());
            }
            catch (BridgeException ex)
            {
                Expect("del default msg", ex.Message == "默认开票单位不能删除，请先设置其他默认");
                return;
            }
            throw new InvalidOperationException("del default msg");
        }

        static void CheckSetDefault()
        {
            ArcCusInvoicePlan plan = ArcCusInvoicePlanner.MakeDefault(Del("C900002", false), Sample());
            Expect("set changed", plan.Changed && plan.Insert == null && plan.Remove == null);
            Expect("set dirty", plan.Dirty.Count == 2);
            Expect("set after", !plan.After[0].Default && plan.After[1].Default && !plan.After[2].Default);
            Expect("set before kept", plan.Before[0].Default && !plan.Before[1].Default);
            Eq("set header", plan.Header, "C900002");
            plan = ArcCusInvoicePlanner.MakeDefault(Del("C900001", true), Sample());
            Expect("set already default", !plan.Changed && Quiet(plan));
            plan = ArcCusInvoicePlanner.MakeDefault(Del("C900003", false), NoDefault());
            Expect("set from none", plan.Changed && plan.Dirty.Count == 1 && plan.After[2].Default);
            Eq("set from none header", plan.Header, "C900003");
        }

        // 数据已不一致（多行默认）时设默认：多余的默认取消，表头按改后的默认写。
        static void CheckSetLegacy()
        {
            List<ArcCusInvoiceRow> rows = Sample();
            rows[2].Default = true;
            ArcCusInvoicePlan plan = ArcCusInvoicePlanner.MakeDefault(Del("C900001", true), rows);
            Expect("set heals two defaults", plan.Changed && plan.Dirty.Count == 1 && plan.Dirty[0] == 2);
            Expect("set heals after", plan.After[0].Default && !plan.After[2].Default);
            Eq("set heals header", plan.Header, "C900001");
        }

        static void CheckSetRejects()
        {
            Fail("set missing", 404, "invoice_company_not_found", delegate { ArcCusInvoicePlanner.MakeDefault(Del("C900009", false), Sample()); });
            Fail("set expect", 409, "state_mismatch", delegate { ArcCusInvoicePlanner.MakeDefault(Del("C900002", true), Sample()); });
            Fail("set expect default", 409, "state_mismatch", delegate { ArcCusInvoicePlanner.MakeDefault(Del("C900001", false), Sample()); });
            try
            {
                ArcCusInvoicePlanner.MakeDefault(Del("C900002", true), Sample());
            }
            catch (BridgeException ex)
            {
                Dictionary<string, object> detail = ex.Detail as Dictionary<string, object>;
                Dictionary<string, object> current = detail == null ? null : detail["current"] as Dictionary<string, object>;
                Expect("set expect detail", current != null && (bool)current["bdefault"] == false);
                return;
            }
            throw new InvalidOperationException("set expect detail");
        }

        static void CheckStatements()
        {
            ArcCusInvoicePlan plan = ArcCusInvoicePlanner.MakeAdd(AskDefault("C900004"), Sample());
            List<ArcCusAddrStmt> updates = ArcCusInvoicePlanner.Updates(plan, "C900001");
            Eq("update count", updates.Count, 1);
            Eq("update sql", updates[0].Sql, "UPDATE sa_invoicecustomers SET bdefault=? WHERE autoid=CONVERT(bigint, ?) AND ccuscode=?");
            Eq("update value", updates[0].Args[0], 0);
            Eq("update id", updates[0].Args[1], "11");
            Eq("update customer", updates[0].Args[2], "C900001");
            ArcCusAddrStmt insert = ArcCusInvoicePlanner.Insert(plan, "C900001");
            Eq("insert sql", insert.Sql, "INSERT INTO sa_invoicecustomers (ccuscode, cinvoicecompany, bdefault) VALUES (?, ?, ?)");
            Expect("insert args", insert.Args.Length == 3 && (string)insert.Args[0] == "C900001"
                && (string)insert.Args[1] == "C900004" && (int)insert.Args[2] == 1);
            plan = ArcCusInvoicePlanner.MakeDelete(Del("C900003", false), Sample());
            ArcCusAddrStmt delete = ArcCusInvoicePlanner.Delete(plan, "C900001");
            Eq("delete sql", delete.Sql, "DELETE FROM sa_invoicecustomers WHERE autoid=CONVERT(bigint, ?) AND ccuscode=?");
            Expect("delete args", delete.Args.Length == 2 && (string)delete.Args[0] == "13" && (string)delete.Args[1] == "C900001");
            plan = ArcCusInvoicePlanner.MakeDefault(Del("C900002", false), Sample());
            updates = ArcCusInvoicePlanner.Updates(plan, "C900001");
            Expect("set updates", updates.Count == 2 && (int)updates[0].Args[0] == 0 && (int)updates[1].Args[0] == 1
                && (string)updates[1].Args[1] == "12");
            Expect("none changed", ArcCusInvoicePlanner.Updates(ArcCusInvoicePlanner.MakeAdd(Ask("C900002"), Sample()), "C900001").Count == 0);
            ArgsBindable("update args", updates[0].Args);
            ArgsBindable("insert args bindable", insert.Args);
            ArgsBindable("delete args bindable", delete.Args);
        }

        // GlSql.Exec 的参数只收 int / string / null。
        static void ArgsBindable(string name, object[] args)
        {
            foreach (object arg in args)
            {
                Expect(name, arg == null || arg is int || arg is string);
            }
        }

        // 没有要改的默认标志，也没有表头变化。
        static bool Quiet(ArcCusInvoicePlan plan)
        {
            return plan.Dirty.Count == 0 && plan.Header == null;
        }

        static void CheckMatches()
        {
            ArcCusInvoicePlan plan = ArcCusInvoicePlanner.MakeAdd(AskDefault("C900004"), Sample());
            List<ArcCusInvoiceRow> actual = Clone(plan.After);
            actual[3].Id = 14;
            Expect("matches", ArcCusInvoicePlanner.Matches(plan.After, actual));
            List<ArcCusInvoiceRow> reorder = new List<ArcCusInvoiceRow>();
            reorder.Add(actual[3]);
            reorder.AddRange(actual.GetRange(0, 3));
            Expect("matches reordered", ArcCusInvoicePlanner.Matches(plan.After, reorder));
            Expect("matches needs id", !ArcCusInvoicePlanner.Matches(plan.After, Clone(plan.After)));
            List<ArcCusInvoiceRow> other = Clone(actual);
            other[0].Default = true;
            Expect("matches default not cleared", !ArcCusInvoicePlanner.Matches(plan.After, other));
            other = Clone(actual);
            other[3].Default = false;
            Expect("matches default not set", !ArcCusInvoicePlanner.Matches(plan.After, other));
            other = Clone(actual);
            other.RemoveAt(3);
            Expect("matches count", !ArcCusInvoicePlanner.Matches(plan.After, other));
            other = Clone(actual);
            other[1].Id = 99;
            Expect("matches id", !ArcCusInvoicePlanner.Matches(plan.After, other));
            other = Clone(actual);
            other[3].Code = "C900008";
            Expect("matches missing code", !ArcCusInvoicePlanner.Matches(plan.After, other));
            plan = ArcCusInvoicePlanner.MakeDelete(Del("C900003", false), Sample());
            Expect("matches delete", ArcCusInvoicePlanner.Matches(plan.After, Sample().GetRange(0, 2)));
            Expect("matches delete still there", !ArcCusInvoicePlanner.Matches(plan.After, Sample()));
        }

        static void CheckBodies()
        {
            ArcCusInvoiceAsk ask = AskDefault("C900004");
            ArcCusInvoicePlan plan = ArcCusInvoicePlanner.MakeAdd(ask, Sample());
            Dictionary<string, object> body = ArcCusInvoice.OkBody(ask, plan, true);
            Eq("ok flag", body["ok"], true);
            Eq("ok code", body["code"], "C900001:C900004");
            Eq("ok changed", body["changed"], true);
            Eq("ok before rows", ((List<object>)body["before"]).Count, 3);
            Eq("ok after rows", ((List<object>)body["after"]).Count, 4);
            Eq("ok keys", body.Count, 5);
            Dictionary<string, object> row = (Dictionary<string, object>)((List<object>)body["after"])[3];
            Eq("ok row code", row["code"], "C900004");
            Eq("ok row bdefault", row["bdefault"], true);
            Eq("ok row keys", row.Count, 2);
            Eq("ok before default", ((Dictionary<string, object>)((List<object>)body["before"])[0])["bdefault"], true);
            Eq("ok after default cleared", ((Dictionary<string, object>)((List<object>)body["after"])[0])["bdefault"], false);
            body = ArcCusInvoice.OkBody(ask, plan, false);
            Eq("replay changed", body["changed"], false);
            Eq("replay after rows", ((List<object>)body["after"]).Count, 3);
            body = ArcCusInvoice.DryBody(ask, plan);
            Eq("dry flag", body["dry_run"], true);
            Eq("dry mode", body["mode"], DryRunModes.Validate);
            Eq("dry changed", body["changed"], true);
            Eq("dry after rows", ((List<object>)body["after"]).Count, 4);
            Eq("dry keys", body.Count, 7);
        }

        static void CheckLocksAndPerm()
        {
            string[] paths = new string[] { ArcCusInvoice.AddPath, ArcCusInvoice.DeletePath, ArcCusInvoice.DefaultPath };
            foreach (string path in paths)
            {
                WorkItem item = new WorkItem();
                item.Config = new BridgeConfig();
                item.Config.SerializeWrites = false;
                item.Path = path;
                item.Body = Body("c900001:c900002", null, null);
                string[] keys = DocLocks.KeysOf(item);
                Expect("lock customer " + path, Array.IndexOf(keys, "arc:customer:C900001") >= 0);
                Expect("lock no pair " + path, Array.IndexOf(keys, "arc:customer:C900001:C900002") < 0);
                Expect("lock write " + path, Array.IndexOf(keys, "arc:write") >= 0);
                Expect("is write " + path, WriteGate.IsWrite(path));
            }
            Expect("perm add", PermRegistry.ForKey(ArcCusInvoice.AddRuleKey) != null);
            Expect("perm delete", PermRegistry.ForKey(ArcCusInvoice.DeleteRuleKey) != null);
            Expect("perm default", PermRegistry.ForKey(ArcCusInvoice.DefaultRuleKey) != null);
            Expect("pair spec", ArcPair.Named("customer_invoice_company") != null && ArcPair.Named("nope") == null);
        }

        static List<ArcCusInvoiceRow> Sample()
        {
            List<ArcCusInvoiceRow> rows = new List<ArcCusInvoiceRow>();
            rows.Add(Row(11, "C900001", true));
            rows.Add(Row(12, "C900002", false));
            rows.Add(Row(13, "C900003", false));
            return rows;
        }

        static List<ArcCusInvoiceRow> NoDefault()
        {
            List<ArcCusInvoiceRow> rows = Sample();
            rows[0].Default = false;
            return rows;
        }

        static List<ArcCusInvoiceRow> Clone(List<ArcCusInvoiceRow> rows)
        {
            List<ArcCusInvoiceRow> list = new List<ArcCusInvoiceRow>();
            foreach (ArcCusInvoiceRow row in rows)
            {
                list.Add(row.Copy());
            }
            return list;
        }

        static ArcCusInvoiceRow Row(long id, string code, bool dflt)
        {
            ArcCusInvoiceRow row = new ArcCusInvoiceRow();
            row.Id = id;
            row.Code = code;
            row.Default = dflt;
            return row;
        }

        static ArcCusInvoiceAsk Ask(string company)
        {
            ArcCusInvoiceAsk ask = new ArcCusInvoiceAsk();
            ask.Customer = "C900001";
            ask.Company = company;
            ask.Code = "C900001:" + company;
            return ask;
        }

        static ArcCusInvoiceAsk AskDefault(string company)
        {
            ArcCusInvoiceAsk ask = Ask(company);
            ask.Default = true;
            return ask;
        }

        static ArcCusInvoiceAsk Del(string company, bool expectDefault)
        {
            ArcCusInvoiceAsk ask = Ask(company);
            ask.ExpDefault = expectDefault;
            return ask;
        }

        static Dictionary<string, object> Body(string code, object dflt, Dictionary<string, object> expect)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            if (code != null)
            {
                body["code"] = code;
            }
            if (dflt != null)
            {
                body["default"] = dflt;
            }
            if (expect != null)
            {
                body["expect"] = expect;
            }
            return body;
        }

        static Dictionary<string, object> BodyRaw(string code, object expect)
        {
            Dictionary<string, object> body = Body(code, null, null);
            body["expect"] = expect;
            return body;
        }

        static Dictionary<string, object> Exp(bool? dflt, string ts)
        {
            return ExpRaw(dflt.HasValue ? (object)dflt.Value : null, ts);
        }

        static Dictionary<string, object> ExpRaw(object dflt, object ts)
        {
            Dictionary<string, object> expect = new Dictionary<string, object>();
            expect["bdefault"] = dflt;
            if (ts != null)
            {
                expect["customer_ufts"] = ts;
            }
            return expect;
        }

        static Dictionary<string, object> Ts(string ts)
        {
            Dictionary<string, object> expect = new Dictionary<string, object>();
            expect["customer_ufts"] = ts;
            return expect;
        }

        static Dictionary<string, object> WithKey(Dictionary<string, object> map, string key, object value)
        {
            map[key] = value;
            return map;
        }

        static void Bad(string name, string op, Dictionary<string, object> body)
        {
            try
            {
                ArcCusInvoice.Parse(op, body);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400);
                return;
            }
            throw new InvalidOperationException("customer_invoice_company " + name);
        }

        static void Fail(string name, int status, string code, Action run)
        {
            try
            {
                run();
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == status && ex.Code == code);
                return;
            }
            throw new InvalidOperationException("customer_invoice_company " + name);
        }

        static void Eq(string name, object got, object want)
        {
            Expect(name, object.Equals(got, want));
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("customer_invoice_company " + name);
            }
        }
    }
}
