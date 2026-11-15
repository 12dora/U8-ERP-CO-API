using System.Collections.Generic;

namespace U8Co
{
    // vouchers/create 的其余类型（Dispatch.CreateOf 末尾调用，从 Dispatch.cs 移来：文件行数上限）。
    internal static class DispatchCreate
    {
        internal static ApiResult More(WorkContext ctx, VoucherKind kind, Dictionary<string, object> head, object[] lines)
        {
            // 其他报检单无来源新增（QmOthOps，VO 接口，U8 自己提交）。
            if (QmOthOps.Handles(kind))
            {
                return QmOthOps.Create(ctx, kind, head, lines);
            }
            // 采购手工结算（PuSettleMan，按 U8 手工结算界面执行的 SQL，第一级）。
            if (PuSettleReq.Handles(kind))
            {
                return PuSettleMan.Create(ctx, kind);
            }
            throw new BridgeException(400, "bad_request", "该单据类型不支持新增");
        }
    }
}
