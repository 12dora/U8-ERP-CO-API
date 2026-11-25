using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Web.Script.Serialization;

namespace U8Co
{
    internal static class ProbeLogin
    {
        // 只登录 comprobe.json 里的测试账套，而且它必须就在桥 config.json 的 allowedAccounts 里。
        static string FixedAcc
        {
            get { return ProbeConf.Current.Account; }
        }

        static string ConfigFile
        {
            get { return Path.Combine(ProbeConf.Current.BridgeRoot, "config.json"); }
        }

        public static void Login(ProbeRun run, Dictionary<string, object> step)
        {
            RefuseOther(step);
            RequireAllowed();
            object login = Open(
                run,
                NeedToken(step, "sub", "需要子系统"),
                YearOf(step),
                NeedToken(step, "operator", "需要操作员"),
                DateOf(step),
                ServerOf(step));
            try
            {
                Attach(run, step, login);
            }
            catch
            {
                if (run.Login == null)
                {
                    Shut(login);
                    ComUtil.Final(login);
                }
                throw;
            }
        }

        static void Attach(ProbeRun run, Dictionary<string, object> step, object login)
        {
            object conn = OpenConn(run, login);
            // 同一脚本再次登录时，先登出上一个登录对象，否则它占着的许可点直到进程退出都不释放。
            ProbeRun.Release(run);
            run.Login = login;
            run.Conn = conn;
            run.Put("login", login);
            run.Put("conn", conn);
            Line line = ProbeOut.Begin(run, step, true);
            ProbeOut.Keep(run, step, login, line);
            line.Str("operator", Values.Text(ComUtil.Get(login, "cUserName")));
            line.Str("sub", NeedToken(step, "sub", "需要子系统"));
            line.Str("acc", FixedAcc);
            ProbeOut.Emit(line);
        }

        internal static object Open(ProbeRun run, string sub, string year, string user, string date, string server)
        {
            object login = ComUtil.Create("U8Login.clsLogin");
            if (login == null)
            {
                throw new BridgeException(503, "com_unavailable", "U8 组件未注册");
            }
            try
            {
                object ok = ComUtil.Call(login, "Login", new object[]
                {
                    sub, FixedAcc, year, user, run.Secret, date, server, ""
                });
                if (!Values.Flag(ok))
                {
                    throw new BridgeException(422, "login_failed", Share(login));
                }
                string state = Values.Text(ComUtil.Get(login, "LogState"));
                if (state != "0")
                {
                    throw new BridgeException(422, "login_failed", "登录状态不是 0");
                }
                return login;
            }
            catch
            {
                Shut(login);
                ComUtil.Final(login);
                throw;
            }
        }

        static object OpenConn(ProbeRun run, object login)
        {
            string raw = Values.Text(ComUtil.Get(login, "UfDbName"));
            if (raw.IndexOf('=') < 0)
            {
                throw new BridgeException(500, "internal", "UfDbName 不是 OLE DB 连接串");
            }
            // 连接串含口令，只留在进程里，结果里不出现。第二条连接用来读 most_recent_sql_handle。
            run.ConnText = raw;
            return ProbeAdo.Open(raw);
        }

        internal static void RefuseOther(Dictionary<string, object> step)
        {
            RefuseKey(step, "acc");
            RefuseKey(step, "account");
        }

        static void RefuseKey(Dictionary<string, object> step, string key)
        {
            if (!step.ContainsKey(key) || step[key] == null)
            {
                return;
            }
            if (!IsFixed(step[key]))
            {
                throw new BridgeException(403, "account_not_allowed", "只允许测试账套 " + FixedAcc);
            }
        }

        static bool IsFixed(object value)
        {
            string text = value as string;
            if (text != null)
            {
                return text == FixedAcc;
            }
            if (value is int)
            {
                return ((int)value).ToString("000", CultureInfo.InvariantCulture) == FixedAcc;
            }
            if (value is long)
            {
                return ((long)value).ToString("000", CultureInfo.InvariantCulture) == FixedAcc;
            }
            return false;
        }

