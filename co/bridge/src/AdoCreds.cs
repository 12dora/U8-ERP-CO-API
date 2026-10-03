using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 连接串只从这一处来：默认登录对象的 UfDbName(与 U8 客户端相同),配置了 sql.json 才换成专用登录。
    internal static class AdoCreds
    {
        public static string Resolve(string ufDbName, string sqlUser, string sqlPassword)
        {
            if (ufDbName == null || ufDbName.IndexOf('=') < 0)
            {
                throw new BridgeException(500, "internal", "UfDbName 不是 OLE DB 连接串");
            }
            // 与 U8 客户端一致,默认直接用登录对象给的连接(U8 自己通常用 sa)。
            // CO 组件审核时还会跨库访问 UFSystem,低权限登录容易在 U8 内部失败。
            // 只有配置了 sql.json 才换成专用登录;sql.json 里不允许再写 sa。
            if (sqlUser == null || sqlUser.Length == 0)
            {
                return ufDbName;
            }
            if (string.Equals(sqlUser, "sa", StringComparison.OrdinalIgnoreCase))
            {
                throw new BridgeException(500, "internal", "sql.json 不允许使用 sa");
            }
            return Rebuild(Split(ufDbName), sqlUser, sqlPassword ?? "");
        }

        static string Rebuild(List<string[]> parts, string user, string password)
        {
            StringBuilder buf = new StringBuilder();
            for (int i = 0; i < parts.Count; i++)
            {
                if (IsSecretKey(parts[i][0]))
                {
                    continue;
                }
                if (buf.Length > 0)
                {
                    buf.Append(';');
                }
                buf.Append(parts[i][0]);
                buf.Append('=');
                buf.Append(parts[i][1]);
            }
            if (buf.Length > 0)
            {
                buf.Append(';');
            }
            buf.Append("User ID=");
            buf.Append(user);
            buf.Append(";Password=");
            buf.Append(password);
            return buf.ToString();
        }

        static bool IsSecretKey(string key)
        {
            return KeyIs(key, "User ID") || KeyIs(key, "UID") || KeyIs(key, "Password") || KeyIs(key, "Pwd");
        }

        static bool KeyIs(string left, string right)
        {
            return string.Equals(left, right, StringComparison.OrdinalIgnoreCase);
        }

        static List<string[]> Split(string raw)
        {
            string[] bits = raw.Split(';');
            List<string[]> list = new List<string[]>();
            for (int i = 0; i < bits.Length; i++)
            {
                string bit = bits[i].Trim();
                int eq = bit.IndexOf('=');
                if (eq <= 0)
                {
                    continue;
                }
                string[] pair = new string[2];
                pair[0] = bit.Substring(0, eq).Trim();
                pair[1] = bit.Substring(eq + 1).Trim();
                list.Add(pair);
            }
            return list;
        }
    }
}
