using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的客户收货地址新增 / 删除部分：请求解析、新增判重与幂等重放、默认地址取消、删除的 expect / 默认地址 / 不存在，
    // INSERT / DELETE / 取消默认的 SQL 生成，写后回读核对，响应体。引用检查本身（ArcCusAddrRefs）已在 ArcCusAddrSelfTest 里测。不连库。
    // 备注：cCusAddressGUID 一律由 INSERT 写 NEWID()；U8 界面新增地址时是否自己填这一列，要在泳道库里对照一条界面新增的行确认。
    internal static class ArcCusAddrRowsSelfTest
    {
        public static void Run()
        {
            CheckParseAdd();
            CheckParseAddRejects();
            CheckParseDelete();
            CheckParseDeleteRejects();
            CheckRoutes();
            CheckAddInsert();
            CheckAddReplay();
            CheckAddRejects();
            CheckAddDefault();
            CheckDelete();
            CheckDeleteRejects();
            CheckStatements();
            CheckMatches();
            CheckBodies();
        }

        static void CheckParseAdd()
        {
            ArcCusAddrRowsAsk ask = ArcCusAddrRows.ParseAdd(Body("C900001", "9", Fields("name", " 示例车间 ", "unit", "张三", "default", true), null));
            Eq("add code", ask.Code, "C900001");
            Eq("add address", ask.Address, "9");
            Eq("add name trimmed", ask.Name, "示例车间");
            Eq("add unit", ask.Unit, "张三");
            Eq("add default", ask.Default, true);
            ask = ArcCusAddrRows.ParseAdd(Body("C900001", "9", Fields("name", "示例车间二"), null));
            Eq("add unit default empty", ask.Unit, "");
            Eq("add default false", ask.Default, false);
            ask = ArcCusAddrRows.ParseAdd(Body("C900001", "9", Fields("name", "x", "unit", "  "), null));
            Eq("add unit blank", ask.Unit, "");
            ask = ArcCusAddrRows.ParseAdd(Body("C900001", "9", Fields("name", new string('N', 255)), null));
            Eq("add name max", ask.Name.Length, 255);
        }

        static void CheckParseAddRejects()
        {
            Dictionary<string, object> ok = Fields("name", "x");
            BadAdd("no code", Body(null, "9", ok, null));
            BadAdd("empty code", Body("", "9", ok, null));
            BadAdd("no address", Body("C900001", null, ok, null));
            BadAdd("long address", Body("C900001", new string('A', 31), ok, null));
            BadAdd("space address", Body("C900001", "9 ", ok, null));
            BadAdd("no fields", Body("C900001", "9", null, null));
            BadAdd("empty fields", Body("C900001", "9", new Dictionary<string, object>(), null));
            BadAdd("fields not object", BodyRaw("C900001", "9", "x", null));
            BadAdd("no name", Body("C900001", "9", Fields("unit", "u"), null));
            BadAdd("empty name", Body("C900001", "9", Fields("name", ""), null));
            BadAdd("blank name", Body("C900001", "9", Fields("name", "   "), null));
            BadAdd("null name", Body("C900001", "9", Fields("name", null), null));
            BadAdd("name type", Body("C900001", "9", Fields("name", 5), null));
            BadAdd("name long", Body("C900001", "9", Fields("name", new string('N', 256)), null));
            BadAdd("name control", Body("C900001", "9", Fields("name", "a\nb"), null));
            BadAdd("unit long", Body("C900001", "9", Fields("name", "x", "unit", new string('U', 256)), null));
            BadAdd("unit null", Body("C900001", "9", Fields("name", "x", "unit", null), null));
            BadAdd("default type", Body("C900001", "9", Fields("name", "x", "default", "true"), null));
            BadAdd("default number", Body("C900001", "9", Fields("name", "x", "default", 1), null));
            BadAdd("unknown key", Body("C900001", "9", Fields("name", "x", "cLinkPerson", "p"), null));
            BadAdd("code key", Body("C900001", "9", Fields("name", "x", "code", "10"), null));
        }

        static void CheckParseDelete()
        {
            ArcCusAddrRowsAsk ask = ArcCusAddrRows.ParseDelete(Body("C900001", "9", null,
                Fields("name", " 示例车间 ", "unit", "", "default", false)));
            Eq("del code", ask.Code, "C900001");
            Eq("del address", ask.Address, "9");
            Eq("del expect name trimmed", ask.ExpName, "示例车间");
            Eq("del expect unit empty", ask.ExpUnit, "");
            Eq("del expect default", ask.ExpDefault, false);
            ask = ArcCusAddrRows.ParseDelete(Body("C900001", "9", null, Fields("name", "", "unit", "u", "default", true)));
            Eq("del expect name empty", ask.ExpName, "");
            Eq("del expect default true", ask.ExpDefault, true);
        }

        static void CheckParseDeleteRejects()
        {
            Dictionary<string, object> ok = Fields("name", "x", "unit", "", "default", false);
            BadDel("no code", Body(null, "9", null, ok));
            BadDel("no address", Body("C900001", null, null, ok));
            BadDel("no expect", Body("C900001", "9", null, null));
            BadDel("expect not object", BodyRaw("C900001", "9", null, "x"));
            BadDel("no expect name", Body("C900001", "9", null, Fields("unit", "", "default", false)));
            BadDel("no expect unit", Body("C900001", "9", null, Fields("name", "x", "default", false)));
            BadDel("no expect default", Body("C900001", "9", null, Fields("name", "x", "unit", "")));
            BadDel("expect null name", Body("C900001", "9", null, Fields("name", null, "unit", "", "default", false)));
            BadDel("expect name type", Body("C900001", "9", null, Fields("name", 5, "unit", "", "default", false)));
            BadDel("expect default type", Body("C900001", "9", null, Fields("name", "x", "unit", "", "default", "no")));
            BadDel("expect default null", Body("C900001", "9", null, Fields("name", "x", "unit", "", "default", null)));
            BadDel("expect extra key", Body("C900001", "9", null, Fields("name", "x", "unit", "", "default", false, "code", "9")));
        }

        static void CheckRoutes()
        {
            Expect("is add", ArcCusAddrRows.Is("customer_address_add") && ArcCusAddrRows.Is(ArcCusAddrRows.DeleteOp));
            Expect("is other", !ArcCusAddrRows.Is("customer_address") && !ArcCusAddrRows.Is(null) && !ArcCusAddrRows.Is("update"));
            Eq("add path", ArcCusAddrRows.AddPath, Requests.ArcRoot + "customer_address_add");
            Eq("delete path", ArcCusAddrRows.DeletePath, Requests.ArcRoot + "customer_address_delete");
            Eq("add rule", ArcCusAddrRows.AddRuleKey, "write:archive:customer:customer_address_add");
            Eq("delete rule", ArcCusAddrRows.DeleteRuleKey, "write:archive:customer:customer_address_delete");
            ArcCusAddrRows.Check(ArcCusAddrRows.AddOp, Body("C900001", "9", Fields("name", "x"), null));
            ArcCusAddrRows.Check(ArcCusAddrRows.DeleteOp, Body("C900001", "9", null, Fields("name", "x", "unit", "", "default", false)));
            try
            {
                ArcCusAddrRows.Check("update", Body("C900001", "9", Fields("name", "x"), null));
            }
            catch (BridgeException ex)
            {
                Expect("check unknown op", ex.Status == 500);
                return;
            }
            throw new InvalidOperationException("check unknown op");
        }

        // 新增一行：只插一行、不碰别的行；新行排在最后、Id 未知。
        static void CheckAddInsert()
        {
            ArcCusAddrRowsPlan plan = ArcCusAddrRowsPlanner.MakeAdd(Add("9", "示例车间三", "", false), Sample());
            Expect("insert changed", plan.Changed && plan.Insert != null && plan.Remove == null && plan.Dirty.Count == 0);
            Expect("insert rows", plan.Before.Count == 3 && plan.After.Count == 4);
            Expect("insert last", plan.After[3] == plan.Insert && plan.Insert.Id == 0 && plan.Insert.Name == "示例车间三");
            Eq("insert address", plan.Address, "9");
            Expect("insert keeps default", plan.After[0].Default && !plan.After[3].Default);
            // 另一个新编码、带收货单位，照常新增。
            plan = ArcCusAddrRowsPlanner.MakeAdd(Add("10", "别的名称", "李四", false), Sample());
            Expect("insert other code", plan.Insert != null && plan.Insert.Unit == "李四");
        }

        // 幂等重放：同编码、同名称 / 单位 / 默认，不写；编码比较不分大小写，响应用库里存的编码。
        static void CheckAddReplay()
        {
            ArcCusAddrRowsPlan plan = ArcCusAddrRowsPlanner.MakeAdd(Add("2", "示例车间二", "张三", false), Sample());
            Expect("replay noop", !plan.Changed && plan.Insert == null && plan.Dirty.Count == 0);
            Expect("replay rows", plan.Before.Count == 3 && plan.After.Count == 3);
            plan = ArcCusAddrRowsPlanner.MakeAdd(Add("1", "3车间", "", true), Sample());
            Expect("replay default noop", !plan.Changed);
            plan = ArcCusAddrRowsPlanner.MakeAdd(Add("a", "甲", "", false), Mixed());
            Expect("replay case", !plan.Changed);
            Eq("replay stored address", plan.Address, "A");
        }

        static void CheckAddRejects()
        {
            FailAdd("taken diff name", 409, "address_code_taken", Add("2", "别的名称", "张三", false));
            FailAdd("taken diff unit", 409, "address_code_taken", Add("2", "示例车间二", "", false));
            FailAdd("taken diff default", 409, "address_code_taken", Add("2", "示例车间二", "张三", true));
            FailAdd("taken default now off", 409, "address_code_taken", Add("1", "3车间", "", false));
            FailAdd("taken case", 409, "address_code_taken", Add("B", "别的名称", "", false), Mixed());
            FailAdd("name taken", 409, "address_name_taken", Add("9", "办公室", "", false));
            FailAdd("name taken other unit", 409, "address_name_taken", Add("9", "办公室", "别人", true));
            // 编码已存在时先报编码冲突，不报名称冲突。
            FailAdd("code before name", 409, "address_code_taken", Add("3", "示例车间二", "", false));
            try
            {
                ArcCusAddrRowsPlanner.MakeAdd(Add("2", "别的名称", "张三", false), Sample());
            }
            catch (BridgeException ex)
            {
                Expect("taken msg", ex.Message.IndexOf("示例车间二", StringComparison.Ordinal) >= 0);
                return;
            }
            throw new InvalidOperationException("taken msg");
        }

        // 新增并设默认：已有的默认行取消默认（含数据已不一致、多行默认的情况），不设默认则不动别的行。
        static void CheckAddDefault()
        {
            ArcCusAddrRowsPlan plan = ArcCusAddrRowsPlanner.MakeAdd(Add("9", "新地址", "", true), Sample());
            Expect("def dirty", plan.Dirty.Count == 1 && plan.Dirty[0] == 0);
            Expect("def after", !plan.After[0].Default && plan.Before[0].Default && plan.After[3].Default);
            List<ArcCusAddrRow> rows = Sample();
            rows[2].Default = true;
            plan = ArcCusAddrRowsPlanner.MakeAdd(Add("9", "新地址", "", true), rows);
            Expect("def dirty both", plan.Dirty.Count == 2 && !plan.After[0].Default && !plan.After[2].Default);
            List<ArcCusAddrStmt> flips = ArcCusAddrRowsPlanner.Flips(plan, "C900001");
            Eq("def flip count", flips.Count, 2);
            Eq("def flip sql", flips[0].Sql, "UPDATE CusDeliverAdd SET bDefault=? WHERE id=? AND cCusCode=?");
            Eq("def flip value", flips[0].Args[0], 0);
            Eq("def flip id", flips[0].Args[1], 11);
            Eq("def flip id2", flips[1].Args[1], 13);
            plan = ArcCusAddrRowsPlanner.MakeAdd(Add("9", "新地址", "", false), Sample());
            Expect("not default keeps others", plan.Dirty.Count == 0 && plan.After[0].Default);
            // 客户还没有任何地址：直接插一行。
            plan = ArcCusAddrRowsPlanner.MakeAdd(Add("1", "第一个", "", true), new List<ArcCusAddrRow>());
            Expect("add into empty", plan.Insert != null && plan.Dirty.Count == 0 && plan.After.Count == 1);
        }

        static void CheckDelete()
        {
            ArcCusAddrRowsPlan plan = ArcCusAddrRowsPlanner.MakeDelete(Del("3", "办公室", "李四", false), Sample());
            Expect("del changed", plan.Changed && plan.Remove != null && plan.Insert == null && plan.Dirty.Count == 0);
            Expect("del rows", plan.Before.Count == 3 && plan.After.Count == 2 && plan.Remove.Id == 13);
            Expect("del after codes", plan.After[0].Code == "1" && plan.After[1].Code == "2");
            Eq("del address", plan.Address, "3");
            // 删中间行，后面各行 Id 与顺序不变。
            plan = ArcCusAddrRowsPlanner.MakeDelete(Del("2", "示例车间二", "张三", false), Sample());
            Expect("del middle", plan.After.Count == 2 && plan.After[1].Id == 13);
            Expect("del matches", ArcCusAddrPlanner.Matches(plan.After, Clone(plan.After)));
            // 只剩这一行且是默认地址：允许。
            List<ArcCusAddrRow> single = new List<ArcCusAddrRow>();
            single.Add(Row(31, "1", "唯一", "", true));
            plan = ArcCusAddrRowsPlanner.MakeDelete(Del("1", "唯一", "", true), single);
            Expect("del only default", plan.Remove != null && plan.After.Count == 0);
            // 编码不分大小写，响应用库里存的编码。
            plan = ArcCusAddrRowsPlanner.MakeDelete(Del("a", "甲", "", false), Mixed());
            Eq("del stored address", plan.Address, "A");
        }

        static void CheckDeleteRejects()
        {
            FailDel("del not found", 404, "address_not_found", Del("9", "x", "", false));
            FailDel("del name mismatch", 409, "state_mismatch", Del("3", "错的", "李四", false));
            FailDel("del unit mismatch", 409, "state_mismatch", Del("3", "办公室", "", false));
            FailDel("del default mismatch", 409, "state_mismatch", Del("3", "办公室", "李四", true));
            FailDel("del is default", 409, "address_is_default", Del("1", "3车间", "", true));
            try
            {
                ArcCusAddrRowsPlanner.MakeDelete(Del("3", "错的", "李四", true), Sample());
            }
            catch (BridgeException ex)
            {
                Expect("mismatch lists fields", ex.Message.IndexOf("地址名称", StringComparison.Ordinal) >= 0
                    && ex.Message.IndexOf("默认地址", StringComparison.Ordinal) >= 0
                    && ex.Message.IndexOf("收货单位", StringComparison.Ordinal) < 0);
                CheckDefaultAfterMismatch();
                return;
            }
            throw new InvalidOperationException("mismatch lists fields");
        }

        // expect 不符先于默认地址拒绝：别人改过的行不该被「是默认」的提示误导。
        static void CheckDefaultAfterMismatch()
        {
            FailDel("mismatch before default", 409, "state_mismatch", Del("1", "错的", "", true));
            FailDel("not default mismatch", 409, "state_mismatch", Del("1", "3车间", "", false));
        }

        static void CheckStatements()
        {
            ArcCusAddrRowsPlan plan = ArcCusAddrRowsPlanner.MakeAdd(Add("9", "示例车间三", "", true), Sample());
            ArcCusAddrStmt insert = ArcCusAddrRowsPlanner.Insert(plan, "C900001");
            Eq("insert sql", insert.Sql, "INSERT INTO CusDeliverAdd (cCusCode, cAddCode, cDeliverAdd, cDeliverUnit, bDefault,"
                + " cCusAddressGUID) VALUES (?, ?, ?, ?, ?, NEWID())");
            Eq("insert args", insert.Args.Length, 5);
            Eq("insert customer", insert.Args[0], "C900001");
            Eq("insert code", insert.Args[1], "9");
            Eq("insert name", insert.Args[2], "示例车间三");
            Eq("insert empty unit is null", insert.Args[3], null);
            Eq("insert default", insert.Args[4], 1);
            plan = ArcCusAddrRowsPlanner.MakeAdd(Add("10", "有单位", "张三", false), Sample());
            insert = ArcCusAddrRowsPlanner.Insert(plan, "C900001");
            Eq("insert unit", insert.Args[3], "张三");
            Eq("insert not default", insert.Args[4], 0);
            plan = ArcCusAddrRowsPlanner.MakeDelete(Del("3", "办公室", "李四", false), Sample());
            ArcCusAddrStmt delete = ArcCusAddrRowsPlanner.Delete(plan, "C900001");
            Eq("delete sql", delete.Sql, "DELETE FROM CusDeliverAdd WHERE id=? AND cCusCode=?");
            Eq("delete id", delete.Args[0], 13);
            Eq("delete customer", delete.Args[1], "C900001");
            Eq("delete args", delete.Args.Length, 2);
        }

        // 新增后的回读核对：新行 Id 未知，按编码找；已有行 Id 与字段都要一致。
        static void CheckMatches()
        {
            ArcCusAddrRowsPlan plan = ArcCusAddrRowsPlanner.MakeAdd(Add("9", "示例车间三", "", true), Sample());
            List<ArcCusAddrRow> actual = Clone(plan.After);
            actual[3].Id = 14;
            Expect("add matches", ArcCusAddrRowsPlanner.MatchesAdd(plan.After, actual));
            // 新行 Id 不在末尾（自增被重置）也认。
            List<ArcCusAddrRow> reorder = new List<ArcCusAddrRow>();
            ArcCusAddrRow first = actual[3].Copy();
            first.Id = 5;
            reorder.Add(first);
            reorder.AddRange(actual.GetRange(0, 3));
            Expect("add matches reordered", ArcCusAddrRowsPlanner.MatchesAdd(plan.After, reorder));
            Expect("add matches needs id", !ArcCusAddrRowsPlanner.MatchesAdd(plan.After, Clone(plan.After)));
            List<ArcCusAddrRow> other = Clone(actual);
            other[3].Name = "别的";
            Expect("add matches name differs", !ArcCusAddrRowsPlanner.MatchesAdd(plan.After, other));
            other = Clone(actual);
            other[0].Default = true;
            Expect("add matches default not cleared", !ArcCusAddrRowsPlanner.MatchesAdd(plan.After, other));
            other = Clone(actual);
            other.RemoveAt(3);
            Expect("add matches count", !ArcCusAddrRowsPlanner.MatchesAdd(plan.After, other));
            other = Clone(actual);
            other[1].Id = 99;
            Expect("add matches id", !ArcCusAddrRowsPlanner.MatchesAdd(plan.After, other));
            other = Clone(actual);
            other[3].Code = "8";
            Expect("add matches missing code", !ArcCusAddrRowsPlanner.MatchesAdd(plan.After, other));
            Expect("refs msg", ArcCusAddrRefs.Message(new List<string>(new string[] { "A", "B" }), "删除")
                == "收货地址已被单据引用，不能删除：A、B");
            Expect("refs msg recode unchanged", ArcCusAddrRefs.Message(new List<string>(new string[] { "A", "B" }))
                == "收货地址已被单据引用，不能改编码：A、B");
        }

        static void CheckBodies()
        {
            ArcCusAddrRowsAsk ask = Add("9", "示例车间三", "", true);
            ArcCusAddrRowsPlan plan = ArcCusAddrRowsPlanner.MakeAdd(ask, Sample());
            Dictionary<string, object> body = ArcCusAddrRows.OkBody(ask, plan, true);
            Eq("ok flag", body["ok"], true);
            Eq("ok code", body["code"], "C900001");
            Eq("ok address", body["address"], "9");
            Eq("ok changed", body["changed"], true);
            Eq("ok before rows", ((List<object>)body["before"]).Count, 3);
            Eq("ok after rows", ((List<object>)body["after"]).Count, 4);
            Eq("ok keys", body.Count, 6);
            Dictionary<string, object> row = (Dictionary<string, object>)((List<object>)body["after"])[3];
            Eq("ok row code", row["code"], "9");
            Eq("ok row name", row["name"], "示例车间三");
            Eq("ok row unit", row["unit"], "");
            Eq("ok row default", row["default"], true);
            Eq("ok row keys", row.Count, 4);
            Eq("ok before kept default", ((Dictionary<string, object>)((List<object>)body["before"])[0])["default"], true);
            Eq("ok after cleared default", ((Dictionary<string, object>)((List<object>)body["after"])[0])["default"], false);
            CheckBodyOther(ask, plan);
        }

        static void CheckBodyOther(ArcCusAddrRowsAsk ask, ArcCusAddrRowsPlan plan)
        {
            Dictionary<string, object> body = ArcCusAddrRows.DryBody(ask, plan);
            Eq("dry flag", body["dry_run"], true);
            Eq("dry mode", body["mode"], DryRunModes.Validate);
            Eq("dry changed", body["changed"], true);
            Eq("dry after rows", ((List<object>)body["after"]).Count, 4);
            // 幂等重放：changed:false，before 与 after 相同。
            ArcCusAddrRowsAsk again = Add("2", "示例车间二", "张三", false);
            ArcCusAddrRowsPlan noop = ArcCusAddrRowsPlanner.MakeAdd(again, Sample());
            body = ArcCusAddrRows.OkBody(again, noop, false);
            Eq("noop changed", body["changed"], false);
            Eq("noop after rows", ((List<object>)body["after"]).Count, 3);
            Eq("noop address", body["address"], "2");
            Eq("noop dry changed", ArcCusAddrRows.DryBody(again, noop)["changed"], false);
            ArcCusAddrRowsAsk del = Del("3", "办公室", "李四", false);
            ArcCusAddrRowsPlan gone = ArcCusAddrRowsPlanner.MakeDelete(del, Sample());
            body = ArcCusAddrRows.OkBody(del, gone, true);
            Eq("del ok address", body["address"], "3");
            Eq("del ok before rows", ((List<object>)body["before"]).Count, 3);
            Eq("del ok after rows", ((List<object>)body["after"]).Count, 2);
            Eq("del dry after rows", ((List<object>)ArcCusAddrRows.DryBody(del, gone)["after"]).Count, 2);
        }

        // 样本：同一客户三行；行 0 是默认地址（编码 1，id 11），行 1 编码 2（id 12），行 2 编码 3（id 13）。
        static List<ArcCusAddrRow> Sample()
        {
            List<ArcCusAddrRow> rows = new List<ArcCusAddrRow>();
            rows.Add(Row(11, "1", "3车间", "", true));
            rows.Add(Row(12, "2", "示例车间二", "张三", false));
            rows.Add(Row(13, "3", "办公室", "李四", false));
            return rows;
        }

        static List<ArcCusAddrRow> Mixed()
        {
            List<ArcCusAddrRow> rows = new List<ArcCusAddrRow>();
            rows.Add(Row(21, "A", "甲", "", false));
            rows.Add(Row(22, "b", "乙", "", false));
            return rows;
        }

        static ArcCusAddrRow Row(int id, string code, string name, string unit, bool dflt)
        {
            ArcCusAddrRow row = new ArcCusAddrRow();
            row.Id = id;
            row.Code = code;
            row.Name = name;
            row.Unit = unit;
            row.Default = dflt;
            return row;
        }

        static List<ArcCusAddrRow> Clone(List<ArcCusAddrRow> rows)
        {
            List<ArcCusAddrRow> list = new List<ArcCusAddrRow>();
            foreach (ArcCusAddrRow row in rows)
            {
                list.Add(row.Copy());
            }
            return list;
        }

        static ArcCusAddrRowsAsk Add(string address, string name, string unit, bool dflt)
        {
            ArcCusAddrRowsAsk ask = new ArcCusAddrRowsAsk();
            ask.Code = "C900001";
            ask.Address = address;
            ask.Name = name;
            ask.Unit = unit;
            ask.Default = dflt;
            return ask;
        }

        static ArcCusAddrRowsAsk Del(string address, string name, string unit, bool dflt)
        {
            ArcCusAddrRowsAsk ask = new ArcCusAddrRowsAsk();
            ask.Code = "C900001";
            ask.Address = address;
            ask.ExpName = name;
            ask.ExpUnit = unit;
            ask.ExpDefault = dflt;
            return ask;
        }

        static Dictionary<string, object> Fields(params object[] pairs)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            for (int i = 0; i + 1 < pairs.Length; i += 2)
            {
                map[(string)pairs[i]] = pairs[i + 1];
            }
            return map;
        }

        static Dictionary<string, object> Body(string code, string address, Dictionary<string, object> fields,
            Dictionary<string, object> expect)
        {
            return BodyRaw(code, address, fields, expect);
        }

        static Dictionary<string, object> BodyRaw(string code, string address, object fields, object expect)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            if (code != null)
            {
                body["code"] = code;
            }
            if (address != null)
            {
                body["address"] = address;
            }
            if (fields != null)
            {
                body["fields"] = fields;
            }
            if (expect != null)
            {
                body["expect"] = expect;
            }
            return body;
        }

        static void BadAdd(string name, Dictionary<string, object> body)
        {
            try
            {
                ArcCusAddrRows.ParseAdd(body);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Code == "bad_request");
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void BadDel(string name, Dictionary<string, object> body)
        {
            try
            {
                ArcCusAddrRows.ParseDelete(body);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Code == "bad_request");
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void FailAdd(string name, int status, string code, ArcCusAddrRowsAsk ask)
        {
            FailAdd(name, status, code, ask, Sample());
        }

        static void FailAdd(string name, int status, string code, ArcCusAddrRowsAsk ask, List<ArcCusAddrRow> rows)
        {
            try
            {
                ArcCusAddrRowsPlanner.MakeAdd(ask, rows);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == status && ex.Code == code);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void FailDel(string name, int status, string code, ArcCusAddrRowsAsk ask)
        {
            try
            {
                ArcCusAddrRowsPlanner.MakeDelete(ask, Sample());
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == status && ex.Code == code);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void Eq(string name, object got, object want)
        {
            Expect(name, object.Equals(got, want));
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("customer_address_rows " + name);
            }
        }
    }
}
