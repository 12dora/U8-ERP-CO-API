using System.Collections.Generic;

namespace U8Co
{
    // gl/transfer/pnl、gl/transfer/custom（期间损益结转、自定义转账）在登录前的处理：不带 type、id；
    // 先校验字段（400，GlTransferReq），再查测试账套名单（403 test_account_only），都在登录 U8 之前。登录子系统 GL。
    // 审计 action：gl_transfer_pnl / gl_transfer_custom（RequestsIa.IaAction 一档）。处理在 GlTransfer。
    internal static partial class Requests
    {
        // 不是结转路由返回 false，不动任务。
        static bool ApplyGlTransfer(Dictionary<string, object> body, WorkItem item, string path)
        {
            if (!GlTransferReq.IsPath(path))
            {
                return false;
            }
            GlTransferAsk ask = GlTransferReq.Parse(path, body, item.DryRun, 0);
            TestAccountGate.Require(item, GlTransferReq.TestOnlyOf(path));
            item.SubId = GlTransferReq.Sub;
            item.Action = GlTransferReq.ActionOf(path);
            CoRows.Note(item, ask.Title());
            return true;
        }
    }
}
