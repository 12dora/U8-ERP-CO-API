using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的核对字段白名单与编号族部分：SoFields、SaleGen 发货单 / 发票的白名单，编号族（cdefine1–16、
    // chdefineN、cbdefineN，N = 1–99，不带前导零）的识别与列名，meta 字段探测。不连库。
    internal static class SalePinsFamilySelfTest
    {
        public static void Run()
        {
            CheckOrderWhitelist();
            CheckDispatchWhitelist();
            CheckDefineNo();
            CheckFind();
            CheckFindRefused();
            CheckMeta();
            CheckMetaRed();
        }

        static void CheckOrderWhitelist()
        {
            Expect("so head gathering", SoFields.Allowed("cgatheringplan", true));
            Expect("so head addcode", SoFields.Allowed("caddcode", true));
            Expect("so head define9", SoFields.Allowed("cdefine9", true));
            Expect("so head define16", SoFields.Allowed("cdefine16", true));
            Expect("so head define17 refused", !SoFields.Allowed("cdefine17", true));
            Expect("so line wh", SoFields.Allowed("cwhcode", false));
            Expect("so line bdefine7", SoFields.Allowed("cbdefine7", false));
            Expect("so line bdefine99", SoFields.Allowed("cbdefine99", false));
            Expect("so line unit", SoFields.Allowed("cunitid", false));
            Expect("so line num", SoFields.Allowed("inum", false));
            Expect("so line bdefine0 refused", !SoFields.Allowed("cbdefine0", false));
            Expect("so line bdefine07 refused", !SoFields.Allowed("cbdefine07", false));
            Expect("so line bdefine100 refused", !SoFields.Allowed("cbdefine100", false));
            Expect("so head hdefine refused", !SoFields.Allowed("chdefine1", true));
            Expect("so head bdefine refused", !SoFields.Allowed("cbdefine7", true));
        }

        static void CheckDispatchWhitelist()
        {
            Expect("disp head addcode", SaleGen.MetaDispatch("caddcode", true));
            string[] ok = new string[] { "chdefine1", "chdefine12", "chdefine99", "cdefine1", "cdefine16" };
            for (int i = 0; i < ok.Length; i++)
            {
                Expect("disp head " + ok[i], SaleGen.MetaDispatch(ok[i], true));
            }
            string[] refused = new string[] { "chdefine0", "chdefine07", "chdefine100", "chdefinen", "cdefine17" };
            for (int i = 0; i < refused.Length; i++)
            {
                Expect("disp head " + refused[i] + " refused", !SaleGen.MetaDispatch(refused[i], true));
            }
            Expect("disp line hdefine refused", !SaleGen.MetaDispatch("chdefine1", false));
            Expect("disp line bdefine refused", !SaleGen.MetaDispatch("cbdefine1", false));
            Expect("invoice head hdefine refused", !SaleGen.MetaInvoice("chdefine1", true));
            Expect("invoice head addcode refused", !SaleGen.MetaInvoice("caddcode", true));
        }

        // 编号 1 起、不带前导零，cdefine 到 16，扩展自定义项到 99。
        static void CheckDefineNo()
        {
            Eq("no plain", SalePins.DefineNo("cbdefine7", "cbdefine", 99), 7);
            Eq("no two digits", SalePins.DefineNo("chdefine12", "chdefine", 99), 12);
            Eq("no zero", SalePins.DefineNo("chdefine0", "chdefine", 99), 0);
            Eq("no leading zero", SalePins.DefineNo("chdefine07", "chdefine", 99), 0);
            Eq("no over max", SalePins.DefineNo("cdefine17", "cdefine", 16), 0);
            Eq("no three digits", SalePins.DefineNo("cbdefine100", "cbdefine", 99), 0);
            Eq("no sign", SalePins.DefineNo("cbdefine+1", "cbdefine", 99), 0);
            Eq("no prefix only", SalePins.DefineNo("cbdefine", "cbdefine", 99), 0);
            Eq("no other prefix", SalePins.DefineNo("chdefine1", "cdefine", 16), 0);
        }

        // 列名由编号拼出：{类型, H / B, 请求字段, 表, 列, A / C}。
        static void CheckFind()
        {
            Row("find so head define", SalePins.Find(SalePins.Order, true, "cdefine9"), "SO_SOMain", "cDefine9");
            Row("find so line extra", SalePins.Find(SalePins.Order, false, "cbdefine7"), "SO_SODetails_extradefine", "cbdefine7");
            Row("find disp extra", SalePins.Find(SalePins.Dispatch, true, "chdefine12"), "DispatchList_extradefine", "chdefine12");
            Row("find disp define", SalePins.Find(SalePins.Dispatch, true, "cdefine16"), "DispatchList", "cDefine16");
            Row("find inv extra", SalePins.Find(SalePins.Invoice, false, "cbdefine4"), "SaleBillVouchs_extradefine", "cbdefine4");
        }

        static void CheckFindRefused()
        {
            Expect("find so define17", SalePins.Find(SalePins.Order, true, "cdefine17") == null);
            Expect("find disp hdefine0", SalePins.Find(SalePins.Dispatch, true, "chdefine0") == null);
            Expect("find disp hdefine07", SalePins.Find(SalePins.Dispatch, true, "chdefine07") == null);
            Expect("find so head hdefine", SalePins.Find(SalePins.Order, true, "chdefine1") == null);
            Expect("find disp line bdefine", SalePins.Find(SalePins.Dispatch, false, "cbdefine1") == null);
            Expect("find inv head hdefine", SalePins.Find(SalePins.Invoice, true, "chdefine1") == null);
        }

        static void Row(string name, string[] row, string table, string column)
        {
            Expect(name + " found", row != null);
            Eq(name + " table", row[3], table);
            Eq(name + " column", row[4], column);
            Eq(name + " kind", row[5], "A");
        }

        // meta 的字段探测：编号族以区间列出（1–99），记号 chdefineN / cbdefineN 不当字段。
        static void CheckMeta()
        {
            Dictionary<string, object> disp = MetaFields.Side(delegate(string low) { return SaleGen.MetaDispatch(low, true); });
            Eq("meta disp hdefine", SpanOf(disp, "chdefine"), "1-99");
            Eq("meta disp define", SpanOf(disp, "cdefine"), "1-16");
            string[] exact = (string[])disp["exact"];
            Expect("meta disp exact addcode", Array.IndexOf(exact, "caddcode") >= 0);
            Expect("meta disp exact no marker", Array.IndexOf(exact, "chdefinen") < 0);
            Expect("meta disp exact no span name", Array.IndexOf(exact, "chdefine1") < 0);
            Dictionary<string, object> so = MetaFields.Side(delegate(string low) { return SoFields.Allowed(low, false); });
            Eq("meta so line bdefine", SpanOf(so, "cbdefine"), "1-99");
            Dictionary<string, object> blue = MetaFields.Side(delegate(string low) { return SaleGen.MetaInvoice(low, false); });
            Eq("meta blue line bdefine", SpanOf(blue, "cbdefine"), "1-99");
            Expect("meta blue exact no marker", Array.IndexOf((string[])blue["exact"], "cbdefinen") < 0);
        }

        // 红字发票表体不列扩展自定义项。
        static void CheckMetaRed()
        {
            Expect("red line bdefine1 refused", !SaleGen.RedInvoiceLine("cbdefine1"));
            Expect("red line bdefine4 refused", !SaleGen.RedInvoiceLine("cbdefine4"));
            Expect("red line memo", SaleGen.RedInvoiceLine("cmemo"));
            Dictionary<string, object> red = SaleGen.MetaRedInvoice()["lines"] as Dictionary<string, object>;
            Eq("meta red line bdefine", SpanOf(red, "cbdefine"), "");
            Eq("meta red line define", SpanOf(red, "cdefine"), "22-37");
            Expect("meta red exact", Array.IndexOf((string[])red["exact"], "cmemo") >= 0);
        }

        // side 的 spans 里该前缀的区间，"from-to"，多段用逗号连；没有为空串。
        static string SpanOf(Dictionary<string, object> side, string prefix)
        {
            List<string> parts = new List<string>();
            object[] spans = (object[])side["spans"];
            for (int i = 0; i < spans.Length; i++)
            {
                Dictionary<string, object> span = (Dictionary<string, object>)spans[i];
                if ((string)span["prefix"] == prefix)
                {
                    parts.Add(Convert.ToString(span["from"]) + "-" + Convert.ToString(span["to"]));
                }
            }
            return string.Join(",", parts.ToArray());
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
