using System;
using System.Globalization;

namespace U8Co
{
    // --selftest 的操作员固定部分（OperatorPin、StaPool 取任务、StaWorker 分池与读线程转交、健康检查片段、读线程池不登录）：
    // 规范化与散列（写死 FNV-1a 期望值，防止换成进程内随机的 GetHashCode）、分散、不带操作员不固定、固定只在入队时算一次、
    // 固定线程之外的线程不代取活任务、过期任务任一线程可取、AuthCache 命中进读线程池（不固定）、读线程上条目失效时
    // 原样转交写线程池（按操作员固定、截止时刻不变、入队失败按 429 / 503）、排队计数不含过期任务。
    // 不起线程、不建 COM、不连库；AuthEntry 是手工造的（Dead 置真即失效），不碰 AuthCache 的全局表。
    internal static class OperatorPinSelfTest
    {
        const int Workers = 4;

        public static void Run()
        {
            CheckNorm();
            CheckSpread();
            CheckPick();
            CheckPinOnce();
            CheckExpired();
            CheckUnpinnedPool();
            CheckHealth();
            CheckReadPool();
            CheckEnqueue();
            CheckRoute();
        }

        // 大小写、首尾空白不同算同一操作员；期望值按 UTF-16 小端字节的 FNV-1a 算出。
        static void CheckNorm()
        {
            Expect("pin norm", OperatorPin.Norm(" op-020 ") == "OP-020" && OperatorPin.Norm(null) == "");
            Expect("pin index case", OperatorPin.Index("OP-020", Workers) == OperatorPin.Index(" op-020 ", Workers));
            Expect("pin index fnv", OperatorPin.Index("OP-020", 4) == 1 && OperatorPin.Index("OP-020", 3) == 2
                && OperatorPin.Index("op-004", 4) == 3);
            Expect("pin index none", OperatorPin.Index(null, Workers) == OperatorPin.None
                && OperatorPin.Index("  ", Workers) == OperatorPin.None
                && OperatorPin.Index("OP-020", 0) == OperatorPin.None);
            Expect("pin index single", OperatorPin.Index("OP-020", 1) == 0);
        }

        // 16 个操作员落到 4 条线程上，每条都有，且都在范围内。
        static void CheckSpread()
        {
            bool[] hit = new bool[Workers];
            for (int i = 0; i < 16; i++)
            {
                int pin = OperatorPin.Index("OP" + i.ToString("000", CultureInfo.InvariantCulture), Workers);
                Expect("pin spread range", pin >= 0 && pin < Workers);
                hit[pin] = true;
            }
            for (int i = 0; i < Workers; i++)
            {
                Expect("pin spread " + i, hit[i]);
            }
            WorkItem none = Item(null);
            for (int i = 0; i < Workers; i++)
            {
                Expect("pin none matches " + i, OperatorPin.Matches(none, i));
            }
        }

        // OP-020 固定在 1，op-004 固定在 3：线程 0 只取不带操作员的；别的线程空闲也不代取；同一操作员先进先出。
        static void CheckPick()
        {
            StaPool pool = Pool(true);
            WorkItem a1 = Enqueue(pool, "OP-020");
            WorkItem free = Enqueue(pool, null);
            WorkItem c = Enqueue(pool, "op-004");
            WorkItem a2 = Enqueue(pool, " op-020");
            Expect("pin pick free", object.ReferenceEquals(pool.PickFor(0), free));
            Expect("pin pick no steal 0", pool.PickFor(0) == null);
            Expect("pin pick no steal 2", pool.PickFor(2) == null);
            Expect("pin pick own 3", object.ReferenceEquals(pool.PickFor(3), c));
            Expect("pin pick fifo 1", object.ReferenceEquals(pool.PickFor(1), a1));
            Expect("pin pick fifo 2", object.ReferenceEquals(pool.PickFor(1), a2));
            Expect("pin pick empty", pool.PickFor(1) == null && pool.Queued == 0);
        }

        // 固定线程在入队时算好存在 WorkItem.Pin，之后操作员字段变了也不重算。
        static void CheckPinOnce()
        {
            StaPool pool = Pool(true);
            WorkItem a = Enqueue(pool, "OP-020");
            Expect("pin once stored", a.Pin == 1);
            a.Operator = "op-004";
            Expect("pin once not 3", pool.PickFor(3) == null);
            Expect("pin once 1", object.ReferenceEquals(pool.PickFor(1), a));
            WorkItem b = Enqueue(Pool(false), "OP-020");
            Expect("pin once unpinned pool", b.Pin == OperatorPin.None);
        }

