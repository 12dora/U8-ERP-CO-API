using System;
using System.Collections.Generic;
using System.Globalization;
using System.Web.Script.Serialization;

namespace U8Co
{
    // idempotency/get 的请求：原请求的桥路径、幂等键、调用方（缺省 direct），校验规则同新建路由（IdemReq）。
    internal sealed class IdemGetAsk
    {
        public string Route;
        public IdemAsk Idem;
    }

    // idempotency/get：按原请求的 caller + 账套 + 路径 + 幂等键查幂等记录。读路由：照常登录核对（读线程、登录缓存），
    // 不持写闸门、不持锁，不改记录。found=false 表示没有记录、已过期、只是执行前被拒绝（不占用键，同 IdemFlow.Load），
    // 或记录不是本操作员写的（记录里的 operator，去空格、不分大小写比较）。早期版本写的记录没有 operator：只给账套主管看，
    // 其他人一律 found=false（存下的响应可能含别人的单据内容，不能按「没有操作员」放行）。
    // state：ok（2xx 已完成）、outcome_unknown（可能已写入）、in_flight（本进程正在执行）。
    // 磁盘上是 in_flight、本进程却没有在跑（服务中断遗留）时按 outcome_unknown 报，同重放时的处理（IdemFlow.Orphan），但不改写记录。
    internal static class IdemGet
    {
        internal const string Path = "/u8co/v1/idempotency/get";
        internal const string Action = "idempotency_get";
        const string GlPrefix = "/u8co/v1/gl/";
        const string RouteHint = "取原请求的写路由桥路径（meta routes[] 里 optional 含 idempotency_key 的路由），如 /u8co/v1/vouchers/verify";
        const string UnknownText = "该幂等键的上次请求结果未知；请先用读取或列表核对";

        // 登录前和处理函数里各调一次，纯校验。
        public static IdemGetAsk Parse(Dictionary<string, object> body)
        {
            IdemGetAsk ask = new IdemGetAsk();
            string route = Field(body, "route") as string;
            if (route == null || !IdemReq.Supports(route))
            {
                throw new BridgeException(400, "bad_request", route == null ? "缺少 route" : "route 不支持幂等键", "route", RouteHint);
            }
            ask.Route = route;
            ask.Idem = new IdemAsk();
            ask.Idem.Key = Checked(IdemReq.KeyField, Field(body, IdemReq.KeyField), true);
            // 同 IdemReq.Take：出现了 caller 就校验（含 null），没出现才是 direct。
            bool hasCaller = body != null && body.ContainsKey(IdemReq.CallerField);
            ask.Idem.Caller = hasCaller ? Checked(IdemReq.CallerField, body[IdemReq.CallerField], false) : IdemReq.DirectCaller;
            return ask;
        }

        // 登录子系统：总账路由（/u8co/v1/gl/…）用 GL，其余写路由用 AS（基础设置，任何操作员都有的最小权限）。
        // 查询不带单据类型、flag，不需要 SA / AR / AP。
        internal static string SubOf(string route)
        {
            return route != null && route.StartsWith(GlPrefix, StringComparison.Ordinal) ? "GL" : "AS";
        }

