namespace U8Co
{
    // 只读：退货申请单 sale_return_apply（vouchers/load、list、search 与 reports/doc_trace 的 voucher:<type>）。
    // 功能 id 按 U8 授权目录（UFSystem 的 UA_Auth）核对：退货申请单查询 SA03250104、列表 SA03250201。
    // 受控对象同退货单：客户、部门、业务员、销售类型在表头；存货、仓库在表体，仓库可能为空，按可空列。
    // K12 写入的功能 id（录入 SA03250101、审核 SA03250102、弃审 SA03250103、删除 SA03250110、退货单参照退货申请单 SA03020218）
    // 不登记在这里，由 ReturnsApply 在调用 U8 之前用 PermCheck.Require 现查。
    internal static partial class PermRegistry
    {
        static PermRule[] ReturnsApplyRules()
        {
            return new PermRule[]
            {
                V(ReturnsApplyRead.KindName, "退货申请单", A("SA03250104", "SA03250201"),
                    PermObj.H(PermObj.Customer, "cCusCode"), PermObj.H(PermObj.Department, "cDepCode"),
                    PermObj.H(PermObj.Person, "cPersonCode"), PermObj.H(PermObj.SaleType, "cSTCode"),
                    PermObj.B(PermObj.Inventory, "SA_ReturnsApplyDetail", "ID", "ID", "cInvCode"),
                    PermObj.BOpt(PermObj.Warehouse, "SA_ReturnsApplyDetail", "ID", "ID", "cWhCode"))
            };
        }
    }
}
