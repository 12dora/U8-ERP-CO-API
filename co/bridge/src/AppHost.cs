using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Xml;

namespace U8Co
{
    internal static class AppInfo
    {
        public const string Version = "1";
        public const string HealthJson = "{\"ok\":true,\"version\":\"1\"}";
        public const string SickJson = "{\"ok\":false,\"code\":\"unhealthy\"}";
    }

    internal static class AppHost
    {
        static HttpServer _http;
        static ManualResetEvent _consoleStop;
        static string _prefix;
        // 按线程防重入：多条工作线程可能同时解析程序集。
        [ThreadStatic]
        static int _resolve;
        static volatile bool _workflowReady;
        static readonly HashSet<string> _missed =
            new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        static volatile string _workflowProblem;

        public static void StartOrExplain()
        {
            try
            {
                Start();
            }
            catch (Exception ex)
            {
                string safe = Scrub(ex.Message);
                WriteStartup(safe);
                throw new InvalidOperationException(safe);
            }
        }

        public static void RunConsole()
        {
            StartOrExplain();
            Console.WriteLine("控制台模式。按 Ctrl+C 退出。正在监听 " + _prefix);
            _consoleStop = new ManualResetEvent(false);
            Console.CancelKeyPress += OnCancel;
            _consoleStop.WaitOne();
            Stop();
        }

        static void OnCancel(object sender, ConsoleCancelEventArgs e)
        {
            e.Cancel = true;
            _consoleStop.Set();
        }

        static void Start()
        {
            // U8 的 COM 是 32 位进程内服务。64 位进程 CreateObject 找不到类。
            if (IntPtr.Size != 4)
            {
                throw new InvalidOperationException("必须使用 32 位进程");
            }
            BridgeConfig cfg = BridgeConfig.Load();
            Paths.SetU8Home(cfg.U8Home);
            // 移动审批推送开关：之后才放行 U8.MA 程序集
            U8Resolve.SetMobilePush(cfg.MobilePush);
            // 终审后清孤儿任务行的开关
            TaskOrphans.Configure(cfg.CleanOrphanTasks);
            // 空白模板缓存开关
            TplCache.Configure(cfg.TemplateCache);
            Directory.CreateDirectory(cfg.AuditLog);
            // 签名自检与程序集加载的事件行写进审计日志
            AuditEvent.SetDir(cfg.AuditLog);
            // 写入策略：配置了 writePolicyFile 才加载并起后台重载线程
            WritePolicy.Start(cfg);
            // 只读账套名单（健康检查、meta 显示用；拦截按请求的配置判断）
            ReadOnlyGate.Configure(cfg);
            // 第二级写入总开关（健康检查、meta 显示用；拦截按请求的配置判断）
            TestAccountGate.Configure(cfg);
            NoteWarnings(cfg);
            CheckWorkflowConfig();
            WidenThreadPool(cfg);
            StaWorker.Start(cfg);
            IdemStore.Start();
            Watchdog.Start();
            // 许可点数采样（licenseSampleMinutes 为 0 时只记登录时见到的饱和）
            LicenseSampler.Start(cfg);
            _prefix = cfg.ListenPrefix;
            _http = new HttpServer(cfg);
            _http.Start();
            // COM 签名自检：后台跑一次，不阻塞启动
            SigCheck.StartBackground();
            ClearStartupError();
        }

        // 每个 HTTP 请求在线程池线程上最多等 75 秒。线程池按需慢慢加线程，突发时会先排在线程池里。
        static void WidenThreadPool(BridgeConfig cfg)
        {
            int want = 2 * cfg.QueueCap + cfg.StaWorkers + cfg.ReadWorkers + 8;
            int worker;
            int io;
            ThreadPool.GetMinThreads(out worker, out io);
            ThreadPool.SetMinThreads(Math.Max(worker, want), Math.Max(io, want));
        }

        public static bool WorkflowReady
        {
            get { return _workflowReady; }
        }

