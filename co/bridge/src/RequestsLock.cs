using System.Collections.Generic;

namespace U8Co
{
    // vouchers/lock（销售订单锁定、解锁；采购订单暂不支持）在登录前的字段校验。处理在 VoucherLock。
    internal static partial class Requests
    {
        internal const string LockPath = "/u8co/v1/vouchers/lock";

        static string NeedLock(Dictionary<string, object> body)
        {
            string action = Need(body, "action");
            if (action != "lock" && action != "unlock")
            {
                throw new BridgeException(400, "bad_request", "action 只能是 lock 或 unlock");
            }
            return action;
        }

        static void GuardLock(VoucherKind kind)
        {
            VoucherLock.Refuse(kind);
        }
    }
}