        static string Checked(string field, object value, bool key)
        {
            if (key && value == null)
            {
                throw BridgeException.BadField(field, "缺少 " + field);
            }
            try
            {
                return key ? IdemReq.CheckKey(value) : IdemReq.CheckCaller(value);
            }
            catch (BridgeException ex)
            {
                throw BridgeException.BadField(field, ex.Message);
            }
        }

        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Item == null || ctx.Item.Body == null)
            {
                throw new BridgeException(500, "internal", "查询请求缺少请求体");
            }
            IdemGetAsk ask = Parse(ctx.Item.Body);
            string id = ask.Idem.StoreId(ctx.Item.Acc, ask.Route);
            CoRows.Note(ctx.Item, "幂等键 " + ask.Idem.Key);
            bool running;
            IdemRecord rec = IdemFlow.Peek(id, out running);
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            if (rec == null || rec.State == IdemStore.Failed || IdemStore.Expired(rec, DateTime.UtcNow)
                || !Visible(ctx, rec))
            {
                body["found"] = false;
                return ApiResult.Ok(body);
            }
            body["found"] = true;
            Describe(body, rec, running);
            body["created_utc"] = new DateTime(rec.CreatedTicks, DateTimeKind.Utc)
                .ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture);
            return ApiResult.Ok(body);
        }

        // 只有 2xx 且内容完整的 ok 记录报 ok；其余（含内容缺失、中断遗留）一律 outcome_unknown，同 IdemFlow.ReplayBody。
        static void Describe(Dictionary<string, object> body, IdemRecord rec, bool running)
        {
            if (rec.State == IdemStore.InFlight && running)
            {
                body["state"] = "in_flight";
                return;
            }
            object stored = ParseJson(rec.Response);
            if (Done(rec, stored))
            {
                body["state"] = "ok";
                body["status"] = rec.Status;
                body["response"] = stored;
                return;
            }
            bool known = rec.State == IdemStore.Unknown && rec.Status >= 500 && stored != null;
            body["state"] = "outcome_unknown";
            body["status"] = known ? rec.Status : 504;
            body["response"] = known ? stored : UnknownBody();
        }

        // 记录只给写入它的操作员看。没有操作员的旧记录才查权限快照（账套主管放行），其余不查库。
        static bool Visible(WorkContext ctx, IdemRecord rec)
        {
            if (Blank(rec.Operator))
            {
                // 本路由不是 PermRegistry 的读路由，PermCheck.Of 每次现读（不进权限缓存）。
                return Allowed(rec.Operator, ctx.Item.Operator, PermCheck.Of(ctx).Supervisor);
            }
            return Allowed(rec.Operator, ctx.Item.Operator, false);
        }

        // 纯判断（自检用）：记录有操作员时须与调用方相同（去空格、不分大小写，调用方为空不放行）；
        // 记录没有操作员时只放行账套主管。
        internal static bool Allowed(string stored, string op, bool supervisor)
        {
            if (Blank(stored))
            {
                return supervisor;
            }
            string caller = op == null ? "" : op.Trim();
            return caller.Length > 0 && string.Equals(stored.Trim(), caller, StringComparison.OrdinalIgnoreCase);
        }

        static bool Blank(string text)
        {
            return text == null || text.Trim().Length == 0;
        }

        static bool Done(IdemRecord rec, object stored)
        {
            return rec.State == IdemStore.Ok && rec.Status >= 200 && rec.Status < 300 && stored != null;
        }

        static Dictionary<string, object> UnknownBody()
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = false;
            body["code"] = "outcome_unknown";
            body["message"] = UnknownText;
            return body;
        }

        // 存下的响应是 JSON 对象原文；解析不了按内容缺失处理。
        static object ParseJson(string text)
        {
            if (text == null || text.Length == 0)
            {
                return null;
            }
            try
            {
                JavaScriptSerializer ser = new JavaScriptSerializer();
                ser.MaxJsonLength = Json.ResponseLimit;
                return ser.DeserializeObject(text) as Dictionary<string, object>;
            }
            catch (Exception)
            {
                return null;
            }
        }

        static object Field(Dictionary<string, object> body, string key)
        {
            object value;
            if (body == null || !body.TryGetValue(key, out value))
            {
                return null;
            }
            return value;
        }
    }

    internal static partial class IdemFlow
    {
        // idempotency/get 用：在 Gate 里同时读记录和本进程的执行槽，与 Claim / Settle 不交错。读不出记录时 503，同 IdemFlow.Load。
        internal static IdemRecord Peek(string id, out bool running)
        {
            lock (Gate)
            {
                running = Slots.ContainsKey(id);
                try
                {
                    return IdemStore.Read(id);
                }
                catch (Exception)
                {
                    throw StoreDown();
                }
            }
        }
    }
}
