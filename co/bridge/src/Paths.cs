using System;
using System.Collections.Generic;
using System.IO;
using System.Security.AccessControl;
using System.Security.Principal;

namespace U8Co
{
    // 运行目录（config.json、secret.hex、sql.json、日志）和 U8 安装目录。
    // 运行目录由命令行 --root 在 Main 里设一次；U8 安装目录由 config.json 的 u8Home 在启动时设一次。之后只读。
    internal static class Paths
    {
        public const string DefaultU8Home = @"C:\U8SOFT";
        // DELETE、FILE_DELETE_CHILD、WRITE_DAC、WRITE_OWNER、GENERIC_ALL（完全控制、修改都含其中之一）。
        const int AncestorDanger = 0x10000 | 0x40 | 0x40000 | 0x80000 | 0x10000000;
        const int DeleteRight = 0x10000;
        const string TrustedInstaller = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464";
        static volatile string _root = DefaultRoot();
        static volatile string _u8Home = DefaultU8Home;
        // 上级检查结果：0 未查，1 通过，-1 不通过。不通过时不往运行目录写任何诊断文件。
        static volatile int _ancestors;

        public static string Root
        {
            get { return _root; }
        }

        // 服务启动和 --check-config 都经 BridgeConfig.Load 读这个文件；Load 读之前先调 CheckAncestors。
        public static string ConfigFile
        {
            get { return Path.Combine(_root, "config.json"); }
        }

        public static string SecretFile
        {
            get { return Path.Combine(_root, "secret.hex"); }
        }

        public static string SqlFile
        {
            get { return Path.Combine(_root, "sql.json"); }
        }

        public static string StartupError
        {
            get { return Path.Combine(_root, "startup-error.txt"); }
        }

        public static string DefaultAuditLog
        {
            get { return Path.Combine(_root, "logs"); }
        }

        // U8 安装目录。只从这里读程序集和 EAI 字段表，不在里面写任何东西。
        public static string U8Home
        {
            get { return _u8Home; }
        }

        // 缺省 %ProgramData%\U8Co\u8co，与 install.ps1 的 -Root 缺省值一致。
        static string DefaultRoot()
        {
            string data = Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData);
            if (data == null || data.Length == 0)
            {
                data = @"C:\ProgramData";
            }
            return Path.Combine(data, @"U8Co\u8co");
        }

        public static void SetRoot(string dir)
        {
            string full = CheckDir(dir, "--root");
            if (Depth(full) < 2)
            {
                throw new InvalidOperationException("--root 至少要在盘符下两级，例如 C:\\ProgramData\\U8Co\\u8co");
            }
            _root = full;
            _ancestors = 0;
        }

        // U8 安装目录里有 EAI 字段对照表时为 true。自检里读真实对照表的项在没有 U8 的机器上跳过。
        public static bool U8HomePresent
        {
            get { return Directory.Exists(Path.Combine(_u8Home, @"EAI\XML\RsXml")); }
        }

        public static void SetU8Home(string dir)
        {
            _u8Home = CheckDir(dir, "u8Home");
        }

        // 运行目录的每一级上级（盘符根到直接上级）都要可信：谁能把其中一级改名、删掉或改权限，
        // 谁就能把整个运行目录换成自己的，而服务以 LocalSystem 读它。别人在上级里新建东西不要紧，
        // 新建的归他自己，换不掉已有的这一支。与 SafePath.ps1 的 Assert-SafeAncestors 相同，改一处要同步另一处。
        public static void CheckAncestors()
        {
            try
            {
                CheckChain();
            }
            catch (Exception)
            {
                _ancestors = -1;
                throw;
            }
            _ancestors = 1;
        }

        // 能否以 LocalSystem 往运行目录写 startup-error.txt、unhandled.log。上级不可信时路径可能被
        // 换到别处（例如经联接点落进系统目录），这时一律不写；还没查过就先查一次，结果记下来。
        public static bool RootWritable()
        {
            if (_ancestors == 0)
            {
                try
                {
                    CheckAncestors();
                }
                catch (Exception)
                {
                }
            }
            return _ancestors == 1 && Directory.Exists(_root);
        }

        static void CheckChain()
        {
            string[] chain = Ancestors(_root);
            for (int i = 0; i < chain.Length; i++)
            {
                if (!Directory.Exists(chain[i]))
                {
                    return;
                }
                CheckAncestor(chain[i]);
            }
        }

        // 从盘符根往下排到直接上级。
        static string[] Ancestors(string full)
        {
            List<string> list = new List<string>();
            string dir = Path.GetDirectoryName(full);
            while (dir != null && dir.Length > 0)
            {
                list.Insert(0, dir);
                dir = Path.GetDirectoryName(dir);
            }
            return list.ToArray();
        }

