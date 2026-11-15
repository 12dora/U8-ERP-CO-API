using System;
using System.IO;
using System.Reflection;

namespace U8Co
{
    // U8 API 框架（生产订单审核）的 .NET 依赖装在 U8 安装目录（config.json 的 u8Home）的各子目录，
    // U8 自己的 exe 靠 probing 配置找到，桥进程找不到。
    // 这里只按短名在这些固定子目录里找已存在的 dll 并 LoadFrom（只读，不在这些目录里写任何东西）。
    internal static class U8Resolve
    {
        // U8 API 框架目录排第一：同名程序集在 Interop 等目录也有旧副本时，优先用框架自带的那份。
        static readonly string[] SubDirs = new string[]
        {
            @"UFMOM\U8APIFramework", "Interop", "EAI", @"EAI\U8M",
            "Framework", @"AppServer\Bin\U8M", @"AppServer\Bin", "U8M",
            "UFMOM"
        };

        // 移动审批（友空间）程序集所在目录，只在 config.json 的 mobilePush 为 true 时查找。
        static readonly string[] MobileDirs = new string[] { @"U8AuditWebSite\bin", @"U8AuditWebSite\bin\Query" };

        // 移动审批程序集的短名前缀，大小写不一（YonYou / yonyou / Yonyouup / yonyouup / UFIDA），按不区分大小写比较。
        static readonly string[] MobilePrefixes = new string[] { "YonYou.U8.MA.", "Yonyouup.U8.MA.", "UFIDA.U8.MA." };

        // 读许可总数（LicenseTotals）在它自己的 AppDomain 里解析，只在这两个目录里找依赖，卫星资源和 .XmlSerializers 一律不找
        // （LicenseWorker.Resolve）。默认 AppDomain 的解析器不看这两个目录的这层规则。
        public static readonly string[] LeanDirs = new string[] { "Framework", "Interop" };

        // 只在生产订单审核调用期间生效，其余时间交给 U8 自己后装的解析器。按线程记。
        [ThreadStatic]
        static int _active;

        static volatile bool _mobilePush;

        public static void Enter()
        {
            _active++;
        }

        public static void Leave()
        {
            if (_active > 0)
            {
                _active--;
            }
        }

        // 启动时按 config.json 的 mobilePush 设置。读配置之前（以及 --check-config）一律为 false。
        public static void SetMobilePush(bool on)
        {
            _mobilePush = on;
        }

        public static bool MobilePush
        {
            get { return _mobilePush; }
        }

        public static Assembly FromU8(object sender, ResolveEventArgs args)
        {
            if (_active == 0)
            {
                return null;
            }
            string name = ShortName(args == null ? null : args.Name);
            if (!Wanted(name))
            {
                return null;
            }
            return FindIn(SubDirs, name);
        }

        // mobilePush 开启时：移动审批程序集只在 U8AuditWebSite 下找；它们引用的其他程序集先在 U8AuditWebSite 下找
        // （与移动审批同目录的那份版本配套），找不到再按原子目录找。
        // 与移动审批无关的名字返回 null，交给 FromU8。
        public static Assembly FromMobile(ResolveEventArgs args)
        {
            if (!_mobilePush || args == null)
            {
                return null;
            }
            string name = ShortName(args.Name);
            if (!SafeName(name))
            {
                return null;
            }
            if (IsMobile(name))
            {
                return FindIn(MobileDirs, name);
            }
            if (!FromMobileAsm(args.RequestingAssembly))
            {
                return null;
            }
            Assembly asm = FindIn(MobileDirs, name);
            return asm ?? FindIn(SubDirs, name);
        }

        public static bool IsMobile(string name)
        {
            if (name == null)
            {
                return false;
            }
            for (int i = 0; i < MobilePrefixes.Length; i++)
            {
                if (name.StartsWith(MobilePrefixes[i], StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }

        // 卫星资源（.resources）和序列化程序集（.XmlSerializers）本来就常常不存在，找不到不记日志。
        public static bool Optional(string name)
        {
            return name != null && (name.EndsWith(".resources", StringComparison.OrdinalIgnoreCase)
                || name.EndsWith(".XmlSerializers", StringComparison.OrdinalIgnoreCase));
        }

        // 发起解析的程序集本身是移动审批程序集，或是从 U8AuditWebSite 下加载的。
        static bool FromMobileAsm(Assembly requester)
        {
            if (requester == null)
            {
                return false;
            }
            try
            {
                if (IsMobile(requester.GetName().Name))
                {
                    return true;
                }
                string loc = requester.IsDynamic ? "" : requester.Location;
                string dir = loc.Length == 0 ? "" : Path.GetDirectoryName(loc);
                for (int i = 0; i < MobileDirs.Length; i++)
                {
                    if (string.Equals(dir, Path.Combine(Paths.U8Home, MobileDirs[i]), StringComparison.OrdinalIgnoreCase))
                    {
                        return true;
                    }
                }
            }
            catch (Exception)
            {
            }
            return false;
        }

        // U8 安装目录下这几个子目录里的 <短名>.dll，存在才加载；拼出的路径必须仍在该子目录内。
        static Assembly FindIn(string[] dirs, string name)
        {
            return FindUnder(Paths.U8Home, dirs, name, true);
        }

        // 同上，安装目录由调用方给（读许可总数的 AppDomain 里没有 Paths 的设置，也不写审计：log 为 false）。
        public static Assembly FindUnder(string home, string[] dirs, string name, bool log)
        {
            for (int i = 0; i < dirs.Length; i++)
            {
                string dir = Path.Combine(home, dirs[i]);
                string path = Path.Combine(dir, name + ".dll");
                if (!string.Equals(Path.GetDirectoryName(path), dir, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }
                if (File.Exists(path))
                {
                    Assembly asm = Assembly.LoadFrom(path);
                    if (log)
                    {
                        U8ResolveLog.Loaded(asm, path);
                    }
                    return asm;
                }
            }
            return null;
        }

        // 友空间移动端程序集（MobilePrefixes 各前缀，不区分大小写）在 FromU8 里不加载（开了 mobilePush 由 FromMobile 处理）。
        static bool Wanted(string name)
        {
            if (IsMobile(name))
            {
                return false;
            }
            return SafeName(name);
        }

        // 卫星资源交给默认回退；短名里不许有路径字符。
        public static bool SafeName(string name)
        {
            if (name.Length == 0 || name.EndsWith(".resources", StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }
            if (name.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 || name.IndexOf("..", StringComparison.Ordinal) >= 0)
            {
                return false;
            }
            return true;
        }

        public static string ShortName(string full)
        {
            if (full == null)
            {
                return "";
            }
            int comma = full.IndexOf(',');
            string name = comma < 0 ? full : full.Substring(0, comma);
            return name.Trim();
        }
    }
}
