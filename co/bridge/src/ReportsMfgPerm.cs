using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 生产类报表的数据权限。规则取报表自己的 report:<name>（对象同对应的单据类型：产成品入库单、生产订单），
    // 但这里汇总的是行，不是单据：规则里按表体登记的对象（存货等）改成对行逐行过滤，只统计有权限的行
    // （比单据级「表体有一行放行就保留」更严）；表头对象过滤 headAlias 上的同名列。
    // 开关没开、账套主管、该对象的数据权限管理员都不过滤（PermSql.AppendObj 里判断）；ctx 为 null（自检）时不过滤。
    // ApplyBody 给列表 APPLY 合计用：只过滤表体行（存货、仓库），表头条件仍在外层 PermHook。
    internal static class ReportsMfgPerm
    {
        // 把规则里全部受控对象的条件追加到 WHERE 末尾（ORDER BY / GROUP BY 之前）。
        // headAlias 是表头（或订单行）的别名，rowAlias 是表体行的别名；两者可以相同。
        internal static void Apply(StringBuilder sql, List<object> ps, WorkContext ctx, string key, string headAlias,
            string rowAlias)
        {
            PermContext p = ReadPerm(ctx);
            if (p == null)
            {
                return;
            }
            PermRule rule = ctx.PermRule ?? PermRegistry.ForKey(key);
            if (rule == null)
            {
                throw new BridgeException(403, "no_permission", "该接口没有登记权限规则");
            }
            for (int i = 0; i < rule.Objs.Length; i++)
            {
                PermObj o = rule.Objs[i];
                if (o.Mode == PermObj.Body)
                {
                    PermSql.AppendObj(sql, ps, p, RowObj(o), rowAlias);
                }
                else
                {
                    PermSql.AppendObj(sql, ps, p, o, headAlias);
                }
            }
        }

        // 只把表体对象改成对行过滤（合计、APPLY 用）：没有存货/仓库权限的行不进合计。
        internal static void ApplyBody(StringBuilder sql, List<object> ps, WorkContext ctx, string key, string rowAlias)
        {
            PermContext p = ReadPerm(ctx);
            if (p == null)
            {
                return;
            }
            PermRule rule = ctx.PermRule ?? PermRegistry.ForKey(key);
            if (rule == null)
            {
                throw new BridgeException(403, "no_permission", "该接口没有登记权限规则");
            }
            for (int i = 0; i < rule.Objs.Length; i++)
            {
                PermObj o = rule.Objs[i];
                if (o.Mode == PermObj.Body)
                {
                    PermSql.AppendObj(sql, ps, p, RowObj(o), rowAlias);
                }
            }
        }

        // 表体对象按行过滤：同一个编码列，登记时可空的仍可空。
        static PermObj RowObj(PermObj o)
        {
            return o.Optional ? PermObj.Opt(o.Obj, o.Column) : PermObj.H(o.Obj, o.Column);
        }

        // 读路由上的权限快照；自检（ctx 为 null）或不在读路由上时为 null（不过滤）。
        static PermContext ReadPerm(WorkContext ctx)
        {
            if (ctx == null || ctx.Item == null || !PermRegistry.IsRead(ctx.Item.Path))
            {
                return null;
            }
            return PermCheck.Of(ctx);
        }
    }
}
