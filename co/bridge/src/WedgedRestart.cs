using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace U8Co
{
    // 文件里的一条结束进程记录。Wedged 为真是「登录状态不是 0」自愈的重启，否则是看门狗卡死或崩溃。
    internal sealed class FailRecord
    {
        public long Ticks;
        public bool Wedged;
    }

    // 「登录状态不是 0」自愈第二步（重启进程）的外围：是否以服务运行、服务的失败动作（ScmPolicy：开头几条是重启、重置周期）、
    // 桥自己结束进程的时刻落盘（<root>\bridge-failfast.log，一行「UTC ticks 种类」，种类为 wedged / watchdog / crash）。
    // 进程重启后靠这份文件估计 SCM 的失败计数（FailChain）、限频和数 restarts_24h。只在以服务运行时记（控制台的退出 SCM 不计）。
    internal static class WedgedRestart
    {
        const string FileName = "bridge-failfast.log";
        const int KeepLines = 48;
        const int ScmConnect = 0x0001;
        const int ServiceQueryConfig = 0x0001;
        const int ConfigFailureActions = 2;
        const int ActionRestart = 1;
        public const string KindWedged = "wedged";
        public const string KindWatchdog = "watchdog";
        public const string KindCrash = "crash";

        // 以服务运行时的服务名（Program 在 ServiceBase.Run 之前设）；控制台、--check-config 为 null。
        public static volatile string ServiceName;
        // 命令行 --service-name（缺省 u8co），--check-config 用它查服务的失败动作。
        public static volatile string InstalledName = "u8co";

        [StructLayout(LayoutKind.Sequential)]
        struct FailureActions
        {
            public int ResetPeriod;
            public IntPtr RebootMsg;
            public IntPtr Command;
            public int Actions;
            public IntPtr ActionList;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct ScAction
        {
            public int Type;
            public int Delay;
        }

        [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode, EntryPoint = "OpenSCManagerW")]
        static extern IntPtr OpenSCManager(string machine, string database, int access);

        [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode, EntryPoint = "OpenServiceW")]
        static extern IntPtr OpenService(IntPtr scm, string name, int access);

        [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode, EntryPoint = "QueryServiceConfig2W")]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool QueryServiceConfig2(IntPtr service, int level, IntPtr buffer, int size, out int needed);

        [DllImport("advapi32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool CloseServiceHandle(IntPtr handle);

        // 服务 name 的失败动作：开头连续几条是重启、是否全部为重启、重置周期。没装、查不到返回 null。
        public static ScmPolicy FailureMode(string name)
        {
            if (string.IsNullOrEmpty(name))
            {
                return null;
            }
            IntPtr scm = IntPtr.Zero;
            IntPtr svc = IntPtr.Zero;
            try
            {
                scm = OpenSCManager(null, null, ScmConnect);
                svc = scm == IntPtr.Zero ? IntPtr.Zero : OpenService(scm, name, ServiceQueryConfig);
                return svc == IntPtr.Zero ? null : ReadActions(svc);
            }
            catch (Exception)
            {
                return null;
            }
            finally
            {
                if (svc != IntPtr.Zero)
                {
                    CloseServiceHandle(svc);
                }
                if (scm != IntPtr.Zero)
                {
                    CloseServiceHandle(scm);
                }
            }
        }

        static ScmPolicy ReadActions(IntPtr svc)
        {
            int needed;
            QueryServiceConfig2(svc, ConfigFailureActions, IntPtr.Zero, 0, out needed);
            if (needed <= 0)
            {
                return null;
            }
            IntPtr buf = Marshal.AllocHGlobal(needed);
            try
            {
                if (!QueryServiceConfig2(svc, ConfigFailureActions, buf, needed, out needed))
                {
                    return null;
                }
                FailureActions fa = (FailureActions)Marshal.PtrToStructure(buf, typeof(FailureActions));
                return Classify(ActionTypes(fa), fa.ResetPeriod);
            }
            finally
            {
                Marshal.FreeHGlobal(buf);
            }
        }

        // dwResetPeriod 是秒；INFINITE（按 int 读为 -1）和 0 都按「不重置」算（0 文档没写明，保守）。
        static long ResetTicks(int seconds)
        {
            return seconds <= 0 ? 0 : seconds * TimeSpan.TicksPerSecond;
        }

        // 各条失败动作的类型（SC_ACTION_TYPE），按顺序；ActionList 为空时为空数组。
        static int[] ActionTypes(FailureActions fa)
        {
            if (fa.Actions <= 0 || fa.ActionList == IntPtr.Zero)
            {
                return new int[0];
            }
            int size = Marshal.SizeOf(typeof(ScAction));
            int[] types = new int[fa.Actions];
            for (int i = 0; i < fa.Actions; i++)
            {
                IntPtr at = new IntPtr(fa.ActionList.ToInt64() + (long)i * size);
                types[i] = ((ScAction)Marshal.PtrToStructure(at, typeof(ScAction))).Type;
            }
            return types;
        }

        // 失败动作分类（纯函数，自检直接调）：开头连续几条是重启（SC_ACTION_RESTART = 1）；全部为重启（至少一条）时
        // SCM 重复最后一条重启，失败计数不再封顶（Unlimited）。resetSeconds 是 dwResetPeriod。
        internal static ScmPolicy Classify(int[] types, int resetSeconds)
        {
            int count = types == null ? 0 : types.Length;
            int leading = 0;
            while (leading < count && types[leading] == ActionRestart)
            {
                leading++;
            }
            return new ScmPolicy(leading, ResetTicks(resetSeconds), leading >= 1 && leading == count);
        }

        // 第二步的实际策略，null 表示不重启。控制台模式一律 null（不管配置，控制台进程从不因此退出）。
        // 服务：配置写 false、失败动作查不到或第一条不是重启都为 null；否则返回 scm，由 FailChain 再按计数判断。
        public static ScmPolicy Policy(bool asService, bool? configured, ScmPolicy scm)
        {
            if (!asService || (configured.HasValue && !configured.Value))
            {
                return null;
            }
            return scm != null && scm.Restarts >= 1 ? scm : null;
        }

        // 读文件里记着的结束进程记录（最多 KeepLines 条，不按时间裁：重置周期可能很长）。读不到返回空数组。
        public static FailRecord[] Load()
        {
            return Load(SafePath());
        }

        // 以服务运行时追加一条。只由 StopGuard 在结束进程的同一把锁里调用（看门狗、自愈重启、停止兜底经 StopGuard.Kill，
        // 终止进程的未处理异常经 StopGuard.NoteCrash）：只有 OnStop 返回前本桥自己报已停止成功时不调。写不进去照样结束。
        public static void RecordFailure(string kind)
        {
            if (ServiceName == null)
            {
                return;
            }
            Record(SafePath(), DateTime.UtcNow.Ticks, kind);
        }

        // 自检直接给临时目录里的路径。path 为 null 时什么都不做。
        internal static FailRecord[] Load(string path)
        {
            List<FailRecord> list = new List<FailRecord>();
            try
            {
                if (path == null || !File.Exists(path))
                {
                    return list.ToArray();
                }
                foreach (string line in File.ReadAllLines(path, Encoding.ASCII))
                {
                    FailRecord r = Parse(line);
                    if (r != null)
                    {
                        list.Add(r);
                    }
                }
            }
            catch (Exception)
            {
                // 读不到只是从本进程起算（偏宽，第二步仍受 RestartGapTicks 限频）。
            }
            return list.ToArray();
        }

        static FailRecord Parse(string line)
        {
            string[] parts = line.Trim().Split(' ');
            long t;
            if (parts.Length != 2 || !long.TryParse(parts[0], NumberStyles.None, CultureInfo.InvariantCulture, out t))
            {
                return null;
            }
            FailRecord r = new FailRecord();
            r.Ticks = t;
            r.Wedged = parts[1] == KindWedged;
            return r;
        }

        // 追加一条，只留最近 KeepLines 行。
        internal static void Record(string path, long ticks, string kind)
        {
            try
            {
                if (path == null)
                {
                    return;
                }
                List<string> lines = new List<string>();
                if (File.Exists(path))
                {
                    lines.AddRange(File.ReadAllLines(path, Encoding.ASCII));
                }
                lines.Add(ticks.ToString(CultureInfo.InvariantCulture) + " " + kind);
                if (lines.Count > KeepLines)
                {
                    lines.RemoveRange(0, lines.Count - KeepLines);
                }
                File.WriteAllLines(path, lines.ToArray(), Encoding.ASCII);
            }
            catch (Exception)
            {
            }
        }

        static string SafePath()
        {
            try
            {
                if (!Paths.RootWritable())
                {
                    return null;
                }
                return Paths.UnderRoot(Path.Combine(Paths.Root, FileName));
            }
            catch (Exception)
            {
                return null;
            }
        }

        // --check-config 的说明。
        public static string ScmLabel(ScmPolicy scm)
        {
            if (scm == null)
            {
                return "未安装或查不到（以服务运行时第二步不会执行）";
            }
            string reset = scm.ResetTicks <= 0 ? "不重置"
                : (scm.ResetTicks / TimeSpan.TicksPerSecond).ToString(CultureInfo.InvariantCulture) + " 秒无失败后重置";
            if (scm.Unlimited)
            {
                return "全部为重启，第二步不按失败次数封顶（仍间隔 30 分钟），失败计数" + reset;
            }
            string text = "开头 " + scm.Restarts.ToString(CultureInfo.InvariantCulture) + " 条为重启，失败计数" + reset;
            if (scm.Restarts < 2)
            {
                return text + "（第二步不会执行：要留一次重启给看门狗卡死）";
            }
            return text + "（第二步只在估计的失败链加上本次不超过 " + (scm.Restarts - 1).ToString(CultureInfo.InvariantCulture)
                + " 次时执行，留一次给看门狗卡死）";
        }
    }
}
