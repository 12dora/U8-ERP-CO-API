using System;
using System.Collections;
using System.Data;
using System.Reflection;
using System.Runtime.ExceptionServices;

namespace U8Co
{
    // U8 自己的总账 .NET 组件（U8 界面「记账」走的那一套，经 FAG HTTP 池调用）在桥进程里直接用：
    // <u8Home>\LZW 的 UFIDA.U8.GL.Dal（BalanceDao.VouchPostGather、GetTrialBalance）和 UFIDA.U8.GL.Rules
    // （BalanceRule.VouchPostAll、QcCheck），LServer 的 UFIDA.U8.U8HttpPoolContext（HttpLoginContext.UserData，
    // [ThreadStatic] 公共静态字段），Framework 的 UFSoft.U8.Framework.LoginContext（UserData）。AnyCPU、net4.8，32 位桥能加载。
    // 一律反射晚绑定，不引用这些程序集。依赖（Dto、Utils、Dapper 等）只在记账调用期间（Enter / Leave，按线程）
    // 按短名在下面几个子目录里找，带防重入；卫星资源和移动审批程序集不找。首次用到时核对类型、方法、字段，缺了 503。
    internal sealed class GlPostNet
    {
        static readonly string[] Dirs = new string[] { "LZW", "LServer", "Framework", "U8Framework", "Interop", "" };
        static readonly string[] UserFields = new string[] { "ConnString4Dn", "UserName", "AccID" };
        static readonly object Gate = new object();
        static bool _hooked;
        static GlPostNet _loaded;

        [ThreadStatic]
        static int _active;

        [ThreadStatic]
        static bool _busy;

        Type _dao;
        Type _rule;
        Type _user;
        FieldInfo _context;
        MethodInfo _gather;
        MethodInfo _trial;
        MethodInfo _postAll;
        MethodInfo _qcCheck;

        public static void Enter()
        {
            lock (Gate)
            {
                if (!_hooked)
                {
                    AppDomain.CurrentDomain.AssemblyResolve += Resolve;
                    _hooked = true;
                }
            }
            _active++;
        }

        public static void Leave()
        {
            if (_active > 0)
            {
                _active--;
            }
        }

        static Assembly Resolve(object sender, ResolveEventArgs args)
        {
            if (_active == 0 || _busy || args == null)
            {
                return null;
            }
            string name = U8Resolve.ShortName(args.Name);
            if (!U8Resolve.SafeName(name) || U8Resolve.Optional(name) || U8Resolve.IsMobile(name))
            {
                return null;
            }
            _busy = true;
            try
            {
                return U8Resolve.FindUnder(Paths.U8Home, Dirs, name, true);
            }
            catch (Exception)
            {
                return null;
            }
            finally
            {
                _busy = false;
            }
        }

        // 必须在 Enter 之后调用。成功的结果缓存，失败下次重试。
        public static GlPostNet Load()
        {
            lock (Gate)
            {
                if (_loaded == null)
                {
                    _loaded = Build();
                }
                return _loaded;
            }
        }

        static GlPostNet Build()
        {
            GlPostNet net = new GlPostNet();
            Assembly dal = Need("LZW", "UFIDA.U8.GL.Dal");
            Assembly rules = Need("LZW", "UFIDA.U8.GL.Rules");
            Assembly pool = Need("LServer", "UFIDA.U8.U8HttpPoolContext");
            Assembly login = Need("Framework", "UFSoft.U8.Framework.LoginContext");
            net._dao = NeedType(dal, "UFIDA.U8.GL.Dal.BalanceDao");
            net._rule = NeedType(rules, "UFIDA.U8.GL.Rules.BalanceRule");
            net._user = NeedType(login, "UFSoft.U8.Framework.LoginContext.UserData");
            Type context = NeedType(pool, "UFIDA.U8.U8HttpPoolContext.HttpLoginContext");
            net._context = context.GetField("UserData", BindingFlags.Public | BindingFlags.Static);
            if (net._context == null || !net._context.FieldType.IsAssignableFrom(net._user))
            {
                throw Unavailable("HttpLoginContext.UserData 不是 UserData 类型的静态字段");
            }
            net._gather = NeedMethod(net._dao, "VouchPostGather",
                new Type[] { typeof(int), typeof(byte), typeof(string), typeof(byte), typeof(int) });
            net._trial = NeedMethod(net._dao, "GetTrialBalance", new Type[] { typeof(int), typeof(int), typeof(bool) });
            net._postAll = NeedMethod(net._rule, "VouchPostAll", new Type[] { typeof(int), typeof(int) });
            net._qcCheck = NeedMethod(net._rule, "QcCheck", new Type[] { typeof(int), typeof(int), typeof(int) });
            foreach (string field in UserFields)
            {
                FieldInfo f = net._user.GetField(field, BindingFlags.Public | BindingFlags.Instance);
                if (f == null || f.FieldType != typeof(string))
                {
                    throw Unavailable("UserData 没有字符串字段 " + field);
                }
            }
            return net;
        }

