using System.Collections.Generic;

namespace U8Co
{
    // 不经 EditMore / SaleEdit 的受控 SQL 单据表头修正路由：历史发货单表头修正（VoucherHeadFix）、
    // 销售订单合同号回写（VoucherSoContract）。
    internal static partial class Dispatch
    {
        static void AddFixes(Dictionary<string, Handler> map)
        {
            map.Add(VoucherHeadFix.Path, VoucherHeadFix.Run);
            map.Add(VoucherSoContract.Path, VoucherSoContract.Run);
        }
    }
}
