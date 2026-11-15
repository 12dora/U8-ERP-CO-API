using System;
using System.Net;
using System.Threading;

namespace U8Co
{
    internal sealed class HttpServer
    {
        const string Unauthorized = "{\"ok\":false,\"code\":\"unauthorized\"}";
        readonly BridgeConfig _cfg;
        readonly HttpListener _listener;
        volatile bool _stop;
        int _miss;
        static volatile Thread _accept;
        static int _expect;

        public HttpServer(BridgeConfig cfg)
        {
            _cfg = cfg;
            _listener = new HttpListener();
            _listener.Prefixes.Add(cfg.ListenPrefix);
            _listener.IgnoreWriteExceptions = true;
            ApplyTimeouts(_listener);
        }

        public static bool IsAlive
        {
            get
            {
                if (Interlocked.CompareExchange(ref _expect, 0, 0) == 0)
                {
                    return true;
                }
                Thread thread = _accept;
                return thread != null && thread.IsAlive;
            }
        }

        public void Start()
        {
            _listener.Start();
            Thread thread = new Thread(AcceptLoop);
            thread.IsBackground = true;
            thread.Name = "u8co-http";
            _accept = thread;
            thread.Start();
            Interlocked.Exchange(ref _expect, 1);
        }

        public void Stop()
        {
            Interlocked.Exchange(ref _expect, 0);
            _stop = true;
            try
            {
                _listener.Stop();
            }
            catch (Exception)
            {
            }
            try
            {
                _listener.Close();
            }
            catch (Exception)
            {
            }
        }

        static void ApplyTimeouts(HttpListener listener)
        {
            listener.TimeoutManager.IdleConnection = TimeSpan.FromSeconds(30);
            listener.TimeoutManager.HeaderWait = TimeSpan.FromSeconds(15);
            listener.TimeoutManager.EntityBody = TimeSpan.FromSeconds(20);
            listener.TimeoutManager.DrainEntityBody = TimeSpan.FromSeconds(10);
            listener.TimeoutManager.MinSendBytesPerSecond = 240;
            listener.TimeoutManager.RequestQueue = TimeSpan.FromSeconds(30);
        }

        void AcceptLoop()
        {
            while (!_stop)
            {
                HttpListenerContext ctx = Take();
                if (ctx == null)
                {
                    if (_stop)
                    {
                        return;
                    }
                    continue;
                }
                ThreadPool.QueueUserWorkItem(Handle, ctx);
            }
        }

        HttpListenerContext Take()
        {
            try
            {
                HttpListenerContext ctx = _listener.GetContext();
                _miss = 0;
                return ctx;
            }
            catch (HttpListenerException)
            {
                Backoff();
                return null;
            }
            catch (ObjectDisposedException)
            {
                _stop = true;
                return null;
            }
            catch (InvalidOperationException)
            {
                Backoff();
                return null;
            }
        }

        void Backoff()
        {
            if (_stop)
            {
                return;
            }
            if (_miss < 8)
            {
                _miss++;
            }
            Thread.Sleep(40 * _miss);
        }

        // 单个坏请求必须停在这里，不能把进程带走。
        void Handle(object state)
        {
            HttpListenerContext ctx = (HttpListenerContext)state;
            AuditDraft draft = new AuditDraft();
            draft.Start = DateTime.UtcNow;
            try
            {
                draft.Ip = Auth.ClientIp(ctx.Request);
                draft.Path = ctx.Request.Url == null ? "" : ctx.Request.Url.AbsolutePath;
                draft.Route = draft.Path;
                Route(ctx, draft);
            }
            catch (Exception ex)
            {
                GuardFail(draft, ex);
                if (!draft.Responded)
                {
                    TryWrite(ctx, ex);
                }
            }
        }

        void GuardFail(AuditDraft draft, Exception ex)
        {
            if (draft.Audited)
            {
                return;
            }
            try
            {
                AuditLog.Fail(_cfg, draft, ex, null);
            }
            catch (Exception)
            {
            }
        }

        void Route(HttpListenerContext ctx, AuditDraft draft)
        {
            string method = ctx.Request.HttpMethod == null ? "" : ctx.Request.HttpMethod.ToUpperInvariant();
            if (draft.Path == "/u8co/v1/health")
            {
                Health(ctx, draft, method);
                return;
            }
            if (!Auth.IpAllowed(_cfg, draft.Ip))
            {
                throw new BridgeException(401, "unauthorized", "");
            }
            if (_stop)
            {
                throw new BridgeException(503, "stopping", "服务正在停止");
            }
            byte[] body = Json.ReadBody(ctx.Request);
            RequireAuth(ctx, draft, method, body);
            if (method != "POST")
            {
                throw new BridgeException(400, "bad_request", "方法不允许");
            }
            Dispatch(ctx, draft, body);
        }