        static Assembly Need(string dir, string name)
        {
            Assembly asm;
            try
            {
                asm = U8Resolve.FindUnder(Paths.U8Home, new string[] { dir }, name, true);
            }
            catch (Exception ex)
            {
                throw Unavailable(dir + "\\" + name + ".dll 加载失败：" + GlPostTx.FirstLine(ex.Message));
            }
            if (asm == null)
            {
                throw Unavailable("u8Home\\" + dir + " 下没有 " + name + ".dll");
            }
            return asm;
        }

        static Type NeedType(Assembly asm, string name)
        {
            Type type;
            try
            {
                type = asm.GetType(name, false);
            }
            catch (Exception ex)
            {
                throw Unavailable("类型 " + name + " 加载失败：" + GlPostTx.FirstLine(ex.Message));
            }
            if (type == null)
            {
                throw Unavailable("没有类型 " + name);
            }
            return type;
        }

        static MethodInfo NeedMethod(Type type, string name, Type[] args)
        {
            MethodInfo method = type.GetMethod(name, BindingFlags.Public | BindingFlags.Instance, null, args, null);
            if (method == null)
            {
                throw Unavailable(type.Name + "." + name + " 的签名与预期不符");
            }
            return method;
        }

        static BridgeException Unavailable(string what)
        {
            return new BridgeException(503, "u8_unavailable", "U8 总账记账组件不可用：" + what);
        }

        // U8 的 DAL 从 HttpLoginContext.UserData 取连接串（ConnString4Dn）、操作员姓名（UserName → cbook）、账套号（AccID）；
        // QcCheck 的 ZzPub 还要 operDate。连接串只进这个对象，不记日志。
        public object NewUser(string conn, GlPostReq req, WorkItem item, string date)
        {
            object user = Activator.CreateInstance(_user);
            Set(user, "ConnString4Dn", conn);
            Set(user, "UserName", req.Poster);
            Set(user, "AccID", item.Acc ?? "");
            Set(user, "iYear", req.Year.ToString(System.Globalization.CultureInfo.InvariantCulture));
            Set(user, "operDate", date);
            Set(user, "UserId", item.Operator ?? "");
            Set(user, "cSubID", "GL");
            return user;
        }

        void Set(object user, string name, string value)
        {
            FieldInfo f = _user.GetField(name, BindingFlags.Public | BindingFlags.Instance);
            if (f != null && f.FieldType == typeof(string))
            {
                f.SetValue(user, value);
            }
        }

        // [ThreadStatic]：只在当前（写工人）线程上生效。调用方必须在 finally 里 Unbind。
        public void Bind(object user)
        {
            _context.SetValue(null, user);
        }

        public void Unbind()
        {
            try
            {
                _context.SetValue(null, null);
            }
            catch (Exception)
            {
            }
        }

        public DataSet Gather(GlPostReq req)
        {
            object dao = Create(_dao);
            object[] args = new object[] { req.Period, (byte)(req.Cash ? 1 : 0), req.Cond, (byte)(req.Master ? 1 : 0), req.Year };
            return Call(_gather, dao, args) as DataSet;
        }

        public void PostAll(GlPostReq req)
        {
            Call(_postAll, Create(_rule), new object[] { req.Period, req.Year });
        }

        // 照 UCPost.QCCheck：期初对账按第 0 期（BalanceCheck(0, 年度)），checktype -1 全做。会写 GL_merror 和 GL_mend 第 0 期的 bpri_check。
        public IDictionary QcCheck(int year)
        {
            return Call(_qcCheck, Create(_rule), new object[] { 0, year, -1 }) as IDictionary;
        }

        // 期初试算（UCTrial 用的 GetTrialBalance(期间, 年度)）：期间 0 时一级科目按类汇总第 1 期的 mb，
        // 期间 1（年中建账的建账年度）时汇总第 1 期的 me（借正贷负）。
        public IEnumerable Trial(int period, int year)
        {
            return Call(_trial, Create(_dao), new object[] { period, year, false }) as IEnumerable;
        }

        static object Create(Type type)
        {
            try
            {
                return Activator.CreateInstance(type);
            }
            catch (TargetInvocationException ex)
            {
                throw Inner(ex);
            }
        }

        static object Call(MethodInfo method, object target, object[] args)
        {
            try
            {
                return method.Invoke(target, args);
            }
            catch (TargetInvocationException ex)
            {
                throw Inner(ex);
            }
        }

        // 把 U8 抛的原异常连同堆栈原样抛出；没有内层异常时抛外层。
        static Exception Inner(TargetInvocationException ex)
        {
            if (ex.InnerException != null)
            {
                ExceptionDispatchInfo.Capture(ex.InnerException).Throw();
            }
            return ex;
        }
    }
}
