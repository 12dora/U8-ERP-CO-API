using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 固定资产报表（reports/fa_changes、fa_depreciation）与卡片档案的部门数据权限。功能权限仍由 PermGate 按登记表查。
    // 只在部门开关打开（AA_BusObject_base.bAuthControl=1）、操作员不是账套主管也不是部门数据权限管理员时生效。
    // 口径从严：卡片的全部版本、全部使用部门（fa_DeptScale，按卡片编号不分版本）都在授权内，且至少有一个使用部门，
    // 卡片的行才给；部门转移过的卡片，只有转出、转入两边的部门都有权限的人才看得到。变动单另要求其部门明细
    // （fa_Vouchers_Detail，空部门不算）都在授权内。列名表名都是常量，部门编码只进 ? 参数。
    // 固定资产卡片档案 fa_card（archives/get、list、get_many，ArcFa）用同一口径。
    internal static class ReportsFaPerm
    {
        // 读路由上的权限快照；自检（ctx 为 null）或不在读路由上时为 null（不过滤）。
        internal static PermContext Of(WorkContext ctx)
        {
            return ReportsMgmtCashSql.ReadPerm(ctx);
        }

        // cardExpr 是外层的卡片编号列（如 "v.sCardNum"、"t.sCardNum"），来自调用方的常量。
        internal static void AppendCard(StringBuilder sql, List<object> ps, PermContext p, string cardExpr)
        {
            if (p == null || !p.Controls(PermObj.Department))
            {
                return;
            }
            sql.Append(" AND EXISTS (SELECT 1 FROM fa_DeptScale fs WHERE fs.sCardNum = ").Append(cardExpr).Append(")");
            PermSql.AppendAllRows(sql, ps, p, PermObj.H(PermObj.Department, "fd.sDeptNum"),
                "FROM fa_DeptScale fd WHERE fd.sCardNum = " + cardExpr);
        }

        // 固定资产卡片档案 fa_card 的单张读取（archives/get、get_many 经 ArcRead.Get）：卡片已确认存在之后调用，
        // 不在部门授权内 403（同其他档案「没有该档案的数据权限」），不存在仍由调用方 404。
        internal static void CheckCard(WorkContext ctx, string code)
        {
            List<object> ps = new List<object>();
            string sql = CardProbe(Of(ctx), code, ps);
            if (sql != null && Rows.One(ctx.Conn, sql, ps.ToArray()) == null)
            {
                throw new BridgeException(403, "no_permission", PermCheck.DeniedRow);
            }
        }

        // 探测语句；部门不受控（开关没开、账套主管、部门数据权限管理员、自检）时返回 null，不必探。
        internal static string CardProbe(PermContext p, string code, List<object> ps)
        {
            if (p == null || !p.Controls(PermObj.Department))
            {
                return null;
            }
            StringBuilder sql = new StringBuilder("SELECT TOP 1 1 AS x FROM fa_Cards h WHERE h.sCardNum = ?");
            ps.Add(code ?? "");
            AppendCard(sql, ps, p, "h.sCardNum");
            return sql.ToString();
        }

        // numExpr 是外层的变动单号列（"v.sNum"）。
        internal static void AppendVoucher(StringBuilder sql, List<object> ps, PermContext p, string numExpr)
        {
            PermSql.AppendAllRows(sql, ps, p, PermObj.Opt(PermObj.Department, "fv.sDeptNum"),
                "FROM fa_Vouchers_Detail fv WHERE fv.sNum = " + numExpr);
        }
    }
}
