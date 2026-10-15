using System;
using System.Collections.Generic;
using System.Threading;

namespace U8Co
{
    // 线程取到任务、执行前决定在哪跑：返回本线程的执行体；返回 null 表示已转交别的池，本线程不再管它。
    internal delegate WorkExec WorkRoute(WorkItem item);

    // N 条 STA 线程共用一条队列。线程取第一个键全部空闲的任务（可运行任务之间先进先出），
    // 跑完在 finally 里放键。键被占用的任务留在队列里，照样受 75 秒排队期限约束；
    // 写路由剩下的时间不够 WorkItem.MinWriteStartMs 就不再开始（503 busy_timeout，没有执行）。
    // pinned 时（写线程池）带操作员的任务只给 OperatorPin.Index 那条线程取，别的线程空闲也不代取；
    // 已过期或已放弃的任务任一线程都可取出，尽快按 503 busy_timeout 返回。
    // route 非空时（读线程池）每个任务执行前先问它，见 WorkRoute。
    internal sealed class StaPool
    {
        public const int Enqueued = 0;
        public const int QueueFull = 1;
        public const int Stopping = 2;
        // 只用于 TryEnqueueHandoff：任务已不在排队状态（HTTP 线程等超时已放弃），没有入队。
        public const int Gone = 3;
        readonly object _gate = new object();
        readonly List<WorkItem> _queue = new List<WorkItem>();
        readonly DocLocks _locks = new DocLocks();
        readonly string _name;
        readonly int _size;
        readonly int _cap;
        readonly WorkExec _exec;
        readonly bool _pinned;
        readonly WorkRoute _route;
        Thread[] _threads = new Thread[0];
        int _running;
        int _expect;
        bool _stop;

        public StaPool(string name, int size, int cap, WorkExec exec, bool pinned, WorkRoute route)
        {
            _name = name;
            _size = size;
            _cap = cap;
            _exec = exec;
            _pinned = pinned;
            _route = route;
        }

        public bool Pinned
        {
            get { return _pinned; }
        }

        public int Size
        {
            get { return _size; }
        }

        public int Running
        {
            get { return Interlocked.CompareExchange(ref _running, 0, 0); }
        }

        public int Queued
        {
            get
            {
                lock (_gate)
                {
                    return _queue.Count;
                }
            }
        }

        // 健康检查用：按线程序号数排队中固定到该线程、还会执行的任务。不固定的、已过期或已放弃的不计入（总数见 Queued）。
        public int[] QueuedByWorker()
        {
            int[] counts = new int[_size];
            long now = DateTime.UtcNow.Ticks;
            lock (_gate)
            {
                for (int i = 0; i < _queue.Count; i++)
                {
                    WorkItem item = _queue[i];
                    if (item.Pin >= 0 && item.Pin < _size && !Expired(item, now))
                    {
                        counts[item.Pin]++;
                    }
                }
            }
            return counts;
        }

        public void Start()
        {
            Thread[] threads = new Thread[_size];
            lock (_gate)
            {
                _stop = false;
            }
            for (int i = 0; i < _size; i++)
            {
                int slot = Watchdog.Register();
                Thread thread = new Thread(new ParameterizedThreadStart(Loop));
                thread.IsBackground = true;
                thread.Name = _name + "-" + (i + 1);
                thread.SetApartmentState(ApartmentState.STA);
                threads[i] = thread;
                // 状态：{看门狗槽位, 线程序号}，序号即 OperatorPin.Index 的取值。
                thread.Start(new int[] { slot, i });
            }
            _threads = threads;
            Interlocked.Exchange(ref _expect, 1);
        }

        // 停止后不再检查线程存活。运行中任一线程死掉都算整池不健康。
        public bool IsAlive
        {
            get
            {
                if (Interlocked.CompareExchange(ref _expect, 0, 0) == 0)
                {
                    return true;
                }
                Thread[] threads = _threads;
                for (int i = 0; i < threads.Length; i++)
                {
                    if (threads[i] == null || !threads[i].IsAlive)
                    {
                        return false;
                    }
                }
                return true;
            }
        }

