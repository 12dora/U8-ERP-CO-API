using System;
using System.Collections.Generic;

namespace U8Co
{
    // 客户档案的受控 SQL 专用路由 archives/<op>：customer_person（ArcPerson）、customer_fields（ArcCusFields）、
    // customer_address（ArcCusAddr）、customer_address_add / customer_address_delete（ArcCusAddrRows）。op 即路径末段、写入分类的 op、规则键的最后一段。
    // 新增一条：加进 Ops，并在 Check、Run 各补一个分支；写闸门、写入分类、预演模式、锁键、只读例外都从 Ops 派生。
    internal static class ArcCustomerOps
    {
        internal static readonly string[] Ops = new string[]
        {
            ArcPerson.Op, ArcCusFields.Op, ArcCusAddr.Op, ArcCusAddrRows.AddOp, ArcCusAddrRows.DeleteOp
        };

        // archives/<op> 的全部 op：通用的 get、list、create、update、delete 加专用路由（Dispatch 的路由表用）。
        public static string[] AllOps()
        {
            List<string> all = new List<string>(new string[] { "get", "list", "create", "update", "delete" });
            all.AddRange(Ops);
            return all.ToArray();
        }

        public static bool Is(string op)
        {
            return op != null && Array.IndexOf(Ops, op) >= 0;
        }

        // 路径是 archives/<专用 op>。
        public static bool IsPath(string path)
        {
            return path != null && path.StartsWith(Requests.ArcRoot, StringComparison.Ordinal)
                && Is(path.Substring(Requests.ArcRoot.Length));
        }

        // 登录前校验，出错抛 400。
        public static void Check(string op, Dictionary<string, object> body)
        {
            if (op == ArcPerson.Op)
            {
                ArcPerson.Parse(body);
            }
            else if (op == ArcCusFields.Op)
            {
                ArcCusFields.Parse(body);
            }
            else if (op == ArcCusAddr.Op)
            {
                ArcCusAddr.Parse(body);
            }
            else if (ArcCusAddrRows.Is(op))
            {
                ArcCusAddrRows.Check(op, body);
            }
            else
            {
                throw Unknown(op);
            }
        }

        public static ApiResult Run(WorkContext ctx, string op)
        {
            if (op == ArcPerson.Op)
            {
                return ArcPerson.Run(ctx, op);
            }
            if (op == ArcCusFields.Op)
            {
                return ArcCusFields.Run(ctx, op);
            }
            if (op == ArcCusAddr.Op)
            {
                return ArcCusAddr.Run(ctx, op);
            }
            if (ArcCusAddrRows.Is(op))
            {
                return ArcCusAddrRows.Run(ctx, op);
            }
            throw Unknown(op);
        }

        // Ops 里登记了却没有分支：开发期错误，不当作某条已有路由处理。
        static BridgeException Unknown(string op)
        {
            return new BridgeException(500, "internal", "未处理的客户档案路由 " + (op ?? ""));
        }
    }
}
