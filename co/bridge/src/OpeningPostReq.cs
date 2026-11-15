using System.Collections.Generic;

namespace U8Co
{
    // openings/post 的请求：module（pu 采购管理、ia 存货核算）、action（post 期初记账 / unpost 取消记账）。
    internal sealed class OpeningAsk
    {
        public string Module;
        public bool Post;

        public string ActionText()
        {
            return Post ? "post" : "unpost";
        }
    }

    // openings/post（期初记账 / 取消记账；加存货核算 ia）在登录前的校验（400）：不带 type、id，登录子系统按模块（pu → PU，ia → IA）。
    // 处理在 OpeningPost（存货核算在 OpeningIa）；锁键 "opening:<模块>"，另持全局写闸门（WriteGate）。
    internal static class OpeningPostReq
    {
        internal const string Path = "/u8co/v1/openings/post";
        // 审计 action：opening_post / opening_unpost（入队前的拒绝一律记 opening_post）。
        public const string Action = "opening_post";
        public const string UndoAction = "opening_unpost";
        // 权限规则键（PermRegistryOpening）。
        public const string PuRule = "write:opening:pu";
        public const string IaRule = "write:opening:ia";
        public const string ModulesHint = "module 只能是 pu（采购管理）或 ia（存货核算）";

        public static OpeningAsk Parse(Dictionary<string, object> body)
        {
            OpeningAsk ask = new OpeningAsk();
            ask.Module = Module(Requests.Field(body, "module"));
            string action = Requests.Field(body, "action") as string;
            if (action == null)
            {
                throw BridgeException.BadField("action", "缺少字段 action 或不是字符串").WithHint("action 只能是 post 或 unpost");
            }
            if (action != "post" && action != "unpost")
            {
                throw BridgeException.BadField("action", "action 只能是 post 或 unpost");
            }
            ask.Post = action == "post";
            return ask;
        }

        static string Module(object raw)
        {
            if (raw == null)
            {
                throw BridgeException.BadField("module", "缺少字段 module").WithHint(ModulesHint);
            }
            string module = raw as string;
            if (module == null)
            {
                throw BridgeException.BadField("module", "module 必须是字符串").WithHint(ModulesHint);
            }
            if (module != "pu" && module != "ia")
            {
                throw BridgeException.BadField("module", "不支持的模块 " + Clip(module)).WithHint(ModulesHint);
            }
            return module;
        }

        static string Clip(string text)
        {
            return text.Length > 20 ? text.Substring(0, 20) + "…" : text;
        }

        // 登录子系统：pu → PU，ia → IA。
        public static string SubOf(OpeningAsk ask)
        {
            return ask.Module.ToUpperInvariant();
        }

        public static string AuditAction(OpeningAsk ask)
        {
            return ask.Post ? Action : UndoAction;
        }

        public static string RuleKey(OpeningAsk ask)
        {
            return "write:opening:" + ask.Module;
        }

        // 同一模块的期初记账 / 取消记账串行。请求体不合法时（登录前已 400）返回空。
        public static string[] LockKeys(Dictionary<string, object> body)
        {
            try
            {
                return new string[] { "opening:" + Parse(body).Module };
            }
            catch (BridgeException)
            {
                return new string[0];
            }
        }
    }
}
