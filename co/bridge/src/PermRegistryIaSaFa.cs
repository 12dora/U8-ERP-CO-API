namespace U8Co
{
    // 只读：出入库调整单 ia_adjust、存货调价单 inventory_price_adjust（vouchers/load、list、search 的 voucher:<type>），
    // 固定资产变动单与折旧报表（reports/fa_changes、reports/fa_depreciation）。功能 id 按 U8 授权目录（UFSystem 的 UA_Auth）核对：
    // - 出入库调整单：入库调整单查询 IA1001 / 列表 IA02040201、出库调整单查询 IA1004 / 列表 IA02040301（两种同表，任一即可）。
    // - 存货调价单：查询 SA03120202 / 列表 SA0312020301。
    // - 变动单：变动单查看 FA1603；折旧：折旧清单查看 FA2403、折旧清单表 FA18105。两张报表另按卡片使用部门过滤
    //   （部门开关打开时，ReportsFaPerm 在 SQL 里挂，不登记在规则里）；固定资产卡片档案 fa_card（ArcFa）同一口径。
    // 出入库调整单的仓库、部门、业务员在出库调整（期末自动生成）上常为空，按可空列；存货在表体（按单号 cJVCode 挂）。
    internal static partial class PermRegistry
    {
        static PermRule[] IaSaFaRules()
        {
            return new PermRule[]
            {
                V(IaAdjustRead.KindName, "出入库调整单", A("IA1001", "IA02040201", "IA1004", "IA02040301"),
                    PermObj.Opt(PermObj.Warehouse, "cWhCode"), PermObj.Opt(PermObj.Department, "cDepCode"),
                    PermObj.Opt(PermObj.Person, "cPersonCode"),
                    PermObj.B(PermObj.Inventory, "JustInVouchs", "cJVCode", "cJVCode", "cInvCode")),
                V(InvPriceAdjustRead.KindName, "存货调价单", A("SA03120202", "SA0312020301"),
                    PermObj.Opt(PermObj.Department, "cdepcode"), PermObj.Opt(PermObj.Person, "cpersoncode"),
                    PermObj.B(PermObj.Inventory, "SA_InvPriceJustDetail", "id", "id", "cinvcode")),
                R(ReportsFaReq.ChangesRule, "固定资产变动单", A("FA1603")),
                R(ReportsFaReq.DeprRule, "固定资产折旧", A("FA2403", "FA18105"))
            };
        }
    }
}
