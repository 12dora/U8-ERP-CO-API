namespace U8Co
{
    // Load 换回的表头、表体、货位 DOM。
    internal sealed class StockLoaded
    {
        public object Head;
        public object Body;
        public object Pos;
    }

    // Insert / Update 的 DOM 和连接、消息槽。
    internal sealed class StockForms
    {
        public object Head;
        public object Body;
        public object Pos;
        public object Conn;
        public object Msg;
    }

    // RunAt 事务里的附加核对：Before 在调用 U8 前、After 在提交前，都在同一连接同一事务里；抛异常即回滚。
    internal delegate void StockCheck(object conn);

    // RunAt 的方法名、参数数组和引用槽下标。
    internal sealed class StockAt
    {
        public string Method;
        public object[] Args;
        public int[] Refs;
        public string Ufts;
        public int ErrAt;
        public int MsgAt;
        public int ConnAt;
        public StockCheck Before;
        public StockCheck After;
        // 预演（DryRun.Active）时在 After 之后、提交之前调用（同一连接同一事务），登记新单 / 来源单。
        // 为空时按方法名走缺省（StockCall.DryAt：Insert 登记新主键，12 参 Verify 登记生成的 08/09）。
        public StockCheck Dry;
    }
}
