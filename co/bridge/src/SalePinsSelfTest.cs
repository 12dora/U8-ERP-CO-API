using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的 r50 约定字段部分（SalePins、SaleExtraRead）：白名单（SoFields、SaleGen 发货单表头）、
    // 不进 DOM 的规则、请求收集（新增 / 修改 / 生单、重复与类型拒绝）、类型长度校验、回读比较、补写 SQL 形状、
    // 扩展表并入 load。不连库。
    internal static class SalePinsSelfTest
    {
        public static void Run()
        {
            CheckWhitelist();
            CheckOffDom();
            CheckCollectCreate();
            CheckCollectEdit();
            CheckCollectDispatch();
            CheckCollectRejects();
            CheckValues();
            CheckSame();
            CheckSql();
            CheckMerge();
            CheckCalc();
            CheckDates();
            CheckGroups();
            CheckGroupLines();
            CheckSupplied();
            CheckProduct();
        }

        static void CheckWhitelist()
        {
            Expect("so head gathering", SoFields.Allowed("cgatheringplan", true));
            Expect("so head addcode", SoFields.Allowed("caddcode", true));
            Expect("so head define2", SoFields.Allowed("cdefine2", true));
            Expect("so line wh", SoFields.Allowed("cwhcode", false));
            Expect("so line bdefine5", SoFields.Allowed("cbdefine5", false));
            Expect("so line unit", SoFields.Allowed("cunitid", false) && SoFields.Allowed("inum", false));
            Expect("so line bdefine6 refused", !SoFields.Allowed("cbdefine6", false));
            Expect("so head hdefine refused", !SoFields.Allowed("chdefine1", true));
            Expect("disp head addcode", SaleGen.MetaDispatch("caddcode", true));
            for (int n = 1; n <= 4; n++)
            {
                Expect("disp head hdefine" + n, SaleGen.MetaDispatch("chdefine" + n, true));
            }
            Expect("disp head hdefine5 refused", !SaleGen.MetaDispatch("chdefine5", true));
            Expect("disp head define1", SaleGen.MetaDispatch("cdefine1", true));
            Expect("invoice head hdefine refused", !SaleGen.MetaInvoice("chdefine1", true));
            Expect("invoice head addcode refused", !SaleGen.MetaInvoice("caddcode", true));
        }

        static void CheckOffDom()
        {
            Expect("off extra", SalePins.OffDom(SalePins.Order, "cbdefine5", false, true));
            Expect("on base in template", !SalePins.OffDom(SalePins.Order, "cwhcode", false, true));
            Expect("off base missing", SalePins.OffDom(SalePins.Order, "cwhcode", false, false));
            Expect("not pinned stays", !SalePins.OffDom(SalePins.Order, "cmemo", true, false));
            Expect("order has no hdefine", !SalePins.OffDom(SalePins.Order, "chdefine1", true, true));
            Expect("disp extra", SalePins.OffDom(SalePins.Dispatch, "chdefine3", true, true));
            Expect("disp addcode on", !SalePins.OffDom(SalePins.Dispatch, "caddcode", true, true));
            Eq("key so head", SalePins.KeyOf("SO_SOMain"), "ID");
            Eq("key so line extra", SalePins.KeyOf("SO_SODetails_extradefine"), "iSOsID");
            Eq("key disp extra", SalePins.KeyOf("DispatchList_extradefine"), "DLID");
        }

        static void CheckCollectCreate()
        {
            Dictionary<string, object> head = Map("ccuscode", "C900001", "cAddCode", " A01 ", "cMemo", "x");
            head["cgatheringplan"] = "";
            Dictionary<string, object> line = Map("cinvcode", "A01", "cbdefine5", "型号X", "inum", 2);
            SalePins pins = SalePins.Collect(SalePins.Order, head, new object[] { line }, false);
            Eq("create count", pins.Items.Count, 3);
            Eq("create customer", pins.Customer, "C900001");
            Eq("create adds", pins.Adds, 1);
            SalePin addr = Pin(pins, "head.caddcode");
            Eq("create addr value", addr.Value, "A01");
            Eq("create addr table", addr.Table, "SO_SOMain");
            Eq("create addr line", addr.Line, -1);
            SalePin model = Pin(pins, "lines.0.cbdefine5");
            Eq("create extra table", model.Table, "SO_SODetails_extradefine");
            Eq("create extra key", model.KeyColumn, "iSOsID");
            Eq("create extra line", model.Line, 0);
            Eq("create extra inv", model.Inv, "A01");
            Eq("create num text", Pin(pins, "lines.0.inum").Value, "2");
            Expect("create blank skipped", Pin(pins, "head.cgatheringplan") == null);
            pins.MarkDom(delegate(string name) { return name == "caddcode"; }, delegate(string name) { return true; });
            Expect("mark on", addr.OnDom && !addr.Explicit);
            Expect("mark extra explicit", !model.OnDom && model.Explicit);
        }

        static void CheckCollectEdit()
        {
            Dictionary<string, object> upd = Map("op", "update", "line_id", 7, "cwhcode", "01");
            Dictionary<string, object> del = Map("op", "delete", "line_id", 8);
            Dictionary<string, object> add = Map("op", "add", "cinvcode", "A02", "cbdefine5", "");
            SalePins pins = SalePins.Collect(SalePins.Order, null, new object[] { upd, del, add }, true);
            Eq("edit count", pins.Items.Count, 2);
            Eq("edit adds", pins.Adds, 1);
            SalePin wh = Pin(pins, "lines.0.cwhcode");
            Eq("edit update key", wh.Key, 7);
            Eq("edit update line", wh.Line, -1);
            SalePin clear = Pin(pins, "lines.2.cbdefine5");
            Eq("edit add line", clear.Line, 0);
            Eq("edit clear kept", clear.Value, "");
            Eq("edit add inv", clear.Inv, "A02");
        }

        static void CheckCollectDispatch()
        {
            Dictionary<string, object> head = Map("chdefine3", 12.5m, "caddcode", "A01", "ddate", "2026-01-15");
            head["cDefine1"] = "HT001";
            SalePins pins = SalePins.Collect(SalePins.Dispatch, head, null, false);
            Eq("disp count", pins.Items.Count, 3);
            Eq("disp extra value", Pin(pins, "head.chdefine3").Value, "12.5");
            Eq("disp extra table", Pin(pins, "head.chdefine3").Table, "DispatchList_extradefine");
            Eq("disp define column", Pin(pins, "head.cdefine1").Column, "cDefine1");
        }

        static void CheckCollectRejects()
        {
            Dictionary<string, object> dup = Map("caddcode", "A", "CADDCODE", "B", "x", "y");
            Bad("dup", delegate { SalePins.Collect(SalePins.Order, dup, null, false); });
            Dictionary<string, object> nested = Map("caddcode", new object[0], "x", "y", "z", "w");
            Bad("nested", delegate { SalePins.Collect(SalePins.Order, nested, null, false); });
            // 不是约定字段的值不归这里管（SoFields / SaleGen 自己拒绝）。
            Dictionary<string, object> other = Map("cmemo", new object[0], "x", "y", "z", "w");
            Eq("other ignored", SalePins.Collect(SalePins.Order, other, null, false).Items.Count, 0);
        }

        static void CheckValues()
        {
            Eq("kind nvarchar", SalePins.KindOf("nvarchar"), "text");
            Eq("kind decimal", SalePins.KindOf("Decimal"), "number");
            Eq("kind datetime", SalePins.KindOf("datetime"), "date");
            Eq("kind image", SalePins.KindOf("image"), "");
            SalePins.CheckValue(Typed("text", 4, "ABCD"));
            SalePins.CheckValue(Typed("text", -1, new string('A', 900)));
            SalePins.CheckValue(Typed("number", 0, "-12.5"));
            SalePins.CheckValue(Typed("number", 0, "1E+01"));
            SalePins.CheckValue(Typed("number", 0, "1e3"));
            SalePins.CheckValue(Typed("date", 0, "2026-01-15"));
            SalePins.CheckValue(Typed("number", 0, ""));
            Bad("too long", delegate { SalePins.CheckValue(Typed("text", 4, "ABCDE")); });
            Bad("not number", delegate { SalePins.CheckValue(Typed("number", 0, "x")); });
            Bad("not date", delegate { SalePins.CheckValue(Typed("date", 0, "2026/01/15")); });
            Bad("bad kind", delegate { SalePins.CheckValue(Typed("", 0, "x")); });
        }

        static void CheckSame()
        {
            Expect("same num scale", SalePins.Same("number", "2", "2.000000"));
            Expect("same num exponent", SalePins.Same("number", "1E+01", "10"));
            Expect("diff num", !SalePins.Same("number", "2", "2.5"));
            Expect("same date", SalePins.Same("date", "2026-01-15", "2026-01-15 10:00:00"));
            Expect("same text case", SalePins.Same("text", " a01 ", "A01"));
            Expect("both blank", SalePins.Same("text", "", ""));
            Expect("sent blank stored", !SalePins.Same("text", "", "x"));
            Expect("stored blank", !SalePins.Same("text", "x", " "));
        }

        static void CheckSql()
        {
            SalePin baseCol = Typed("text", 30, "01");
            baseCol.Table = "SO_SODetails";
            baseCol.Column = "cWhCode";
            baseCol.KeyColumn = "iSOsID";
            baseCol.Key = 42;
            Eq("base write", SalePins.WriteSql(baseCol), "UPDATE [SO_SODetails] SET [cWhCode]=? WHERE [iSOsID]=?");
            object[] args = SalePins.WriteArgs(baseCol);
            Expect("base args", args.Length == 2 && (string)args[0] == "01" && (int)args[1] == 42);
            Eq("base read", SalePins.ReadSql(baseCol), "SELECT [cWhCode] AS v FROM [SO_SODetails] WHERE [iSOsID]=?");
            SalePin extra = Typed("text", 120, "");
            extra.Table = "DispatchList_extradefine";
            extra.Column = "chdefine3";
            extra.KeyColumn = "DLID";
            extra.Key = 9;
            Eq("extra write", SalePins.WriteSql(extra), "IF EXISTS (SELECT 1 FROM [DispatchList_extradefine] WHERE [DLID]=?) "
                + "UPDATE [DispatchList_extradefine] SET [chdefine3]=? WHERE [DLID]=? "
                + "ELSE INSERT INTO [DispatchList_extradefine] ([DLID], [chdefine3]) VALUES (?, ?)");
            args = SalePins.WriteArgs(extra);
            Expect("extra args clear", args.Length == 5 && (int)args[0] == 9 && args[1] == null && args[4] == null);
        }

        static void CheckMerge()
        {
            Dictionary<string, object> target = new Dictionary<string, object>();
            target["cbdefine5"] = "";
            target["cmemo"] = "m";
            Dictionary<string, object> extra = new Dictionary<string, object>();
            extra["iSOsID"] = "9";
            extra["cbdefine5"] = "X";
            extra["cbdefine6"] = "Y";
            extra["CMEMO"] = "z";
            SaleExtraRead.MergeRow(target, extra, "iSOsID");
            Eq("merge fills blank", target["cbdefine5"], "X");
            Eq("merge adds lower", target["cbdefine6"], "Y");
            Eq("merge keeps value", target["cmemo"], "m");
            Expect("merge skips key", !target.ContainsKey("isosid") && !target.ContainsKey("iSOsID"));
            Expect("merge kinds", SaleExtraRead.Find(Kinds.Find("sale_order")) != null
                && SaleExtraRead.Find(Kinds.Find("dispatch")) != null && SaleExtraRead.Find(Kinds.Find("sale_invoice")) == null);
        }

        // 只走组件的字段（cunitid、inum、cgatheringplan）：模板没有时不进「桥补写」，MarkDom 400；从不 Explicit。
        static void CheckCalc()
        {
            Expect("calc unit not off", !SalePins.OffDom(SalePins.Order, "cunitid", false, false));
            Expect("calc num not off", !SalePins.OffDom(SalePins.Order, "inum", false, false));
            Expect("calc plan not off", !SalePins.OffDom(SalePins.Order, "cgatheringplan", true, false));
            Expect("annot addr off", SalePins.OffDom(SalePins.Order, "caddcode", true, false));
            Dictionary<string, object> line = Map("cinvcode", "A01", "inum", 7, "cwhcode", "01");
            SalePins pins = SalePins.Collect(SalePins.Order, null, new object[] { line }, false);
            Expect("calc flag", Pin(pins, "lines.0.inum").Calc && !Pin(pins, "lines.0.cwhcode").Calc);
            Bad("calc missing in template", delegate
            {
                pins.MarkDom(null, delegate(string name) { return name != "inum" && name != "cwhcode"; });
            });
            pins = SalePins.Collect(SalePins.Order, null, new object[] { line }, false);
            pins.MarkDom(null, delegate(string name) { return name == "inum"; });
            Expect("calc on dom not explicit", !Pin(pins, "lines.0.inum").Explicit);
            Expect("annot missing explicit", Pin(pins, "lines.0.cwhcode").Explicit);
            List<string[]> head = SalePins.BaseColumns(SalePins.Order, true);
            List<string[]> body = SalePins.BaseColumns(SalePins.Order, false);
            Eq("base head count", head.Count, 4);
            Eq("base body count", body.Count, 3);
            Expect("base body no extra", body.TrueForAll(delegate(string[] c) { return c[0] == "SO_SODetails"; }));
            Eq("base disp head", SalePins.BaseColumns(SalePins.Dispatch, true).Count, 3);
            Eq("base disp body", SalePins.BaseColumns(SalePins.Dispatch, false).Count, 0);
        }

        // 日期列：只收 yyyy-MM-dd，写入显式 CONVERT（UPDATE 与 INSERT 都是）。
        static void CheckDates()
        {
            Bad("date with time", delegate { SalePins.CheckValue(Typed("date", 0, "2026-01-15 10:00:00")); });
            SalePin d = Typed("date", 0, "2026-11-20");
            d.Col.Type = "datetime";
            d.Table = "DispatchList_extradefine";
            d.Column = "chdefine3";
            d.KeyColumn = "DLID";
            Eq("datetime expr", SalePins.ValueExpr(d.Col), "CONVERT(datetime, CONVERT(date, ?, 23))");
            Eq("datetime write", SalePins.WriteSql(d), "IF EXISTS (SELECT 1 FROM [DispatchList_extradefine] WHERE [DLID]=?) "
                + "UPDATE [DispatchList_extradefine] SET [chdefine3]=CONVERT(datetime, CONVERT(date, ?, 23)) WHERE [DLID]=? "
                + "ELSE INSERT INTO [DispatchList_extradefine] ([DLID], [chdefine3]) VALUES (?, CONVERT(datetime, CONVERT(date, ?, 23)))");
            d.Col.Type = "datetime2";
            Eq("datetime2 expr", SalePins.ValueExpr(d.Col), "CAST(CONVERT(date, ?, 23) AS datetime2)");
            SalePin old = Typed("date", 0, "1600-01-01");
            old.Col.Type = "datetime2";
            SalePins.CheckValue(old);
            old.Col.Type = "datetime";
            Bad("datetime range", delegate { SalePins.CheckValue(old); });
            old.Col.Type = "smalldatetime";
            Bad("smalldatetime range", delegate { SalePins.CheckValue(old); });
            d.Col.Type = "date";
            Eq("date expr", SalePins.ValueExpr(d.Col), "CONVERT(date, ?, 23)");
            Eq("text expr", SalePins.ValueExpr(Typed("text", 10, "x").Col), "?");
            Eq("no col expr", SalePins.ValueExpr(null), "?");
        }

        // 整组核对（纯函数）：单计量放行；固定换算比单位档案的换算率；单位须在组内、换算率大于 0。
        static void CheckGroups()
        {
            SaleDigits dig = Digits(2, 2);
            Eq("single unit ok", SalePins.GroupProblem(Qty(100m, 0m, 0m, 0, 0m, false), dig), "");
            Eq("fixed ok", SalePins.GroupProblem(Qty(100m, 10m, 10m, 1, 10m, true), dig), "");
            Expect("fixed rate off", SalePins.GroupProblem(Qty(100m, 10m, 10m, 1, 12m, true), dig).Length > 0);
            Expect("fixed rate-only change", SalePins.GroupProblem(Qty(100m, 10m, 20m, 1, 10m, true), dig).Length > 0);
            Expect("fixed no rate", SalePins.GroupProblem(Qty(100m, 10m, 0m, 1, 10m, true), dig).Length > 0);
            Expect("fixed archive without rate", SalePins.GroupProblem(Qty(100m, 10m, 10m, 1, 0m, true), dig).Length > 0);
            Expect("unit outside group", SalePins.GroupProblem(Qty(100m, 10m, 10m, 1, 10m, false), dig).Length > 0);
            Eq("floating ok", SalePins.GroupProblem(Qty(100m, 10m, 10m, 2, 0m, true), dig), "");
            Eq("floating own rate ok", SalePins.GroupProblem(Qty(100m, 12.5m, 8m, 2, 10m, true), dig), "");
            Expect("floating no rate", SalePins.GroupProblem(Qty(100m, 10m, 0m, 2, 0m, true), dig).Length > 0);
            Expect("floating outside group", SalePins.GroupProblem(Qty(100m, 10m, 10m, 2, 0m, false), dig).Length > 0);
        }

        // 请求给的数量、件数、换算率必须原样落库；计量组可能变的行才收集。
        static void CheckGroupLines()
        {
            SaleDigits dig = Digits(2, 2);
            SaleQtyLine g = new SaleQtyLine();
            g.Qty = "100";
            g.Num = "7";
            Expect("qty replaced by num", SalePins.LineProblem(g, Qty(70m, 7m, 10m, 1, 10m, true), dig).Length > 0);
            Expect("num replaced by qty", SalePins.LineProblem(g, Qty(100m, 10m, 10m, 1, 10m, true), dig).Length > 0);
            g.Num = null;
            g.Rate = "9";
            Expect("rate replaced", SalePins.LineProblem(g, Qty(100m, 10m, 10m, 2, 10m, true), dig).Length > 0);
            g.Qty = null;
            Eq("rate held", SalePins.LineProblem(g, Qty(100m, 11.11m, 9m, 2, 10m, true), dig), "");
            Dictionary<string, object> rate = Map("op", "update", "line_id", 101, "iinvexchrate", 20);
            Dictionary<string, object> unit = Map("op", "update", "line_id", 102, "cunitid", "U2");
            Dictionary<string, object> memo = Map("op", "update", "line_id", 103, "cmemo", "x");
            Dictionary<string, object> add = Map("op", "add", "cinvcode", "A01", "iquantity", 5);
            SalePins pins = SalePins.Collect(SalePins.Order, null, new object[] { rate, unit, memo, add }, true);
            Eq("group count", pins.Groups.Count, 3);
            Eq("group rate-only key", pins.Groups[0].Key, 101);
            Eq("group rate-only value", pins.Groups[0].Rate, "20");
            Expect("group rate-only no qty", pins.Groups[0].Qty == null && pins.Groups[0].Line == -1);
            Eq("group unit-only key", pins.Groups[1].Key, 102);
            Eq("group add slot", pins.Groups[2].Line, 0);
            Eq("group add qty", pins.Groups[2].Qty, "5");
            Eq("group add path", pins.Groups[2].Path, "lines.3");
            Dictionary<string, object> line = Map("cinvcode", "A01", "iquantity", 10);
            Eq("create every line", SalePins.Collect(SalePins.Order, null, new object[] { line, line }, false).Groups.Count, 2);
            Eq("dispatch no group", SalePins.Collect(SalePins.Dispatch, Map("ddate", "2026-01-15", "cmemo", "x"), null, false).Groups.Count, 0);
            Expect("touches rate", SalePins.TouchesGroup(Map("iinvexchrate", 2, "cmemo", "x")));
            Expect("memo only", !SalePins.TouchesGroup(Map("cmemo", "x", "cbdefine5", "y")));
        }

        // 保存前：数量、件数、换算率都给了且不成一套 400（纯判断部分；容差只取数量小数位半个单位，无额外 epsilon）。
        static void CheckSupplied()
        {
            Expect("triple ok", SalePins.Fits(100m, 10m, 10m, Digits(2, 4)));
            Expect("triple off", !SalePins.Fits(100m, 7m, 10m, Digits(2, 4)));
            Expect("6/1/10 refused", !SalePins.Fits(6m, 1m, 10m, Digits(2, 0)));
            Expect("51/1e-4/1e6 refused", !SalePins.Fits(51m, 0.0001m, 1000000m, Digits(2, 4)));
            Expect("1.000001/1/1 refused", !SalePins.Fits(1.000001m, 1m, 1m, Digits(6, 6)));
        }

        // 请求给了两个或三个用数量小数位半个单位（无额外容差）；只给一个时舍入后须与落库值相等；指数与计算路径同一解析。
        static void CheckProduct()
        {
            SaleDigits d2 = Digits(2, 0);
            SaleDigits d4 = Digits(2, 4);
            SaleDigits d22 = Digits(2, 2);
            SaleDigits d6 = Digits(6, 6);
            Expect("qty from num rounded", SalePins.Fits(33.33m, 11.11m, 3m, d22));
            Expect("qty precision", !SalePins.Fits(100.01m, 10m, 10m, d4));
            CheckReviewTuples(d2, d4, d22, d6);
            Expect("explicit 6/1/10", SalePins.LineProblem(Sent("6", "1", "10"),
                Qty(6m, 1m, 10m, 1, 10m, true), d2).Length > 0);
            Expect("fixed num off", SalePins.LineProblem(Sent("100", "7", "10"),
                Qty(100m, 7m, 10m, 1, 10m, true), d22).Length > 0);
            Expect("floating 100/7/9", SalePins.LineProblem(Sent("100", "7", "9"),
                Qty(100m, 7m, 9m, 2, 0m, true), d22).Length > 0);
            Expect("floating 100/10/9", SalePins.LineProblem(Sent("100", "10", "9"),
                Qty(100m, 10m, 9m, 2, 10m, true), d22).Length > 0);
            Expect("archive rate off", SalePins.LineProblem(Sent("100", "20", "5"),
                Qty(100m, 20m, 5m, 1, 10m, true), d4).Length > 0);
            Eq("single unit exponent", SalePins.LineProblem(Sent("1E-06", null, null),
                Qty(0.000001m, 0m, 0m, 0, 0m, false), d4), "");
            Eq("single unit qty", SalePins.LineProblem(Sent("100", null, null),
                Qty(100m, 0m, 0m, 0, 0m, false), d22), "");
        }

        static void CheckReviewTuples(SaleDigits d2, SaleDigits d4, SaleDigits d22, SaleDigits d6)
        {
            Eq("qty-only 4dp", SalePins.LineProblem(Sent("100", null, null),
                Qty(100m, 33.3333m, 3m, 1, 3m, true), d4), "");
            Eq("qty-only 2dp", SalePins.LineProblem(Sent("100", null, null),
                Qty(100m, 33.33m, 3m, 1, 3m, true), d22), "");
            Eq("qty-only 0dp", SalePins.LineProblem(Sent("100", null, null),
                Qty(100m, 33m, 3m, 1, 3m, true), d2), "");
            Eq("qty-only 6dp", SalePins.LineProblem(Sent("100", null, null),
                Qty(100m, 33.333333m, 3m, 1, 3m, true), d6), "");
            Expect("qty-only extra pieces", SalePins.LineProblem(Sent("100", null, null),
                Qty(100m, 0.000101m, 1000000m, 1, 1000000m, true), d6).Length > 0);
            Expect("explicit 1.000001/1/1", SalePins.LineProblem(Sent("1.000001", "1", "1"),
                Qty(1.000001m, 1m, 1m, 1, 1m, true), d6).Length > 0);
            Eq("pieces-only", SalePins.LineProblem(Sent(null, "10", null),
                Qty(100m, 10m, 10m, 1, 10m, true), d4), "");
            Eq("pieces-only 2/0", SalePins.LineProblem(Sent(null, "10", null),
                Qty(30m, 10m, 3m, 1, 3m, true), d2), "");
            Eq("pieces-only 2/4", SalePins.LineProblem(Sent(null, "10", null),
                Qty(30m, 10m, 3m, 1, 3m, true), d4), "");
            Eq("pieces-only 6/6", SalePins.LineProblem(Sent(null, "10", null),
                Qty(30m, 10m, 3m, 1, 3m, true), d6), "");
            Eq("pieces-only qty rounded", SalePins.LineProblem(Sent(null, "1", null),
                Qty(2.68m, 1m, 2.675m, 1, 2.675m, true), d2), "");
            Eq("rate-only 4dp", SalePins.LineProblem(Sent(null, null, "9"),
                Qty(100m, 11.1111m, 9m, 2, 0m, true), d4), "");
            Eq("pieces-only exponent", SalePins.LineProblem(Sent(null, "1E+01", null),
                Qty(30m, 10m, 3m, 1, 3m, true), d4), "");
        }

        static SaleQtyLine Sent(string qty, string num, string rate)
        {
            SaleQtyLine g = new SaleQtyLine();
            g.Qty = qty;
            g.Num = num;
            g.Rate = rate;
            return g;
        }

        static SaleDigits Digits(int qty, int num)
        {
            SaleDigits dig = new SaleDigits();
            dig.Qty = qty;
            dig.Num = num;
            return dig;
        }

        static SaleQty Qty(decimal qty, decimal num, decimal rate, int groupType, decimal unitRate, bool inGroup)
        {
            SaleQty q = new SaleQty();
            q.Qty = qty;
            q.Num = num;
            q.Rate = rate;
            q.GroupType = groupType;
            q.UnitRate = unitRate;
            q.InGroup = inGroup;
            return q;
        }

        static SalePin Typed(string kind, int len, string value)
        {
            SalePin pin = new SalePin();
            pin.Path = "head.x";
            pin.Field = "x";
            pin.Table = "SO_SOMain";
            pin.Value = value;
            pin.Col = new SaleCol();
            pin.Col.Kind = kind;
            pin.Col.Len = len;
            return pin;
        }

        static SalePin Pin(SalePins pins, string path)
        {
            for (int i = 0; i < pins.Items.Count; i++)
            {
                if (pins.Items[i].Path == path)
                {
                    return pins.Items[i];
                }
            }
            return null;
        }

        static Dictionary<string, object> Map(string k1, object v1, string k2, object v2, string k3, object v3)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            map[k1] = v1;
            map[k2] = v2;
            map[k3] = v3;
            return map;
        }

        static Dictionary<string, object> Map(string k1, object v1, string k2, object v2)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            map[k1] = v1;
            map[k2] = v2;
            return map;
        }

        delegate void Act();

        static void Bad(string name, Act act)
        {
            try
            {
                act();
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Code == "bad_request");
                return;
            }
            throw new InvalidOperationException("sale_pins " + name);
        }

        static void Eq(string name, object got, object want)
        {
            Expect(name + " (" + got + " != " + want + ")", object.Equals(got, want));
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("sale_pins " + name);
            }
        }
    }
}
