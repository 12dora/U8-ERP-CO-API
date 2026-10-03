using System;
using System.Threading;

namespace U8Co
{
    // U8 调用若卡住（会话 0 里的对话框、COM 不返回），健康检查先变 503，再让进程退出，由 SCM 按失败动作拉起。
    internal static class Watchdog
    {
        const long LimitTicks = 3L * 60L * 10000000L;
        const long SettleTicks = 75L * 10000000L;
        const int MaxSlots = 64;
        // 自检用的槽：不经 Register，不计入 _slots，看门狗线程不看它；自检用完 Leave。
        internal const int SelfTestSlot = MaxSlots - 1;
        // 每条工作线程（写线程和读线程）一个槽，放它这次忙的起点和卡死阈值；整个对象一次换掉，读的一方不会拿到半新半旧。
        // null 表示空闲。阈值缺省 3 分钟；存货核算脚本执行期间临时放宽（Extend / Restore），登录等其余阶段仍按 3 分钟。
        static readonly Busy[] Slots = new Busy[MaxSlots];
        static int _slots;
        static int _sick;
        // 本线程占的槽号加 1（0 表示没占）。Extend / Restore 只改本线程的槽。
        [ThreadStatic]
        static int _mine;

        sealed class Busy
        {
            public readonly long Since;
            public readonly long Limit;

            public Busy(long since, long limit)
            {
                Since = since;
                Limit = limit;
            }
        }

        public static int Register()
        {
            int slot = Interlocked.Increment(ref _slots) - 1;
            if (slot >= MaxSlots)
            {
                throw new InvalidOperationException("工作线程过多");
            }
            return slot;
        }

        public static void Enter(int slot)
        {
            _mine = slot + 1;
            Interlocked.Exchange(ref Slots[slot], new Busy(DateTime.UtcNow.Ticks, LimitTicks));
        }

        public static void Leave(int slot)
        {
            Interlocked.Exchange(ref Slots[slot], null);
            _mine = 0;
        }

        // 本线程正在做的任务从现在起再允许 extraTicks（存货核算脚本：执行加回滚的时间），不少于 3 分钟。本线程没占槽时什么也不做。
        public static void Extend(long extraTicks)
        {
            Reset(extraTicks);
        }

        // 脚本做完（成功或失败）：恢复成从现在起 3 分钟。
        public static void Restore()
        {
            Reset(LimitTicks);
        }

        static void Reset(long extraTicks)
        {
            int slot = _mine - 1;
            Busy cur = slot < 0 ? null : Read(slot);
            if (cur == null)
            {
                return;
            }
            long elapsed = DateTime.UtcNow.Ticks - cur.Since;
            Interlocked.Exchange(ref Slots[slot], new Busy(cur.Since, elapsed + Math.Max(LimitTicks, extraTicks)));
        }

        static Busy Read(int slot)
        {
            return Interlocked.CompareExchange(ref Slots[slot], null, null);
        }

        // 本线程当前槽还剩的允许时间（ticks），没占槽为 0。--selftest 用。
        internal static long CurrentAllowance()
        {
            int slot = _mine - 1;
            Busy cur = slot < 0 ? null : Read(slot);
            return cur == null ? 0 : cur.Limit - (DateTime.UtcNow.Ticks - cur.Since);
        }

        internal static long DefaultLimit
        {
            get { return LimitTicks; }
        }

        public static bool Unhealthy()
        {
            if (_sick != 0)
            {
                return true;
            }
            // legacyUnhandledExceptionPolicy 下线程池异常不会让进程退出。受理线程或任何工作线程死了也要报不健康。
            if (!HttpServer.IsAlive || !StaWorker.IsAlive)
            {
                return true;
            }
            return AnyStuck(DateTime.UtcNow.Ticks);
        }

        static bool AnyStuck(long now)
        {
            int count = Math.Min(Interlocked.CompareExchange(ref _slots, 0, 0), MaxSlots);
            for (int i = 0; i < count; i++)
            {
                Busy b = Read(i);
                if (b != null && now - b.Since > b.Limit)
                {
                    return true;
                }
            }
            return false;
        }

        public static void Start()
        {
            Thread thread = new Thread(Loop);
            thread.IsBackground = true;
            thread.Name = "u8co-watch";
            thread.Start();
        }

        static void Loop()
        {
            while (true)
            {
                Thread.Sleep(2000);
                if (!Unhealthy())
                {
                    continue;
                }
                Interlocked.Exchange(ref _sick, 1);
                StaWorker.Halt();
                Settle();
                // 经 StopGuard：恰逢请求停止时先报已停止，不触发失败重启；平时照常 FailFast，由恢复动作拉起。
                StopGuard.Kill("看门狗：任务超时，强制结束", "u8co job exceeded its time limit");
            }
        }

        // 先不再接新任务，等其他线程手上的任务做完（最多 75 秒），再退出进程，少打断正在写的单据。
        static void Settle()
        {
            Thread.Sleep(1500);
            long until = DateTime.UtcNow.Ticks + SettleTicks;
            while (DateTime.UtcNow.Ticks < until && AnyBusyWell(DateTime.UtcNow.Ticks))
            {
                Thread.Sleep(500);
            }
        }

        // 还有没超时的线程在忙。已经卡住的线程不等。
        static bool AnyBusyWell(long now)
        {
            int count = Math.Min(Interlocked.CompareExchange(ref _slots, 0, 0), MaxSlots);
            for (int i = 0; i < count; i++)
            {
                Busy b = Read(i);
                if (b != null && now - b.Since <= b.Limit)
                {
                    return true;
                }
            }
            return false;
        }
    }
}
