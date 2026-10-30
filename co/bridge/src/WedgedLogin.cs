using System;
using System.Collections.Generic;

namespace U8Co
{
    // 换线程后新线程先做的一次登录：失败的那次登录的参数副本（含口令，只在内存里，用完清掉）。
    internal sealed class WedgedProbe
    {
        public BridgeConfig Config;
        public U8Open Ask;
        public string Operator;
        public int Worker;
        public int Strikes;
        // 新线程等旧线程退出（Join 5 秒）后旧线程是否仍活着，写进 login_probe。
        public bool PreviousAlive;
    }

    // 「登录状态不是 0」自愈（wedgedLoginRecycle、wedgedLoginRestart）。U8 的登录组件是 VB6 套间线程 DLL，全局状态按 STA 线程各一份；
    // 实测出现过（推测是这份状态卡住，换线程能否恢复待实际环境确认，确认不了由第二步兜底）：本进程没有该操作员的登录、UFSystem 也没有任务行，固定线程上该操作员的登录却一直回「登录状态不是 0」，
    // 新进程立即能登。判定和动作：
    // - 写线程（BindWorker）上的一次登录最终（重试用完）回 422「登录状态不是 0」，而本进程此刻没有该操作员的任何登录
    //   （在用、复用缓存都算，HeldCount）、也没有别的正在进行的登录（OpeningCount 只有本次）时，记一次「卡住」。
    //   本进程自己持有登录时的冲突不算（嵌套登录）。别的程序或机器以同一操作员登录时从里面分不出来，所以动作有上限。
    // - 连续 wedgedLoginRecycle 次（相邻不超过 10 分钟）：第一步，请求换掉本线程（TakeRecycle → StaPool.Recycle），
    //   本次请求照常 422；新线程先用这次的参数登录一次（Probe）。
    // - 换线程后 30 分钟内再记一次：第二步，请求重启进程（Watchdog.RequestRestart：Halt → Settle → FailFast，由服务失败动作拉起），
    //   只在 WedgedRestart.Policy 非空时（以服务运行且第一条失败动作为重启，控制台从不重启）；距桥上一次结束进程不到 30 分钟限频，
    //   按 FailChain 会用掉服务失败动作里留给看门狗卡死的那次重启时不重启，照常 422，健康检查 active 保持 true。
    // - 该操作员任何一次登录成功清零。
    // 审计事件：login_wedged、worker_recycled、login_probe（ok / failed:错误码）、restart_requested。健康检查 wedged_logins 不含操作员。
    // 自检用的内部接口：Now（时钟）、RestartPolicy（代替以服务运行 + 查 SCM 的策略）、RestartHook（代替 Watchdog）、Reset。
    internal static class WedgedLogin
    {
        static readonly object Gate = new object();
        static readonly Dictionary<string, int> Held = new Dictionary<string, int>(StringComparer.Ordinal);
        static readonly Dictionary<string, int> Opening = new Dictionary<string, int>(StringComparer.Ordinal);
        static volatile WedgedState _state = new WedgedState(2);
        static volatile object _restartCfg;
        // SCM 失败动作的缓存（每分钟最多查一次），只在升级时用。
        static readonly object CacheGate = new object();
        static ScmPolicy _scm;
        static long _scmAt;
        // 写线程序号加 1（0 表示不是固定写线程，不判定）。
        [ThreadStatic]
        static int _worker;
        [ThreadStatic]
        static WedgedProbe _recycle;

        internal static Func<DateTime> Now;
        internal static Func<ScmPolicy> RestartPolicy;
        internal static Action<long> RestartHook;

        // StaWorker.Start 调用一次：阈值、重启开关，并读回文件里的重启时刻。
        public static void Configure(BridgeConfig cfg)
        {
            WedgedState state = new WedgedState(cfg.WedgedLoginRecycle);
            state.SeedFailures(WedgedRestart.Load());
            _restartCfg = cfg.WedgedLoginRestart;
            _state = state;
        }

