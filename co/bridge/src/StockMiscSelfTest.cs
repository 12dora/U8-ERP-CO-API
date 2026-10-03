using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的形态转换单、调拨申请单、盘点单部分：只测纯逻辑（类型登记、白名单、成组、08/09 的来源判断），不连库、不建 COM。
    internal static class StockMiscSelfTest
    {
        public static void Run()
        {
            CheckKinds();
            CheckFields();
            CheckGroups();
            CheckMadeBy();
            CheckTransferFromRequest();
            // 货位调整单（同族的 USERPCO 库存单据）。
            PositionAdjustSelfTest.Run();
        }

        // 调拨单参照调拨申请单：来源登记、生单字段（仓库取自申请单，不收）、申请单行可写核准数量。
        static void CheckTransferFromRequest()
        {
            VoucherKind tv = Kinds.Find("transfer");
            Expect("transfer source", tv.GenerateFrom == "transfer_request" && tv.Sources.Length == 1);
            Expect("transfer gen head", StockGen.TrHeadKey("dtvdate") && !StockGen.TrHeadKey("cowhcode"));
            Expect("transfer gen line", StockGen.TrLineKey("quantity") && !StockGen.TrLineKey("cinvcode"));
            Expect("request chk field", StockDom.MetaAllowed(Kinds.Find("transfer_request"), false, "itvchkquantity"));
        }

        static void CheckKinds()
        {
            VoucherKind shape = Kinds.Find("shape_change");
            VoucherKind req = Kinds.Find("transfer_request");
            VoucherKind check = Kinds.Find("stock_check");
            Expect("misc kinds", shape != null && req != null && check != null);
            Expect("misc handled", StockMisc.Handles(shape) && StockMisc.Handles(req) && StockMisc.Handles(check));
            Expect("transfer not misc", !StockMisc.Handles(Kinds.Find("transfer")));
            Expect("misc types", shape.StType == "15" && req.StType == "62" && check.StType == "18");
            CheckKindOps(shape, req, check);
            CheckListUfts();
        }

        // 调拨申请、盘点的列表增量按表头、表体较大的 rowversion 算；形态转换表体没有 rowversion。
        static void CheckListUfts()
        {
            Expect("misc list body ufts", ListKinds.Find("transfer_request").HasBodyUfts
                && ListKinds.Find("stock_check").HasBodyUfts && !ListKinds.Find("shape_change").HasBodyUfts);
        }

        static void CheckKindOps(VoucherKind shape, VoucherKind req, VoucherKind check)
        {
            Expect("misc ops", shape.Updatable && req.Updatable && !check.Updatable && !req.Closable);
            Expect("misc check verifier", check.VerifierColumn == "cAccounter" && shape.VerifierColumn == "cVerifyPerson");
            Expect("misc views", StockDom.HeadView(shape) == "AssemM" && StockDom.HeadView(req) == "transrequestm");
            Expect("misc check view", StockDom.HeadView(check) == "checkm");
            Expect("misc list", ListKinds.Find("shape_change") != null && ListKinds.Find("stock_check") != null);
            Expect("misc perm", PermRegistry.ForKey("voucher:transfer_request") != null);
            Expect("check not verifiable", !check.Verifiable && shape.Verifiable && req.Verifiable);
        }

        static void CheckFields()
        {
            VoucherKind shape = Kinds.Find("shape_change");
            VoucherKind check = Kinds.Find("stock_check");
            Expect("shape fields", StockDom.MetaAllowed(shape, false, "bavtype") && StockDom.MetaAllowed(shape, false, "cdefine22"));
            Expect("shape refused", !StockDom.MetaAllowed(shape, true, "cwhcode") && !StockDom.MetaAllowed(shape, false, "iavnum"));
            Expect("check fields", StockDom.MetaAllowed(check, false, "icvcquantity") && StockDom.MetaAllowed(check, true, "cwhcode"));
            Expect("check refused", !StockDom.MetaAllowed(check, false, "iadinquantity"));
            Expect("transfer fields kept", StockDom.MetaAllowed(Kinds.Find("transfer"), true, "cmemo"));
        }

        static void CheckGroups()
        {
            List<string[]> ok = new List<string[]>();
            ok.Add(new string[] { StockDom.AvBefore, "1" });
            ok.Add(new string[] { StockDom.AvAfter, "1" });
            StockDom.CheckGroups(ok);
            List<string[]> half = new List<string[]>(ok);
            half.Add(new string[] { StockDom.AvBefore, "2" });
            Expect("shape group refused", Refused(half));
            List<string[]> bad = new List<string[]>();
            bad.Add(new string[] { "前", "1" });
            Expect("shape type refused", Refused(bad));
        }

        static void CheckMadeBy()
        {
            VoucherKind other = Kinds.Find("other_in");
            Expect("made shape", StockMisc.MadeBy(other, Head("形态转换", "转换入库")) == "形态转换单");
            Expect("made check", StockMisc.MadeBy(other, Head("盘点", "盘盈入库")) == "盘点单");
            Expect("made transfer", StockMisc.MadeBy(other, Head("调拨", "调拨入库")) == "调拨单");
            Expect("made stock", StockMisc.MadeBy(other, Head("库存", "其他入库")) == "");
            Expect("made not rd", StockMisc.MadeBy(Kinds.Find("transfer"), Head("调拨", "")) == "");
        }

        static bool Refused(List<string[]> pairs)
        {
            try
            {
                StockDom.CheckGroups(pairs);
                return false;
            }
            catch (BridgeException)
            {
                return true;
            }
        }

        static Dictionary<string, object> Head(string source, string bus)
        {
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["cSource"] = source;
            head["cBusType"] = bus;
            return head;
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
