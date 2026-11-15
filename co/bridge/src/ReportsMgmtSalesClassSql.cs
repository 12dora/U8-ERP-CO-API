using System.Collections.Generic;

namespace U8Co
{
    // 经营管理报表里按存货分类取数的 SQL 片段（mgmt/sales 的 inventory_class 维度与 inv_class 过滤，mgmt/cash_stock 的产量分类）。
    // 表名、列名都是这里的常量，年级和分类前缀只进 ? 参数。
    // 不用分类编码方案做汇总：往上取祖先分类时，取 InventoryClass 里级次等于 class_grade、编码是存货分类编码前缀的那一行
    // （OUTER APPLY + TOP 1，同一存货最多一行，不会让明细行翻倍）；存货的分类比该级次浅（没有祖先）时保持自己的分类。
    // 存货 → 分类只算一次：在 Inventory 上做一张派生表（每个存货一行：k 存货编码、own 自己的分类编码、cls 取到的分类），
    // 明细按存货编码连它，所以祖先匹配的次数是存货个数，不是发票行、核算明细行的个数。
    // 比较用 LEFT(…, LEN(…)) = … 而不是 LIKE，分类编码里的 _ % [ 不会被当成通配符。
    // 收入、成本两侧、产量都用同一个取法，所以 FULL OUTER JOIN 按分类键仍能配对。
    internal static class ReportsMgmtSalesClassSql
    {
        // 祖先分类：? = 级次。{INV} 是存货分类编码列，{ALIAS} 是别名（列 cInvCCode）。
        const string Ancestor = " OUTER APPLY (SELECT TOP 1 g.cInvCCode FROM InventoryClass g WHERE g.iInvCGrade = ?"
            + " AND LEFT({INV}, LEN(g.cInvCCode)) = g.cInvCCode ORDER BY LEN(g.cInvCCode) DESC) {ALIAS}";

        internal static string Apply(string invClassCol, string alias)
        {
            return Ancestor.Replace("{INV}", invClassCol).Replace("{ALIAS}", alias);
        }

        // 分类过滤：存货自己的分类编码以前缀开头（前缀里的通配符转义）。? = 前缀 + %。
        internal static string Filter(string invClassCol)
        {
            return " AND " + invClassCol + " LIKE ? ESCAPE '\\'";
        }

        internal static string FilterArg(string prefix)
        {
            return ArcRead.Like(prefix, false);
        }

        // 销售分析内层（收入侧 b.cInvCode / 成本侧 s.cInvCode）的占位符替换：{CLS} 分类键列，{JOIN} 连存货 → 分类的派生表，
        // {FILTER} 分类过滤。不需要分类时全部为空，SQL 与没有这项功能时完全一样。
        internal static string Inner(string template, MgmtSalesArgs m, string invCol)
        {
            string join = "";
            if (m.NeedClass)
            {
                join = " LEFT JOIN (SELECT i.cInvCode AS k, i.cInvCCode AS own, "
                    + (m.RollUp ? "COALESCE(cg.cInvCCode, i.cInvCCode)" : "i.cInvCCode") + " AS cls FROM Inventory i"
                    + (m.RollUp ? Apply("i.cInvCCode", "cg") : "") + ") ci ON ci.k = " + invCol;
            }
            return template.Replace("{CLS}", KeyCol(m)).Replace("{JOIN}", join)
                .Replace("{FILTER}", m.InvClass == null ? "" : Filter("ci.own"));
        }

        static string KeyCol(MgmtSalesArgs m)
        {
            if (!m.Has("inventory_class"))
            {
                return "";
            }
            return " ISNULL(ci.cls, N'') AS cInvClass,";
        }

        // 内层的 ? 参数：[级次]，核心参数（窗口或年度、起止期间），[分类前缀]。顺序同内层文本里 ? 出现的先后。
        internal static object[] Args(MgmtSalesArgs m, object[] core)
        {
            List<object> args = new List<object>();
            if (m.RollUp)
            {
                args.Add(m.ClassGrade);
            }
            args.AddRange(core);
            if (m.InvClass != null)
            {
                args.Add(FilterArg(m.InvClass));
            }
            return args.ToArray();
        }
    }
}