        // 自检用：换一份新的状态（不读文件）。
        internal static void Reset(int threshold)
        {
            _state = new WedgedState(threshold);
            lock (Gate)
            {
                Held.Clear();
                Opening.Clear();
            }
            _recycle = null;
        }

        internal static WedgedState State
        {
            get { return _state; }
        }

        // 固定写线程启动时调用（StaPool.Loop）；index 小于 0 解除（自检用）。
        public static void BindWorker(int index)
        {
            _worker = index < 0 ? 0 : index + 1;
        }

        // U8Session.Open 进出时调用：本进程正在进行的该操作员登录数。
        public static void Begin(string op)
        {
            Bump(Opening, op, 1);
        }

        public static void End(string op)
        {
            Bump(Opening, op, -1);
        }

        // 登录成功（U8Session.Open 返回前）：在用登录数加一，清零卡住计数。
        public static void Opened(string op)
        {
            Bump(Held, op, 1);
            _state.Ok(op);
        }

        // U8Session.Dispose：在用登录数减一（复用缓存里的登录没 Dispose 之前一直算在用）。
        public static void Closed(string op)
        {
            Bump(Held, op, -1);
        }

        internal static int HeldCount(string op)
        {
            return Read(Held, op);
        }

        internal static int OpeningCount(string op)
        {
            return Read(Opening, op);
        }

        // U8Session.Open 失败时调用（Begin 之后、End 之前）。只看最终的 422「登录状态不是 0」。不抛异常。
        public static void Failed(BridgeConfig cfg, U8Open ask, BridgeException ex)
        {
            try
            {
                string op = ask == null ? "" : OperatorPin.Norm(ask.User);
                int worker = _worker - 1;
                if (worker < 0 || op.Length == 0 || !IsStateError(ex) || OthersHold(op))
                {
                    return;
                }
                long now = Clock();
                int strikes;
                int action = _state.Strike(op, now, PolicyNow, out strikes);
                if (action == WedgedAction.None)
                {
                    return;
                }
                WedgedProbe at = new WedgedProbe();
                at.Config = cfg;
                at.Operator = op;
                at.Worker = worker;
                at.Strikes = strikes;
                Audit("login_wedged", at, WedgedAction.Label(action));
                Act(action, at, ask, now);
            }
            catch (Exception)
            {
                // 自愈只是兜底，不能盖住原来的登录错误。
            }
        }

        static void Act(int action, WedgedProbe at, U8Open ask, long now)
        {
            if (action == WedgedAction.Recycle)
            {
                at.Ask = Copy(ask);
                _recycle = at;
                return;
            }
            if (action != WedgedAction.Restart)
            {
                return;
            }
            Audit("restart_requested", at, "login_wedged");
            Action<long> hook = RestartHook;
            if (hook != null)
            {
                hook(now);
                return;
            }
            // 落盘（种类 wedged）在 Watchdog.Loop 结束进程前做，与看门狗卡死同一处。
            Watchdog.RequestRestart();
        }

        static bool IsStateError(BridgeException ex)
        {
            return ex != null && ex.Code == "login_failed" && LicenseRetry.Transient(ex);
        }

        // 本进程还持有该操作员的登录，或除本次之外还有正在进行的登录：是自己的冲突，不算卡住。
        static bool OthersHold(string op)
        {
            return Read(Held, op) > 0 || Read(Opening, op) > 1;
        }

        // 第二步的策略，只在升级时调用（WedgedState.Escalate）。SCM 每分钟最多查一次。
        static ScmPolicy PolicyNow()
        {
            Func<ScmPolicy> seam = RestartPolicy;
            if (seam != null)
            {
                return seam();
            }
            string service = WedgedRestart.ServiceName;
            if (service == null)
            {
                return null;
            }
            return WedgedRestart.Policy(true, _restartCfg as bool?, CachedScm(service));
        }

