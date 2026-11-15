using System.Collections.Generic;

namespace U8Co
{
    // periods/close（月末结账 / 取消结账）在登录前的处理：不带 type、id；字段 module、fiscal_year、period、action、through
    // （PeriodCloseReq）。先校验字段（400），再查测试账套名单（403 test_account_only），都在登录 U8 之前。
    // 登录子系统按模块（through 用 GL）。审计 action：入队前记 period_close，解析后取消结账改成 period_reopen。处理在 PeriodClose。
    internal static partial class Requests
    {
        // 审计 action（OpeningAction 的下一档）：月末结账，其余交给 IaAction（存货核算，再往后是 BatchAction）。
        static string PeriodAction(string path)
        {
            return path == PeriodCloseReq.Path ? PeriodCloseReq.Action : IaAction(path);
        }

        // 不是 periods/close 返回 false，不动任务。
        static bool ApplyPeriod(Dictionary<string, object> body, WorkItem item, string path)
        {
            if (path != PeriodCloseReq.Path)
            {
                return false;
            }
            PeriodAsk ask = PeriodCloseReq.Parse(body);
            PeriodCloseReq.RequireTestAccount(item);
            item.SubId = PeriodCloseReq.SubOf(ask);
            item.Action = PeriodCloseReq.AuditAction(ask);
            // 存货核算有数据的月份走脚本（PeriodIa），可能要几分钟：存货核算和 through 按存货核算脚本的长等待。
            if (ask.Through || ask.Mod == PeriodModules.Ia)
            {
                item.WaitMs = IaReq.LongWait(item.Config);
            }
            string what = ask.Through ? "逐月结账到 " : (ask.Close ? "月末结账 " : "取消结账 ") + PeriodModules.Code(ask.Mod) + " ";
            CoRows.Note(item, what + KeyPart(ask.Year) + "-" + KeyPart(ask.Period));
            return true;
        }
    }
}
