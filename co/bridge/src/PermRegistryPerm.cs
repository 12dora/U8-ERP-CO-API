namespace U8Co
{
    // 权限快照（perm/snapshot）、权限评估（perm/evaluate）：只要登录成功（A()），快照即本人在 U8 里的权限。
    // 评估查的是别的操作员，规则本身也只要登录；调用操作员名单（permEvaluateOperators）在登录前（Requests.ApplyPerm）
    // 和处理函数（PermEvaluate.Run）里各查一次，账套主管不因主管身份放行。
    internal static partial class PermRegistry
    {
        static PermRule[] PermSnapshotRules()
        {
            return new PermRule[]
            {
                R(PermSnapshot.RuleKey, "权限快照", A()),
                R(PermEvaluate.RuleKey, "权限评估", A())
            };
        }

        // 本组路由的规则键；其他路由返回 null。
        static string PermKey(string path)
        {
            if (path == PermSnapshot.Path)
            {
                return PermSnapshot.RuleKey;
            }
            return path == PermEvaluate.Path ? PermEvaluate.RuleKey : null;
        }
    }
}
