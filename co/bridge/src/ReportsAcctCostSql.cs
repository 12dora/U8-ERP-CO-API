namespace U8Co
{
    // 完工产品单位成本 cost_unit 的 SQL（成本核算模块 CA）。全是常量，调用方的值只进 ? 参数。
    internal static class ReportsAcctCostSql
    {
        // 已结账的期间：参数 年度。
        internal const string ClosedPeriods = "SELECT iperiod FROM GL_mend WHERE iyear=? AND iperiod BETWEEN 1 AND 12"
            + " AND ISNULL(bflag_CA,0)=1 ORDER BY iperiod";

        // 单位成本：产品（成本核算对象按 iPartID 去重，同一产品在几个成本中心各有一行时取最小的存货编码和名称）每期每个成本要素
        // 一行。参数：起 YYYYMM, 止 YYYYMM，再接过滤条件、数据权限（别名 b，存货编码列 InvCode）和 CostOrder。
        internal const string Unit = "SELECT u.iPeriod per, b.iPartID part, b.InvCode inv_code,"
            + " ISNULL(i.cInvName, b.cInvName) inv_name, ISNULL(cu.cComUnitName, b.cInvM_Unit) unit,"
            + " u.cAmotype typ, u.UnitAmo amt FROM Caq_UnitAmo u"
            + " JOIN (SELECT iPartID, MIN(InvCode) InvCode, MIN(cInvName) cInvName, MIN(cInvM_Unit) cInvM_Unit"
            + " FROM Caq_CoBase GROUP BY iPartID) b ON b.iPartID=u.iPartID"
            + " LEFT JOIN Inventory i ON i.cInvCode=b.InvCode"
            + " LEFT JOIN ComputationUnit cu ON cu.cComUnitCode=i.cComUnitCode"
            + " WHERE u.iPeriod BETWEEN ? AND ?";

        internal const string CostOrder = " ORDER BY b.InvCode, u.iPeriod, u.cAmotype";

        // 存货编码过滤：存货分类前缀。参数：前缀 + %。
        internal const string ClassFilter = " AND b.InvCode IN (SELECT ic.cInvCode FROM Inventory ic WHERE ic.cInvCCode LIKE ?)";

        // 完工数量：成本核算的生产订单成本行（CA_AmoCt）上每张订单每期的完工数量（iFinqua，同一订单的各成本行相同，取最大），
        // 按产品合计；实测与产成品入库单的入库数量一致。参数：起 YYYYMM, 止 YYYYMM。
        internal const string FinishedQty = "SELECT m.iPeriod per, r.iPartID part, SUM(m.fin) qty"
            + " FROM (SELECT cPPID, iPeriod, MAX(iFinqua) fin FROM CA_AmoCt WHERE iPeriod BETWEEN ? AND ?"
            + " GROUP BY cPPID, iPeriod) m JOIN Caq_CoReal r ON r.iRealCOID=m.cPPID GROUP BY m.iPeriod, r.iPartID";

        // 成本要素名称（0 材料费用、1 人工费用…），中文的排前面。
        internal const string ElementNames = "SELECT cAmoType code, cAmoTypeName name FROM Ca_AmoTypeName"
            + " ORDER BY cAmoType, CASE WHEN localID=N'zh-cn' THEN 0 ELSE 1 END";
    }
}
