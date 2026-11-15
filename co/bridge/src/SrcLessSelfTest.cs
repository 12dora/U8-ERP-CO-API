using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的无来源新增部分：只测纯逻辑（类型登记、字段名单、必填、发票类型、锁键、产成品入库的来源），不连库、不建 COM。
    internal static class SrcLessSelfTest
    {
        delegate void Step();

        public static void Run()
        {
            CheckKinds();
            CheckFields();
            CheckRequests();
            CheckRd();
            CheckExch();
            CheckInvoiceHead();
            // 无来源销售出库单（走 StockDom / StockCo.Create，不在 SrcLessReq 名单里）。
            StockSaleOutSelfTest.Run();
            CheckPurInHavePo();
        }

        // 「普通业务必有订单」只拦无来源蓝字采购入库，红字退库放行（同 U8 客户端）；选项值 1 / True 不分大小写。
        static void CheckPurInHavePo()
        {
            Expect("havepo blue 1", StockPurInPos.HavePoRefuses("1", false));
            Expect("havepo blue true", StockPurInPos.HavePoRefuses(" True ", false));
            Expect("havepo blue off", !StockPurInPos.HavePoRefuses("False", false) && !StockPurInPos.HavePoRefuses(null, false));
            Expect("havepo red on", !StockPurInPos.HavePoRefuses("1", true) && !StockPurInPos.HavePoRefuses("true", true));
            Expect("red head", StockCo.RedHead(Map("red", true)) && !StockCo.RedHead(Map("cwhcode", "01")));
        }

        // 先开票发票表头固定 csource=销售（U8 读单、复核取数都按它过滤），idisp=0；发货单不写 csource。
        static void CheckInvoiceHead()
        {
            Dictionary<string, string> inv = SrcLessSa.FixedHead(true, "26");
            Expect("inv csource", inv.ContainsKey("CSource") && inv["csource"] == "销售");
            Expect("inv idisp", inv["idisp"] == "0" && inv["cvouchtype"] == "26");
            Dictionary<string, string> dl = SrcLessSa.FixedHead(false, "05");
            Expect("dl no csource", !dl.ContainsKey("csource") && dl["bfirst"] == "0");
        }

        // 销售无来源的币种：不填或本位币写本位币、汇率 1；外币缺汇率、汇率非正、本位币汇率不是 1 都是 400 head.iexchrate。
        static void CheckExch()
        {
            string[] home = SrcLessReq.Exch(Map("cCusCode", "C1"), "人民币");
            Expect("exch home", home[0] == "人民币" && home[1] == "1");
            string[] same = SrcLessReq.Exch(Map("cexch_name", "人民币", "iExchRate", 1), "人民币");
            Expect("exch same", same[0] == "人民币" && same[1] == "1");
            string[] usd = SrcLessReq.Exch(Map("cExch_Name", "美元", "iexchrate", 7.1), "人民币");
            Expect("exch usd", usd[0] == "美元" && usd[1] == "7.1");
            BadRate("usd no rate", Map("cexch_name", "美元"));
            BadRate("zero rate", Map("cexch_name", "美元", "iexchrate", 0));
            BadRate("home rate 2", Map("iexchrate", "2"));
        }

        static void BadRate(string name, Dictionary<string, object> head)
        {
            try
            {
                SrcLessReq.Exch(head, "人民币");
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Field == "head.iexchrate");
                return;
            }
            throw new InvalidOperationException("srcless " + name);
        }

        // 材料出库、产成品入库没有缺省收发类别：无来源新增和生单缺 crdcode 都是 400 head.crdcode。
        static void CheckRd()
        {
            VoucherKind mo = Kinds.Find("material_out");
            VoucherKind pi = Kinds.Find("product_in");
            object[] free = new object[] { Map("cInvCode", "A", "iQuantity", 1) };
            object[] gen = new object[] { Map("source_line_id", 1, "quantity", 1) };
            NoRd("free material_out", delegate { SrcLessReq.Check(mo, Map("cWhCode", "01"), free); });
            NoRd("gen material_out", delegate { MfgReq.CheckGenerate(mo, Map("cWhCode", "01"), gen); });
            NoRd("gen product_in", delegate { MfgReq.CheckGenerate(pi, Map("cWhCode", "01", "cRdCode", " "), gen); });
            SrcLessReq.Check(mo, Map("cWhCode", "01", "cRdCode", "R1"), free);
            MfgReq.CheckGenerate(pi, Map("cWhCode", "01", "crdcode", "R2"), gen);
            Expect("rd required", Array.IndexOf(SrcLessReq.RequiredHead(mo), "crdcode") >= 0);
        }

        static void NoRd(string name, Step step)
        {
            try
            {
                step();
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Field == "head.crdcode" && ex.Message == "必须指定收发类别");
                return;
            }
            throw new InvalidOperationException("srcless " + name);
        }

        static void CheckKinds()
        {
            string[] names = new string[] { "dispatch", "sale_invoice", "arrival", "material_out" };
            for (int i = 0; i < names.Length; i++)
            {
                VoucherKind kind = Kinds.Find(names[i]);
                Expect("creatable " + names[i], kind != null && kind.Creatable && SrcLessReq.Handles(kind));
                Expect("meta " + names[i], SrcLessReq.MetaRule(kind) != null && SrcLessReq.RequiredLine(kind) != null);
            }
            Expect("not purchase_in", !SrcLessReq.Handles(Kinds.Find("purchase_in")));
            VoucherKind product = Kinds.Find("product_in");
            Expect("product_in from mo", product != null && Array.IndexOf(product.Sources, "production_order") >= 0
                && product.GenerateFrom == "qm_product_check");
            string[] invoice = SrcLess.CreateLocks("sale_invoice");
            Expect("invoice locks", invoice.Length == 2 && invoice[1] == "new:dispatch");
            Expect("arrival locks", SrcLess.CreateLocks("arrival")[1] == "new:purchase_return");
            Expect("plain locks", SrcLess.CreateLocks("dispatch").Length == 1);
        }

        static void CheckFields()
        {
            VoucherKind dispatch = Kinds.Find("dispatch");
            VoucherKind invoice = Kinds.Find("sale_invoice");
            Expect("sa head", SrcLessReq.HeadAllowed(dispatch, "ccuscode") && SrcLessReq.HeadAllowed(dispatch, "cdefine16"));
            Expect("sa no sosid", !SrcLessReq.LineAllowed(dispatch, "isosid") && !SrcLessReq.LineAllowed(invoice, "idlsid"));
            Expect("invoice type", SrcLessReq.HeadAllowed(invoice, "cvouchtype") && !SrcLessReq.HeadAllowed(dispatch, "cvouchtype"));
            Expect("no cdefine01", !SrcLessReq.HeadAllowed(dispatch, "cdefine01"));
            Expect("pu no poid", !SrcLessReq.LineAllowed(Kinds.Find("arrival"), "iposid"));
            Expect("st no mpoids", !SrcLessReq.LineAllowed(Kinds.Find("material_out"), "impoids"));
            Expect("st cposition", SrcLessReq.LineAllowed(Kinds.Find("material_out"), "cposition"));
            Expect("mfg cposition", MfgReq.LineOf(Map("source_line_id", 1, "cPosition", "01"), true).Count == 2);
            bool qm = false;
            try
            {
                MfgReq.LineOf(Map("source_line_id", 1, "cposition", "01"));
            }
            catch (BridgeException ex)
            {
                qm = ex.Status == 400;
            }
            Expect("qm no cposition", qm);
        }

        static void CheckRequests()
        {
            VoucherKind dispatch = Kinds.Find("dispatch");
            Dictionary<string, object> head = Map("cCusCode", "C1", "cSTCode", "01");
            SrcLessReq.Check(dispatch, head, new object[] { Map("cWhCode", "01", "cInvCode", "A", "iQuantity", 2) });
            Refused("no wh", dispatch, head, Map("cInvCode", "A", "iQuantity", 2));
            Refused("zero qty", dispatch, head, Map("cWhCode", "01", "cInvCode", "A", "iQuantity", 0));
            Refused("sosid", dispatch, head, Map("cWhCode", "01", "cInvCode", "A", "iQuantity", 1, "iSOsID", 3));
            Expect("invoice 26", SrcLessReq.InvoiceType(Map("cCusCode", "C1")) == "26");
            Expect("invoice 27", SrcLessReq.InvoiceType(Map("cVouchType", "27")) == "27");
            try
            {
                SrcLessReq.InvoiceType(Map("cVouchType", "28"));
            }
            catch (BridgeException ex)
            {
                Expect("invoice 28", ex.Status == 400);
                return;
            }
            throw new InvalidOperationException("invoice 28");
        }

        static void Refused(string name, VoucherKind kind, Dictionary<string, object> head, Dictionary<string, object> line)
        {
            try
            {
                SrcLessReq.Check(kind, head, new object[] { line });
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static Dictionary<string, object> Map(params object[] pairs)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            for (int i = 0; i + 1 < pairs.Length; i += 2)
            {
                map[(string)pairs[i]] = pairs[i + 1];
            }
            return map;
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("srcless " + name);
            }
        }
    }
}
