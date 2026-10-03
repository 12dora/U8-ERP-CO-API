using System;
using System.Globalization;

namespace U8Co
{
    // VB6 COM 是套间线程。U8 登录对象不能交给线程池，业务只在自建的 STA 线程上跑：
    // 写线程池每个任务一次登录；只跑 SQL 的读路由命中登录缓存时进读线程池，不登录。
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
            StaPool write = new StaPool("u8co-sta", cfg.StaWorkers, cfg.QueueCap, StaExec.Write);
            StaPool read = new StaPool(ReadPoolName, cfg.ReadWorkers, cfg.QueueCap, StaExec.Read);
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
            AuthEntry auth = CachedAuth(item);
            if (auth != null)
            {
                item.Auth = auth;
                item.LockKeys = new string[0];
                return read.TryEnqueue(item);
            }
            // 读路由未命中缓存也走写线程：登录一次，顺便把缓存补上。
            item.LockKeys = DocLocks.KeysOf(item);
            return write.TryEnqueue(item);
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
        // 写入策略、只读账套和第二级写入开关（TestAccountGate）。
        public static string HealthJson()
        {
            StaPool write = _write;
            StaPool read = _read;
            int running = (write == null ? 0 : write.Running) + (read == null ? 0 : read.Running);
            return string.Format(
                CultureInfo.InvariantCulture,
                "{{\"ok\":true,\"version\":\"{0}\",\"workers\":{1},\"read_workers\":{2},"
                + "\"queued\":{3},\"read_queued\":{4},\"running\":{5},\"signatures\":\"{6}\"{7}{8}{9}{10}{11}}}",
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
                TestAccountGate.HealthFragment());
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
