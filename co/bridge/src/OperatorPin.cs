namespace U8Co
{
    // 操作员固定到一条写线程。U8 同一台机器上同一操作员同时只能有一个有效登录（clsLogin），
    // 第二个会报「登录状态不是 0」。要登录的任务（AuthCache 未命中的读、COM 读、写、登录检查）一律只在
    // Index(操作员, 写线程数) 这条写线程上跑，彼此先进先出，不会在两条线程上同时登录。
    // 命中 AuthCache 的读不登录，不固定（读线程池）；不带操作员的任务也不固定，任一写线程可取。
    internal static class OperatorPin
    {
        public const int None = -1;

        // 去掉首尾空白、按不变区域性转大写：OP-020 与 op-020 是同一条线程。
        public static string Norm(string op)
        {
            return op == null ? "" : op.Trim().ToUpperInvariant();
        }

        // FNV-1a（32 位）按 UTF-16 小端字节算 Norm 的散列再对 n 取余。不用 string.GetHashCode：
        // 结果要跨进程稳定，自检才能写死期望值。没有操作员或 n 不大于 0 时返回 None。
        public static int Index(string op, int n)
        {
            string norm = Norm(op);
            if (norm.Length == 0 || n <= 0)
            {
                return None;
            }
            uint hash = 2166136261;
            unchecked
            {
                for (int i = 0; i < norm.Length; i++)
                {
                    char c = norm[i];
                    hash = (hash ^ (uint)(c & 0xFF)) * 16777619;
                    hash = (hash ^ (uint)(c >> 8)) * 16777619;
                }
            }
            return (int)(hash % (uint)n);
        }

        // 第 index 条写线程可以取这个任务：不固定，或入队时（WorkItem.Pin）正好固定在这条线程上。
        public static bool Matches(WorkItem item, int index)
        {
            return item.Pin == None || item.Pin == index;
        }
    }
}
