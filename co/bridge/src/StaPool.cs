using System;
using System.Collections.Generic;
using System.Threading;

namespace U8Co
{
    // N 条 STA 线程共用一条队列。线程取第一个键全部空闲的任务（可运行任务之间先进先出），
    // 跑完在 finally 里放键。键被占用的任务留在队列里，照样受 75 秒排队期限约束；
    // 写路由剩下的时间不够 WorkItem.MinWriteStartMs 就不再开始（503 busy_timeout，没有执行）。
    internal sealed class StaPool
    {
        public const int Enqueued = 0;
        public const int QueueFull = 1;
        public const int Stopping = 2;
        readonly object _gate = new object();
        readonly List<WorkItem> _queue = new List<WorkItem>();
        readonly DocLocks _locks = new DocLocks();
        readonly string _name;
        readonly int _size;
        readonly int _cap;
        readonly WorkExec _exec;
        Thread[] _threads = new Thread[0];
        int _running;
        int _expect;
        bool _stop;

        public StaPool(string name, int size, int cap, WorkExec exec)
        {
            _name = name;
            _size = size;
            _cap = cap;
            _exec = exec;
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
                thread.Start(slot);
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
                item.Phase = WorkItem.Queued;
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
            int slot = (int)state;
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
                    WorkItem item = Take(out idle);
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
        WorkItem Take(out bool idle)
        {
            idle = false;
            lock (_gate)
            {
                while (true)
                {
                    WorkItem item = Pick();
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

        // 调用方持有 _gate。已过期或已放弃的任务不需要键，直接取出去记 expired，不占队列。
        WorkItem Pick()
        {
            for (int i = 0; i < _queue.Count; i++)
            {
                WorkItem item = _queue[i];
                bool dead = Dead(item);
                if (!dead && !_locks.Free(item.LockKeys))
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

        void Run(WorkItem item, int slot)
        {
            Interlocked.Increment(ref _running);
            try
            {
                WorkRun.Run(item, _exec, slot);
            }
            finally
            {
                Interlocked.Decrement(ref _running);
                Release(item);
            }
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
