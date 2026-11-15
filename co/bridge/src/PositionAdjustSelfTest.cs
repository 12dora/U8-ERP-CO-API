using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的货位调整单部分：只测纯逻辑（类型登记、视图、列表、权限、白名单、结存键和变动合计），不连库、不建 COM。
    internal static class PositionAdjustSelfTest
    {
        public static void Run()
        {
            CheckKind();
            CheckFields();
            CheckDeltas();
            CheckSameLine();
        }

        // 新增提交前的逐行回读（PositionAdjustSaved）：编码不分大小写、去空格，数量差 1e-6 以内算相同。
        static void CheckSameLine()
        {
            BinLine a = Line("5A01", "5A02", 2m, "");
            BinLine b = Line(" 5a01", "5A02 ", 2.0000001m, "");
            Expect("adjust same line", PositionAdjustSaved.SameLine(a, b));
            Expect("adjust qty differs", !PositionAdjustSaved.SameLine(a, Line("5A01", "5A02", 3m, "")));
            Expect("adjust bin differs", !PositionAdjustSaved.SameLine(a, Line("5A01", "5A03", 2m, "")));
        }

        static void CheckKind()
        {
            VoucherKind kind = Kinds.Find(PositionAdjust.KindName);
            Expect("adjust kind", kind != null && PositionAdjust.Handles(kind) && kind.StType == "19" && kind.Family == "st");
            Expect("adjust tables", kind.HeadTable == "AdjustPVouch" && kind.IdColumn == "Id" && kind.BodyFk == "ID"
                && kind.LineIdColumn == "autoID");
            CheckOps(kind);
            CheckWiring(kind);
        }

        static void CheckOps(VoucherKind kind)
        {
            Expect("adjust verifier", kind.VerifierColumn == "chandler" && kind.VerifyDateColumn == "dVeriDate");
            Expect("adjust ops", kind.Creatable && kind.Deletable && kind.Verifiable && !kind.Updatable && !kind.Closable
                && kind.Sources.Length == 0);
        }

        // 视图、列表、权限、库存类型码：与 StockDomFields、ListKindsStMisc、PermRegistryStMisc、StockCallDry 的登记一致。
        static void CheckWiring(VoucherKind kind)
        {
            Expect("adjust not misc", !StockMisc.Handles(kind) && !PositionAdjust.Handles(Kinds.Find("stock_check")));
            Expect("adjust views", StockDom.HeadView(kind) == "AdjustPM" && StockDom.ViewName("19", false) == "AdjustPD");
            ListKind list = ListKinds.Find(PositionAdjust.KindName);
            Expect("adjust list", list != null && !list.HasBodyUfts && list[ListKind.Verifier] == "h.chandler");
            PermRule rule = PermRegistry.ForKey("voucher:" + PositionAdjust.KindName);
            Expect("adjust perm", rule != null && rule.Auths.Length == 1 && rule.Auths[0] == "ST010807");
            Expect("adjust st kind", StockCall.KindOfSt("19") == kind);
        }

        static void CheckFields()
        {
            VoucherKind kind = Kinds.Find(PositionAdjust.KindName);
            Expect("adjust head", StockDom.MetaAllowed(kind, true, "cwhcode") && StockDom.MetaAllowed(kind, true, "cdefine16"));
            Expect("adjust line", StockDom.MetaAllowed(kind, false, "cbposcode") && StockDom.MetaAllowed(kind, false, "caposcode")
                && StockDom.MetaAllowed(kind, false, "iquantity") && StockDom.MetaAllowed(kind, false, "cfree10"));
            Expect("adjust head refused", !StockDom.MetaAllowed(kind, true, "chandler") && !StockDom.MetaAllowed(kind, true, "cvouchcode")
                && !StockDom.MetaAllowed(kind, true, "cdefine22"));
            Expect("adjust line refused", !StockDom.MetaAllowed(kind, false, "inum") && !StockDom.MetaAllowed(kind, false, "rdsid")
                && !StockDom.MetaAllowed(kind, false, "cwhcode"));
        }

        // A→B 3、B→C 1：审核时 A −3、B +2、C +1；弃审反号。批号不同的是另一个结存键。
        static void CheckDeltas()
        {
            List<BinLine> lines = new List<BinLine>();
            lines.Add(Line("A", "B", 3m, ""));
            lines.Add(Line("b", "C", 1m, ""));
            lines.Add(Line("A", "C", 2m, "L1"));
            Dictionary<string, decimal> on = PositionAdjustBins.Deltas(lines, false);
            Expect("adjust delta keys", on.Count == 5);
            Expect("adjust delta a", on[PositionAdjustBins.Key(lines[0], "A")] == -3m);
            Expect("adjust delta b", on[PositionAdjustBins.Key(lines[0], "B")] == 2m);
            Expect("adjust delta c", on[PositionAdjustBins.Key(lines[1], "C")] == 1m);
            Expect("adjust delta lot", on[PositionAdjustBins.Key(lines[2], "A")] == -2m);
            Dictionary<string, decimal> off = PositionAdjustBins.Deltas(lines, true);
            Expect("adjust undo", off[PositionAdjustBins.Key(lines[0], "B")] == -2m && off[PositionAdjustBins.Key(lines[2], "C")] == -2m);
            string[] part = PositionAdjustBins.Key(lines[2], " A ").Split(PositionAdjustBins.Sep);
            Expect("adjust key parts", part.Length == 13 && part[0] == "A" && part[1] == "INV1" && part[2] == "L1" && part[12] == "");
        }

        static BinLine Line(string from, string to, decimal qty, string batch)
        {
            BinLine line = new BinLine();
            line.Inv = "INV1";
            line.From = from;
            line.To = to;
            line.Qty = qty;
            line.Batch = batch;
            for (int i = 0; i < 10; i++)
            {
                line.Free[i] = "";
            }
            return line;
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
