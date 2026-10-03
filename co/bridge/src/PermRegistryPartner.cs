namespace U8Co
{
    // 客户、供应商子档案（ArcPartner）的读取和写入权限。读取登记「archive:<档案>」，写入登记「write:archive:<档案>:<操作>」
    // （ArcGuard.Permit 取，另按编码第一段判断客户或供应商的数据权限，与读取的 ArcPair.Obj1 一致）。
    // id 取自 UFMeta 的 AA_FormButtonAuths：客户档案 Archives_Customer_List 的查询、修改是 AS011Q / AS011，供应商 AS005Q / AS005；
    // 银行账户在 U8 里是客户（供应商）卡片的一页，改账户就是改上级档案，写入用上级的「修改」AS011 / AS005。
    // 供应商联系人有自己的列表 Archives_VenContact_List（Add AS020305、Modify AS020301、Del AS020306、Filter AS020302），
    // 可写，写入按这三个按钮 id 登记（另按编码第一段判断供应商的数据权限；四个 id 已按 U8 授权目录核对）。
    // 汇率（exchange_rate）的写入也登记在这里：汇率在外币设置 GL_frmWBSZ 里维护，按钮 Add / Delete 都是 AS028M
    // （与币种、汇率读取同一 id；没有 Modify 按钮，修改按同一 id）。
    // 客户联系人（按 U8 授权目录更正）：U8「客户联系人管理」CS0202 下的联系人查询 CS020202、增加 CS020204、编辑 CS020201、
    // 删除 CS020205。读取另收客户档案的 AS011Q / AS011（与供应商联系人另收 AS005Q / AS005 同一口径：联系人挂在客户档案下，
    // 能看客户档案的操作员在 U8 里也能看到客户的联系人）；写入只认联系人自己的 id。
    internal static partial class PermRegistry
    {
        static PermRule[] PartnerRules()
        {
            PermObj cus = PermObj.H(PermObj.Customer, "cCusCode");
            PermObj ven = PermObj.H(PermObj.Vendor, "cVenCode");
            return new PermRule[]
            {
                Arc(ArcPartner.CustomerBank, "客户银行账户", A("AS011Q", "AS011"), cus),
                Arc(ArcPartner.VendorBank, "供应商银行账户", A("AS005Q", "AS005"), ven),
                Arc(ArcPartner.CustomerContact, "客户联系人", A("CS020202", "AS011Q", "AS011"), cus),
                Arc(ArcPartner.VendorContact, "供应商联系人", A("AS020302", "AS005Q", "AS005"), ven),
                WriteRule(ArcPartner.CustomerBank, "create", "客户银行账户新增", A("AS011"), cus),
                WriteRule(ArcPartner.CustomerBank, "update", "客户银行账户修改", A("AS011"), cus),
                WriteRule(ArcPartner.CustomerBank, "delete", "客户银行账户删除", A("AS011"), cus),
                WriteRule(ArcPartner.VendorBank, "create", "供应商银行账户新增", A("AS005"), ven),
                WriteRule(ArcPartner.VendorBank, "update", "供应商银行账户修改", A("AS005"), ven),
                WriteRule(ArcPartner.VendorBank, "delete", "供应商银行账户删除", A("AS005"), ven),
                WriteRule(ArcPartner.CustomerContact, "create", "客户联系人新增", A("CS020204"), cus),
                WriteRule(ArcPartner.CustomerContact, "update", "客户联系人修改", A("CS020201"), cus),
                WriteRule(ArcPartner.CustomerContact, "delete", "客户联系人删除", A("CS020205"), cus),
                WriteRule(ArcPartner.VendorContact, "create", "供应商联系人新增", A("AS020305"), ven),
                WriteRule(ArcPartner.VendorContact, "update", "供应商联系人修改", A("AS020301"), ven),
                WriteRule(ArcPartner.VendorContact, "delete", "供应商联系人删除", A("AS020306"), ven),
                WriteRule(ArcExch.Name, "create", "汇率新增", A("AS028M")),
                WriteRule(ArcExch.Name, "update", "汇率修改", A("AS028M")),
                WriteRule(ArcExch.Name, "delete", "汇率删除", A("AS028M"))
            };
        }
    }
}
