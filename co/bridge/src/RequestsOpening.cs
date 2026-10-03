using System.Collections.Generic;

namespace U8Co
{
    // openings/post（期初记账 / 取消记账）在登录前的处理：不带 type、id；字段 module、action（OpeningPostReq）。
    // 登录子系统按模块（pu → PU，ia → IA；ia 按长任务等待）。审计 action：入队前记 opening_post，解析后取消记账改成 opening_unpost。处理在 OpeningPost。
    // openings/arap（应收 / 应付期初单据）：不带 type；字段 side、action 等（OpeningsArapReq），登录子系统 AR / AP。
    // 审计 action：入队前记 opening_arap，解析后改成 opening_arap_<action>。处理在 OpeningsArap。
    // 两条路由都先校验字段（400），再查测试账套名单（403 test_account_only，TestAccountGate），都在登录 U8 之前。
    internal static partial class Requests
    {
        // 审计 action（RequestsP4.ActionP4 的最后一档）：期初记账、期初单据，其余交给 PeriodAction（月末结账，再往后是 BatchAction）。
        static string OpeningAction(string path)
        {
            if (path == OpeningsArapReq.Path)
            {
                return OpeningsArapReq.Action;
            }
            return path == OpeningPostReq.Path ? OpeningPostReq.Action : PeriodAction(path);
        }

        // 不是 openings/post、openings/arap 返回 false，不动任务。
        static bool ApplyOpening(Dictionary<string, object> body, WorkItem item, string path)
        {
            if (path == OpeningsArapReq.Path)
            {
                OpeningsArapAsk arap = OpeningsArapReq.Parse(body);
                TestAccountGate.Require(item, TestAccountGate.OpeningsArapText);
                item.SubId = OpeningsArapReq.SubOf(arap);
                item.Action = OpeningsArapReq.AuditAction(arap);
                CoRows.Note(item, "期初单据 " + arap.Side + " " + arap.Action + (arap.Create ? "" : " " + arap.Id));
                return true;
            }
            if (path != OpeningPostReq.Path)
            {
                return false;
            }
            OpeningAsk ask = OpeningPostReq.Parse(body);
            TestAccountGate.Require(item, TestAccountGate.OpeningPostText);
            item.SubId = OpeningPostReq.SubOf(ask);
            item.Action = OpeningPostReq.AuditAction(ask);
            if (ask.Module == "ia")
            {
                // 存货核算期初记账整批执行脚本，与 ia/post 一样按长任务等待（IaReq.LongWait）。
                item.WaitMs = IaReq.LongWait(item.Config);
            }
            CoRows.Note(item, (ask.Post ? "期初记账 " : "取消期初记账 ") + ask.Module);
            return true;
        }
    }
}