        internal static void RequireAllowed()
        {
            object[] list = ReadConfigValue("allowedAccounts") as object[];
            if (list != null)
            {
                for (int i = 0; i < list.Length; i++)
                {
                    if (list[i] != null && Convert.ToString(list[i], CultureInfo.InvariantCulture).Trim() == FixedAcc)
                    {
                        return;
                    }
                }
            }
            throw new BridgeException(403, "account_not_allowed", "测试账套不在桥 config.json 的 allowedAccounts 里");
        }

        internal static string YearOf(Dictionary<string, object> step)
        {
            string text = StepIo.Opt(step, "year");
            if (text == null || text.Length == 0)
            {
                return ProbeConf.Current.DefaultYear;
            }
            if (!FourDigits(text))
            {
                throw new BridgeException(400, "bad_request", "年度必须是 4 位数字");
            }
            return text;
        }

        internal static string DateOf(Dictionary<string, object> step)
        {
            string text = StepIo.Opt(step, "date");
            if (text == null || text.Length == 0)
            {
                return DateTime.Today.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
            }
            if (!DateOk(text))
            {
                throw new BridgeException(400, "bad_request", "日期格式应为 yyyy-MM-dd");
            }
            return text;
        }

        internal static string ServerOf(Dictionary<string, object> step)
        {
            string text = StepIo.Opt(step, "server");
            if (text != null && text.Length > 0)
            {
                return text;
            }
            string fromFile = ReadServer();
            if (fromFile != null)
            {
                return fromFile;
            }
            return "127.0.0.1";
        }

        internal static string NeedToken(Dictionary<string, object> step, string key, string message)
        {
            string text = StepIo.Opt(step, key);
            if (text == null || text.Length == 0)
            {
                throw new BridgeException(400, "bad_request", message);
            }
            return text;
        }

        static bool FourDigits(string text)
        {
            if (text.Length != 4)
            {
                return false;
            }
            for (int i = 0; i < 4; i++)
            {
                if (text[i] < '0' || text[i] > '9')
                {
                    return false;
                }
            }
            return true;
        }

        static bool DateOk(string text)
        {
            if (text.Length != 10 || text[4] != '-' || text[7] != '-')
            {
                return false;
            }
            for (int i = 0; i < text.Length; i++)
            {
                if (i == 4 || i == 7)
                {
                    continue;
                }
                if (text[i] < '0' || text[i] > '9')
                {
                    return false;
                }
            }
            return true;
        }

        static string ReadServer()
        {
            object raw = ReadConfigValue("u8Server");
            if (raw == null)
            {
                return null;
            }
            string server = Convert.ToString(raw, CultureInfo.InvariantCulture).Trim();
            if (server.Length == 0)
            {
                return null;
            }
            return server;
        }

        static object ReadConfigValue(string key)
        {
            try
            {
                if (!File.Exists(ConfigFile))
                {
                    return null;
                }
                string text = File.ReadAllText(ConfigFile);
                if (text.Length > 0 && text[0] == '\uFEFF')
                {
                    text = text.Substring(1);
                }
                JavaScriptSerializer ser = new JavaScriptSerializer();
                Dictionary<string, object> map = ser.DeserializeObject(text) as Dictionary<string, object>;
                if (map == null || !map.ContainsKey(key))
                {
                    return null;
                }
                return map[key];
            }
            catch (Exception)
            {
                return null;
            }
        }

        static string Share(object login)
        {
            try
            {
                string text = Values.Text(ComUtil.Get(login, "ShareString")).Trim();
                if (text.Length == 0)
                {
                    return "登录失败";
                }
                return text;
            }
            catch (Exception)
            {
                return "登录失败";
            }
        }

        internal static void Shut(object login)
        {
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
