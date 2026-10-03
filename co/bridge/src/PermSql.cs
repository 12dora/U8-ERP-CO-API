using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 把记录级数据权限拼成 SQL 条件，追加到调用方的 WHERE 后面（每段以 " AND " 开头）。
    // 列名只来自 PermRegistry / 调用方的常量；编码和操作员只进 ? 参数，参数按出现顺序追加到 ps。
    // 调用方要在 WHERE 的最后、ORDER BY / GROUP BY 之前调用，保证 ? 的顺序与 ps 一致。
    // 可查询编码不超过 MaxInline 个时直接写成 IN (?, …)；更多时改用 AA_HoldAuth 的 EXISTS（条件同 PermLoad.CodeSql）。
    internal static class PermSql
    {
        public const int MaxInline = 100;

        // 角色在建快照时已解析（PermContext.Roles），这里直接按参数比，不再逐行跨库查 UFSYSTEM..UA_Role。
        const string HoldHead = "EXISTS (SELECT 1 FROM AA_HoldAuth pa WHERE pa.cBusObId=?"
            + " AND ISNULL(pa.cFuncId, N'') LIKE N'%R%' AND NULLIF(LTRIM(RTRIM(pa.cACCode)), N'') IS NOT NULL"
            + " AND ((pa.isUserGroup=0 AND pa.cUserId=?)";

        // 按一条规则追加全部受控对象的条件。alias 是表头（或报表行）的别名，如 "h"；总账凭证见 AppendGlVoucher。
        public static void AppendRule(StringBuilder sb, List<object> ps, PermContext p, PermRule rule, string alias)
        {
            if (p == null || rule == null)
            {
                return;
            }
            for (int i = 0; i < rule.Objs.Length; i++)
            {
                AppendObj(sb, ps, p, rule.Objs[i], alias);
            }
        }

        public static void AppendObj(StringBuilder sb, List<object> ps, PermContext p, PermObj o, string alias)
        {
            if (p == null || o == null || !p.Controls(o.Obj))
            {
                return;
            }
            string prefix = string.IsNullOrEmpty(alias) ? "" : alias + ".";
            switch (o.Mode)
            {
                case PermObj.Body:
                    AppendBody(sb, ps, p, o, prefix + o.HeadKey);
                    return;
                case PermObj.Pair:
                    AppendItem(sb, ps, p, prefix + o.ClassColumn, prefix + o.Column, o.Optional);
                    return;
                case PermObj.GlLines:
                    AppendGlVoucher(sb, ps, p, string.IsNullOrEmpty(alias) ? o.BodyTable : alias);
                    return;
                default:
                    AppendCode(sb, ps, p, o.Obj, prefix + o.Column, o.Optional);
                    return;
            }
        }

        // 单列编码。optional 为真时空值放行，否则空值丢行。
        public static void AppendCode(StringBuilder sb, List<object> ps, PermContext p, string obj, string expr, bool optional)
        {
            if (p == null || !p.Controls(obj))
            {
                return;
            }
            sb.Append(" AND ").Append(Wrap(CodePred(ps, p, obj, expr), expr, optional));
        }

        // 项目：大类 + 编码两列。
        public static void AppendItem(StringBuilder sb, List<object> ps, PermContext p, string clsExpr, string codeExpr,
            bool optional)
        {
            if (p == null || !p.Controls(PermObj.Item))
            {
                return;
            }
            sb.Append(" AND ").Append(Wrap(ItemPred(ps, p, clsExpr, codeExpr), codeExpr, optional));
        }

        // 表体：表体为空，或至少一行放行，单据才保留。headKey 是外层表头主键表达式（如 "h.ID"）。
        public static void AppendBody(StringBuilder sb, List<object> ps, PermContext p, PermObj o, string headKey)
        {
            if (p == null || o == null || !p.Controls(o.Obj))
            {
                return;
            }
            string from = " FROM " + o.BodyTable + " pd WHERE pd." + o.BodyFk + "=" + headKey;
            string col = "pd." + o.Column;
            sb.Append(" AND (NOT EXISTS (SELECT 1").Append(from).Append(") OR EXISTS (SELECT 1").Append(from);
            sb.Append(" AND ").Append(Wrap(CodePred(ps, p, o.Obj, col), col, o.Optional)).Append("))");
        }

        // 总账凭证（从严）：同一张凭证（年度、期间、类别、凭证号）的全部分录科目都放行，凭证才保留；
        // 有一条分录的科目不在授权内，整张凭证不给（U8 客户端凭证查询是否按科目逐张控制未核对，取较严的口径）。
        // outer 是外层 GL_accvouch 的别名；外层没有别名时传 "GL_accvouch"（子查询里的表用别名 pg，不会混淆）。
        // 按整张凭证保留或去掉，外层的分录不会被拆开，列表、摘要的借方合计只含可见凭证。
        // 子查询按聚集索引 VouchCluster 的列序（iperiod、isignseq、ino_id）关联，可以索引查找，不逐行扫描整个期间；
        // 类别字 csign 与类别序号 isignseq 一一对应（dsign），年度、类别字照旧比较，口径不变。isignseq 为空的分录
        // 等号关联不上自己，会漏检，外层先排除这类行（U8 填制、审核都会写 isignseq，正常数据没有空值），保持从严。
        public static void AppendGlVoucher(StringBuilder sb, List<object> ps, PermContext p, string outer)
        {
            if (p == null || !p.Controls(PermObj.Account))
            {
                return;
            }
            string from = "FROM GL_accvouch pg WHERE pg.iperiod=" + outer + ".iperiod AND pg.isignseq=" + outer + ".isignseq"
                + " AND pg.ino_id=" + outer + ".ino_id AND pg.iyear=" + outer + ".iyear AND pg.csign=" + outer + ".csign";
            sb.Append(" AND ").Append(outer).Append(".isignseq IS NOT NULL");
            AppendAllRows(sb, ps, p, PermObj.H(PermObj.Account, "pg.ccode"), from);
        }

        // 子表的全部行都放行（严格口径）：from 是「FROM <表> <别名> WHERE <关联条件>」形式的常量片段（表名、列名只来自
        // 本仓库常量），o 的 Column 是子表上的编码列（带子表别名）。子表只要有一行不放行，外层行就去掉；o.Optional 为真时
        // 空编码不算越权。子表没有行时放行，调用方需要「至少一行」时另加 EXISTS。编码为 NULL 按空串比较，不会因三值逻辑漏判。
        public static void AppendAllRows(StringBuilder sb, List<object> ps, PermContext p, PermObj o, string from)
        {
            if (p == null || o == null || !p.Controls(o.Obj))
            {
                return;
            }
            string code = "LTRIM(RTRIM(ISNULL(" + o.Column + ", N'')))";
            sb.Append(" AND NOT EXISTS (SELECT 1 ").Append(from).Append(" AND NOT (");
            sb.Append(Wrap(CodePred(ps, p, o.Obj, code), code, o.Optional)).Append("))");
        }

        static string Wrap(string pred, string expr, bool optional)
        {
            if (!optional)
            {
                return pred;
            }
            return "(NULLIF(LTRIM(RTRIM(" + expr + ")), N'') IS NULL OR " + pred + ")";
        }

        static string CodePred(List<object> ps, PermContext p, string obj, string expr)
        {
            HashSet<string> codes = p.CodesOf(obj);
            if (codes.Count == 0)
            {
                return "1=0";
            }
            if (codes.Count > MaxInline)
            {
                return Hold(ps, p, obj) + " AND pa.cACCode=" + expr + ")";
            }
            StringBuilder sb = new StringBuilder(expr).Append(" IN (");
            int n = 0;
            foreach (string code in codes)
            {
                sb.Append(n == 0 ? "?" : ", ?");
                ps.Add(code);
                n++;
            }
            return sb.Append(")").ToString();
        }

        static string ItemPred(List<object> ps, PermContext p, string clsExpr, string codeExpr)
        {
            HashSet<string> pairs = p.CodesOf(PermObj.Item);
            if (pairs.Count == 0)
            {
                return "1=0";
            }
            if (pairs.Count > MaxInline)
            {
                return Hold(ps, p, PermObj.Item) + " AND LTRIM(RTRIM(ISNULL(pa.cClassCode, N'')))=" + clsExpr + " AND pa.cACCode=" + codeExpr + ")";
            }
            StringBuilder sb = new StringBuilder("EXISTS (SELECT 1 FROM (VALUES ");
            int n = 0;
            foreach (string pair in pairs)
            {
                int at = pair.IndexOf(PermContext.PairSep);
                sb.Append(n == 0 ? "(?, ?)" : ", (?, ?)");
                ps.Add(pair.Substring(0, at));
                ps.Add(pair.Substring(at + 1));
                n++;
            }
            sb.Append(") pv(c, k) WHERE pv.c=").Append(clsExpr).Append(" AND pv.k=").Append(codeExpr).Append(")");
            return sb.ToString();
        }

        // AA_HoldAuth 的实时条件头（不含结尾括号）：对象、本人、所属角色。
        static string Hold(List<object> ps, PermContext p, string obj)
        {
            ps.Add(p.Source(obj));
            ps.Add(p.Operator);
            StringBuilder sb = new StringBuilder(HoldHead);
            if (p.Roles.Count > 0)
            {
                sb.Append(" OR (pa.isUserGroup=1 AND pa.cUserId IN (");
                for (int i = 0; i < p.Roles.Count; i++)
                {
                    sb.Append(i == 0 ? "?" : ", ?");
                    ps.Add(p.Roles[i]);
                }
                sb.Append("))");
            }
            return sb.Append(")").ToString();
        }

        // 单个编码的实时判断（授权超过 MaxInline 个时用，和列表同一套条件，避免列表与读取在缓存期内不一致）。
        // 返回 SELECT 语句；参数按顺序追加到 ps。
        internal static string LiveCodeSql(List<object> ps, PermContext p, string obj, string code)
        {
            string pred = Hold(ps, p, obj) + " AND pa.cACCode=?)";
            ps.Add(code ?? "");
            return "SELECT 1 AS x WHERE " + pred;
        }

        internal static string LiveItemSql(List<object> ps, PermContext p, string cls, string code)
        {
            string pred = Hold(ps, p, PermObj.Item)
                + " AND LTRIM(RTRIM(ISNULL(pa.cClassCode, N'')))=? AND pa.cACCode=?)";
            ps.Add(cls ?? "");
            ps.Add(code ?? "");
            return "SELECT 1 AS x WHERE " + pred;
        }
    }
}
