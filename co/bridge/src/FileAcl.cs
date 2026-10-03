using System;
using System.IO;
using System.Security.AccessControl;
using System.Security.Principal;

namespace U8Co
{
    internal static class FileAcl
    {
        public static void RefuseExtraReaders(string path)
        {
            if (!File.Exists(path))
            {
                return;
            }
            FileSecurity acl = File.GetAccessControl(path);
            AuthorizationRuleCollection rules = acl.GetAccessRules(true, true, typeof(SecurityIdentifier));
            foreach (FileSystemAccessRule rule in rules)
            {
                if (!AllowsRead(rule))
                {
                    continue;
                }
                SecurityIdentifier sid = rule.IdentityReference as SecurityIdentifier;
                if (sid == null || !Trusted(sid))
                {
                    throw new InvalidOperationException(Path.GetFileName(path) + " 对其他账户可读");
                }
            }
        }

        static bool AllowsRead(FileSystemAccessRule rule)
        {
            if (rule.AccessControlType != AccessControlType.Allow)
            {
                return false;
            }
            int bits = (int)rule.FileSystemRights;
            int read = (int)FileSystemRights.Read;
            // .NET Framework 的 FileSystemRights 没有通用位,按 Win32 访问掩码写:GENERIC_READ 0x80000000、GENERIC_ALL 0x10000000
            int generic = unchecked((int)0x80000000) | 0x10000000;
            if ((bits & read) != 0)
            {
                return true;
            }
            return (bits & generic) != 0;
        }

        static bool Trusted(SecurityIdentifier sid)
        {
            string value = sid.Value;
            if (value == "S-1-5-18")
            {
                return true;
            }
            return value == "S-1-5-32-544";
        }
    }
}
