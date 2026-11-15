using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // archives/get_many：同一类档案按编码一次取几条（1 到 20 个，不分大小写不重复）。
    // 读路由，只跑 SQL（读线程池、登录缓存）；每个编码走与 archives/get 完全相同的读取（ArcRead.Get，含 PermHook.Archive），
    // 条目就是该编码的 archives/get 响应体；不存在、越权等 4xx 只记在该条目的 error 里，5xx 整个请求失败。
    // 功能权限按 archive:<档案> 整条路由检查一次（PermGate，同 archives/get）。
    internal static class ArcGetMany
    {
        internal const string Op = "get_many";
        internal const string Path = Requests.ArcRoot + Op;
        internal const int MaxCodes = 20;

        // 登录前（ArcRoutes.Check）和处理函数里各解析一次：每个编码都按 archives/get 的规则校验（ArcReq.Parse）。
        public static List<ArcReq> Parse(Dictionary<string, object> body)
        {
            IList codes = CodeList(body);
            List<ArcReq> reqs = new List<ArcReq>(codes.Count);
            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < codes.Count; i++)
            {
                string at = FieldPath.Item("codes", i);
                string code = codes[i] as string;
                if (code == null)
                {
                    throw BridgeException.BadField(at, "codes 的元素必须是字符串");
                }
                if (!seen.Add(code))
                {
                    throw BridgeException.BadField(at, "codes 有重复");
                }
                reqs.Add(One(body, code, at));
            }
            return reqs;
        }

        static IList CodeList(Dictionary<string, object> body)
        {
            object raw = Requests.Field(body, "codes");
            if (raw == null)
            {
                throw BridgeException.BadField("codes", "缺少字段 codes");
            }
            IList codes = raw as IList;
            if (codes == null || raw is string)
            {
                throw BridgeException.BadField("codes", "codes 必须是 JSON 数组");
            }
            if (codes.Count < 1 || codes.Count > MaxCodes)
            {
                throw BridgeException.BadField("codes", "codes 必须是 1 到 20 个");
            }
            return codes;
        }

        // archive 的错误保持 field=archive；编码的错误改成 codes.<i>。
        static ArcReq One(Dictionary<string, object> body, string code, string at)
        {
            Dictionary<string, object> one = new Dictionary<string, object>(StringComparer.Ordinal);
            one["archive"] = Requests.Field(body, "archive");
            one["code"] = code;
            try
            {
                return ArcReq.Parse("get", one);
            }
            catch (BridgeException ex)
            {
                if (ex.Status == 400 && ex.Field != "archive")
                {
                    ex.WithField(at);
                }
                throw;
            }
        }

        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Item == null || ctx.Item.Body == null)
            {
                throw new BridgeException(500, "internal", "档案请求缺少请求体");
            }
            List<ArcReq> reqs = Parse(ctx.Item.Body);
            List<object> items = new List<object>(reqs.Count);
            for (int i = 0; i < reqs.Count; i++)
            {
                items.Add(Get(ctx, reqs[i]));
            }
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["archive"] = reqs[0].Kind.Name;
            body["items"] = items;
            return ApiResult.Ok(body);
        }

        static Dictionary<string, object> Get(WorkContext ctx, ArcReq req)
        {
            ApiResult result;
            try
            {
                result = ArcRead.Get(ctx, req);
            }
            catch (BridgeException ex)
            {
                if (ex.Status >= 500)
                {
                    throw;
                }
                return Failed(req.Code, ex.Code, ex.Message);
            }
            if (result != null && result.Status == 200 && result.Body != null)
            {
                return result.Body;
            }
            if (result == null || result.Status >= 500 || result.Status < 400)
            {
                throw new BridgeException(500, "internal", "档案读取没有返回结果");
            }
            object message = null;
            if (result.Body != null)
            {
                result.Body.TryGetValue("message", out message);
            }
            return Failed(req.Code, result.Code, message as string);
        }

        static Dictionary<string, object> Failed(string code, string errorCode, string message)
        {
            Dictionary<string, object> error = new Dictionary<string, object>();
            error["code"] = errorCode ?? "bad_request";
            error["message"] = message ?? "";
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["code"] = code;
            item["error"] = error;
            return item;
        }
    }
}