        // 与 IsAlive 不同，停止后也看：还有线程活着就是 true。
        public bool AnyAlive
        {
            get
            {
                Thread[] threads = _threads;
                for (int i = 0; i < threads.Length; i++)
                {
                    if (threads[i] != null && threads[i].IsAlive)
                    {
                        return true;
                    }
                }
                return false;
            }
        }

        public int TryEnqueue(WorkItem item)
        {
            return Add(item, false);
        }

        // 别的池转交过来的任务（读线程上 AuthCache 条目失效）：不重置 Phase，只有仍在排队状态才入队，
        // 免得把 HTTP 线程已放弃的任务又放回队列；放弃了返回 Gone，由原线程按 expired 结束一次。
        public int TryEnqueueHandoff(WorkItem item)
        {
            return Add(item, true);
        }

        int Add(WorkItem item, bool handoff)
        {
            lock (_gate)
            {
                if (_stop)
                {
                    return Stopping;
                }
                if (_queue.Count >= _cap)
                {
                    return QueueFull;
                }
                if (!handoff)
                {
                    item.Phase = WorkItem.Queued;
                }
                else if (Interlocked.CompareExchange(ref item.Phase, 0, 0) != WorkItem.Queued)
                {
                    return Gone;
                }
                // 固定线程只在入队时算一次；不固定的池一律 None。从读线程池转交过来的任务在这里按操作员重新固定。
                item.Pin = _pinned ? OperatorPin.Index(item.Operator, _size) : OperatorPin.None;
                // 调用方已按自己更早的等待截止时刻设过的（幂等请求先等过同键请求）保留，否则从现在起 WaitMs（缺省 75 秒）。
                long cap = DateTime.UtcNow.AddMilliseconds(item.WaitMs).Ticks;
                if (item.DeadlineUtcTicks <= 0 || item.DeadlineUtcTicks > cap)
                {
                    item.DeadlineUtcTicks = cap;
                }
                _queue.Add(item);
                Monitor.PulseAll(_gate);
                return Enqueued;
            }
        }

        public WorkItem[] Drain()
        {
            Interlocked.Exchange(ref _expect, 0);
            lock (_gate)
            {
                _stop = true;
                WorkItem[] left = _queue.ToArray();
                _queue.Clear();
                Monitor.PulseAll(_gate);
                return left;
            }
        }

        // 所有线程共用同一个截止时刻，不按线程数放大等待。
        public void Join(long deadlineTicks)
        {
            Thread[] threads = _threads;
            for (int i = 0; i < threads.Length; i++)
            {
                long left = deadlineTicks - DateTime.UtcNow.Ticks;
                int ms = left <= 0 ? 0 : (int)Math.Min(left / TimeSpan.TicksPerMillisecond, int.MaxValue);
                threads[i].Join(ms);
            }
        }

        void Loop(object state)
        {
            int[] args = (int[])state;
            int slot = args[0];
            int index = args[1];
            if (_name == StaWorker.ReadPoolName)
            {
                // 读线程池不缓存登录（loginReuse 只作用于写线程）。
                LoginCache.MarkNoKeepThread();
            }
            try
            {
                while (true)
                {
                    bool idle;
                    WorkItem item = Take(index, out idle);
                    if (item == null && idle)
                    {
                        // 空闲：在本 STA 上关掉已过期的缓存登录（loginReuse），不持队列锁。
                        Guarded(slot, false);
                        continue;
                    }
                    if (item == null)
                    {
                        return;
                    }
                    Run(item, slot);
                }
            }
            finally
            {
                // 线程退出前（含异常退出）在本 STA 上关掉缓存的登录（loginReuse）。
                Guarded(slot, true);
            }
        }

