using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的无来源销售出库单部分（StockSaleOut）：类型登记、固定表头、字段名单、必填，不连库、不建 COM。
    internal static class StockSaleOutSelfTest
    {
        public static void Run()
        {
            VoucherKind kind = Kinds.Find("sale_out");
            Expect("kind", kind != null && kind.StType == "32" && kind.Creatable && kind.Deletable && kind.Updatable);
            Expect("still generate", kind.Sources != null && Array.IndexOf(kind.Sources, "dispatch") >= 0);
            Expect("head 32", StockDom.BusType("32") == "普通销售" && StockDom.TemplateId("32") == "87");
            Expect("head 09 kept", StockDom.BusType("09") == "其他出库" && StockDom.TemplateId("09") == "85");
            Expect("head 08 kept", StockDom.BusType("08") == "其他入库" && StockDom.TemplateId("08") == "67");
            CheckFields(kind);
            CheckRequests(kind);
        }

        static void CheckFields(VoucherKind kind)
        {
            Expect("head customer", StockDom.MetaAllowed(kind, true, "ccuscode") && StockDom.MetaAllowed(kind, true, "cstcode"));
            Expect("head define", StockDom.MetaAllowed(kind, true, "cdefine16") && !StockDom.MetaAllowed(kind, true, "cdefine01"));
            Expect("head no vendor", !StockDom.MetaAllowed(kind, true, "cvencode") && !StockDom.MetaAllowed(kind, true, "cdlcode"));
            Expect("line fields", StockDom.MetaAllowed(kind, false, "cbatch") && StockDom.MetaAllowed(kind, false, "iunitcost"));
            Expect("line no dispatch", !StockDom.MetaAllowed(kind, false, "idlsid") && !StockDom.MetaAllowed(kind, false, "cbdlcode"));
            Expect("other_out no cstcode", !StockDom.MetaAllowed(Kinds.Find("other_out"), true, "cstcode"));
            string[] req = StockSaleOut.RequiredHead();
            Expect("required head", string.Join(",", req) == "cwhcode,ccuscode,cdepcode");
        }

        static void CheckRequests(VoucherKind kind)
        {
            Dictionary<string, object> head = Map("cWhCode", "05", "cCusCode", "C1", "cDepCode", "D1", "cRdCode", "101");
            StockSaleOut.Check(kind, head, new object[] { Map("cInvCode", "A", "iQuantity", 2) });
            Refused("no customer", kind, Map("cWhCode", "05", "cDepCode", "D1"), Map("cInvCode", "A", "iQuantity", 1),
                "head.ccuscode");
            Refused("no dept", kind, Map("cWhCode", "05", "cCusCode", "C1"), Map("cInvCode", "A", "iQuantity", 1),
                "head.cdepcode");
            Refused("vendor", kind, Map("cWhCode", "05", "cCusCode", "C1", "cDepCode", "D1", "cVenCode", "V1"),
                Map("cInvCode", "A", "iQuantity", 1), "head.cVenCode");
            Refused("zero qty", kind, head, Map("cInvCode", "A", "iQuantity", 0), "lines.0.iquantity");
            Refused("no inv", kind, head, Map("iQuantity", 1), "lines.0.cinvcode");
            Refused("dispatch line", kind, head, Map("cInvCode", "A", "iQuantity", 1, "iDLsID", 5), "lines.0.iDLsID");
        }

        static void Refused(string name, VoucherKind kind, Dictionary<string, object> head, Dictionary<string, object> line,
            string field)
        {
            try
            {
                StockSaleOut.Check(kind, head, new object[] { line });
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Field == field);
                return;
            }
            throw new InvalidOperationException("sale_out nosrc " + name);
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
                throw new InvalidOperationException("sale_out nosrc " + name);
            }
        }
    }
}