        public static string WorkflowProblem
        {
            get
            {
                string text = _workflowProblem;
                if (text != null && text.Length > 0)
                {
                    return text;
                }
                return "缺少 u8co-bridge.exe.config";
            }
        }

        // 没有 legacyUnhandledExceptionPolicy 时，审批引擎的线程池异常会打死进程。单据读写仍可启动。
        static void CheckWorkflowConfig()
        {
            string problem = WorkflowConfigProblem();
            _workflowProblem = problem;
            _workflowReady = problem == null;
            if (problem != null)
            {
                NoteDiag(problem);
            }
        }

        static string WorkflowConfigProblem()
        {
            string path = ConfigPath();
            if (path.Length == 0 || !File.Exists(path))
            {
                return "缺少 u8co-bridge.exe.config";
            }
            try
            {
                return PolicyProblem(path);
            }
            catch (Exception)
            {
                return "u8co-bridge.exe.config 无法读取";
            }
        }

        static string PolicyProblem(string path)
        {
            XmlDocument doc = new XmlDocument();
            doc.Load(path);
            XmlNode node = doc.SelectSingleNode("/configuration/runtime/legacyUnhandledExceptionPolicy");
            XmlElement el = node as XmlElement;
            if (el == null || !PolicyOn(el.GetAttribute("enabled")))
            {
                return "u8co-bridge.exe.config 未启用 legacyUnhandledExceptionPolicy";
            }
            return null;
        }

        static bool PolicyOn(string value)
        {
            if (value == null)
            {
                return false;
            }
            string text = value.Trim();
            if (string.Equals(text, "1", StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }
            return string.Equals(text, "true", StringComparison.OrdinalIgnoreCase);
        }

        static string ConfigPath()
        {
            try
            {
                string path = AppDomain.CurrentDomain.SetupInformation.ConfigurationFile;
                return path ?? "";
            }
            catch (Exception)
            {
                return "";
            }
        }

        public static void NoteDiag(string message)
        {
            try
            {
                if (!Paths.RootWritable())
                {
                    return;
                }
                string path = Paths.UnderRoot(Path.Combine(Paths.Root, "unhandled.log"));
                string line = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture);
                File.AppendAllText(path, Scrub(line + " " + (message ?? "")) + "\r\n", Encoding.UTF8);
            }
            catch (Exception)
            {
            }
        }

        // 白名单为空不算错：服务照常启动，只是拒绝所有请求。在 unhandled.log 里留一行，方便排查。
        static void NoteWarnings(BridgeConfig cfg)
        {
            string[] warnings = ConfigRules.Warnings(cfg);
            for (int i = 0; i < warnings.Length; i++)
            {
                NoteDiag("config " + warnings[i]);
            }
        }

        public static int CheckConfig()
        {
            try
            {
                if (IntPtr.Size != 4)
                {
                    throw new InvalidOperationException("必须使用 32 位进程");
                }
                BridgeConfig cfg = BridgeConfig.Load();
                Console.WriteLine("配置有效。运行目录 " + Paths.Root + "，U8 安装目录 " + cfg.U8Home);
                Console.WriteLine("mobilePush（移动审批推送）: " + (cfg.MobilePush ? "true" : "false"));
                Console.WriteLine(TestAccountGate.Describe(cfg));
                Console.WriteLine("testAccounts（月末结账开放的测试账套）: " + string.Join(",", cfg.TestAccounts));
                Console.WriteLine(ReadOnlyGate.Describe(cfg));
                Console.WriteLine("permEvaluateOperators（可查询其他操作员权限的调用操作员）: "
                    + string.Join(",", cfg.PermEvaluateOperators));
                Console.WriteLine(WritePolicy.Describe(cfg));
                string[] extra = LicenseRules.Describe(cfg);
                for (int i = 0; i < extra.Length; i++)
                {
                    Console.WriteLine(extra[i]);
                }
                string[] warnings = ConfigRules.Warnings(cfg);
                for (int i = 0; i < warnings.Length; i++)
                {
                    Console.WriteLine("警告: " + warnings[i]);
                }
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine(Scrub(ex.Message));
                return 1;
            }
        }

