using System.Collections.Generic;

namespace U8Co
{
    // arap/exchange_gain、arap/exchange_gain/cancel（汇兑损益、取消汇兑损益）在登录前的处理：不带 type、id；
    // 校验字段（400，ArapExGainReq），再查测试账套名单（403 test_account_only，
    // ArapExGainReq.TestGate），都在登录 U8 之前。登录子系统就是 flag。
    // 审计 action：exchange_gain / exchange_gain_cancel（RequestsIa.IaAction 一档）。处理在 ArapExGain / ArapExGainCancel。
    internal static partial class Requests
    {
        // 不是汇兑损益路由返回 false，不动任务。
        static bool ApplyExGain(Dictionary<string, object> body, WorkItem item, string path)
        {
            if (!ArapExGainReq.IsPath(path))
            {
                return false;
            }
            ExGainAsk ask = ArapExGainReq.Parse(path, body);
            ArapExGainReq.TestGate(item);
            item.SubId = ask.Flag;
            CoRows.Note(item, ArapExGainReq.Title(ask));
            return true;
        }
    }
}
