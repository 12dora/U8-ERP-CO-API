using System.Collections.Generic;

namespace U8Co
{
    // 路由表（Dispatch.BuildMap）的账务处理一段，从 Dispatch.cs 拆出（文件行数上限）。
    internal static partial class Dispatch
    {
        // 第二级写入（只对测试账套开放）的账务处理路由：坏账处理、期初记账、期初单据、月末结账、存货核算。
        static void AddLedger(Dictionary<string, Handler> map)
        {
            // 坏账处理：坏账发生、坏账收回、计提坏账准备（ArapBad 按 action 分派，只对测试账套开放）。
            map.Add(ArapBadReq.Path, ArapBad.Run);
            // 期初记账 / 取消记账（OpeningPost：采购管理；存货核算交给 OpeningIa）。
            map.Add(OpeningPostReq.Path, OpeningPost.Run);
            // 应收 / 应付期初单据（OpeningsArap）。
            map.Add(OpeningsArapReq.Path, OpeningsArap.Run);
            // 月末结账 / 取消结账（PeriodClose，只对测试账套开放）。
            map.Add(PeriodCloseReq.Path, PeriodClose.Run);
            // 存货核算记账 / 恢复记账、期末处理 / 取消期末处理（IaRun，只对测试账套开放）。
            map.Add(IaReq.PostPath, IaRun.Run);
            map.Add(IaReq.PeriodEndPath, IaRun.Run);
            // 存货核算制单、取消制单（IaVoucher、IaVoucherDrop，只对测试账套开放）。
            map.Add(IaVoucherReq.Path, IaVoucher.Run);
            map.Add(IaVoucherReq.DropPath, IaVoucherDrop.Run);
            // 期间损益结转、自定义转账（GlTransfer，只对测试账套开放）。
            map.Add(GlTransferReq.PnlPath, GlTransfer.Run);
            map.Add(GlTransferReq.CustomPath, GlTransfer.Run);
        }
    }
}
