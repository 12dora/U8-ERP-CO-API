using System.Collections.Generic;

namespace U8Co
{
    // 现金流量统计 cash_flow 的 SQL。口径见 ReportsFinCash。全是常量，条件只进 ? 参数。
    internal static class ReportsFinCashSql
    {
        // 现金流量项目挂在凭证的对方科目分录上（GL_CashTable 与 GL_accvouch 按 年度、期间、凭证类别号、凭证号、分录号一一对应）。
        // 项目档案 fitemss98（citemccode 前两位是一级分类，如 01 经营活动），分类档案 fitemss98class。
        // 作废凭证（iflag=1）不算；{POSTED} 在不含未记账时是 " AND ISNULL(g.ibook, 0)=1"。参数：年度, 起期间, 止期间。
        const string ItemSql = "SELECT c.cCashItem code, f.citemname name, LEFT(f.citemccode, 2) class_code,"
            + " k.cItemCname class_name, f.cDirection direction, c.iPeriod per,"
            + " SUM(ISNULL(c.md, 0)) md, SUM(ISNULL(c.mc, 0)) mc"
            + " FROM GL_CashTable c JOIN GL_accvouch g ON g.iyear=c.iyear AND g.iperiod=c.iPeriod"
            + " AND g.isignseq=c.iSignSeq AND g.ino_id=c.iNo_id AND g.inid=c.inid"
            + " LEFT JOIN fitemss98 f ON f.citemcode=c.cCashItem"
            + " LEFT JOIN fitemss98class k ON k.cItemCcode=LEFT(f.citemccode, 2)"
            + " WHERE c.iyear=? AND c.iPeriod BETWEEN ? AND ? AND (g.iflag IS NULL OR g.iflag<>1){POSTED}"
            + " GROUP BY c.cCashItem, f.citemname, f.citemccode, k.cItemCname, f.cDirection, c.iPeriod"
            + " ORDER BY c.cCashItem, c.iPeriod";

        // 没有现金流量项目的凭证：库存现金、银行存款、其他货币资金（1001、1002、1012 及其下级）分录的借贷净额不为 0
        // （现金账户之间互转的净额为 0，不要求项目），且整张凭证在 GL_CashTable 里没有任何行。
        // 参数：年度, 起期间, 止期间, 年度。
        const string UnassignedSql = ";WITH v AS (SELECT g.iperiod per, g.isignseq sq, g.ino_id vno,"
            + " SUM(ISNULL(g.md, 0) - ISNULL(g.mc, 0)) net FROM GL_accvouch g"
            + " WHERE g.iyear=? AND g.iperiod BETWEEN ? AND ? AND (g.iflag IS NULL OR g.iflag<>1){POSTED}"
            + " AND (g.ccode LIKE N'1001%' OR g.ccode LIKE N'1002%' OR g.ccode LIKE N'1012%')"
            + " GROUP BY g.iperiod, g.isignseq, g.ino_id)"
            + " SELECT COUNT(*) vouchers, SUM(ABS(v.net)) amount FROM v WHERE ROUND(v.net, 2)<>0"
            + " AND NOT EXISTS (SELECT 1 FROM GL_CashTable c WHERE c.iyear=? AND c.iPeriod=v.per"
            + " AND c.iSignSeq=v.sq AND c.iNo_id=v.vno)";

        const string PostedOnly = " AND ISNULL(g.ibook, 0)=1";

        internal static string Items(bool unposted, int year, int from, int to, List<object> ps)
        {
            ps.Add(year);
            ps.Add(from);
            ps.Add(to);
            return ItemSql.Replace("{POSTED}", unposted ? "" : PostedOnly);
        }

        internal static string Unassigned(bool unposted, int year, int from, int to, List<object> ps)
        {
            ps.Add(year);
            ps.Add(from);
            ps.Add(to);
            ps.Add(year);
            return UnassignedSql.Replace("{POSTED}", unposted ? "" : PostedOnly);
        }
    }
}
