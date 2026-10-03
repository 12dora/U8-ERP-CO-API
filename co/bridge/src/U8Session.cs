using System;

namespace U8Co
{
    internal sealed class U8Open
    {
        public string SubId;
        public string Acc;
        public string Year;
        public string User;
        public string Password;
        public string Date;
        // 写路由（含预演）的登录：许可保护的 holdWritesWhen 只拦这种（LicenseHold）。
        public bool Write;
    }

    internal sealed class U8Session : IDisposable
    {
        object _login;
        string _token;
        bool _tokenLoaded;
        string _employee;
        bool _employeeLoaded;
        // 占着 LicenseHold 的在用登录名额，Dispose 时退回。
        bool _held;
        public string OperatorName;

        public object Login
        {
            get { return _login; }
        }

        // 第一次读取才碰 userToken。审计和异常文本都不写它。
        public string Token
        {
            get
            {
                if (_tokenLoaded)
                {
                    return _token ?? "";
                }
                if (_login == null)
                {
                    _tokenLoaded = true;
                    _token = "";
                    return _token;
                }
                _token = Values.Text(ComUtil.Get(_login, "userToken"));
                _tokenLoaded = true;
                return _token ?? "";
            }
        }

        // 登录检查不读人员编码。审批动作第一次用到时才取 cEmployeeId。
        public string EmployeeId
        {
            get
            {
                if (_employeeLoaded)
                {
                    return _employee ?? "";
                }
                if (_login == null)
                {
                    _employeeLoaded = true;
                    _employee = "";
                    return _employee;
                }
                _employee = Values.Text(ComUtil.Get(_login, "cEmployeeId"));
                _employeeLoaded = true;
                return _employee ?? "";
            }
        }

        public string LoadedToken()
        {
            if (!_tokenLoaded || _token == null)
            {
                return "";
            }
            return _token;
        }

        // 先过许可保护（LicenseHold：holdWritesWhen、maxConcurrentLogins），占一个在用登录名额再调 clsLogin；
        // 登录失败退回名额，成功的由 Dispose 退回。同一请求内的嵌套登录不占名额（LicenseHold.BeginNested）。
        public static U8Session Open(BridgeConfig cfg, U8Open ask)
        {
            bool held = LicenseHold.Enter(ask);
            U8Session session = null;
            try
            {
                session = OpenRetry(cfg, ask);
                session._held = held;
                return session;
            }
            finally
            {
                if (session == null && held)
                {
                    LicenseHold.Leave();
                }
            }
        }

        // 自检用：只过许可保护、不登录 U8 的会话（登录对象为空），Dispose 照常退回名额。
        internal static U8Session HeldForTest(U8Open ask)
        {
            U8Session session = new U8Session();
            session._held = LicenseHold.Enter(ask);
            return session;
        }

        // 许可点数饱和、登录状态不是 0 都是暂时的：按 LicenseRetry 的等待表重试，每次换一个新的 clsLogin，
        // 连同重试总共不超过 LicenseRetry.BudgetMs。口令错误等其他 login_failed 不重试。
        // 任何一次见过饱和、最后一次仍是暂时性失败时，按饱和报 503 u8_license_full。
        static U8Session OpenRetry(BridgeConfig cfg, U8Open ask)
        {
            string subId = ask.SubId;
            if (subId == null || subId.Length == 0)
            {
                subId = "SA";
            }
            int retries = LicenseRetry.Count(cfg.LicenseRetries);
            long start = DateTime.UtcNow.Ticks;
            BridgeException full = null;
            for (int attempt = 0; ; attempt++)
            {
                long begun = DateTime.UtcNow.Ticks;
                try
                {
                    U8Session session = OpenOnce(cfg, ask, subId);
                    LicenseState.NoteOk(subId, session._login, cfg);
                    return session;
                }
                catch (BridgeException ex)
                {
                    if (!LicenseRetry.Transient(ex))
                    {
                        throw;
                    }
                    if (ex.Code == LicenseRetry.FullCode)
                    {
                        full = ex;
                    }
                    long lastMs = (DateTime.UtcNow.Ticks - begun) / TimeSpan.TicksPerMillisecond;
                    if (attempt >= retries || !LicenseRetry.Fits(start, attempt, lastMs))
                    {
                        throw GiveUp(subId, full ?? ex, attempt + 1);
                    }
                }
                LicenseRetry.Wait(attempt);
            }
        }

        static BridgeException GiveUp(string subId, BridgeException ex, int attempts)
        {
            LicenseState.NoteFinal(subId, ex, attempts);
            return ex;
        }

        static U8Session OpenOnce(BridgeConfig cfg, U8Open ask, string subId)
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
                    subId, ask.Acc, ask.Year, ask.User, ask.Password, ask.Date, cfg.U8Server, ""
                });
                if (!Values.Flag(ok))
                {
                    throw LoginError(login, subId);
                }
                // 参考实现在 Login 返回 true 之后还要求 LogState 的字符串是 0。
                string state = Values.Text(ComUtil.Get(login, "LogState"));
                if (state != "0")
                {
                    throw new BridgeException(422, "login_failed", LicenseRetry.StateMessage);
                }
                U8Session session = new U8Session();
                session._login = login;
                session.OperatorName = Values.Text(ComUtil.Get(login, "cUserName"));
                login = null;
                return session;
            }
            finally
            {
                if (login != null)
                {
                    Shut(login);
                    ComUtil.Final(login);
                }
            }
        }

        // ShareString 含「加密点数已饱和」：该子系统的许可点数都被别的工作站占着，503 u8_license_full。
        static BridgeException LoginError(object login, string subId)
        {
            string text = Share(login);
            if (text.IndexOf(LicenseRetry.FullMark, StringComparison.Ordinal) < 0)
            {
                return new BridgeException(422, "login_failed", text);
            }
            LicenseState.NoteSaturated(subId);
            return new BridgeException(503, LicenseRetry.FullCode,
                "U8 许可点数已满（子系统 " + subId + "），请稍后重试：" + text);
        }

        public void Dispose()
        {
            if (_held)
            {
                _held = false;
                LicenseHold.Leave();
            }
            object login = _login;
            _login = null;
            if (login == null)
            {
                return;
            }
            Shut(login);
            ComUtil.Final(login);
        }

        static void Shut(object login)
        {
            try
            {
                ComUtil.Call(login, "ShutDown", new object[0]);
            }
            catch (Exception)
            {
                // 登录失败时也可能没有可关闭的会话，不能因此盖住原来的错误。
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
    }
}
