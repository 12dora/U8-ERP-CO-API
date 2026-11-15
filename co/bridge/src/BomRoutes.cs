using System.Collections.Generic;

namespace U8Co
{
    // 物料清单（type=bom）的写路由分派和登录前校验。Dispatch.Handle 先问这里，返回 null 再走原来的路由表。
    // 读取不在这里：VoucherRead 直接调 BomRead.Load（纯 SQL，读线程）。关闭、生单在登录前就被 Kinds 的开关挡住。
    // 写路由都走 U8API（BomCom），与生产订单一样在 U8Resolve 里调用，登录子系统用 BO（Kinds.VerifySub）。
    internal static class BomRoutes
    {
        internal const string KindName = "bom";
        public const string CreateRule = "write:bom:create";
        public const string UpdateRule = "write:bom:update";
        public const string DeleteRule = "write:bom:delete";
        public const string VerifyRule = "write:bom:verify";
        public const string UnverifyRule = "write:bom:unverify";
        const string V = "/u8co/v1/vouchers/";

        public static ApiResult Try(WorkContext ctx, string path)
        {
            if (ctx == null || ctx.Item == null || ctx.Item.Type == null || ctx.Item.Type.Name != KindName)
            {
                return null;
            }
            U8Resolve.Enter();
            try
            {
                return Route(ctx, path);
            }
            finally
            {
                U8Resolve.Leave();
            }
        }

        static ApiResult Route(WorkContext ctx, string path)
        {
            WorkItem item = ctx.Item;
            VoucherKind kind = item.Type;
            switch (path)
            {
                case V + "verify":
                    return BomState.Verify(ctx, kind, item.Id, item.Action);
                case V + "create":
                    return PuAppRoutes.StampNewId(ctx, BomCreate.Run(ctx, kind, item.Head, item.Lines));
                case V + "update":
                    return BomEdit.Run(ctx, kind, item.Id, item.Head, item.Lines ?? new object[0]);
                case V + "delete":
                    return BomState.Delete(ctx, kind, item.Id);
            }
            return null;
        }

        // 登录前：新增、修改的表头表体按 BomReq 校验（400 不登录）；新增、修改、删除换成 BO 登录（审核在 CheckKind 里已换）。
        internal static void Check(WorkItem item, string path)
        {
            if (path == V + "create")
            {
                BomReq.ParseCreate(item.Head, item.Lines);
            }
            else if (path == V + "update")
            {
                BomReq.ParseUpdate(item.Head, item.Lines);
            }
            else if (path != V + "delete")
            {
                return;
            }
            item.SubId = item.Type.VerifySub;
        }

        // 数据权限按母件存货判断（列名与 PermRegistry 的物料清单写规则一致）。
        internal static List<Dictionary<string, object>> ParentRows(string invCode)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["inv_code"] = invCode;
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            rows.Add(row);
            return rows;
        }
    }
}
