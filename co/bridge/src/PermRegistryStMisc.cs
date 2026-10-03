namespace U8Co
{
    // PermRegistry 的形态转换单、调拨申请单、盘点单。
    // 查询 id 取各单据列表（ST_List_0305 / 0324 / 0307）tlbLink 按钮的 ST010501 / ST01130101 / ST010201
    // （UFMeta AA_FormButtonAuths，与调拨单的 ST010101 同一取法）。写路由不在这里（同调拨单，只查读取）。
    internal static partial class PermRegistry
    {
        static PermRule[] StMiscRules()
        {
            return new PermRule[]
            {
                // 形态转换单表头没有仓库：仓库、存货都在行上；部门、业务员可空。
                V("shape_change", "形态转换单", A("ST010501"),
                    PermObj.Opt(PermObj.Department, "cDepCode"), PermObj.Opt(PermObj.Person, "cPersonCode"),
                    PermObj.B(PermObj.Inventory, "AssemVouchs", "ID", "ID", "cInvCode"),
                    PermObj.B(PermObj.Warehouse, "AssemVouchs", "ID", "ID", "cWhCode")),
                // 调拨申请单：同调拨单，调出、调入两个仓库都要有权限；部门在 U8 里常为空，按可空列处理。
                V("transfer_request", "调拨申请单", A("ST01130101"),
                    PermObj.H(PermObj.Warehouse, "cOWhCode"), PermObj.H(PermObj.Warehouse, "cIWhCode"),
                    PermObj.Opt(PermObj.Department, "cODepCode"), PermObj.Opt(PermObj.Person, "cPersonCode"),
                    PermObj.B(PermObj.Inventory, "ST_AppTransVouchs", "ID", "ID", "cInvCode")),
                V("stock_check", "盘点单", A("ST010201"),
                    PermObj.H(PermObj.Warehouse, "cWhCode"), PermObj.Opt(PermObj.Department, "cDepCode"),
                    PermObj.Opt(PermObj.Person, "cPersonCode"),
                    PermObj.B(PermObj.Inventory, "CheckVouchs", "ID", "ID", "cInvCode")),
                // 货位调整单：查询 ST010807（U8 授权目录 UA_Auth 核对）；写入的录入 ST010806、审核 ST010802、
                // 弃审 ST010803 由 PositionAdjust.Try 现查。仓库在表头，存货在行上。
                V("position_adjust", "货位调整单", A("ST010807"),
                    PermObj.H(PermObj.Warehouse, "cWhCode"), PermObj.Opt(PermObj.Department, "cDepCode"),
                    PermObj.Opt(PermObj.Person, "cPersonCode"),
                    PermObj.B(PermObj.Inventory, "AdjustPVouchs", "ID", "ID", "cInvCode"))
            };
        }
    }
}
