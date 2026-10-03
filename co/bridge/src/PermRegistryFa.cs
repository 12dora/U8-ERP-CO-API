namespace U8Co
{
    // 固定资产卡片写入与设备台账。功能 id 取自 UFMeta 的 AA_FormButtonAuths：
    // - 卡片：FA_Card 的「增加」（K107）FA1502，FA_Card / FA_Cards 的「删除」（K123）FA1506（ArcGuard.Permit 按 write:archive:fa_card:<操作>）；
    //   另按卡片使用部门的数据权限判断（FaCardSql.Dept、ArcFaWrite，同读取的 ReportsFaPerm 口径，不登记在规则里）。
    // - 变动单不开放写入：本机 U8 的 EAI 没有变动单（capitalvouchers）的导入样式表，导入回「未设置对象变量」，请在 U8 客户端录入。
    // - 设备台账（EQ_EQData）：设备管理的授权目录 EQ002 下，设备台账 EQ00202（设备类型台账是 EQ00201）。
    //   已按 UFSystem UA_Auth 核对：EQ0020201 设备台账查询、EQ0020208 设备台账增加。
    internal static partial class PermRegistry
    {
        static PermRule[] FaEqRules()
        {
            return new PermRule[]
            {
                R(ArcGuard.RuleKey(ArcFa.Name, "create"), "固定资产卡片新增", A("FA1502")),
                R(ArcGuard.RuleKey(ArcFa.Name, "delete"), "固定资产卡片删除", A("FA1506")),
                Arc(ArcEq.Name, "设备台账", A("EQ0020201")),
                R(ArcGuard.RuleKey(ArcEq.Name, "create"), "设备台账新增", A("EQ0020208"))
            };
        }
    }
}
