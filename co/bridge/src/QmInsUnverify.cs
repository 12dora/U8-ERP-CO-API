using System.Collections.Generic;

namespace U8Co
{
    // 产品报检单（QM02）单独弃审（vouchers/verify action=unverify）。产品报检单不开放修改：
    // VoucherOperate("update") 不可用（见 docs/u8-notes.md「质检单修改」）。
    // U8 在保存时按 bProInspectAutoVerify 自动审核报检单；VoucherOperate("unconfirm") 正常（QmVerify.Unconfirm，自己提交，
    // 删除前的弃审同一条路径），confirm 对报检单总是失败（见 QmVerify），所以审核仍然 400、不开放。
    // 闸门：功能权限 write:qm_product_inspect:unverify（弃审按钮 QmSpec.UnverifyAuth）、数据权限按每一行、已审核、还没有检验单。
    internal static class QmInsUnverify
    {
        public const string Kind = "qm_product_inspect";
        public const string OnlyUnverify = "产品报检单只支持弃审（action=unverify）：审核由 U8 在保存时按选项自动完成";

        public static bool Handles(VoucherKind kind)
        {
            return kind != null && kind.Name == Kind;
        }

        // QmReq.Check 的钩子：产品报检单的 vouchers/verify 只收 unverify，登录前 400。
        public static void PreLogin(WorkItem item, string path)
        {
            if (path == "/u8co/v1/vouchers/verify" && item != null && Handles(item.Type) && item.Action != "unverify")
            {
                throw BridgeException.BadField("action", OnlyUnverify);
            }
        }

        public static ApiResult Run(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            if (action != "unverify")
            {
                throw BridgeException.BadField("action", OnlyUnverify);
            }
            QmSpec spec = QmSpec.Of(kind);
            PermContext perm = QmGen.RequireRule(ctx, spec, "unverify");
            Dictionary<string, object> doc = QmDoc.Need(ctx.Conn, spec, id);
            QmGen.CheckRows(perm, spec, "unverify", QmDoc.PermRows(ctx.Conn, spec, id, doc));
            if (!QmDoc.Verified(doc))
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            QmDoc.RequireNoCheck(ctx.Conn, id, "弃审");
            Dictionary<string, object> after;
            using (QmCo co = QmCo.Open(ctx, spec))
            {
                after = QmVerify.Unconfirm(ctx, co, spec, id);
            }
            Dictionary<string, object> state = CoRows.StateOf(after);
            state["verified"] = false;
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = spec.Kind;
            body["id"] = id;
            body["code"] = CoRows.Col(after, "code");
            body["action"] = action;
            body["state"] = state;
            return ApiResult.Ok(body);
        }
    }
}
