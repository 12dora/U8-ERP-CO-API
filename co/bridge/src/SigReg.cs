using System;
using System.Globalization;
using System.IO;
using Microsoft.Win32;

namespace U8Co
{
    // 一个 ProgID 在注册表里的落点。只读 HKCR（32 位进程看到的是 32 位视图），不创建对象。
    internal sealed class SigComp
    {
        public string ProgId;
        public Guid Clsid;
        public string Server = "";
        public bool Managed;
        public string CodeBase = "";
        public string TypeLibPath = "";
        // 非空表示组件缺失（ProgID 未注册、没有服务器、文件不存在）。
        public string Missing;
    }

    internal static class SigReg
    {
        public static SigComp Lookup(string progId)
        {
            SigComp comp = new SigComp();
            comp.ProgId = progId;
            string clsid = ProgIdClsid(progId);
            Guid guid;
            if (clsid == null || !TryGuid(clsid, out guid))
            {
                comp.Missing = "ProgID 未注册";
                return comp;
            }
            comp.Clsid = guid;
            ReadClsid(comp);
            return comp;
        }

        static string ProgIdClsid(string progId)
        {
            string direct = DefaultValue(progId + @"\CLSID");
            if (direct != null)
            {
                return direct;
            }
            // 版本无关 ProgID 只挂 CurVer，跟一层。
            string cur = DefaultValue(progId + @"\CurVer");
            if (cur == null || cur.Length == 0 || cur.IndexOf('\\') >= 0)
            {
                return null;
            }
            return DefaultValue(cur + @"\CLSID");
        }

        static void ReadClsid(SigComp comp)
        {
            string key = "CLSID\\" + comp.Clsid.ToString("B");
            string inproc = DefaultValue(key + @"\InprocServer32");
            string local = DefaultValue(key + @"\LocalServer32");
            if (inproc == null && local == null)
            {
                comp.Missing = "CLSID 没有登记服务器";
                return;
            }
            if (inproc != null)
            {
                ReadInproc(comp, key, Unquote(inproc));
            }
            else
            {
                comp.Server = Unquote(local);
            }
            if (comp.Missing == null)
            {
                comp.TypeLibPath = TypeLibOf(key);
            }
        }

        static void ReadInproc(SigComp comp, string key, string server)
        {
            comp.Server = server;
            comp.Managed = server.EndsWith("mscoree.dll", StringComparison.OrdinalIgnoreCase)
                || NamedValue(key + @"\InprocServer32", "Assembly") != null;
            if (comp.Managed)
            {
                comp.CodeBase = LocalPath(NamedValue(key + @"\InprocServer32", "CodeBase"));
                if (comp.CodeBase.Length > 0 && !File.Exists(comp.CodeBase))
                {
                    comp.Missing = ".NET 组件文件不存在";
                }
                return;
            }
            if (Path.IsPathRooted(server) && !File.Exists(server))
            {
                comp.Missing = "组件文件不存在";
            }
        }

        // CLSID\{clsid}\TypeLib + Version → TypeLib\{libid}\{ver}\{lcid}\win32。找不到返回空串。
        static string TypeLibOf(string clsidKey)
        {
            string libid = DefaultValue(clsidKey + @"\TypeLib");
            Guid lib;
            if (libid == null || !TryGuid(libid, out lib))
            {
                return "";
            }
            string root = "TypeLib\\" + lib.ToString("B");
            string ver = DefaultValue(clsidKey + @"\Version");
            if (ver == null || !KeyExists(root + "\\" + ver))
            {
                ver = HighestVersion(root);
            }
            if (ver == null)
            {
                return "";
            }
            return Win32Path(root + "\\" + ver);
        }

        static string Win32Path(string verKey)
        {
            string path = DefaultValue(verKey + @"\0\win32");
            if (path != null)
            {
                return Unquote(path);
            }
            string[] lcids = SubKeys(verKey);
            for (int i = 0; i < lcids.Length; i++)
            {
                path = DefaultValue(verKey + "\\" + lcids[i] + @"\win32");
                if (path != null)
                {
                    return Unquote(path);
                }
            }
            return "";
        }

        static string HighestVersion(string root)
        {
            string best = null;
            long bestRank = -1;
            string[] names = SubKeys(root);
            for (int i = 0; i < names.Length; i++)
            {
                long rank = VersionRank(names[i]);
                if (rank > bestRank)
                {
                    bestRank = rank;
                    best = names[i];
                }
            }
            return best;
        }

        // 类型库版本号是十六进制的「主.次」。
        static long VersionRank(string text)
        {
            string[] parts = text.Split('.');
            int major;
            int minor;
            if (parts.Length != 2
                || !int.TryParse(parts[0], NumberStyles.HexNumber, CultureInfo.InvariantCulture, out major)
                || !int.TryParse(parts[1], NumberStyles.HexNumber, CultureInfo.InvariantCulture, out minor))
            {
                return -1;
            }
            return ((long)major << 32) | (uint)minor;
        }

        static string LocalPath(string codeBase)
        {
            if (codeBase == null || codeBase.Length == 0)
            {
                return "";
            }
            try
            {
                return new Uri(codeBase).LocalPath;
            }
            catch (Exception)
            {
                return "";
            }
        }

        static string Unquote(string text)
        {
            return (text ?? "").Trim().Trim('"');
        }

        static bool TryGuid(string text, out Guid guid)
        {
            guid = Guid.Empty;
            try
            {
                guid = new Guid(text.Trim());
                return true;
            }
            catch (FormatException)
            {
                return false;
            }
            catch (OverflowException)
            {
                return false;
            }
        }

        static string DefaultValue(string key)
        {
            return NamedValue(key, "");
        }

        static string NamedValue(string key, string name)
        {
            using (RegistryKey k = Registry.ClassesRoot.OpenSubKey(key, false))
            {
                if (k == null)
                {
                    return null;
                }
                object value = k.GetValue(name);
                return value == null ? null : Convert.ToString(value, CultureInfo.InvariantCulture);
            }
        }

        static bool KeyExists(string key)
        {
            using (RegistryKey k = Registry.ClassesRoot.OpenSubKey(key, false))
            {
                return k != null;
            }
        }

        static string[] SubKeys(string key)
        {
            using (RegistryKey k = Registry.ClassesRoot.OpenSubKey(key, false))
            {
                return k == null ? new string[0] : k.GetSubKeyNames();
            }
        }
    }
}
