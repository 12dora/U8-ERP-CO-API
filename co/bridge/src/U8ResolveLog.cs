using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Reflection;

namespace U8Co
{
    // U8Resolve 每加载一个程序集，按完整路径在审计日志里记一次 assembly_loaded：路径、程序集版本、文件版本。
    // 换 U8 补丁后对照这些行就知道桥实际用的是哪一份 DLL。只读文件版本信息，不再加载别的程序集。
    internal static class U8ResolveLog
    {
        static readonly object Gate = new object();
        static readonly HashSet<string> Seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        public static void Loaded(Assembly asm, string path)
        {
            try
            {
                lock (Gate)
                {
                    if (!Seen.Add(path))
                    {
                        return;
                    }
                }
                Dictionary<string, object> evt = new Dictionary<string, object>();
                AssemblyName name = asm == null ? null : asm.GetName();
                evt["name"] = name == null ? "" : name.Name;
                evt["path"] = path;
                evt["version"] = name == null || name.Version == null ? "" : name.Version.ToString();
                evt["file_version"] = FileVersion(path);
                AuditEvent.Write("assembly_loaded", evt);
            }
            catch (Exception)
            {
                // 日志失败不能影响程序集解析。
            }
        }

        static string FileVersion(string path)
        {
            try
            {
                string text = FileVersionInfo.GetVersionInfo(path).FileVersion;
                return text ?? "";
            }
            catch (Exception)
            {
                return "";
            }
        }
    }
}
