using System.Collections.Generic;

namespace U8Co
{
    internal static partial class Requests
    {
        // vouchers/verify 的 action：verify / unverify 照旧；arap_verify / arap_unverify（应付审核 / 应收审核及弃审）
        // 只给采购发票、销售发票，其余类型 400，登录子系统换成 AP / AR（ArapAudit）。类型已由 ApplyType 定好。
        static string NeedVerifyAction(Dictionary<string, object> body, WorkItem item)
        {
            string action = Need(body, "action");
            if (!ArapAudit.IsAction(action))
            {
                return NeedAction(body);
            }
            ArapAuditSpec spec = ArapAuditSpec.Of(item.Type);
            item.SubId = spec.Sub;
            return action;
        }
    }
}