        // 过了排队截止或已放弃的任务不等固定线程，任一线程取出去按 503 busy_timeout 结束。
        static void CheckExpired()
        {
            StaPool pool = Pool(true);
            WorkItem late = Item("OP-020");
            late.DeadlineUtcTicks = DateTime.UtcNow.AddSeconds(-1).Ticks;
            Expect("pin expired enqueue", pool.TryEnqueue(late) == StaPool.Enqueued);
            Expect("pin expired any", object.ReferenceEquals(pool.PickFor(0), late));
            WorkItem gone = Enqueue(pool, "OP-020");
            gone.Phase = WorkItem.Abandoned;
            Expect("pin abandoned any", object.ReferenceEquals(pool.PickFor(2), gone));
        }

        // 不固定的池（读线程池）照旧：任一线程取任一任务。
        static void CheckUnpinnedPool()
        {
            StaPool pool = Pool(false);
            WorkItem a = Enqueue(pool, "OP-020");
            Expect("pin unpinned any", object.ReferenceEquals(pool.PickFor(0), a));
        }

        static void CheckHealth()
        {
            StaPool pool = Pool(true);
            Enqueue(pool, "OP-020");
            Enqueue(pool, "op-020");
            Enqueue(pool, "op-004");
            Enqueue(pool, null);
            WorkItem late = Item("OP-020");
            late.DeadlineUtcTicks = DateTime.UtcNow.AddSeconds(-1).Ticks;
            pool.TryEnqueue(late);
            WorkItem gone = Enqueue(pool, "op-004");
            gone.Phase = WorkItem.Abandoned;
            int[] counts = pool.QueuedByWorker();
            Expect("pin queued", counts.Length == Workers && counts[0] == 0 && counts[1] == 2 && counts[2] == 0 && counts[3] == 1);
            Expect("pin health", StaWorker.PinHealth(pool) == ",\"pinning\":true,\"worker_queued\":[0,2,0,1]");
            Expect("pin health none", StaWorker.PinHealth(null) == ",\"pinning\":false,\"worker_queued\":[]");
        }

        // 读线程池收到没有有效 AuthCache 条目的任务直接报错，不走登录。
        static void CheckReadPool()
        {
            WorkItem item = Item("OP-020");
            bool refused = false;
            try
            {
                StaExec.Read(item);
            }
            catch (BridgeException ex)
            {
                refused = ex.Status == 500;
            }
            Expect("pin read pool no login", refused);
        }

        // AuthCache 命中：进读线程池、不固定、不带单据锁，任一读线程可取。未命中：进写线程池，按操作员固定。
        static void CheckEnqueue()
        {
            StaPool write = Pool(true);
            StaPool read = Pool(false);
            WorkItem hit = Item("OP-020");
            AuthEntry auth = Auth();
            Expect("pin hit enqueue", StaWorker.Enqueue(hit, auth, write, read) == StaPool.Enqueued);
            Expect("pin hit read pool", read.Queued == 1 && write.Queued == 0);
            Expect("pin hit unpinned", hit.Pin == OperatorPin.None && hit.LockKeys.Length == 0
                && object.ReferenceEquals(hit.Auth, auth));
            Expect("pin hit any reader", object.ReferenceEquals(read.PickFor(2), hit));
            WorkItem miss = Item("OP-020");
            Expect("pin miss enqueue", StaWorker.Enqueue(miss, null, write, read) == StaPool.Enqueued);
            Expect("pin miss write pool", write.Queued == 1 && read.Queued == 0 && miss.Pin == 1 && miss.Auth == null);
        }

