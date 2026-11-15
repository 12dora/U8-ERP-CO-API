using System.Collections.Generic;

namespace U8Co
{
    // arap/voucher（应收 / 应付制单）：带 type、id（与 vouchers/* 一样由公共校验解析，审计里有单据类型和 id），
    // 另收 flag、sign、voucher_date、digest（ArapVoucherReq）。合并制单用 ids = [{type, id}, …] 代替 id：公共校验不要求 id，
    // 顶层 type 照常必填（API 层省略时按 ids 补上）。arap/voucher/delete（取消制单）不带 type、id：flag、pz_id。
    // 两条路由的登录子系统都是 flag。处理在 ArapVoucher、ArapVoucherDrop。
    internal static partial class Requests
    {
        internal const string ArapVoucherPath = "/u8co/v1/arap/voucher";
        internal const string ArapVoucherDropPath = "/u8co/v1/arap/voucher/delete";

        static bool IsArapVoucher(string path)
        {
            return path == ArapVoucherPath || path == ArapVoucherDropPath;
        }

        // 合并制单（带 ids）：公共校验不解析顶层 id（ApplyId）。
        static bool ArapVoucherMerge(Dictionary<string, object> body, string path)
        {
            return path == ArapVoucherPath && ArapVoucherReq.Merging(body);
        }

        static string ArapVoucherAction(string path)
        {
            return path == ArapVoucherDropPath ? ArapVoucherReq.DropAction : ArapVoucherReq.Action;
        }

        static void ApplyArapVoucher(Dictionary<string, object> body, WorkItem item)
        {
            if (item.Path == ArapVoucherDropPath)
            {
                VoucherDropAsk drop = ArapVoucherReq.ParseDrop(body);
                item.SubId = drop.Flag;
                CoRows.Note(item, "取消制单 " + drop.PzId);
                return;
            }
            VoucherAsk ask = ArapVoucherReq.Parse(body);
            item.SubId = ask.Flag;
            if (ask.Merge)
            {
                List<string> ids = new List<string>();
                foreach (int id in ask.Ids)
                {
                    ids.Add(KeyPart(id));
                }
                CoRows.Note(item, "合并制单 " + ask.Kind + " " + string.Join(",", ids.ToArray()));
                return;
            }
            CoRows.Note(item, "制单 " + ask.Kind + " " + KeyPart(ask.Id));
        }
    }
}
