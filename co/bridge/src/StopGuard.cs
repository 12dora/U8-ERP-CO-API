using System;
using System.Runtime.InteropServices;
using System.ServiceProcess;
using System.Threading;

namespace U8Co
{
    // 服务停止的兜底。SCM 在 OnStop 返回时就报「已停止」，但卡在 STA 上的 COM 调用或 RCW 终结器可能让进程一直不退、
    // exe 一直被锁。进入 OnStop 时起一条后台线程计时，到时限仍未退出就记一行 unhandled.log 后 FailFast；
    // OnStop 返回后照样计时，进程退出时线程随之结束。
    // OnStop 还没返回时先向 SCM 报 SERVICE_STOPPED 再结束进程，SCM 把这次当作正常停止，不按恢复策略重新拉起服务。
    // 桥自己结束进程（看门狗、自愈重启、停止兜底、终止进程的未处理异常）只有一条路径：在同一把锁里决定记不记失败链
    // （WedgedRestart.RecordFailure）、报不报已停止（StopExit），然后在锁里 FailFast；Arm / Returned 也拿这把锁。
    // 规则（宁可多记，不可少记）：只有本桥这次自己向 SCM 报已停止成功（OnStop 还没返回时）才不记，其余一律记。
    // 少记会让自愈用掉留给看门狗的那次重启、之后服务没人拉起；多记只让第二步在重置周期内偏保守。
    internal static class StopGuard
    {
        // 低于 OnStop 申请的 120 秒附加时间。
        public const int LimitMs = 90000;
        const int OwnProcess = 0x10;
        const int StateStopped = 1;
        static readonly object Gate = new object();
        static readonly StopExit State = new StopExit();
        static ServiceBase _service;
        static int _armed;

        [StructLayout(LayoutKind.Sequential)]
        struct ServiceStatus
        {
            public int ServiceType;
            public int CurrentState;
            public int ControlsAccepted;
            public int Win32ExitCode;
            public int ServiceSpecificExitCode;
            public int CheckPoint;
            public int WaitHint;
        }

        [DllImport("advapi32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool SetServiceStatus(IntPtr handle, ref ServiceStatus status);

        // 服务的 OnStop 入口调用，只起一次计时线程。
        public static void Arm(ServiceBase service, int limitMs)
        {
            lock (Gate)
            {
                _service = service;
                State.Arm();
            }
            if (Interlocked.Exchange(ref _armed, 1) != 0)
            {
                return;
            }
            Thread thread = new Thread(new ParameterizedThreadStart(Watch));
            thread.IsBackground = true;
            thread.Name = "u8co-stop-watch";
            thread.Start(limitMs);
        }

        // OnStop 正常返回后调用：之后由 ServiceBase 报 SERVICE_STOPPED，兜底时不再重复上报。ServiceBase 真正报出之前
        // 我们无法确认，所以这之后结束进程照记（偏保守）。
        public static void Returned()
        {
            lock (Gate)
            {
                State.Returned();
            }
        }

        static void Watch(object state)
        {
            int ms = (int)state;
            Thread.Sleep(ms);
            Kill("stop 停止超时：" + (ms / 1000) + " 秒内进程未退出，强制结束", "u8co: stop timeout", WedgedRestart.KindWatchdog);
        }

        // 在 unhandled.log 记一行 note，再走 Exit 结束进程。
        public static void Kill(string note, string reason, string kind)
        {
            AppHost.NoteDiag(note);
            Exit(reason, kind);
        }

        // 终止进程的未处理异常：AppHost 先写完诊断再调，这里按同一条规则记账并结束进程（不等运行时自己结束）。
        public static void NoteCrash()
        {
            Exit("u8co: unhandled exception", WedgedRestart.KindCrash);
        }

        // 唯一的结束路径：锁里决定（OnStop 还没返回时报已停止，成功则不记；其余先记 kind），然后在锁里 FailFast，
        // 记账和结束进程不可分割，决定之后 Arm / Returned / 另一次结束都插不进来。
        static void Exit(string reason, string kind)
        {
            lock (Gate)
            {
                if (State.Exit(ReportStopped))
                {
                    WedgedRestart.RecordFailure(kind);
                }
                Environment.FailFast(reason);
            }
        }

        static bool ReportStopped()
        {
            U8CoService service = _service as U8CoService;
            if (service == null)
            {
                return false;
            }
            try
            {
                ServiceStatus status = new ServiceStatus();
                status.ServiceType = OwnProcess;
                status.CurrentState = StateStopped;
                return SetServiceStatus(service.StatusHandle, ref status);
            }
            catch (Exception)
            {
                return false;
            }
        }
    }

    // 桥结束进程时的决定（StopGuard 在锁里用，自检直接建实例按顺序驱动）。
    // InStop：OnStop 已进入、还没返回；OwnReported：本桥自己向 SCM 报已停止成功过（之后在锁里立即 FailFast）。
    // ServiceBase 在 OnStop 返回后自己报的已停止我们确认不了，不算。
    internal sealed class StopExit
    {
        public const int Record = 0;
        public const int ReportStopped = 1;
        public const int Skip = 2;
        public bool InStop;
        public bool OwnReported;

        public void Arm()
        {
            InStop = true;
        }

        public void Returned()
        {
            InStop = false;
        }

        // 纯决定：自己报已停止成功过 → Skip；在 OnStop 里 → ReportStopped（报成功才不记）；其余 → Record（偏保守）。
        public static int Decide(bool inStop, bool ownReported)
        {
            if (ownReported)
            {
                return Skip;
            }
            return inStop ? ReportStopped : Record;
        }

        // 按 Decide 执行；report 向 SCM 报已停止，成功返回 true。返回是否要记入失败链（报已停止失败时照记）。
        public bool Exit(Func<bool> report)
        {
            int action = Decide(InStop, OwnReported);
            if (action == ReportStopped && report != null && report())
            {
                OwnReported = true;
                return false;
            }
            return action != Skip;
        }
    }
}
