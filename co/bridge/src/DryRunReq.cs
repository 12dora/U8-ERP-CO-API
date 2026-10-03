using System.Collections.Generic;

namespace U8Co
{
    // 请求体里的 dry_run（写预演）：只在写路由上认（WriteGate），取走后再交给原有的字段校验；读路由上照旧 400「含未知字段」。
    // arap/writeoff/auto 的 dry_run 是它自己的试算，不在这里取。旧版审核路由带 dry_run 一律 400。
    internal static class DryRunReq
    {
        public const string Field = "dry_run";
        // 入队前被拒（解析 400 等）时审计 dry_run 字段的值：模式还没查出来。查出后换成 rollback / validate。
        public const string Requested = "requested";

        // HTTP 线程上的审计草稿（入队前的拒绝、排队 / 等待失败都用它）记下预演。value 为空不动。
        public static void Mark(AuditDraft draft, string value)
        {
            if (draft != null && !string.IsNullOrEmpty(value))
            {
                draft.DryRun = value;
            }
        }

        // 返回 true 表示这是一次预演。不是写路由或是自动核销时不动请求体。
        public static bool Take(Dictionary<string, object> body, string path)
        {
            if (body == null || !body.ContainsKey(Field) || !Accepts(path))
            {
                return false;
            }
            object value = body[Field];
            body.Remove(Field);
            if (!(value is bool))
            {
                throw BridgeException.BadField(Field, "dry_run 只能是 true 或 false");
            }
            return (bool)value;
        }

        public static void RefuseLegacy(Dictionary<string, object> body, AuditDraft draft)
        {
            if (body != null && body.ContainsKey(Field))
            {
                Mark(draft, Requested);
                throw BridgeException.BadField(Field, "旧路由不支持预演，请用 vouchers/verify");
            }
        }

        // 预演不占用幂等键（IdemReq / IdemFlow 不经手预演）。
        public static void RefuseIdem(bool dry, IdemAsk idem)
        {
            if (dry && idem != null)
            {
                throw BridgeException.BadField(IdemReq.KeyField, "预演不能带 Idempotency-Key");
            }
        }

        // 类型、action、来源都已知之后（登录前）：查模式表，不支持的组合 400。
        public static void Apply(WorkItem item, Dictionary<string, object> body, bool dry)
        {
            item.DryRun = dry;
            if (!dry)
            {
                return;
            }
            string mode = DryRunModes.ModeOf(item, body);
            if (mode != DryRunModes.Rollback && mode != DryRunModes.Validate)
            {
                throw BridgeException.BadField(Field, "该操作不支持预演");
            }
            item.DryRunMode = mode;
        }

        // 该路由的请求体收不收 dry_run（meta 的 routes[].optional）。
        public static bool Accepts(string path)
        {
            bool legacy = path == "/u8co/v1/sale-orders/verify" || path == "/u8co/v1/dispatches/verify";
            return !legacy && path != Requests.WriteoffAutoPath && WriteGate.IsWrite(path);
        }

        // 自动核销的 dry_run=true 是试算（它自己的参数，不经 Take），同样不能带幂等键，否则试算结果会占用键被重放。
        // IdemReq.Take 在带了键时调用；dry_run 不是布尔值时交给 ArapAutoWriteoffReq 报 400。
        public static void RefusePlanIdem(Dictionary<string, object> body, string path)
        {
            object value;
            if (path != Requests.WriteoffAutoPath || body == null || !body.TryGetValue(Field, out value))
            {
                return;
            }
            if (value is bool && (bool)value)
            {
                throw BridgeException.BadField(IdemReq.KeyField, "预演不能带 Idempotency-Key");
            }
        }
    }
}
