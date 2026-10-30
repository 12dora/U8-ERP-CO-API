using System;
using System.Globalization;
using System.Text;
using System.Threading;

namespace U8Co
{
    // VB6 COM 是套间线程。U8 登录对象不能交给线程池，业务只在自建的 STA 线程上跑。
    // 要登录的任务进写线程池，带操作员的固定在 OperatorPin.Index 那条线程上（同一操作员不会同时在两条线程上登录）；
    // 只跑 SQL 的读路由命中 AuthCache 时进读线程池，任一读线程可取，不登录。读线程执行前条目已失效的，
    // 不在读线程登录，转交写线程池（ReadRoute）。
    internal static class StaWorker
    {
        public const int Enqueued = StaPool.Enqueued;
        public const int QueueFull = StaPool.QueueFull;
        public const int Stopping = StaPool.Stopping;
        public const string ReadPoolName = "u8co-read";
        static volatile StaPool _write;
        static volatile StaPool _read;

        public static void Start(BridgeConfig cfg)
        {
            AuthCache.Configure(cfg.AuthCacheSeconds);
            LoginCache.Configure(cfg.LoginReuse, 16, 60, cfg.LoginReuseOffHours);
            // 「登录状态不是 0」自愈（wedgedLoginRecycle、wedgedLoginRestart）。
            WedgedLogin.Configure(cfg);
            StaPool write = new StaPool("u8co-sta", cfg.StaWorkers, cfg.QueueCap, StaExec.Write, true, null);
            StaPool read = new StaPool(ReadPoolName, cfg.ReadWorkers, cfg.QueueCap, StaExec.Read, false, ReadRoute);
            write.Start();
            read.Start();
            _write = write;
            _read = read;
        }

        public static bool IsAlive
        {
            get
            {
                StaPool write = _write;
                StaPool read = _read;
                return (write == null || write.IsAlive) && (read == null || read.IsAlive);
            }
        }

        // 停止后用：两个池里还有线程没退出（多半卡在 COM 调用里）。
        public static bool AnyAlive
        {
            get
            {
                StaPool write = _write;
                StaPool read = _read;
                return (write != null && write.AnyAlive) || (read != null && read.AnyAlive);
            }
        }

        public static int TryEnqueue(WorkItem item)
        {
            StaPool write = _write;
            StaPool read = _read;
            if (write == null || read == null)
            {
                return Stopping;
            }
            return Enqueue(item, CachedAuth(item), write, read);
        }

        // 命中 AuthCache 的读路由只跑 SQL，不要单据锁，进读线程池（不固定）。
        // 未命中的读路由也走写线程：登录一次，顺便把缓存补上。自检直接传池和条目。
        internal static int Enqueue(WorkItem item, AuthEntry auth, StaPool write, StaPool read)
        {
            if (auth != null)
            {
                item.Auth = auth;
                item.LockKeys = new string[0];
                return read.TryEnqueue(item);
            }
            item.Auth = null;
            item.LockKeys = DocLocks.KeysOf(item);
            return write.TryEnqueue(item);
        }

        static WorkExec ReadRoute(WorkItem item)
        {
            return Route(item, _write);
        }

        // 读线程取到任务、执行前：AuthCache 条目仍可用就在本线程跑 SQL（StaExec.Read）。
        // 失效了（排队期间过期或被删掉）不在读线程登录：同一个任务原样转交写线程池，按操作员固定，
        // 排队截止时刻不变（StaPool.TryEnqueue 保留更早的截止），排在写队列末尾。转交失败按入队失败的
        // 429 busy / 503 stopping 结束（审计照写）。已过排队截止或已放弃的留在本线程，由 WorkRun 记 expired。
        // 入队不重置 Phase（TryEnqueueHandoff）：HTTP 线程恰好已放弃的任务返回 Gone，留在本线程由 WorkRun 记 expired。
        // 出异常（如算单据锁）按 500 internal 结束，不在读线程上登录。
        internal static WorkExec Route(WorkItem item, StaPool write)
        {
            try
            {
                if (StaExec.Usable(item.Auth) || DateTime.UtcNow.Ticks > item.DeadlineUtcTicks
                    || Interlocked.CompareExchange(ref item.Phase, 0, 0) != WorkItem.Queued)
                {
                    return StaExec.Read;
                }
                item.Auth = null;
                item.LockKeys = DocLocks.KeysOf(item);
                int queued = write == null ? Stopping : write.TryEnqueueHandoff(item);
                if (queued == Enqueued)
                {
                    return null;
                }
                if (queued == StaPool.Gone)
                {
                    return StaExec.Read;
                }
                return queued == Stopping ? new WorkExec(RefuseStopping) : new WorkExec(RefuseFull);
            }
            catch (Exception)
            {
                return RefuseInternal;
            }
        }