        // config.json 的 mobilePush 为 false（缺省）时，移动审批程序集（U8Resolve.IsMobile：YonYou / Yonyouup / UFIDA 的 .U8.MA.，
        // 不区分大小写）故意不加载（不推友空间）。
        // 为 true 时移动审批程序集从 U8AuditWebSite 下加载，见 U8Resolve.FromMobile。其他名字按短名在 U8 固定目录里找，见 U8Resolve。
        public static Assembly BlockMobile(object sender, ResolveEventArgs args)
        {
            if (_resolve != 0)
            {
                return null;
            }
            _resolve = 1;
            try
            {
                string name = U8Resolve.ShortName(args == null ? null : args.Name);
                if (U8Resolve.MobilePush)
                {
                    Assembly mobile = U8Resolve.FromMobile(args);
                    if (mobile != null)
                    {
                        return mobile;
                    }
                    if (U8Resolve.IsMobile(name) && !U8Resolve.Optional(name) && FirstMiss(name))
                    {
                        NoteDiag("mobilePush 未找到 " + name);
                    }
                }
                else if (U8Resolve.IsMobile(name))
                {
                    NoteBlocked(name);
                    return null;
                }
                return U8Resolve.FromU8(sender, args);
            }
            catch (Exception)
            {
            }
            finally
            {
                _resolve = 0;
            }
            return null;
        }

        // 同一个短名只记一次「未找到」。
        static bool FirstMiss(string name)
        {
            lock (_missed)
            {
                return _missed.Add(name);
            }
        }

        static void NoteBlocked(string name)
        {
            try
            {
                if (!Paths.RootWritable())
                {
                    return;
                }
                string path = Paths.UnderRoot(Path.Combine(Paths.Root, "unhandled.log"));
                string line = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture);
                File.AppendAllText(path, Scrub(line + " blocked " + name) + "\r\n", Encoding.UTF8);
            }
            catch (Exception)
            {
            }
        }

        public static void NoteCrash(object sender, UnhandledExceptionEventArgs e)
        {
            try
            {
                if (!Paths.RootWritable())
                {
                    return;
                }
                string text = CrashText(e);
                string path = Paths.UnderRoot(Path.Combine(Paths.Root, "unhandled.log"));
                File.AppendAllText(path, Scrub(text) + "\r\n", Encoding.UTF8);
            }
            catch (Exception)
            {
            }
        }

        static string CrashText(UnhandledExceptionEventArgs e)
        {
            Exception ex = e.ExceptionObject as Exception;
            if (ex != null)
            {
                return ex.ToString();
            }
            if (e.ExceptionObject != null)
            {
                return e.ExceptionObject.ToString();
            }
            return "unhandled";
        }

        public static void Stop()
        {
            if (_http != null)
            {
                _http.Stop();
                _http = null;
            }
            StaWorker.Stop(65000);
            // 等满时限仍有 STA 线程没退出，说明 COM 调用还卡在线程上。不把带着活 COM 的进程交还给 SCM
            // （SCM 会报已停止而进程不退、exe 被锁），直接结束进程。
            if (StaWorker.AnyAlive)
            {
                StopGuard.Kill("stop 工作线程 65 秒内未退出（COM 调用未返回），强制结束", "u8co: STA worker alive after stop");
            }
        }

        static string Scrub(string message)
        {
            if (message == null)
            {
                return "";
            }
            return Regex.Replace(message, "(?i)(Password|Pwd)\\s*=\\s*[^;\\s]*", "$1=***");
        }

        static void WriteStartup(string message)
        {
            try
            {
                if (!Paths.RootWritable())
                {
                    return;
                }
                string path = Paths.UnderRoot(Paths.StartupError);
                File.WriteAllText(path, message ?? "", Encoding.UTF8);
            }
            catch (Exception)
            {
            }
        }

        static void ClearStartupError()
        {
            try
            {
                if (File.Exists(Paths.StartupError))
                {
                    File.Delete(Paths.StartupError);
                }
            }
            catch (Exception)
            {
            }
        }
    }
}
