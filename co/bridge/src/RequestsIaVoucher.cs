using System.Collections.Generic;

namespace U8Co
{
    // ia/voucher、ia/voucher/delete（存货核算制单、取消制单）在登录前的处理：不带 type、id；先校验字段（400，IaVoucherReq），
    // 再查第二级写入开关和测试账套名单（403），都在登录 U8 之前。登录子系统 IA。处理在 IaVoucher、IaVoucherDrop。
    internal static partial class Requests
    {
        // 存货核算的路由：记账、期末处理（IaReq），制单、取消制单（IaVoucherReq，ids 里各项自带存货核算单据类型）。都不带 type、id。
        static bool IsIaRoute(string path)
        {
            return IaReq.IsPath(path) || IaVoucherReq.IsPath(path);
        }

        // 不是这两条路由返回 false，不动任务。
        static bool ApplyIaVoucher(Dictionary<string, object> body, WorkItem item, string path)
        {
            if (!IaVoucherReq.IsPath(path))
            {
                return false;
            }
            string title;
            if (path == IaVoucherReq.DropPath)
            {
                title = "存货核算取消制单 " + IaVoucherReq.ParseDrop(body).PzId;
            }
            else
            {
                title = IaVoucherReq.Title(IaVoucherReq.Parse(body));
            }
            TestAccountGate.Require(item, IaVoucherReq.TestOnlyOf(path));
            item.SubId = IaReq.Sub;
            item.Action = IaVoucherReq.ActionOf(path);
            CoRows.Note(item, title);
            return true;
        }
    }
}
