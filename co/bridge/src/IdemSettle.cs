using System;
using System.Collections.Generic;
using System.Threading;
using System.Web.Script.Serialization;

namespace U8Co
{
    // 幂等请求结束时的落盘和唤醒。只存两种终态：
    // ok（2xx）和 outcome_unknown（其余 5xx，可能已经写进 U8）。
    // 所有 4xx 都是提交前的拒绝（CHECKLIST：提交之后的失败一律 504），5xx 里只有 FreeCodes 列出的
    // 排队、停止类错误码保证没有执行；这些都删掉记录、不占用键，调用方修正后可以用同一个键重发。
    internal static partial class IdemFlow
    {
        const string UnauthorizedJson = "{\"ok\":false,\"code\":\"unauthorized\"}";
        const int PersistTries = 3;

        // 保证没有执行过 U8 写的错误码。只按错误码判断，不按 HTTP 状态。
        static readonly string[] FreeCodes = new string[]
        {
            "busy", "queue_full", "stopping", "busy_timeout", "unauthorized", "forbidden",
            "login_failed", "account_not_allowed", "no_permission",
            // U8 许可点数已满：登录都没成功，什么也没写
            "u8_license_full",
            // 存货核算脚本超时（SqlScript）：在请求事务里，提交之前超时并回滚，没有写入
            "ia_timeout",
            // 写入策略：都在登录和执行之前拒绝，没有写入
            "write_policy_unavailable", "write_frozen", "write_window", "write_not_allowed", "operator_not_allowed",
            "write_limit", "write_quota", "u8_license_hold"
        };

        // 本进程里正在执行的键。用 Monitor 等待，不占系统句柄；重启后为空，磁盘上剩下的 in_flight 就是中断遗留。
        sealed class Slot
        {
            public readonly string BodySha;
            public readonly IdemRecord Seed;
            readonly object _sync = new object();
            bool _done;
            public int Status;
            public string Response;
            public bool Released;

            public Slot(string bodySha, IdemRecord seed)
            {
                BodySha = bodySha;
                Seed = seed;
            }

            public void Finish(int status, string response, bool released)
            {
                lock (_sync)
                {
                    Status = status;
                    Response = response;
                    Released = released;
                    _done = true;
                    Monitor.PulseAll(_sync);
                }
            }

            public bool Wait(int ms)
            {
                long deadline = DateTime.UtcNow.Ticks + ms * TimeSpan.TicksPerMillisecond;
                lock (_sync)
                {
                    while (!_done)
                    {
                        int left = RemainingMs(deadline);
                        if (left <= 0)
                        {
                            return false;
                        }
                        Monitor.Wait(_sync, left);
                    }
                    return true;
                }
            }
        }

        // 按结果定状态并落盘，再唤醒等同一个键的请求。只有持有这个键的那一次能落盘，重复调用不生效。
        static void Settle(string id, Slot own, ApiResult result)
        {
            int status = result == null ? 500 : result.Status;
            string code = result == null ? "internal" : result.Code;
            string body;
            try
            {
                body = BodyOf(result);
            }
            catch (Exception)
            {
                // 响应体序列化失败（例如超过 8 MiB）：不能让键一直卡在执行中，按结果未知记下。
                status = 504;
                code = "outcome_unknown";
                body = ErrorJson("outcome_unknown", "结果无法返回；请先用读取或列表核对");
            }
            string state = StateOf(status, code);
            lock (Gate)
            {
                Slot cur;
                if (!Slots.TryGetValue(id, out cur) || cur != own)
                {
                    return;
                }
                Persist(id, own, state, status, body);
                Slots.Remove(id);
            }
            own.Finish(status, body, state == null);
        }

        // 终态按 Seed 整条重写（不依赖旧文件还在），失败重试几次；仍失败就记审计事件。
        // 这时磁盘上还是 in_flight（MoveFileEx 原子替换，不会没有文件），之后按结果未知重放，不会重做。
        static void Persist(string id, Slot own, string state, int status, string body)
        {
            Exception last = null;
            for (int i = 0; i < PersistTries; i++)
            {
                try
                {
                    if (state == null)
                    {
                        IdemStore.Delete(id);
                    }
                    else
                    {
                        IdemStore.Write(id, Final(own.Seed, state, status, body));
                    }
                    return;
                }
                catch (Exception ex)
                {
                    last = ex;
                    Thread.Sleep(50 * (i + 1));
                }
            }
            NotePersistFailure(id, state, last);
        }

        static IdemRecord Final(IdemRecord seed, string state, int status, string body)
        {
            IdemRecord rec = new IdemRecord();
            rec.State = state;
            rec.BodySha = seed.BodySha;
            rec.Status = status;
            rec.Response = body;
            rec.CreatedTicks = seed.CreatedTicks;
            rec.Route = seed.Route;
            rec.Acc = seed.Acc;
            rec.Caller = seed.Caller;
            rec.Operator = seed.Operator;
            return rec;
        }

        static void NotePersistFailure(string id, string state, Exception ex)
        {
            Dictionary<string, object> fields = new Dictionary<string, object>();
            fields["id"] = id;
            fields["state"] = state ?? "released";
            fields["error"] = ex == null ? "" : ex.GetType().Name;
            AuditEvent.Write("idem_persist_failed", fields);
        }

        internal static string StateOf(int status, string code)
        {
            if (status >= 200 && status < 300)
            {
                return IdemStore.Ok;
            }
            if (status >= 400 && status < 500)
            {
                return null;
            }
            if (code != null && Array.IndexOf(FreeCodes, code) >= 0)
            {
                return null;
            }
            return IdemStore.Unknown;
        }

        static string BodyOf(ApiResult result)
        {
            if (result == null)
            {
                return ErrorJson("internal", "内部错误");
            }
            if (result.Status == 401)
            {
                return UnauthorizedJson;
            }
            JavaScriptSerializer ser = new JavaScriptSerializer();
            ser.MaxJsonLength = Json.ResponseLimit;
            return ser.Serialize(result.Body);
        }

        static string ErrorJson(string code, string message)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = false;
            body["code"] = code;
            body["message"] = message;
            return new JavaScriptSerializer().Serialize(body);
        }
    }
}
