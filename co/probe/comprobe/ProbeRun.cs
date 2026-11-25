using System;
using System.Collections.Generic;

namespace U8Co
{
    internal sealed class ProbeRun : IDisposable
    {
        readonly Dictionary<string, object> _vars = new Dictionary<string, object>(StringComparer.Ordinal);
        public int Index;
        public string Secret;
        public object Login;
        public object Conn;
        public string ConnText;

        public ProbeRun(string secret)
        {
            Secret = secret;
        }

        public void Put(string name, object value)
        {
            if (!Ident(name))
            {
                throw new BridgeException(400, "bad_request", "变量名无效");
            }
            _vars[name] = value;
        }

        public object NeedRef(string text)
        {
            string name = RefName(text);
            object value;
            if (!_vars.TryGetValue(name, out value))
            {
                throw new BridgeException(400, "bad_request", "变量不存在 " + name);
            }
            return value;
        }

        public object NeedConn()
        {
            if (Conn == null)
            {
                throw new BridgeException(400, "bad_request", "尚未登录");
            }
            return Conn;
        }

        public static string RefName(string text)
        {
            if (text == null || text.Length < 2 || text[0] != '$' || !Ident(text.Substring(1)))
            {
                throw new BridgeException(400, "bad_request", "需要变量引用");
            }
            return text.Substring(1);
        }

        public static bool Ident(string name)
        {
            if (name == null || name.Length == 0 || name.Length > 64)
            {
                return false;
            }
            if (!Head(name[0]))
            {
                return false;
            }
            for (int i = 1; i < name.Length; i++)
            {
                if (!Head(name[i]) && !Digit(name[i]))
                {
                    return false;
                }
            }
            return true;
        }

        static bool Head(char c)
        {
            return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || c == '_';
        }

        static bool Digit(char c)
        {
            return c >= '0' && c <= '9';
        }

        // 登出并放掉当前登录对象和连接（再次登录前调用）。
        public static void Release(ProbeRun run)
        {
            object conn = run.Conn;
            object login = run.Login;
            run.Conn = null;
            run.Login = null;
            ProbeAdo.Close(conn);
            Shut(login);
            ComUtil.Final(login);
        }

        public void Dispose()
        {
            object conn = Conn;
            object login = Login;
            Conn = null;
            Login = null;
            ConnText = null;
            ProbeAdo.Close(conn);
            Shut(login);
            ComUtil.Final(login);
        }

        static void Shut(object login)
        {
            if (login == null)
            {
                return;
            }
            try
            {
                ComUtil.Call(login, "ShutDown", new object[0]);
            }
            catch (Exception)
            {
            }
        }
    }
}
