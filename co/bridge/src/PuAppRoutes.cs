namespace U8Co
{
    // 请购单的写路由分派。Dispatch.Handle 先问这里，返回 null 再走原来的路由表。
    // 读取不在这里：VoucherRead.Load 直接调 PuApp.Load，读取后的处理（去口令列、截行、权限过滤）照常。
    // 登录前的闸门（Creatable / Deletable / Updatable / Closable、字段拦截）照常在 Requests 里做。
    internal static class PuAppRoutes
    {
        internal const string KindName = "purchase_requisition";
        const string V = "/u8co/v1/vouchers/";

        public static ApiResult Try(WorkContext ctx, string path)
        {
            return Mine(ctx) ? Route(ctx, path) : null;
        }

        static bool Mine(WorkContext ctx)
        {
            return ctx != null && ctx.Item != null && ctx.Item.Type != null && ctx.Item.Type.Name == KindName;
        }

        static ApiResult Route(WorkContext ctx, string path)
        {
            WorkItem item = ctx.Item;
            VoucherKind kind = item.Type;
            switch (path)
            {
                case V + "verify":
                    return PuApp.Verify(ctx, kind, item.Id, item.Action);
                case V + "create":
                    return StampNewId(ctx, PuApp.Create(ctx, kind, item.Head, item.Lines));
                case V + "update":
                    return PuApp.Update(ctx, kind, item.Id, item.Head, item.Lines ?? new object[0]);
                case V + "delete":
                    return PuApp.Delete(ctx, kind, item.Id);
                case V + "close":
                    return PuApp.Close(ctx, kind, item.Id, item.Action, item.LineIds);
            }
            return null;
        }

        // 同 Dispatch.StampNewId：新增成功后审计 id 改成新单据。物料清单（BomRoutes）也用它。
        internal static ApiResult StampNewId(WorkContext ctx, ApiResult result)
        {
            if (result == null || result.Status != 200 || result.Body == null)
            {
                return result;
            }
            object raw;
            int id = result.Body.TryGetValue("id", out raw) ? CoRows.AsId(raw) : 0;
            if (id > 0)
            {
                ctx.Item.HasId = true;
                ctx.Item.Id = id;
            }
            return result;
        }
    }
}
