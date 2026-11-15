using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 分录上的档案值照总账新增（GlCheck.Refs）逐项查一遍，SQL 与它相同：部门存在且末级、人员、客户、供应商、结算方式存在、
    // 现金流量项目存在且未关闭（项目来自 GL_CashItemDataSource，可能指向已关闭的项目）。值都取自 U8 单据，但不在导入前挡住，
    // 关闭 / 非末级的值要么变成笼统的 u8_rejected，要么被导入器悄悄写进去。拒绝一律 409 state_mismatch。
    internal static class ArapVoucherRefs
    {
        const string DeptSql = "SELECT TOP 1 'x' x FROM Department WHERE cDepCode=? AND bDepEnd=1";
        const string PersonSql = "SELECT TOP 1 'x' x FROM Person WHERE cPersonCode=?";
        const string CustomerSql = "SELECT TOP 1 'x' x FROM Customer WHERE cCusCode=?";
        const string VendorSql = "SELECT TOP 1 'x' x FROM Vendor WHERE cVenCode=?";
        const string SettleSql = "SELECT TOP 1 'x' x FROM SettleStyle WHERE cSSCode=?";
        const string FlowSql = "SELECT TOP 1 'x' x FROM fitemss98 WHERE citemcode=? AND ISNULL(bclose,0)=0";

        public static void Check(object conn, List<VoucherRow> rows)
        {
            HashSet<string> seen = new HashSet<string>();
            for (int i = 0; i < rows.Count; i++)
            {
                GlLine line = rows[i].Line;
                string at = "第 " + (i + 1).ToString(CultureInfo.InvariantCulture) + " 行分录的";
                Exists(conn, seen, DeptSql, line.Dept, at + "部门 " + line.Dept + " 不存在或不是末级部门");
                Exists(conn, seen, PersonSql, line.Person, at + "人员 " + line.Person + " 不存在");
                Exists(conn, seen, CustomerSql, line.Customer, at + "客户 " + line.Customer + " 不存在");
                Exists(conn, seen, VendorSql, line.Supplier, at + "供应商 " + line.Supplier + " 不存在");
                Exists(conn, seen, SettleSql, line.Settle, at + "结算方式 " + line.Settle + " 不存在");
                foreach (GlFlow flow in line.Flows)
                {
                    Exists(conn, seen, FlowSql, flow.Item, at + "现金流量项目 " + flow.Item + " 不存在或已关闭");
                }
            }
        }

        // 同一张凭证里同一个编码只查一次，键带上 SQL（同 GlCheck.Exists）。
        static void Exists(object conn, HashSet<string> seen, string sql, string code, string message)
        {
            if (code == null || code.Length == 0 || seen.Contains(sql + "\n" + code))
            {
                return;
            }
            if (Rows.Scalar(conn, sql, new object[] { code }) == null)
            {
                throw ArapVoucherDoc.Refuse(message + "，请在 U8 客户端处理后再制单");
            }
            seen.Add(sql + "\n" + code);
        }
    }
}
