using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的库存期初结存部分（stock_opening）：EAI storeqc 报文、字段名单、返回解析、新单与行的对应、
    // 登录前闸门（修改 400、测试账套 403）、预演模式。只跑纯函数，不连库、不建 COM。
    internal static class StockOpeningSelfTest
    {
        const string Day = "2023-12-31";

        public static void Run()
        {
            CheckXml();
            CheckBad();
            CheckFields();
            CheckParse();
            CheckMatch();
            CheckGate();
            TestAccountGateSelfTest.Check("stock opening", StockOpening.TestOnlyText);
        }

        // 两行：第 1 行只给单价（补金额），第 2 行只给金额（补单价）、行上仓库覆盖表头；表头 ddate 不用，备注转义后发进每个 entry。
        static void CheckXml()
        {
            List<OpeningEntry> entries = StockOpeningEai.Entries(Head(), TwoLines(), Day);
            string xml = StockOpeningEai.Xml(entries, "d0c");
            string want = "<?xml version='1.0' encoding='utf-8'?><ufinterface roottag='storeqc' billtype='' docid='d0c' "
                + "receiver='u8' sender='' proc='add' codeexchanged='N' exportneedexch='N' version='2.0'><storeqc><body>"
                + "<entry><cwhcode>06</cwhcode><ddate>2023-12-31</ddate><cinvcode>91020002</cinvcode><iquantity>10</iquantity>"
                + "<iunitcost>2.5</iunitcost><iprice>25</iprice><cmemo>期初 &amp; 导入</cmemo></entry>"
                + "<entry><cwhcode>07</cwhcode><ddate>2023-12-31</ddate><cinvcode>A01</cinvcode><cfree1>红</cfree1>"
                + "<iquantity>3</iquantity><iunitcost>2.5</iunitcost><iprice>7.5</iprice><cdefine22>x</cdefine22>"
                + "<imassDate>12</imassDate><cmemo>期初 &amp; 导入</cmemo></entry></body></storeqc></ufinterface>";
            Expect("opening xml", xml == want);
            Expect("opening entries", entries.Count == 2 && entries[1].Line == 1 && entries[1].Wh == "07" && entries[1].Qty == 3m);
        }

        static Dictionary<string, object> Head()
        {
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["cWhCode"] = "06";
            head["ddate"] = "2024-05-01";
            head["cMemo"] = "期初 & 导入";
            return head;
        }

        static object[] TwoLines()
        {
            Dictionary<string, object> a = new Dictionary<string, object>();
            a["cInvCode"] = "91020002";
            a["iQuantity"] = 10;
            a["iUnitCost"] = 2.5m;
            Dictionary<string, object> b = new Dictionary<string, object>();
            b["cinvcode"] = "A01";
            b["iquantity"] = 3m;
            b["iprice"] = 7.5m;
            b["cwhcode"] = "07";
            b["cfree1"] = "红";
            b["cdefine22"] = "x";
            b["imassdate"] = 12;
            return new object[] { a, b };
        }

        static void CheckBad()
        {
            Bad("opening head ccuscode", "head", "ccuscode", "C01", "head.ccuscode");
            Bad("opening line cbmemo", "line", "cbmemo", "x", "lines.0.cbmemo");
            Bad("opening line ddate", "line", "ddate", Day, "lines.0.ddate");
            Bad("opening cdefine01", "line", "cdefine01", "x", "lines.0.cdefine01");
            Bad("opening qty zero", "line", "iquantity", 0, "lines.0.iquantity");
            Bad("opening qty text", "line", "iquantity", "abc", "lines.0.iquantity");
            Bad("opening no wh", "head", "cwhcode", "  ", "lines.0.cwhcode");
            Bad("opening no inv", "line", "cinvcode", " ", "lines.0.cinvcode");
            Bad("opening nested", "line", "cbatch", new object[0], "lines.0.cbatch");
            Bad("opening qty bool", "line", "iquantity", true, "lines.0.iquantity");
            Bad("opening inum text", "line", "inum", "2件", "lines.0.inum");
            Bad("opening rate bool", "line", "iinvexchrate", false, "lines.0.iinvexchrate");
            Bad("opening made date", "line", "dmadedate", "2024/01/02", "lines.0.dmadedate");
            Bad("opening head item", "head", "citemcode", "P01", "head.citemcode");
        }

        static void Bad(string name, string side, string key, object value, string field)
        {
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["cwhcode"] = "06";
            Dictionary<string, object> line = new Dictionary<string, object>();
            line["cinvcode"] = "A01";
            line["iquantity"] = 1;
            (side == "head" ? head : line)[key] = value;
            try
            {
                StockOpeningEai.Entries(head, new object[] { line }, Day);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Field == field);
                return;
            }
            Expect(name, false);
        }

        static void CheckFields()
        {
            VoucherKind kind = Kinds.Find(StockOpening.KindName);
            Expect("opening fields head", StockDom.MetaAllowed(kind, true, "cdefine16") && StockDom.MetaAllowed(kind, true, "ddate")
                && StockDom.MetaAllowed(kind, true, "crdcode"));
            Expect("opening fields head refused", !StockDom.MetaAllowed(kind, true, "ccuscode") && !StockDom.MetaAllowed(kind, true, "cdefine22"));
            Expect("opening fields line", StockDom.MetaAllowed(kind, false, "iinvexchrate") && StockDom.MetaAllowed(kind, false, "cfree10")
                && StockDom.MetaAllowed(kind, false, "cwhcode"));
            CheckLineFields(kind);
        }

        static void CheckLineFields(VoucherKind kind)
        {
            Expect("opening fields item", StockDom.MetaAllowed(kind, false, "citemcode") && StockDom.MetaAllowed(kind, false, "citem_class"));
            Expect("opening fields line refused", !StockDom.MetaAllowed(kind, false, "cbmemo") && !StockDom.MetaAllowed(kind, false, "cdefine1"));
            Expect("opening kind", kind.Creatable && kind.Deletable && kind.Verifiable && !kind.Updatable);
        }

        static void CheckParse()
        {
            string raw = "<?xml version=\"1.0\" encoding=\"utf-8\"?><ufinterface roottag=\"return\" request-roottag=\"storeqc\">"
                + "<item key=\"\" succeed=\"0\" dsc=\"ok\" u8key=\"\"/>"
                + "<item key=\"\" succeed=\"101\" dsc=\" 库存启用的第一个月已经月结,不可以增加删除期初! \" u8key=\"\"/></ufinterface>";
            List<OpeningReply> items = StockOpeningEai.Parse(raw);
            Expect("opening reply", items != null && items.Count == 2 && items[0].Ok && !items[1].Ok
                && items[1].Succeed == "101" && items[1].Dsc == "库存启用的第一个月已经月结,不可以增加删除期初!");
            Expect("opening reply junk", StockOpeningEai.Parse("not xml") == null && StockOpeningEai.Parse("") == null
                && StockOpeningEai.Parse("<ufinterface/>") == null);
            Expect("opening reply blank dsc", StockOpeningEai.Short("  ") == "U8 拒绝导入，没有返回原因");
        }

        static void CheckMatch()
        {
            List<OpeningEntry> entries = StockOpeningEai.Entries(Head(), TwoLines(), Day);
            List<Dictionary<string, object>> found = Found();
            List<Dictionary<string, object>> docs = StockOpeningMatch.Match(entries, found, 6);
            Expect("opening match", docs != null && docs.Count == 2 && (int)docs[1]["id"] == 900000102
                && (string)docs[1]["code"] == "-102" && (int)docs[1]["line"] == 1 && (decimal)docs[0]["qty"] == 10m);
            found[0]["iQuantity"] = "10.004";
            Expect("opening match digits", StockOpeningMatch.Match(entries, found, 2) != null
                && StockOpeningMatch.Match(entries, found, 6) == null);
            CheckMismatch();
            CheckMatchMore(entries);
        }

        static void CheckMismatch()
        {
            Expect("opening match price", Mismatch("iPrice", "25.01") && Mismatch("iUnitCost", "2.500001"));
            Expect("opening match texts", Mismatch("cMemo", "别的") && Mismatch("cFree1", "") && Mismatch("cInvCode", "A02"));
            Expect("opening match rounding", !Mismatch("iUnitCost", "2.5000004") && !Mismatch("iPrice", "7.500"));
        }

        static void CheckMatchMore(List<OpeningEntry> entries)
        {
            List<Dictionary<string, object>> found = Found();
            found.RemoveAt(1);
            Expect("opening match count", StockOpeningMatch.Match(entries, found, 6) == null);
            found.Add(Row("900000101", "-101", "07", "A01", "3|2.5|7.5"));
            Expect("opening match two lines", StockOpeningMatch.Match(entries, found, 6) == null);
            Expect("opening match none", StockOpeningMatch.Match(new List<OpeningEntry>(), new List<Dictionary<string, object>>(), 6) != null);
            OpeningEntry e = entries[0];
            e.Qty = 1.234m;
            Expect("opening digits", Status(delegate { StockOpeningCheck.CheckDigits(e, 2, "lines.0"); }) == 400
                && Status(delegate { StockOpeningCheck.CheckDigits(e, 3, "lines.0"); }) == 0);
        }

        // 第二张单的一个列改成 value 之后是否对不上。
        static bool Mismatch(string column, string value)
        {
            List<Dictionary<string, object>> found = Found();
            found[1][column] = value;
            return StockOpeningMatch.Match(StockOpeningEai.Entries(Head(), TwoLines(), Day), found, 6) == null;
        }

        static List<Dictionary<string, object>> Found()
        {
            List<Dictionary<string, object>> found = new List<Dictionary<string, object>>
            {
                Row("900000101", "-101", "06 ", "91020002", "10.000000|2.5|25.00"),
                Row("900000102", "-102", "07", "a01", "3|2.5000000000|7.5")
            };
            found[1]["cFree1"] = "红";
            return found;
        }

        // amounts：数量|单价|金额。
        static Dictionary<string, object> Row(string id, string code, string wh, string inv, string amounts)
        {
            string[] parts = amounts.Split('|');
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["ID"] = id;
            row["cCode"] = code;
            row["cWhCode"] = wh;
            row["cInvCode"] = inv;
            row["iQuantity"] = parts[0];
            row["iUnitCost"] = parts[1];
            row["iPrice"] = parts[2];
            row["cMemo"] = "期初 & 导入";
            return row;
        }

        static void CheckGate()
        {
            VoucherKind kind = Kinds.Find(StockOpening.KindName);
            WorkItem item = new WorkItem();
            item.Config = new BridgeConfig();
            item.Acc = "998";
            item.Type = kind;
            Expect("opening update", Status(item, "/u8co/v1/vouchers/update") == 400);
            Expect("opening create test", Status(item, "/u8co/v1/vouchers/create") == 403);
            Expect("opening verify test", Status(item, "/u8co/v1/vouchers/verify") == 403);
            Expect("opening load open", Status(item, "/u8co/v1/vouchers/load") == 0);
            item.Config.TestAccounts = new string[] { "998" };
            Expect("opening delete flag off", Status(item, "/u8co/v1/vouchers/delete") == 403);
            item.Config.EnableReplicatedWrites = true;
            Expect("opening delete listed", Status(item, "/u8co/v1/vouchers/delete") == 0);
            item.Type = Kinds.Find("other_in");
            Expect("opening other kind", Status(item, "/u8co/v1/vouchers/update") == 0);
            Expect("opening stamp", StockOpening.VerifyCheck(kind, 1, false) != null && StockOpening.VerifyCheck(kind, 1, true) == null
                && StockOpening.VerifyCheck(item.Type, 1, false) == null);
            Expect("opening dry", DryRunModes.Lookup("/u8co/v1/vouchers/create", kind.Name, "create", "") == DryRunModes.Validate
                && DryRunModes.Lookup("/u8co/v1/vouchers/verify", kind.Name, "verify", "") == DryRunModes.Rollback
                && DryRunModes.Lookup("/u8co/v1/vouchers/update", kind.Name, "update", "") == DryRunModes.Refuse);
        }

        static int Status(WorkItem item, string path)
        {
            return Status(delegate { StockOpening.PreLogin(item, path); });
        }

        static int Status(Action run)
        {
            try
            {
                run();
                return 0;
            }
            catch (BridgeException ex)
            {
                return ex.Status;
            }
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
