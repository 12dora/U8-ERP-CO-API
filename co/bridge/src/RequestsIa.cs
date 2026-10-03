using System.Collections.Generic;

namespace U8Co
{
    // ia/post、ia/period_end（存货核算记账 / 恢复记账、期末处理 / 取消期末处理）在登录前的处理：不带 type、id；
    // 字段 fiscal_year、period、action，记账另有 on_uncosted（IaReq）。先校验字段（400），再查测试账套名单（403 test_account_only），
    // 都在登录 U8 之前。登录子系统 IA。审计 action：入队前按路由记 ia_post / ia_period_end，解析后按动作改。处理在 IaRun。
    internal static partial class Requests
    {
        // 期初记账、月末结账、存货核算记账与期末处理：不带 type、id 的账务处理路由（Typeless）。
        static bool IsLedgerRoute(string path)
        {
            // 取消应收冲应付 / 应付冲应收 / 并账（ArapProcCancelReq）、处理制单（RequestsProcVoucher）同样不带 type、id。
            return path == OpeningPostReq.Path || path == OpeningsArapReq.Path || path == PeriodCloseReq.Path
                || IaReq.IsPath(path) || ArapProcCancelReq.IsPath(path) || IsProcVoucher(path)
                // 汇兑损益、取消汇兑损益（ArapExGainReq）同样不带 type、id。
                || ArapExGainReq.IsPath(path)
                // 坏账处理（ArapBadReq）同样不带 type、id。
                || ArapBadReq.IsPath(path)
                // 期间损益结转、自定义转账（GlTransferReq）同样不带 type、id。
                || GlTransferReq.IsPath(path)
                // 单张票据读取（NotesReadReq）：type 是票据类型（不是 VoucherKind），id 由 NotesReadReq 自己解析。
                || NotesReadReq.IsPath(path);
        }

        // 审计 action（PeriodAction 的下一档）：存货核算，其余交给 BatchAction。
        static string IaAction(string path)
        {
            // 处理制单 arap_process_voucher 在 ProcVoucherAction（RequestsProcVoucher.cs），再交给 BatchAction。
            // 汇兑损益：exchange_gain / exchange_gain_cancel（ArapExGainReq.ActionOf）。
            if (ArapExGainReq.IsPath(path))
            {
                return ArapExGainReq.ActionOf(path);
            }
            // 坏账处理：入队前记 bad_debt，解析后按动作改成 bad_debt_<action>（ApplyBadDebt）。
            if (ArapBadReq.IsPath(path))
            {
                return ArapBadReq.AuditAction;
            }
            // 期间损益结转、自定义转账：gl_transfer_pnl / gl_transfer_custom（GlTransferReq.ActionOf）。
            if (GlTransferReq.IsPath(path))
            {
                return GlTransferReq.ActionOf(path);
            }
            return IaReq.IsPath(path) ? IaReq.DefaultAction(path) : ProcVoucherAction(path);
        }

        // 不是 ia/post、ia/period_end 返回 false，不动任务。
        static bool ApplyIa(Dictionary<string, object> body, WorkItem item, string path)
        {
            if (!IaReq.IsPath(path))
            {
                return false;
            }
            IaAsk ask = IaReq.Parse(path, body);
            IaReq.RequireTestAccount(item);
            item.SubId = IaReq.Sub;
            item.Action = IaReq.AuditAction(ask);
            item.WaitMs = IaReq.LongWait(item.Config);
            CoRows.Note(item, IaReq.Title(ask) + " " + IaReq.MonthKey(ask));
            return true;
        }
    }
}