        void Dispatch(HttpListenerContext ctx, AuditDraft draft, byte[] body)
        {
            // meta：不登录 U8、不进 STA 队列，在 HTTP 线程上直接返回（BridgeMeta）。
            if (draft.Path == BridgeMeta.Path)
            {
                BridgeMeta.Serve(_cfg, ctx, draft, body);
                return;
            }
            WorkItem item = Requests.ForPath(_cfg, draft.Path, body, draft);
            if (item == null)
            {
                throw new BridgeException(404, "not_found", "未知路径");
            }
            if (WorkflowDown(draft.Path))
            {
                throw new BridgeException(503, "com_unavailable", AppHost.WorkflowProblem);
            }
            // 带幂等键的新建类请求走 IdemFlow：查记录、重放或等待同键请求，再入队。
            if (item.Idem != null)
            {
                IdemFlow.Run(ctx, draft, item);
                return;
            }
            Run(ctx, draft, item);
        }

        static bool WorkflowDown(string path)
        {
            if (AppHost.WorkflowReady || path == null)
            {
                return false;
            }
            return path.StartsWith("/u8co/v1/workflow/", StringComparison.Ordinal);
        }

        void Health(HttpListenerContext ctx, AuditDraft draft, string method)
        {
            if (method != "GET")
            {
                throw new BridgeException(400, "bad_request", "方法不允许");
            }
            if (Watchdog.Unhealthy())
            {
                Json.WriteRaw(ctx.Response, 503, AppInfo.SickJson);
                draft.Responded = true;
                AuditLog.Write(_cfg, draft, "unhealthy", "", null);
                return;
            }
            Json.WriteRaw(ctx.Response, 200, StaWorker.HealthJson());
            draft.Responded = true;
            AuditLog.Write(_cfg, draft, "ok", "", null);
        }

        void RequireAuth(HttpListenerContext ctx, AuditDraft draft, string method, byte[] body)
        {
            string ts = Header(ctx, "X-U8co-Ts");
            string nonce = Header(ctx, "X-U8co-Nonce");
            string sig = Header(ctx, "X-U8co-Sig");
            AuthAttempt attempt = new AuthAttempt();
            attempt.Method = method;
            attempt.Path = draft.Path;
            attempt.Ts = ts;
            attempt.Nonce = nonce;
            attempt.Sig = sig;
            attempt.Body = body;
            attempt.Ip = draft.Ip;
            attempt.Now = Auth.UnixNow();
            if (!Auth.Allow(_cfg, attempt))
            {
                throw new BridgeException(401, "unauthorized", "");
            }
        }

        static string Header(HttpListenerContext ctx, string name)
        {
            string value = ctx.Request.Headers[name];
            return value ?? "";
        }

        void Run(HttpListenerContext ctx, AuditDraft draft, WorkItem item)
        {
            item.Done = new ManualResetEvent(false);
            try
            {
                int queued = StaWorker.TryEnqueue(item);
                if (queued != StaWorker.Enqueued)
                {
                    throw QueueError(queued);
                }
                if (!item.Done.WaitOne(item.WaitMs))
                {
                    throw WaitError(item, draft);
                }
                draft.Audited = true;
                Publish(ctx, item);
                draft.Responded = true;
            }
            finally
            {
                CloseDone(item);
            }
        }

        internal static BridgeException QueueError(int queued)
        {
            if (queued == StaWorker.Stopping)
            {
                return new BridgeException(503, "stopping", "服务正在停止");
            }
            return new BridgeException(429, "busy", "服务忙，请稍后重试");
        }

        internal static BridgeException WaitError(WorkItem item, AuditDraft draft)
        {
            // 工作线程负责记 expired 或真实结果，这里不再写第二行。
            draft.Audited = true;
            int phase = Interlocked.CompareExchange(ref item.Phase, WorkItem.Abandoned, WorkItem.Queued);
            if (phase == WorkItem.Running || phase == WorkItem.Finished)
            {
                return new BridgeException(504, "outcome_unknown", "请求已在执行，结果未知");
            }
            return new BridgeException(503, "busy_timeout", "排队超时，可以重试");
        }

        static void Publish(HttpListenerContext ctx, WorkItem item)
        {
            if (item.Result == null)
            {
                throw new BridgeException(500, "internal", "没有结果");
            }
            WriteResult(ctx, item.Result);
        }

        static void CloseDone(WorkItem item)
        {
            try
            {
                item.Done.Close();
            }
            catch (Exception)
            {
            }
        }

        static void WriteResult(HttpListenerContext ctx, ApiResult result)
        {
            if (result.Status == 401)
            {
                Json.WriteRaw(ctx.Response, 401, Unauthorized);
                return;
            }
            Json.Write(ctx.Response, result.Status, result.Body);
        }

        static void TryWrite(HttpListenerContext ctx, Exception ex)
        {
            try
            {
                BridgeException bridge = ex as BridgeException;
                if (bridge == null)
                {
                    bridge = new BridgeException(500, "internal", "内部错误");
                }
                if (bridge.Status == 401)
                {
                    Json.WriteRaw(ctx.Response, 401, Unauthorized);
                    return;
                }
                Json.Write(ctx.Response, bridge.Status, ApiResult.From(bridge).Body);
            }
            catch (Exception)
            {
                try
                {
                    ctx.Response.Abort();
                }
                catch (Exception)
                {
                }
            }
        }
    }
}
