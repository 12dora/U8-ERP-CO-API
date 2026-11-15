using System;
using System.Collections.Generic;

namespace U8Co
{
    // 总账凭证路由 gl/vouchers/<op>。load、list 走读线程，只用 ctx.Conn，不碰 ctx.Session。
    internal static class GlRoutes
    {
        public static bool IsRead(string op)
        {
            return op == "load" || op == "list" || op == GlDigest.Op;
        }

        // 登录前校验，只抛 400。年度、期间开关、科目档案在登录后查。
        public static void Check(string op, Dictionary<string, object> body)
        {
            if (op == "list")
            {
                GlList.Parse(body);
                return;
            }
            // 凭证摘要（事件源）：fiscal_year、periods、closed_periods、after、limit、keys_only。
            if (op == GlDigest.Op)
            {
                GlDigest.Parse(body);
                return;
            }
            if (op == "create")
            {
                GlReq.Draft(body);
                return;
            }
            if (op == "update")
            {
                GlReq.Key(body, 0);
                GlReq.Draft(body);
                return;
            }
            // 记账、取消记账（CheckBook）。
            if (CheckBook(op, body))
            {
                return;
            }
            // 凭证附件列表（GlAttach）与 load 同样按 period、sign、no 定位。
            if (op == "load" || GlOps.Known(op) || GlAttach.Owns(op))
            {
                GlReq.Key(body, 0);
                return;
            }
            throw GlReq.Bad("不支持的总账凭证操作 " + (op ?? ""));
        }

        // 记账：period、vouchers、可选 fiscal_year（GlPostParse）。取消记账：可选 fiscal_year、period、vouchers（GlUnpostReq）。
        static bool CheckBook(string op, Dictionary<string, object> body)
        {
            if (op == "post")
            {
                GlPostParse.Parse(body, 0);
                return true;
            }
            if (op == GlUnpostReq.Op)
            {
                GlUnpostReq.Parse(body, 0);
                return true;
            }
            // 红字冲销（冲销已记账凭证）：period、sign、no、可选 fiscal_year、voucher_date（GlReverseReq）。
            if (op == GlReverseReq.Op)
            {
                GlReverseReq.Parse(body, 0);
                return true;
            }
            return false;
        }

        public static ApiResult Handle(WorkContext ctx, string op)
        {
            Dictionary<string, object> body = ctx.Item.Body;
            Check(op, body);
            if (op == "list")
            {
                return GlList.Run(ctx, GlList.Parse(body));
            }
            if (op == GlDigest.Op)
            {
                return GlDigest.Run(ctx, GlDigest.Parse(body));
            }
            if (op == "create")
            {
                return GlSave.Create(ctx, GlReq.Draft(body));
            }
            if (op == "post")
            {
                return GlPost.Run(ctx);
            }
            // 取消记账（测试账套，GlUnpost）。
            if (op == GlUnpostReq.Op)
            {
                return GlUnpost.Run(ctx);
            }
            // 红字冲销（GlReverse）。
            if (op == GlReverseReq.Op)
            {
                return GlReverse.Run(ctx);
            }
            GlKey key = GlReq.Key(body, GlState.LoginYear(ctx));
            if (op == "load")
            {
                return GlRead.Load(ctx, key);
            }
            if (op == "update")
            {
                return GlSave.Update(ctx, key, GlReq.Draft(body));
            }
            return GlOps.Run(ctx, key, op);
        }
    }
}
