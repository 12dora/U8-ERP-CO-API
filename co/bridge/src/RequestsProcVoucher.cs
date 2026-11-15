using System.Collections.Generic;

namespace U8Co
{
    // arap/process/voucher（处理制单）在登录前的处理：不带 type、id；字段见 ArapProcVoucherReq.Spec。先校验字段（400），
    // 汇兑损益（SYRAR / SYPAP）、坏账处理（HZAR）再查测试账套名单（403 test_account_only），都在登录 U8 之前。登录子系统就是 flag。处理在 ArapProcVoucher。
    internal static partial class Requests
    {
        static bool IsProcVoucher(string path)
        {
            return path == ArapProcVoucherReq.Path;
        }

        // 审计 action（IaAction 的下一档）：处理制单，其余交给 BatchAction。
        static string ProcVoucherAction(string path)
        {
            return IsProcVoucher(path) ? ArapProcVoucherReq.Action : BatchAction(path);
        }

        // 不是 arap/process/voucher 返回 false，不动任务。
        static bool ApplyProcVoucher(Dictionary<string, object> body, WorkItem item, string path)
        {
            if (!IsProcVoucher(path))
            {
                return false;
            }
            ProcVoucherAsk ask = ArapProcVoucherReq.Parse(body);
            ArapProcVoucherReq.TestGate(item, ask);
            item.SubId = ask.Flag;
            CoRows.Note(item, "处理制单 " + ArapProcVoucherReq.Title(ask.Style) + " " + string.Join(",", ask.CancelNos.ToArray()));
            return true;
        }
    }
}