        static ApiResult RefuseInternal(WorkItem item)
        {
            throw new BridgeException(500, "internal", "读线程转交任务失败");
        }

        static ApiResult RefuseFull(WorkItem item)
        {
            throw HttpServer.QueueError(QueueFull);
        }

        static ApiResult RefuseStopping(WorkItem item)
        {
            throw HttpServer.QueueError(Stopping);
        }

        static AuthEntry CachedAuth(WorkItem item)
        {
            if (!AuthCache.Enabled || !RouteClass.IsSqlRead(item))
            {
                return null;
            }
            return AuthCache.Find(AuthCache.KeyOf(item, WorkRun.SubOf(item)));
        }

        // 两个池先都清空队列，再在同一个总时限内等所有线程。
        public static void Stop(int joinMs)
        {
            long deadline = DateTime.UtcNow.AddMilliseconds(joinMs).Ticks;
            StaPool write = _write;
            StaPool read = _read;
            CancelQueued(write);
            CancelQueued(read);
            if (write != null)
            {
                write.Join(deadline);
            }
            if (read != null)
            {
                read.Join(deadline);
            }
        }

        // 看门狗判定不健康后调用：两个池都不再接新任务，排队中的任务返回 503 stopping，正在跑的不打断。
        public static void Halt()
        {
            CancelQueued(_write);
            CancelQueued(_read);
        }

        static void CancelQueued(StaPool pool)
        {
            if (pool == null)
            {
                return;
            }
            WorkItem[] left = pool.Drain();
            for (int i = 0; i < left.Length; i++)
            {
                WorkRun.Cancel(left[i]);
            }
        }

        // 在原来的 {"ok":true,"version":"1"} 后面追加线程和队列计数、许可点数状态（LicenseView，不影响 200）、登录复用计数（LoginCache）、
        // 写入策略、只读账套和第二级写入开关（TestAccountGate）、操作员固定（PinHealth）、「登录状态不是 0」自愈（WedgedLogin，不含操作员）。
        public static string HealthJson()
        {
            StaPool write = _write;
            StaPool read = _read;
            int running = (write == null ? 0 : write.Running) + (read == null ? 0 : read.Running);
            return string.Format(
                CultureInfo.InvariantCulture,
                "{{\"ok\":true,\"version\":\"{0}\",\"workers\":{1},\"read_workers\":{2},"
                + "\"queued\":{3},\"read_queued\":{4},\"running\":{5},\"signatures\":\"{6}\"{7}{8}{9}{10}{11}{12}{13}}}",
                AppInfo.Version,
                write == null ? 0 : write.Size,
                read == null ? 0 : read.Size,
                write == null ? 0 : write.Queued,
                read == null ? 0 : read.Queued,
                running,
                SigCheck.HealthSummary(),
                LicenseHealth(),
                LoginCache.HealthFragment(),
                WritePolicy.HealthFragment(),
                ReadOnlyGate.HealthFragment(),
                TestAccountGate.HealthFragment(),
                PinHealth(write),
                WedgedLogin.HealthFragment());
        }

        // 操作员固定：pinning，以及每条写线程（按序号）排队中固定到它的任务数 worker_queued。
        internal static string PinHealth(StaPool write)
        {
            int[] counts = write == null ? new int[0] : write.QueuedByWorker();
            StringBuilder sb = new StringBuilder();
            sb.Append(",\"pinning\":").Append(write != null && write.Pinned ? "true" : "false");
            sb.Append(",\"worker_queued\":[");
            for (int i = 0; i < counts.Length; i++)
            {
                if (i > 0)
                {
                    sb.Append(',');
                }
                sb.Append(counts[i].ToString(CultureInfo.InvariantCulture));
            }
            sb.Append(']');
            return sb.ToString();
        }

        static string LicenseHealth()
        {
            try
            {
                return LicenseView.HealthFragment();
            }
            catch (Exception)
            {
                return ",\"license\":\"unknown\",\"license_detail\":{}";
            }
        }
    }
}