        static void CheckAncestor(string dir)
        {
            if ((File.GetAttributes(dir) & FileAttributes.ReparsePoint) != 0)
            {
                throw new InvalidOperationException("运行目录的上级是重解析点，拒绝启动：" + dir);
            }
            DirectorySecurity acl = Directory.GetAccessControl(dir, AccessControlSections.Access | AccessControlSections.Owner);
            SecurityIdentifier owner = acl.GetOwner(typeof(SecurityIdentifier)) as SecurityIdentifier;
            if (owner == null || !TrustedSid(owner.Value))
            {
                throw new InvalidOperationException("运行目录的上级所有者不是 SYSTEM、Administrators 或 TrustedInstaller，拒绝启动：" + dir);
            }
            // 盘符根不能改名或删除，只看删子项、改权限、改所有者。
            int mask = dir.Length <= 3 ? AncestorDanger & ~DeleteRight : AncestorDanger;
            foreach (FileSystemAccessRule rule in acl.GetAccessRules(true, true, typeof(SecurityIdentifier)))
            {
                if (Dangerous(rule, mask))
                {
                    throw new InvalidOperationException("运行目录的上级 " + dir + " 允许 " + rule.IdentityReference.Value + " 删除、改名或改权限，拒绝启动");
                }
            }
        }

        static bool Dangerous(FileSystemAccessRule rule, int mask)
        {
            if (rule.AccessControlType != AccessControlType.Allow)
            {
                return false;
            }
            // 只继承给子项的 ACE 不作用于这一级本身。
            if ((rule.PropagationFlags & PropagationFlags.InheritOnly) != 0)
            {
                return false;
            }
            if (((int)rule.FileSystemRights & mask) == 0)
            {
                return false;
            }
            string sid = rule.IdentityReference.Value;
            return sid != "S-1-3-0" && !TrustedSid(sid);
        }

        static bool TrustedSid(string sid)
        {
            return sid == "S-1-5-18" || sid == "S-1-5-32-544" || sid == TrustedInstaller;
        }

        // 带盘符的绝对路径、不是盘符根目录。返回完整路径，去掉末尾的反斜杠。
        public static string CheckDir(string dir, string name)
        {
            if (dir == null || dir.Trim().Length == 0)
            {
                throw new InvalidOperationException(name + " 不能为空");
            }
            if (!DriveAbsolute(dir))
            {
                throw new InvalidOperationException(name + " 必须是带盘符的绝对路径（如 X:\\目录）：" + dir);
            }
            string full;
            try
            {
                full = Path.GetFullPath(dir);
            }
            catch (Exception)
            {
                throw new InvalidOperationException(name + " 不是有效路径：" + dir);
            }
            full = full.TrimEnd('\\');
            if (Depth(full) < 1)
            {
                throw new InvalidOperationException(name + " 不能是盘符根目录：" + dir);
            }
            return full;
        }

        // a 与 b 相同，或一个在另一个之下。
        public static bool Overlaps(string a, string b)
        {
            return Within(a, b) || Within(b, a);
        }

        static bool Within(string path, string dir)
        {
            string full = path.TrimEnd('\\');
            string root = dir.TrimEnd('\\');
            if (string.Equals(full, root, StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }
            return full.StartsWith(root + "\\", StringComparison.OrdinalIgnoreCase);
        }

        static bool DriveAbsolute(string dir)
        {
            if (dir.Length < 3 || !char.IsLetter(dir[0]) || dir[1] != ':')
            {
                return false;
            }
            return dir[2] == '\\' || dir[2] == '/';
        }

        // 盘符下的层数：C: 为 0，C:\a 为 1，C:\a\b 为 2。
        static int Depth(string full)
        {
            string rest = full.Length > 3 ? full.Substring(3) : "";
            if (rest.Length == 0)
            {
                return 0;
            }
            return rest.Split(new char[] { '\\' }, StringSplitOptions.RemoveEmptyEntries).Length;
        }

        public static string UnderRoot(string path)
        {
            if (path == null || path.Length == 0)
            {
                throw new InvalidOperationException("路径为空");
            }
            string full = Path.GetFullPath(path);
            string root = Path.GetFullPath(_root);
            if (string.Equals(full, root, StringComparison.OrdinalIgnoreCase))
            {
                return full;
            }
            string prefix = root.TrimEnd('\\') + "\\";
            if (!full.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException("路径必须位于 " + prefix + " 之下");
            }
            return full;
        }
    }
}
