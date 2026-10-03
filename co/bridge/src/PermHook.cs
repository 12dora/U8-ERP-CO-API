using System;
using System.Collections;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 处理函数里的一行式数据权限挂钩。规则和权限快照取自 PermGate 填好的 ctx.PermRule / ctx.Perm。
    // 只对读路由生效：写路由（及写后回读）里调用时直接放过，写路由的数据权限不在本契约范围。
    // 开关没开、账套主管、该对象的数据权限管理员都不过滤；列表变少照常 200，单张越权 403，不存在仍由调用方 404。
    internal static class PermHook
    {
        // 列表：把当前路由规则的全部受控对象条件追加到 WHERE（放在 ORDER BY / GROUP BY 之前）。alias 是表头别名。
        public static void Where(StringBuilder sb, List<object> ps, WorkContext ctx, string alias)
        {
            PermRule rule;
            PermContext p = Scope(ctx, out rule);
            if (p != null)
            {
                PermSql.AppendRule(sb, ps, p, rule, alias);
            }
        }

        // 单张单据（vouchers/load、workflow/state、history）：单据已确认存在之后调用。
        // 用与列表同一套 SQL 条件在表头表上探一次：探不到就是越权，403。
        public static void Voucher(WorkContext ctx, VoucherKind kind, int id)
        {
            PermRule rule;
            PermContext p = Scope(ctx, out rule);
            if (p == null || kind == null || !Needed(p, rule))
            {
                return;
            }
            if (!Probe(ctx, kind, id, p, rule, null))
            {
                throw new BridgeException(403, "no_permission", PermCheck.DeniedData);
            }
        }

        // vouchers/load 在读单据（CO 读取）之前调用：带类型条件探一次。放行或不需过滤返回 true；
        // 单据（该类型）存在但越权 403；按表头表查不到时返回 false，由读取自己回 404，读到后调用方再用 Voucher 核对。
        public static bool VoucherBefore(WorkContext ctx, VoucherKind kind, int id)
        {
            PermRule rule;
            PermContext p = Scope(ctx, out rule);
            if (p == null || kind == null || !Needed(p, rule))
            {
                return true;
            }
            string cond = CondOf(kind);
            if (Probe(ctx, kind, id, p, rule, cond))
            {
                return true;
            }
            if (Probe(ctx, kind, id, null, null, cond))
            {
                throw new BridgeException(403, "no_permission", PermCheck.DeniedData);
            }
            return false;
        }

        static bool Probe(WorkContext ctx, VoucherKind kind, int id, PermContext p, PermRule rule, string cond)
        {
            List<object> ps = new List<object>();
            string sql = ProbeSql(kind, id, p, rule, ps, cond);
            return Rows.One(ctx.Conn, sql, ps.ToArray()) != null;
        }

        // 列表的类型条件（收付款单按 cFlag / cVouchType 等），没有列表定义时为 null。
        static string CondOf(VoucherKind kind)
        {
            ListKind list = ListKinds.Find(kind.Name);
            return list == null || !list.Has(ListKind.Cond) ? null : list[ListKind.Cond];
        }

        internal static string ProbeSql(VoucherKind kind, int id, PermContext p, PermRule rule, List<object> ps)
        {
            return ProbeSql(kind, id, p, rule, ps, null);
        }

        internal static string ProbeSql(VoucherKind kind, int id, PermContext p, PermRule rule, List<object> ps,
            string cond)
        {
            StringBuilder sb = new StringBuilder("SELECT TOP 1 1 AS x FROM ").Append(kind.HeadTable).Append(" h WHERE h.");
            sb.Append(kind.IdColumn).Append("=?");
            ps.Add(id);
            if (cond != null)
            {
                sb.Append(" AND ").Append(cond);
            }
            PermSql.AppendRule(sb, ps, p, rule, "h");
            return sb.ToString();
        }

        // 总账凭证读取：分录（含 ccode 列）的科目全部放行，有一条不放行就 403（从严，见 PermSql.AppendGlVoucher）。
        public static void Lines(WorkContext ctx, IList lines)
        {
            PermRule rule;
            PermContext p = Scope(ctx, out rule);
            if (p != null)
            {
                PermCheck.CheckVoucher(p, rule, null, lines);
            }
        }

        // 档案读取：按档案编码判断（项目的编码是「大类:编码」）。
        public static void Archive(WorkContext ctx, string code)
        {
            PermRule rule;
            PermContext p = Scope(ctx, out rule);
            if (p != null && !ArchiveOk(ctx, p, rule, code))
            {
                throw new BridgeException(403, "no_permission", PermCheck.DeniedRow);
            }
        }

        // ctx 为 null（自检）时只按快照判断。
        internal static bool ArchiveOk(WorkContext ctx, PermContext p, PermRule rule, string code)
        {
            string text = code ?? "";
            for (int i = 0; i < rule.Objs.Length; i++)
            {
                PermObj o = rule.Objs[i];
                if (o.Mode == PermObj.Pair)
                {
                    int at = text.IndexOf(':');
                    string cls = at < 0 ? "" : text.Substring(0, at);
                    if (!ItemOk(ctx, p, cls, at < 0 ? text : text.Substring(at + 1)))
                    {
                        return false;
                    }
                }
                else if (!CodeOk(ctx, p, o.Obj, text))
                {
                    return false;
                }
            }
            return true;
        }

        // 报表里按单个编码判断（如物料清单的母件、子件）。不在读路由上时总是放行。
        public static bool Allows(WorkContext ctx, string obj, string code)
        {
            PermRule rule;
            PermContext p = Scope(ctx, out rule);
            return p == null || CodeOk(ctx, p, obj, code);
        }

        // 授权不超过 PermSql.MaxInline 个时按快照判断；更多时列表走 AA_HoldAuth 实时条件，这里也实时查，两边一致。
        static bool CodeOk(WorkContext ctx, PermContext p, string obj, string code)
        {
            string text = code == null ? "" : code.Trim();
            if (!p.Controls(obj) || ctx == null || text.Length == 0 || p.CodesOf(obj).Count <= PermSql.MaxInline)
            {
                return p.Allow(obj, text);
            }
            List<object> ps = new List<object>();
            string sql = PermSql.LiveCodeSql(ps, p, obj, text);
            return Rows.One(ctx.Conn, sql, ps.ToArray()) != null;
        }

        static bool ItemOk(WorkContext ctx, PermContext p, string cls, string code)
        {
            string c = cls == null ? "" : cls.Trim();
            string k = code == null ? "" : code.Trim();
            if (!p.Controls(PermObj.Item) || ctx == null || c.Length == 0 || k.Length == 0
                || p.CodesOf(PermObj.Item).Count <= PermSql.MaxInline)
            {
                return p.AllowItem(c, k);
            }
            List<object> ps = new List<object>();
            string sql = PermSql.LiveItemSql(ps, p, c, k);
            return Rows.One(ctx.Conn, sql, ps.ToArray()) != null;
        }

        public static void Code(WorkContext ctx, string obj, string code)
        {
            if (!Allows(ctx, obj, code))
            {
                throw new BridgeException(403, "no_permission", PermCheck.DeniedData);
            }
        }

        // 审批待办：去掉操作员没有查询权限的单据类型（待办本来就是操作员自己的，不按档案对象过滤）。
        public static void Tasks(WorkContext ctx, List<Dictionary<string, object>> tasks)
        {
            PermRule rule;
            PermContext p = Scope(ctx, out rule);
            if (p == null || p.Supervisor || tasks == null)
            {
                return;
            }
            tasks.RemoveAll(delegate(Dictionary<string, object> task)
            {
                object type;
                task.TryGetValue("type", out type);
                PermRule typeRule = PermRegistry.ForKey("voucher:" + Values.Text(type));
                return typeRule == null || !p.HasAny(typeRule.Auths);
            });
        }

        static bool Needed(PermContext p, PermRule rule)
        {
            for (int i = 0; i < rule.Objs.Length; i++)
            {
                if (p.Controls(rule.Objs[i].Obj))
                {
                    return true;
                }
            }
            return false;
        }

        // 读路由返回权限快照和规则；写路由返回 null（不过滤）。读路由缺规则时 403（同 PermGate，一律拒绝）。
        static PermContext Scope(WorkContext ctx, out PermRule rule)
        {
            rule = null;
            if (ctx == null || ctx.Item == null || !PermRegistry.IsRead(ctx.Item.Path))
            {
                return null;
            }
            rule = ctx.PermRule ?? PermRegistry.Find(ctx.Item);
            if (rule == null)
            {
                PermGate.Validate(ctx.Item);
                throw new BridgeException(403, "no_permission", "该接口没有登记权限规则");
            }
            ctx.PermRule = rule;
            return PermCheck.Of(ctx);
        }
    }
}
