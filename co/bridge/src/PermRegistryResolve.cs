using System;

namespace U8Co
{
    // archives/resolve：读路由（ReadPaths，权限快照走缓存），但一个请求可以含多类档案，没有整条路由的规则。
    // PermGate 对它整体放过，处理函数（ArcResolve.Permit）按每项的 archive:<档案> 规则逐项 RequireRule，
    // 记录级过滤用同一规则的 PermSql 条件。
    internal static partial class PermRegistry
    {
        public static bool PerItem(string path)
        {
            return string.Equals(path, ArcResolve.Path, StringComparison.Ordinal);
        }
    }
}
