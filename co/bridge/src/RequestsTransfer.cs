using System.Collections.Generic;

namespace U8Co
{
    // arap/transfer（应收冲应付 / 应付冲应收）在登录前的校验：不带 type、id；字段见 ArapTransferReq，登录子系统就是 flag。
    // 挂在核销一组（RequestsWriteoff 的 IsWriteoff / WriteoffAction / ApplyWriteoff）里：同样不带 type，审计 action 是 transfer。
    // 处理在 ArapTransfer。
    internal static partial class Requests
    {
        internal const string TransferPath = "/u8co/v1/arap/transfer";

        static bool IsTransfer(string path)
        {
            return path == TransferPath;
        }

        // 是转账路由时校验并定登录子系统，返回 true；否则返回 false。
        static bool ApplyTransfer(Dictionary<string, object> body, WorkItem item)
        {
            if (item.Path != TransferPath)
            {
                return false;
            }
            TransferAsk ask = ArapTransferReq.Parse(body);
            item.SubId = ask.Flag;
            CoRows.Note(item, ArapTransferRule.Title(ask.Flag) + " " + ask.Customer + " / " + ask.Vendor);
            return true;
        }
    }
}
