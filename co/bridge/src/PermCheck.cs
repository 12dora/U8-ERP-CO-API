using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 在内存里判断功能权限和单张单据 / 单行档案的数据权限。列表的过滤用 PermSql。
    // 功能权限不足、单据或档案不在数据权限内都是 403 no_permission；单据本身不存在仍由调用方回 404。
    internal static class PermCheck
    {
        public const string DeniedData = "没有该单据的数据权限";
        public const string DeniedRow = "没有该档案的数据权限";

        // 本请求的权限快照：PermGate 已取过就直接用。读路由按缓存取（60 秒）；写路由（如总账写）每次现读、不进缓存，
        // 在 U8 里收回的写权限立即生效。
        public static PermContext Of(WorkContext ctx)
        {
            if (ctx == null || ctx.Item == null)
            {
                throw new BridgeException(500, "internal", "缺少请求上下文");
            }
            if (ctx.Perm != null)
            {
                return ctx.Perm;
            }
            WorkItem item = ctx.Item;
            int year = YearOf(item.Year, 0);
            if (year == 0)
            {
                throw new BridgeException(400, "bad_request", "年度无效");
            }
            int dateYear = YearOf(item.Date != null && item.Date.Length >= 4 ? item.Date.Substring(0, 4) : null, year);
            if (PermRegistry.IsRead(item.Path))
            {
                ctx.Perm = PermCache.Get(ctx.Conn, item.Acc, year, dateYear, item.Operator);
            }
            else
            {
                ctx.Perm = PermLoad.Load(ctx.Conn, item.Acc, year, dateYear, item.Operator);
            }
            return ctx.Perm;
        }

        // 写路由用：要求持有某个功能 id（或 admin）。name 是 403 文案里的功能名。
        public static void Require(WorkContext ctx, string auth, string name)
        {
            PermContext p = Of(ctx);
            if (!p.Has(auth))
            {
                throw new BridgeException(403, "no_permission", "没有" + name + "权限");
            }
        }

        public static void RequireRule(PermContext p, PermRule rule)
        {
            if (rule == null)
            {
                throw new BridgeException(403, "no_permission", "该接口没有登记权限规则");
            }
            if (p == null || !p.HasAny(rule.Auths))
            {
                throw new BridgeException(403, "no_permission", "没有" + rule.Title + "权限");
            }
        }

        public static bool Allow(PermContext p, string obj, string code)
        {
            return p == null || p.Allow(obj, code);
        }

        // 读取单张单据后调用：表头对象不放行 → 403；表体对象（存货、表体仓库）一行都不放行 → 403；
        // 表体为空、或部分行放行时整张照给（与 U8 卡片一致）。总账凭证把分录作为 lines 传入，head 可为 null；
        // 总账凭证（GlLines）从严：全部分录的科目都放行才给，没有分录也不给（同 PermSql.AppendGlVoucher）。
        public static void CheckVoucher(PermContext p, PermRule rule, Dictionary<string, object> head, IList lines)
        {
            if (p == null || rule == null || p.Supervisor)
            {
                return;
            }
            for (int i = 0; i < rule.Objs.Length; i++)
            {
                PermObj o = rule.Objs[i];
                if (!p.Controls(o.Obj))
                {
                    continue;
                }
                bool ok = o.Mode == PermObj.GlLines ? AllLines(p, o, lines)
                    : o.Mode == PermObj.Body ? AnyLine(p, o, lines) : RowOk(p, o, head);
                if (!ok)
                {
                    throw new BridgeException(403, "no_permission", DeniedData);
                }
            }
        }

        // 档案读取、报表行：该行的表头类对象都要放行。
        public static void CheckRow(PermContext p, PermRule rule, Dictionary<string, object> row)
        {
            if (!RowAllowed(p, rule, row))
            {
                throw new BridgeException(403, "no_permission", DeniedRow);
            }
        }

        public static bool RowAllowed(PermContext p, PermRule rule, Dictionary<string, object> row)
        {
            if (p == null || rule == null || p.Supervisor)
            {
                return true;
            }
            for (int i = 0; i < rule.Objs.Length; i++)
            {
                PermObj o = rule.Objs[i];
                if (o.Mode != PermObj.Body && o.Mode != PermObj.GlLines && p.Controls(o.Obj) && !RowOk(p, o, row))
                {
                    return false;
                }
            }
            return true;
        }

        // 表头类对象（Head / Pair，GlLines 按单行科目）在一行上是否放行。
        public static bool RowOk(PermContext p, PermObj o, Dictionary<string, object> row)
        {
            string code = Text(row, o.Column);
            if (o.Optional && code.Length == 0)
            {
                return true;
            }
            if (o.Mode == PermObj.Pair)
            {
                return p.AllowItem(Text(row, o.ClassColumn), code);
            }
            return p.Allow(o.Obj, code);
        }

        static bool AnyLine(PermContext p, PermObj o, IList lines)
        {
            if (lines == null || lines.Count == 0)
            {
                return true;
            }
            for (int i = 0; i < lines.Count; i++)
            {
                Dictionary<string, object> line = lines[i] as Dictionary<string, object>;
                if (line != null && RowOk(p, o, line))
                {
                    return true;
                }
            }
            return false;
        }

        // 每一行都要放行；没有行时不放行（失败即拒绝）。
        internal static bool AllLines(PermContext p, PermObj o, IList lines)
        {
            if (lines == null || lines.Count == 0)
            {
                return false;
            }
            for (int i = 0; i < lines.Count; i++)
            {
                Dictionary<string, object> line = lines[i] as Dictionary<string, object>;
                if (line == null || !RowOk(p, o, line))
                {
                    return false;
                }
            }
            return true;
        }

        // 列名不分大小写（SQL 行与 DOM 行的大小写不一）。
        static string Text(Dictionary<string, object> row, string column)
        {
            if (row == null || column == null)
            {
                return "";
            }
            object value;
            if (!row.TryGetValue(column, out value))
            {
                value = null;
                foreach (KeyValuePair<string, object> pair in row)
                {
                    if (string.Equals(pair.Key, column, StringComparison.OrdinalIgnoreCase))
                    {
                        value = pair.Value;
                        break;
                    }
                }
            }
            return Values.Text(value).Trim();
        }

        static int YearOf(string text, int fallback)
        {
            int y;
            if (text != null && int.TryParse(text.Trim(), NumberStyles.None, CultureInfo.InvariantCulture, out y) && y > 1900)
            {
                return y;
            }
            return fallback;
        }
    }
}
