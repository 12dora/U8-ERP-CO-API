using System;
using System.Collections.Generic;

namespace U8Co
{
    // archives/<op>：get、list 只走 SQL（读线程，无 Session）；create、update、delete 走 U8SrvTrans.IClsCommon.Transact。
    // 客户档案的三条专用路由（ArcCustomerOps）走受控 SQL，不经 EAI。
    internal static class ArcRoutes
    {
        public static bool IsRead(string op)
        {
            return op == "get" || op == "list";
        }

        // 登录前校验，出错抛 400。
        public static void Check(string op, Dictionary<string, object> body)
        {
            // 名称解析（ArcResolve）：请求体是 items，不走 ArcReq。
            if (op == "resolve")
            {
                ArcResolveReq.Parse(body);
                return;
            }
            // 档案批量读取（ArcGetMany）：请求体是 archive + codes，每个编码按 get 校验。
            if (op == ArcGetMany.Op)
            {
                ArcGetMany.Parse(body);
                return;
            }
            if (ArcCustomerOps.Is(op))
            {
                ArcCustomerOps.Check(op, body);
                return;
            }
            ArcReq.Parse(op, body);
        }

        public static ApiResult Handle(WorkContext ctx, string op)
        {
            if (ctx == null || ctx.Item == null || ctx.Item.Body == null)
            {
                throw new BridgeException(500, "internal", "档案请求缺少请求体");
            }
            if (ArcCustomerOps.Is(op))
            {
                return ArcCustomerOps.Run(ctx, op);
            }
            ArcReq req = ArcReq.Parse(op, ctx.Item.Body);
            switch (req.Op)
            {
                case "get":
                    return ArcRead.Get(ctx, req);
                case "list":
                    return ArcRead.List(ctx, req);
                default:
                    return Write(ctx, req);
            }
        }

        static ApiResult Write(WorkContext ctx, ArcReq req)
        {
            ApiResult special = WriteSpecial(ctx, req);
            return special != null ? special : WriteStandard(ctx, req);
        }

        // 不走通用 EAI 的几类：币种、凭证类别（ArcGl：新增走 U8PzInsert 的 EAI 组件，修改、删除走受控 SQL）、
        // 客户和供应商的银行账户、联系人（ArcPartner）、固定资产卡片和设备台账（U8 官方 EAI 经分发器）、
        // 汇率（新增走 EAI 分发器，修改、删除走受控 SQL，ArcExchWrite）、收付款协议和自定义项档案、客户存货对照的修改（受控 SQL，ArcKindWrites）。
        // 不归这里的返回 null。
        static ApiResult WriteSpecial(WorkContext ctx, ArcReq req)
        {
            if (ArcGlKinds.Owns(req.Kind))
            {
                return ArcGl.Write(ctx, req);
            }
            if (ArcPartner.Is(req.Kind))
            {
                return ArcPartner.Write(ctx, req);
            }
            ApiResult fa = FaWrites.Write(ctx, req);
            if (fa != null)
            {
                return fa;
            }
            if (ArcExch.Is(req.Kind))
            {
                return ArcExchWrite.Write(ctx, req);
            }
            return ArcKindWrites.Write(ctx, req);
        }

        // 项目档案走受控 SQL，不经 EAI：新增、修改在 ArcProjectWrite，删除在 ArcProjectDel；其余走 EAI（ArcWrite）。
        static ApiResult WriteStandard(WorkContext ctx, ArcReq req)
        {
            bool project = ArcKindRo.IsProject(req.Kind);
            if (req.Op == "create")
            {
                return project ? ArcProjectWrite.Create(ctx, req) : ArcWrite.Create(ctx, req);
            }
            if (req.Op == "update")
            {
                return project ? ArcProjectWrite.Update(ctx, req) : ArcWrite.Update(ctx, req);
            }
            return project ? ArcProjectDel.Delete(ctx, req) : ArcWrite.Delete(ctx, req);
        }
    }
}