        // 读线程执行前：条目可用就本线程跑；失效就把同一个任务转交写线程池（固定、截止不变），本线程不执行；
        // 写队列满按 429 busy、已停止按 503 stopping 结束；已过截止的留给 WorkRun 记 expired，不转交。
        static void CheckRoute()
        {
            StaPool write = Pool(true);
            StaPool read = Pool(false);
            WorkItem item = Item("OP-020");
            AuthEntry auth = Auth();
            StaWorker.Enqueue(item, auth, write, read);
            long deadline = item.DeadlineUtcTicks;
            Expect("pin route picked", object.ReferenceEquals(read.PickFor(0), item));
            auth.Dead = true;
            Expect("pin route moved", StaWorker.Route(item, write) == null);
            Expect("pin route pinned", write.Queued == 1 && item.Pin == 1 && item.Auth == null
                && item.Phase == WorkItem.Queued);
            Expect("pin route deadline", item.DeadlineUtcTicks == deadline);
            Expect("pin route not 0", write.PickFor(0) == null);
            Expect("pin route own", object.ReferenceEquals(write.PickFor(1), item));

            WorkItem full = Item("OP-020");
            full.Auth = Auth();
            full.Auth.Dead = true;
            full.DeadlineUtcTicks = DateTime.UtcNow.AddSeconds(60).Ticks;
            Expect("pin route full", Refused(StaWorker.Route(full, Pool(true, 0)), full) == 429);
            Expect("pin route stopping", Refused(StaWorker.Route(full, null), full) == 503);

            WorkItem late = Item("OP-020");
            late.Auth = Auth();
            late.Auth.Dead = true;
            late.DeadlineUtcTicks = DateTime.UtcNow.AddSeconds(-1).Ticks;
            StaPool other = Pool(true);
            Expect("pin route late stays", IsRead(StaWorker.Route(late, other)) && other.Queued == 0);

            // HTTP 线程已放弃（未到截止）：不转交、不改回排队状态，留给 WorkRun 记 expired。
            WorkItem gone = Item("OP-020");
            gone.Auth = Auth();
            gone.Auth.Dead = true;
            gone.DeadlineUtcTicks = DateTime.UtcNow.AddSeconds(60).Ticks;
            gone.Phase = WorkItem.Abandoned;
            Expect("pin route abandoned stays", IsRead(StaWorker.Route(gone, other)) && other.Queued == 0
                && gone.Phase == WorkItem.Abandoned);
            Expect("pin handoff gone", other.TryEnqueueHandoff(gone) == StaPool.Gone && other.Queued == 0
                && gone.Phase == WorkItem.Abandoned);
            CheckRouteLive();
        }

        // 条目仍有效：本读线程跑 SQL（StaExec.Read），写队列不动。临时打开 AuthCache（Live 要求有效期大于 0），用完按原状关回。
        static void CheckRouteLive()
        {
            bool was = AuthCache.Enabled;
            AuthCache.Configure(600);
            try
            {
                StaPool write = Pool(true);
                WorkItem item = Item("OP-020");
                AuthEntry auth = Auth();
                item.Auth = auth;
                item.DeadlineUtcTicks = DateTime.UtcNow.AddSeconds(60).Ticks;
                Expect("pin route live read", IsRead(StaWorker.Route(item, write)));
                Expect("pin route live stays", write.Queued == 0 && object.ReferenceEquals(item.Auth, auth)
                    && item.Phase == WorkItem.Queued);
            }
            finally
            {
                if (!was)
                {
                    AuthCache.Configure(0);
                }
            }
        }

        static bool IsRead(WorkExec exec)
        {
            return exec != null && exec.Equals(new WorkExec(StaExec.Read));
        }

        // 执行转交失败时返回的执行体，取它抛出的状态码；没抛出返回 0。
        static int Refused(WorkExec exec, WorkItem item)
        {
            Expect("pin route refuse exec", exec != null);
            try
            {
                exec(item);
            }
            catch (BridgeException ex)
            {
                return ex.Status;
            }
            return 0;
        }

        static AuthEntry Auth()
        {
            AuthEntry auth = new AuthEntry();
            auth.ConnString = "selftest";
            auth.ExpiresTicks = DateTime.UtcNow.AddMinutes(10).Ticks;
            return auth;
        }

        static StaPool Pool(bool pinned)
        {
            return Pool(pinned, 32);
        }

        static StaPool Pool(bool pinned, int cap)
        {
            return new StaPool("u8co-selftest", Workers, cap, null, pinned, null);
        }

        static WorkItem Enqueue(StaPool pool, string op)
        {
            WorkItem item = Item(op);
            Expect("pin enqueue", pool.TryEnqueue(item) == StaPool.Enqueued);
            return item;
        }

        static WorkItem Item(string op)
        {
            WorkItem item = new WorkItem();
            item.Operator = op;
            item.LockKeys = new string[0];
            return item;
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException(name);
            }
        }
    }
}
