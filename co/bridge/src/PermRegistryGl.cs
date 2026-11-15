namespace U8Co
{
    // 总账记账（gl/vouchers/post，写路由，GlPost 用 ForKey 取）。GL0208 是 U8 总账「凭证 → 记账」（按 U8 授权目录核对）。
    // 只查功能权限；凭证类别的数据权限（bVouchCtlPZLB）U8 界面按列表过滤，桥不做，账套开了这个选项时以 U8 客户端为准。
    internal static partial class PermRegistry
    {
        static PermRule[] GlPostRules()
        {
            // 取消记账（gl/vouchers/unpost，GlUnpost 用 ForKey 取）：U8 的「恢复记账前状态」在对账界面里，没有单独的
            // 功能 id 可核对，暂按记账 GL0208 查；以 U8 授权目录核对后只改这一行。
            return new PermRule[] { R(GlPostParse.Rule, "记账", A("GL0208")), R(GlUnpostReq.Rule, "取消记账", A("GL0208")) };
        }
    }
}
