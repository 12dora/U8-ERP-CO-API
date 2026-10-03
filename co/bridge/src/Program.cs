using System;
using System.ServiceProcess;

namespace U8Co
{
    internal static class Program
    {
        const string Usage =
            "用法: u8co-bridge.exe [--console|--selftest|--check-config|--check-signatures [--strict]] [--root <运行目录>] [--service-name <服务名>] [--u8-home <U8 安装目录>]";

        static int Main(string[] args)
        {
            // 任何 U8 COM 之前装上。友空间程序集在线程池上解析失败会把进程打死。
            AppDomain.CurrentDomain.AssemblyResolve += AppHost.BlockMobile;
            AppDomain.CurrentDomain.UnhandledException += AppHost.NoteCrash;
            BridgeArgs opts = BridgeArgs.Parse(args);
            if (opts == null)
            {
                Console.Error.WriteLine(Usage);
                return 1;
            }
            if (opts.U8Home != null && !ApplyU8Home(opts.U8Home))
            {
                return 1;
            }
            if (opts.Mode == "--selftest")
            {
                Environment.Exit(SelfTest.Run());
            }
            // COM 签名自检：只读注册表和类型库，不需要运行目录和配置
            if (opts.Mode == "--check-signatures")
            {
                return SigCheck.RunCli(opts.Strict);
            }
            if (!ApplyRoot(opts.Root))
            {
                return 1;
            }
            if (opts.Mode == "--check-config")
            {
                return AppHost.CheckConfig();
            }
            if (opts.Mode == "--console")
            {
                AppHost.RunConsole();
                return 0;
            }
            ServiceBase.Run(new U8CoService(opts.ServiceName));
            return 0;
        }

        // --u8-home 只给 --selftest、--check-signatures 用（服务和 --check-config 用 config.json 的 u8Home）。
        static bool ApplyU8Home(string dir)
        {
            try
            {
                Paths.SetU8Home(dir);
                return true;
            }
            catch (InvalidOperationException ex)
            {
                Console.Error.WriteLine(ex.Message);
                return false;
            }
        }

        // 不给 --root 时用缺省运行目录（%ProgramData%\U8Co\u8co）。
        static bool ApplyRoot(string root)
        {
            if (root == null)
            {
                return true;
            }
            try
            {
                Paths.SetRoot(root);
                return true;
            }
            catch (InvalidOperationException ex)
            {
                Console.Error.WriteLine(ex.Message);
                return false;
            }
        }
    }

    // 命令行：至多一个模式开关，外加可选的 --root、--service-name。服务的 binPath 里带后两个。
    internal sealed class BridgeArgs
    {
        public string Mode = "";
        public string Root;
        public string ServiceName = "u8co";
        public string U8Home;
        // --check-signatures 的 --strict：有不匹配时退出码 2
        public bool Strict;

        public static BridgeArgs Parse(string[] args)
        {
            BridgeArgs opts = new BridgeArgs();
            int i = 0;
            while (args != null && i < args.Length)
            {
                int used = opts.Take(args, i);
                if (used == 0)
                {
                    return null;
                }
                i += used;
            }
            return opts;
        }

        // 返回用掉的参数个数；0 表示无法识别。
        int Take(string[] args, int i)
        {
            string flag = args[i].ToLowerInvariant();
            if (flag == "--console" || flag == "--selftest" || flag == "--check-config" || flag == "--check-signatures")
            {
                if (Mode.Length > 0)
                {
                    return 0;
                }
                Mode = flag;
                return 1;
            }
            if (flag == "--strict")
            {
                Strict = true;
                return 1;
            }
            if (i + 1 >= args.Length)
            {
                return 0;
            }
            return TakeValue(flag, args[i + 1]);
        }

        int TakeValue(string flag, string value)
        {
            if (flag == "--root" && Root == null)
            {
                Root = value;
                return 2;
            }
            if (flag == "--u8-home" && U8Home == null)
            {
                U8Home = value;
                return 2;
            }
            if (flag == "--service-name" && ServiceNameOk(value))
            {
                ServiceName = value;
                return 2;
            }
            return 0;
        }

        // 与 install.ps1 的 -ServiceName 规则相同：字母或数字开头，只含字母、数字、点、下划线、连字符，最长 64。
        static bool ServiceNameOk(string name)
        {
            if (name == null || name.Length == 0 || name.Length > 64 || !char.IsLetterOrDigit(name[0]))
            {
                return false;
            }
            for (int i = 0; i < name.Length; i++)
            {
                if (!NameChar(name[i]))
                {
                    return false;
                }
            }
            return true;
        }

        static bool NameChar(char c)
        {
            if (c >= 128)
            {
                return false;
            }
            return char.IsLetterOrDigit(c) || c == '.' || c == '_' || c == '-';
        }
    }

    internal sealed class U8CoService : ServiceBase
    {
        public U8CoService(string name)
        {
            ServiceName = name;
            CanStop = true;
            AutoLog = true;
        }

        // ServiceHandle 是受保护成员；StopGuard 强制结束前向 SCM 报告已停止要用它。
        internal IntPtr StatusHandle
        {
            get { return ServiceHandle; }
        }

        protected override void OnStart(string[] args)
        {
            AppHost.StartOrExplain();
        }

        // 进入停止先挂上 90 秒兜底（StopGuard），再申请 120 秒附加时间：无论卡在哪一步，进程都不会停在「SCM 已停止、exe 仍被锁」。
        protected override void OnStop()
        {
            StopGuard.Arm(this, StopGuard.LimitMs);
            try
            {
                RequestAdditionalTime(120000);
            }
            catch (Exception)
            {
            }
            AppHost.Stop();
            StopGuard.Returned();
        }
    }
}
