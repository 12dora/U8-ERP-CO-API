using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest：同表分两个类型的单据的读取核对（VoucherScope）。只跑纯函数，不连库。
    internal static class VoucherScopeSelfTest
    {
        public static void Run()
        {
            List<string> all = VoucherScope.AllSql();
            Expect("scope pairs", all.Count == 4);
            Expect("scope dispatch", all[0] == "SELECT CASE WHEN h.cVouchType = N'05' AND " + ListKinds.BlueDispatch
                + " THEN 1 ELSE 0 END AS own, CASE WHEN h.cVouchType = N'05' AND " + ListKinds.RedDispatch
                + " THEN 1 ELSE 0 END AS other FROM DispatchList h WHERE h.DLID=?");
            Expect("scope arrival", all[2] == "SELECT CASE WHEN " + ListKinds.BlueArrival + " THEN 1 ELSE 0 END AS own, CASE WHEN "
                + ListKinds.RedArrival + " THEN 1 ELSE 0 END AS other FROM PU_ArrivalVouch h WHERE h.ID=?");
            // 没有同表的另一个类型的单据不核对（不碰连接）。
            Expect("scope other kinds", VoucherScope.Check(null, Kinds.Find("sale_order"), 1) == null
                && VoucherScope.Check(null, null, 1) == null);
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
