namespace U8Co
{
    // 桥自己校验或自己写库的几类档案写入的分派（ArcRoutes 调用）：
    // 收付款协议（ArcAgreement，受控 SQL）、自定义项档案和客户存货对照的修改（ArcPairEdit，受控 SQL）、存货 Inventory_Sub 列的取值（ArcInvSub）。
    internal static class ArcKindWrites
    {
        // 登录前的校验，出错 400。
        public static void Check(ArcReq req)
        {
            if (ArcAgreement.Is(req.Kind))
            {
                ArcAgreement.Check(req);
            }
            ArcPairEdit.Check(req);
            ArcInvSub.Check(req);
        }

        // 新增、修改、删除；不归这里的返回 null。
        public static ApiResult Write(WorkContext ctx, ArcReq req)
        {
            if (ArcAgreement.Is(req.Kind))
            {
                return ArcAgreement.Write(ctx, req);
            }
            if (req.Op == "update" && ArcPairEdit.Is(req.Kind))
            {
                return ArcPairEdit.Update(ctx, req);
            }
            return null;
        }
    }
}
