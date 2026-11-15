using System.Collections.Generic;

namespace U8Co
{
    // 不经 EditMore / SaleEdit 的受控 SQL 单据表头修正路由：历史发货单表头修正（VoucherHeadFix）、
    // 销售订单表头自定义项写入（VoucherSoDefines）。
    internal static partial class Dispatch
    {
        static void AddFixes(Dictionary<string, Handler> map)
        {
            map.Add(VoucherHeadFix.Path, VoucherHeadFix.Run);
            map.Add(VoucherSoDefines.Path, VoucherSoDefines.Run);
        }
    }
}
