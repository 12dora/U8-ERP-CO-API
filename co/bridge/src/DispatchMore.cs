namespace U8Co
{
    // Dispatch.Handle 的后段分派（Dispatch.cs 已到文件行数上限，新的按类型分派接在这里）：返回 null 再走原来的路由表。
    internal static class DispatchMore
    {
        internal static ApiResult Try(WorkContext ctx, string path)
        {
            // 库存期初结存单（StockOpening）。
            ApiResult opening = StockOpening.Try(ctx, path);
            if (opening != null)
            {
                return opening;
            }
            // K12 退货申请单：vouchers/create|update|delete|verify，以及退货单参照退货申请单生成（ReturnsApply）。
            return ReturnsApply.Try(ctx, path);
        }
    }
}
