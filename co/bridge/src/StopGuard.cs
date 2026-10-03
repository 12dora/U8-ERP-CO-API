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
    internal static class StopGuard
    {
        // 低于 OnStop 申请的 120 秒附加时间。
        public const int LimitMs = 90000;
        const int OwnProcess = 0x10;
        const int StateStopped = 1;
        static ServiceBase _service;
        static int _armed;
        static int _inStop;

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
            _service = service;
            Interlocked.Exchange(ref _inStop, 1);
            if (Interlocked.Exchange(ref _armed, 1) != 0)
            {
                return;
            }
            Thread thread = new Thread(new ParameterizedThreadStart(Watch));
            thread.IsBackground = true;
            thread.Name = "u8co-stop-watch";
            thread.Start(limitMs);
        }

        // OnStop 正常返回后调用：之后由 ServiceBase 报 SERVICE_STOPPED，兜底时不再重复上报。
        public static void Returned()
        {
            Interlocked.Exchange(ref _inStop, 0);
        }

        static void Watch(object state)
        {
            int ms = (int)state;
            Thread.Sleep(ms);
            Kill("stop 停止超时：" + (ms / 1000) + " 秒内进程未退出，强制结束", "u8co: stop timeout");
        }

        // 在 unhandled.log 记一行 note；OnStop 还没返回时先报已停止，再结束进程。
        // 不在停止过程中（看门狗平时触发）或控制台模式不报，直接结束。
        public static void Kill(string note, string reason)
        {
            AppHost.NoteDiag(note);
            if (Interlocked.CompareExchange(ref _inStop, 0, 0) == 1)
            {
                ReportStopped();
            }
            Environment.FailFast(reason);
        }

        static void ReportStopped()
        {
            U8CoService service = _service as U8CoService;
            if (service == null)
            {
                return;
            }
            try
            {
                ServiceStatus status = new ServiceStatus();
                status.ServiceType = OwnProcess;
                status.CurrentState = StateStopped;
                SetServiceStatus(service.StatusHandle, ref status);
            }
            catch (Exception)
            {
            }
        }
    }
}
