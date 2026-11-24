using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的退货单参照销售订单与无来源退货单部分：类型登记、来源、名单、锁键、固定表头、订单行可退数量与
    // iFHQuantity 核对口径、预演模式。不连库、不建 COM。
    internal static class SaleReturnSoSelfTest
    {
        public static void Run()
        {
            CheckKind();
            CheckFree();
            CheckRoom();
        }

        static void CheckKind()
        {
            VoucherKind kind = Kinds.Find(SaleReturn.KindName);
            Expect("ret creatable", kind != null && kind.Creatable && SrcLessReq.Handles(kind));
            Expect("ret sources", Array.IndexOf(kind.Sources, "sale_order") >= 0 && kind.GenerateFrom == "dispatch"
                && Array.IndexOf(kind.Sources, ReturnsApplyRead.KindName) >= 0);
            Expect("ret dry create", DryRunModes.Lookup("vouchers/create", SaleReturn.KindName, "create", "") == "rollback");
            Expect("ret dry gen so", DryRunModes.Lookup("vouchers/generate", SaleReturn.KindName, "generate", "sale_order")
                == "rollback");
            WorkItem item = new WorkItem();
            item.Source = Kinds.Find("sale_order");
            Expect("ret from order", SaleGen.FromOrder(item) && !SaleGen.FromOrder(new WorkItem()));
            item.Source = Kinds.Find("dispatch");
            Expect("ret from dispatch", !SaleGen.FromOrder(item));
        }

        // 无来源退货单：同销售名单（不收 isosid / icorid），行要仓库；锁同发货单编号；表头 breturnflag=1、cvouchtype=05。
        static void CheckFree()
        {
            VoucherKind kind = Kinds.Find(SaleReturn.KindName);
            Expect("free head", SrcLessReq.HeadAllowed(kind, "ccuscode") && SrcLessReq.HeadAllowed(kind, "cdefine16")
                && !SrcLessReq.HeadAllowed(kind, "cvouchtype") && !SrcLessReq.HeadAllowed(kind, "breturnflag"));
            Expect("free line", SrcLessReq.LineAllowed(kind, "itaxunitprice") && !SrcLessReq.LineAllowed(kind, "isosid")
                && !SrcLessReq.LineAllowed(kind, "icorid"));
            Expect("free required", Array.IndexOf(SrcLessReq.RequiredLine(kind), "cwhcode") >= 0
                && Array.IndexOf(SrcLessReq.RequiredHead(kind), "ccuscode") >= 0);
            CheckFreeHead(kind);
        }

        static void CheckFreeHead(VoucherKind kind)
        {
            string[] locks = SrcLess.CreateLocks(SaleReturn.KindName);
            Expect("free locks", locks.Length == 2 && locks[0] == "new:sale_return" && locks[1] == "new:dispatch");
            Dictionary<string, string> red = SrcLessSa.FixedHead(false, "05", true);
            Expect("free red head", red["breturnflag"] == "1" && red["cvouchtype"] == "05" && red["bfirst"] == "0");
            Expect("free blue head", SrcLessSa.FixedHead(false, "05")["breturnflag"] == "0");
            Expect("free plain", SrcLess.Plain(kind) == "退货单");
        }

        // 订单行可退 = iFHQuantity（累计发货数量，已扣过以前的退货），不小于 0。
        static void CheckRoom()
        {
            Expect("room shipped", SaleGen.SoRoom(Row("6", "4")) == 6m);
            Expect("room none", SaleGen.SoRoom(Row("0", "10")) == 0m);
            Expect("room negative", SaleGen.SoRoom(Row("-1", "1")) == 0m);
        }

        static Dictionary<string, object> Row(string fh, string ret)
        {
            Dictionary<string, object> row = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            row["fh"] = fh;
            row["ret"] = ret;
            return row;
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("sale_return_so " + name);
            }
        }
    }
}
