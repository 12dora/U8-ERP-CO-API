using System;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;

namespace U8Co
{
    // 与桥相同：友空间程序集不加载，未处理异常写到桥运行目录下的 unhandled.log。
    // 运行目录和 U8 安装目录来自 comprobe.json（ProbeConf）；读到之前两者都不用。
    internal static class ProbeHost
    {
        static int _resolve;

        public static void Install()
        {
            AppDomain.CurrentDomain.AssemblyResolve += BlockMobile;
            AppDomain.CurrentDomain.UnhandledException += NoteCrash;
        }

        static Assembly BlockMobile(object sender, ResolveEventArgs args)
        {
            if (Interlocked.CompareExchange(ref _resolve, 1, 0) != 0)
            {
                return null;
            }
            try
            {
                NoteIfMobile(args);
                return FromU8(args);
            }
            catch (Exception)
            {
                return null;
            }
            finally
            {
                Interlocked.Exchange(ref _resolve, 0);
            }
        }

        // U8 的 .NET API 框架依赖装在 U8 安装目录的这些子目录里；U8 自己的 exe 靠 probing 配置找到它们。这里只按短名在固定目录里找。
        static readonly string[] U8Dirs = new string[]
        {
            @"Interop", @"UFMOM\U8APIFramework", @"EAI", @"EAI\U8M",
            @"Framework", @"AppServer\Bin\U8M", @"AppServer\Bin", @"U8M", @"UFMOM"
        };

        static Assembly FromU8(ResolveEventArgs args)
        {
            string name = ShortName(args == null ? null : args.Name);
            if (name.Length == 0 || name.StartsWith("YonYou.U8.MA.", StringComparison.Ordinal) || name.EndsWith(".resources", StringComparison.Ordinal))
            {
                return null;
            }
            ProbeConf conf = ProbeConf.Current;
            if (conf == null)
            {
                return null;
            }
            for (int i = 0; i < U8Dirs.Length; i++)
            {
                string path = Path.Combine(Path.Combine(conf.U8Home, U8Dirs[i]), name + ".dll");
                if (File.Exists(path))
                {
                    return Assembly.LoadFrom(path);
                }
            }
            return null;
        }

        static void NoteIfMobile(ResolveEventArgs args)
        {
            string full = args == null ? null : args.Name;
            string name = ShortName(full);
            if (name.StartsWith("YonYou.U8.MA.", StringComparison.Ordinal))
            {
                string line = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture);
                AppendLog(Scrub(line + " blocked " + name));
            }
        }

        static string ShortName(string full)
        {
            if (full == null)
            {
                return "";
            }
            int comma = full.IndexOf(',');
            string name = comma < 0 ? full : full.Substring(0, comma);
            return name.Trim();
        }

        static void NoteCrash(object sender, UnhandledExceptionEventArgs e)
        {
            try
            {
                AppendLog(Scrub(CrashText(e)));
            }
            catch (Exception)
            {
            }
        }

        static string CrashText(UnhandledExceptionEventArgs e)
        {
            if (e == null || e.ExceptionObject == null)
            {
                return "unhandled";
            }
            return e.ExceptionObject.ToString();
        }

        public static string Scrub(string message)
        {
            if (message == null)
            {
                return "";
            }
            return Regex.Replace(message, "(?i)(Password|Pwd)\\s*=\\s*[^;\\s]*", "$1=***");
        }

        static void AppendLog(string text)
        {
            try
            {
                ProbeConf conf = ProbeConf.Current;
                if (conf == null || !Directory.Exists(conf.BridgeRoot))
                {
                    return;
                }
                string root = conf.BridgeRoot;
                string path = Path.GetFullPath(Path.Combine(root, "unhandled.log"));
                string prefix = root + "\\";
                if (!path.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                {
                    return;
                }
                File.AppendAllText(path, text + "\r\n", Encoding.UTF8);
            }
            catch (Exception)
            {
            }
        }
    }
}