        static ScmPolicy CachedScm(string service)
        {
            long now = DateTime.UtcNow.Ticks;
            lock (CacheGate)
            {
                if (_scmAt == 0 || now - _scmAt > 60L * TimeSpan.TicksPerSecond)
                {
                    _scm = WedgedRestart.FailureMode(service);
                    _scmAt = now;
                }
                return _scm;
            }
        }

        // StaPool.Loop 每跑完一个任务调用：本线程是否被请求换掉（取出即清）。
        public static WedgedProbe TakeRecycle()
        {
            WedgedProbe probe = _recycle;
            _recycle = null;
            return probe;
        }

        // StaPool 换线程时调用：新线程已启动、已换进线程数组之后，不持队列锁。
        public static void Recycled(WedgedProbe probe)
        {
            Audit("worker_recycled", probe, "");
        }

        // 新线程上、取任务之前：用失败那次的参数再登录一次，成功即关（结果经 U8Session.Open 进同一套判定，失败可能升级到第二步）。
        public static void Probe(WedgedProbe probe, int slot)
        {
            if (probe == null || probe.Ask == null)
            {
                return;
            }
            Watchdog.Enter(slot);
            string result = "ok";
            try
            {
                using (U8Session.Open(probe.Config, probe.Ask))
                {
                }
            }
            catch (Exception ex)
            {
                // 判定已经在 U8Session.Open 里做过；这里只记结果（错误码，不带口令）。
                BridgeException bridge = ex as BridgeException;
                result = "failed:" + (bridge == null ? "internal" : bridge.Code);
            }
            finally
            {
                Watchdog.Leave(slot);
                probe.Ask.Password = null;
                probe.Ask = null;
                // 刚换过线程：探测不会再请求换线程（30 分钟内只会升级），万一有也丢掉。
                _recycle = null;
            }
            // 恢复在审计里表现为 worker_recycled → login_probe（action=ok）。
            Dictionary<string, object> evt = Fields(probe, result);
            evt["previous_alive"] = probe.PreviousAlive;
            AuditEvent.Write("login_probe", evt);
        }

        public static string HealthFragment()
        {
            return _state.HealthFragment(Clock());
        }

        static U8Open Copy(U8Open ask)
        {
            U8Open copy = new U8Open();
            copy.SubId = ask.SubId;
            copy.Acc = ask.Acc;
            copy.Year = ask.Year;
            copy.User = ask.User;
            copy.Password = ask.Password;
            copy.Date = ask.Date;
            // 探测只登录不写：不受 holdWritesWhen 拦。
            copy.Write = false;
            return copy;
        }

        // 池正在停止：不探测，清掉口令副本。
        public static void Discard(WedgedProbe probe)
        {
            if (probe != null && probe.Ask != null)
            {
                probe.Ask.Password = null;
                probe.Ask = null;
            }
        }

        static void Audit(string name, WedgedProbe at, string action)
        {
            AuditEvent.Write(name, Fields(at, action));
        }

        static Dictionary<string, object> Fields(WedgedProbe at, string action)
        {
            Dictionary<string, object> evt = new Dictionary<string, object>();
            evt["operator"] = at.Operator;
            evt["worker"] = at.Worker;
            evt["strikes"] = at.Strikes;
            if (action.Length > 0)
            {
                evt["action"] = action;
            }
            return evt;
        }

        static void Bump(Dictionary<string, int> map, string op, int delta)
        {
            if (string.IsNullOrEmpty(op))
            {
                return;
            }
            lock (Gate)
            {
                int n;
                map.TryGetValue(op, out n);
                n += delta;
                if (n <= 0)
                {
                    map.Remove(op);
                }
                else
                {
                    map[op] = n;
                }
            }
        }

        static int Read(Dictionary<string, int> map, string op)
        {
            lock (Gate)
            {
                int n;
                return op != null && map.TryGetValue(op, out n) ? n : 0;
            }
        }

        static long Clock()
        {
            Func<DateTime> now = Now;
            return now != null ? now().Ticks : DateTime.UtcNow.Ticks;
        }
    }
}
