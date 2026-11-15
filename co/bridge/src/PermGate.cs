using System;
using System.Collections.Generic;

namespace U8Co
{
    // 读路由的功能权限闸门：StaExec 在登录成功（写线程）或登录缓存命中（读线程）之后、进入处理函数之前调用。
    // 只用 ctx.Conn，不碰 ctx.Session。取到的权限快照和规则挂在 ctx.Perm / ctx.PermRule 上，
    // 处理函数用它们做记录级过滤（PermSql / PermCheck）。
    // 写路由（及 login-check、meta、health）不经过这里；总账写在 GlState.Permit 里自己查。
    internal static class PermGate
    {
        public static void Enter(WorkContext ctx)
        {
            if (ctx == null || ctx.Item == null || !PermRegistry.IsRead(ctx.Item.Path))
            {
                return;
            }
            // archives/resolve：一个请求含多类档案，处理函数逐项查（PermRegistry.PerItem、ArcResolve.Permit）。
            if (PermRegistry.PerItem(ctx.Item.Path))
            {
                return;
            }
            // 读路由没有登记规则时一律拒绝，不因漏登记而放行；但请求本身不合法时先按原来的校验回 400。
            PermRule rule = PermRegistry.Find(ctx.Item);
            if (rule == null)
            {
                Validate(ctx.Item);
                throw new BridgeException(403, "no_permission", "该接口没有登记权限规则");
            }
            PermContext p = PermCheck.Of(ctx);
            ctx.PermRule = rule;
            PermCheck.RequireRule(p, rule);
        }

        // 找不到规则时重跑该路由的请求校验（纯校验，登录前已跑过一次），不合法的输入保持 400。
        internal static void Validate(WorkItem item)
        {
            string path = item.Path ?? "";
            Dictionary<string, object> body = item.Body ?? new Dictionary<string, object>();
            if (path.StartsWith(Requests.ArcRoot, StringComparison.Ordinal))
            {
                ArcRoutes.Check(Requests.OpOf(path), body);
            }
            else if (path == Requests.ListPath || path == Requests.StockPath)
            {
                ListRoutes.Check(Requests.OpOf(path), body);
            }
            else if (path.StartsWith(Requests.GlRoot, StringComparison.Ordinal))
            {
                GlRoutes.Check(Requests.OpOf(path), body);
            }
            else if (path.StartsWith(Requests.ReportRoot, StringComparison.Ordinal))
            {
                Reports.Check(path.Substring(Requests.ReportRoot.Length), body);
            }
            else if (item.Type == null && path != "/u8co/v1/workflow/tasks")
            {
                throw new BridgeException(400, "bad_request", "缺少单据类型");
            }
        }
    }
}
