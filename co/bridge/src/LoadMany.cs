using System.Collections.Generic;

namespace U8Co
{
    // vouchers/load_many：同一类型的几张单据一次读出，整个请求只登录一次。
    // 每张走与 vouchers/load 完全相同的读取（VoucherRead.LoadOne：数据权限探测、读取、隐藏口令列、截行、审批状态），
    // 条目就是该张的 vouchers/load 响应体；某张不存在、越权、U8 拒绝等 4xx 只记在该条目的 error 里，不影响其他张。
    // 5xx（服务不可用、结果未知、内部错误）仍按整个请求失败返回，调用方可以改用单张读取定位。
    // 线程池同 vouchers/load：SQL 读取的类型命中登录缓存进读线程池（RouteClass），其余在写线程池上登录一次并逐张持单据锁（DocLocks）。
    internal static class LoadMany
    {
        internal const string Path = "/u8co/v1/vouchers/load_many";
        internal const string Action = "load_many";

        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Item == null || ctx.Item.Type == null)
            {
                throw new BridgeException(400, "bad_request", "缺少单据类型");
            }
            VoucherKind kind = ctx.Item.Type;
            int[] ids = LoadManyReq.Parse(kind, ctx.Item.Body);
            List<object> items = new List<object>(ids.Length);
            for (int i = 0; i < ids.Length; i++)
            {
                items.Add(One(ctx, kind, ids[i]));
            }
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["items"] = items;
            return ApiResult.Ok(body);
        }

        static Dictionary<string, object> One(WorkContext ctx, VoucherKind kind, int id)
        {
            ApiResult result;
            try
            {
                // 同 vouchers/load 在 Dispatch 里经 StockMisc.Try 做的前置核对：形态转换单只认 cVouchType=15。
                StockMisc.RefuseOtherAssem(ctx.Conn, kind, id);
                result = VoucherRead.LoadOne(ctx, kind, id);
            }
            catch (BridgeException ex)
            {
                if (ex.Status >= 500)
                {
                    throw;
                }
                return Failed(id, ex.Code, ex.Message);
            }
            if (result != null && result.Status == 200 && result.Body != null)
            {
                return result.Body;
            }
            return FromResult(id, result);
        }

        // 读取函数以非 200 的结果返回（而不是抛异常）时：4xx 记到条目，5xx 整个请求失败。
        static Dictionary<string, object> FromResult(int id, ApiResult result)
        {
            int status = result == null ? 500 : result.Status;
            string code = result == null || result.Code == null ? "internal" : result.Code;
            object raw = null;
            if (result != null && result.Body != null)
            {
                result.Body.TryGetValue("message", out raw);
            }
            string message = raw as string ?? "";
            if (status >= 500 || status < 400)
            {
                throw new BridgeException(status >= 500 ? status : 500, code, message);
            }
            return Failed(id, code, message);
        }

        static Dictionary<string, object> Failed(int id, string code, string message)
        {
            Dictionary<string, object> error = new Dictionary<string, object>();
            error["code"] = code ?? "bad_request";
            error["message"] = message ?? "";
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["id"] = id;
            item["error"] = error;
            return item;
        }
    }
}