        // ShutDown 可能卡住：本线程有缓存登录时才占看门狗槽位（与 WorkRun.Run 相同），卡住照样触发看门狗。
        static void Guarded(int slot, bool drain)
        {
            if (LoginCache.CachedCountForThisThread() == 0)
            {
                return;
            }
            Watchdog.Enter(slot);
            try
            {
                if (drain)
                {
                    LoginCache.Drain();
                }
                else
                {
                    LoginCache.Sweep();
                }
            }
            finally
            {
                Watchdog.Leave(slot);
            }
        }

        // 等满 500 毫秒仍没有可执行的任务时返回 null 并置 idle，由 Loop 做空闲清理后再来取。
        WorkItem Take(int index, out bool idle)
        {
            idle = false;
            lock (_gate)
            {
                while (true)
                {
                    WorkItem item = Pick(index);
                    if (item != null)
                    {
                        return item;
                    }
                    if (_stop)
                    {
                        return null;
                    }
                    if (!Monitor.Wait(_gate, 500))
                    {
                        idle = true;
                        return null;
                    }
                }
            }
        }

        // 自检（OperatorPinSelfTest）用：不起线程，按第 index 条线程的身份取一次。
        internal WorkItem PickFor(int index)
        {
            lock (_gate)
            {
                return Pick(index);
            }
        }

        // 调用方持有 _gate。已过期或已放弃的任务不需要键，也不看固定线程，直接取出去记 expired，不占队列。
        // 固定到别的线程的活任务跳过（不代取：代取就会在另一条线程上登录同一操作员）。
        WorkItem Pick(int index)
        {
            for (int i = 0; i < _queue.Count; i++)
            {
                WorkItem item = _queue[i];
                bool dead = Dead(item);
                if (!dead && (!OperatorPin.Matches(item, index) || !_locks.Free(item.LockKeys)))
                {
                    continue;
                }
                _queue.RemoveAt(i);
                if (!dead)
                {
                    _locks.Hold(item.LockKeys);
                    item.KeysHeld = true;
                }
                return item;
            }
            return null;
        }

        // 写路由剩下的时间不够开始执行时标成已放弃，WorkRun.Skip 按 expired 返回 503 busy_timeout。
        static bool Dead(WorkItem item)
        {
            long now = DateTime.UtcNow.Ticks;
            if (now > item.DeadlineUtcTicks)
            {
                return true;
            }
            if (WorkItem.TooLateToWrite(item, now))
            {
                Interlocked.CompareExchange(ref item.Phase, WorkItem.Abandoned, WorkItem.Queued);
                return true;
            }
            return Interlocked.CompareExchange(ref item.Phase, 0, 0) != WorkItem.Queued;
        }

        // 同 Dead，但不改 Phase（只读的计数用）。
        static bool Expired(WorkItem item, long now)
        {
            return now > item.DeadlineUtcTicks || WorkItem.TooLateToWrite(item, now)
                || Interlocked.CompareExchange(ref item.Phase, 0, 0) != WorkItem.Queued;
        }

        void Run(WorkItem item, int slot)
        {
            WorkExec exec = _exec;
            if (_route != null)
            {
                // 先放键再问路：转交后另一个池会重新持键（KeysHeld），不能再由本池放。
                // 用 route 的池（读线程池）入队时不带单据锁，执行时也就不持键。
                // 问路出异常不能打死线程：照常走 WorkRun，按 500 internal 结束（一行审计、一次 Done）。
                try
                {
                    Release(item);
                    exec = _route(item);
                }
                catch (Exception)
                {
                    exec = RouteFailed;
                }
                if (exec == null)
                {
                    return;
                }
            }
            Interlocked.Increment(ref _running);
            try
            {
                WorkRun.Run(item, exec, slot);
            }
            finally
            {
                Interlocked.Decrement(ref _running);
                Release(item);
            }
        }

        static ApiResult RouteFailed(WorkItem item)
        {
            throw new BridgeException(500, "internal", "读线程转交任务失败");
        }

        void Release(WorkItem item)
        {
            if (!item.KeysHeld)
            {
                return;
            }
            lock (_gate)
            {
                _locks.Release(item.LockKeys);
                item.KeysHeld = false;
                Monitor.PulseAll(_gate);
            }
        }
    }
}
