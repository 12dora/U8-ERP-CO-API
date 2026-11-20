using System;
using System.Collections;
using System.Data;
using System.Reflection;
using System.Runtime.ExceptionServices;

namespace U8Co
{
    // 生产订单参照销售订单生成调用的 U8 组件，与 U8 界面 MO05001 同一套：UFSoft.U8.U8M.BFDispatcher 的
    // MOMExecutor.BFMomProcess_GetMOGenBaseData（列表）、MOMExecutor.BFBatchProcess_SaveMOGenQtyData（保存），
    // U8MCommonExecutor.BFSystem_GetMinSystemDate / GetMaxSystemDate（列表的日期范围）。代理方法按自己的方法名拼 BF 动作，
    // 所以一律反射调用真实的公共静态方法，不自己拼动作名。程序集晚绑定（构建不引用），只在 U8Resolve.Enter 期间按短名加载。
    //
    // 线程上下文照 U8 API 框架（UFSoft.U8.U8M.API 的 UAPAPIBase：构造时 TargetProviderFactory.SetCustomBFProvider(
    // new U8MEntranceHelper())，__InitContext 里 ThreadDBConnInfo.SetSingleDBConnectionType(true)、
    // ThreadContext.SetLoginInfo(U8Login.userToken)）。MomCallContextCache 那一步只是取 U8Login，桥直接用本次登录。
    // 这几样都是 [ThreadStatic]，写线程会被不同请求、不同操作员、不同账套复用：Open 先记下原值，Dispose 逐项恢复
    // （自定义 BF 提供者、ServerToken、U8LoginInfo、DBConnectionType，每项各自 try，一项失败不跳过其余）。
    // 单连接模式下 U8M 的 DAC 都用 ConnectionCourier 按线程缓存的连接（GetCurrentConnection 不核对 token），
    // U8 API 框架每次调用后由 NoTransactionImple.Dispose 关掉它；这里同样在 Enter（换 token 之前）和 Dispose（恢复之前）
    // 调 ConnectionCourier.Instance.CloseCurrentConnection()，Dispose 另调 RemoveCustomTransaction()，
    // 免得同一线程上随后的调用（其他账套的 U8 API、下一次生单）用到这次账套的连接。
    internal sealed class MoGenU8 : IDisposable
    {
        const string Dispatcher = "UFSoft.U8.U8M.BFDispatcher";
        const string ContextSrv = "UFSoft.U8.Framework.ContextSrv";
        const string DbConn = "UFSoft.U8.Framework.DBConnection";
        static readonly object Gate = new object();
        static Net _net;

        readonly Net _n;
        object _container;
        object _oldToken;
        object _oldInfo;
        object _oldDb;
        object _oldProvider;
        bool _set;

        MoGenU8(Net n)
        {
            _n = n;
        }

        // 必须在 U8Resolve.Enter 之后调用。加载失败（缺 dll、签名不符）抛 503 com_unavailable，这时还没碰 U8。
        public static MoGenU8 Open(string token)
        {
            Net n;
            try
            {
                n = Load();
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                throw MoApi.Failed(ex);
            }
            MoGenU8 u8 = new MoGenU8(n);
            try
            {
                u8.Enter(token);
            }
            catch (Exception ex)
            {
                u8.Dispose();
                Exception inner = ex is TargetInvocationException && ex.InnerException != null ? ex.InnerException : ex;
                throw MoApi.Failed(inner);
            }
            return u8;
        }

        void Enter(string token)
        {
            _oldToken = _n.Token.GetValue(null, null);
            _oldInfo = _n.Info.GetValue(null, null);
            _oldDb = _n.DbType.GetValue(null);
            _oldProvider = _n.Provider.GetValue(null);
            _set = true;
            CloseConnection();
            _n.SetProvider.Invoke(null, new object[] { Activator.CreateInstance(_n.Entrance) });
            _n.SetSingle.Invoke(null, new object[] { true });
            _n.SetLogin.Invoke(null, new object[] { token });
            _container = Activator.CreateInstance(_n.Container);
        }

        public void Dispose()
        {
            if (!_set)
            {
                return;
            }
            _set = false;
            Try(delegate { CloseConnection(); });
            Try(delegate { _n.RemoveTrans.Invoke(_n.Courier.GetValue(null, null), null); });
            Try(delegate { _n.SetProvider.Invoke(null, new object[] { _oldProvider }); });
            Try(delegate { _n.Token.SetValue(null, _oldToken, null); });
            Try(delegate { _n.Info.SetValue(null, _oldInfo, null); });
            Try(delegate { _n.DbType.SetValue(null, _oldDb); });
        }

        // 本线程 ConnectionCourier 缓存的连接（没有时 CloseCurrentConnection 什么也不做）。
        void CloseConnection()
        {
            _n.CloseConn.Invoke(_n.Courier.GetValue(null, null), null);
        }

        static void Try(Action step)
        {
            try
            {
                step();
            }
            catch (Exception)
            {
            }
        }

        // 制单信息与 MO05001._Save 同源（UserContextGetter.U8LoginInfo 的 UserAccountId、UserId、OperDate）。
        public MoGenWho Who()
        {
            object info = _n.LoginInfo.GetValue(null, null);
            if (info == null)
            {
                throw new BridgeException(503, "com_unavailable", "U8 登录上下文不可用");
            }
            Type t = info.GetType();
            MoGenWho who = new MoGenWho();
            who.AccId = Values.Text(Field(t, "UserAccountId").GetValue(info));
            who.UserId = Values.Text(Field(t, "UserId").GetValue(info));
            who.OperDate = (DateTime)Field(t, "OperDate").GetValue(info);
            return who;
        }

        public DateTime[] Range()
        {
            DateTime min = (DateTime)Call(_n.MinDate, new object[] { _container });
            DateTime max = (DateTime)Call(_n.MaxDate, new object[] { _container });
            return new DateTime[] { min, max };
        }

        // 没有符合条件的行时 U8 返回 null（BOMomProcess.GetMOGenBaseData），这里给空表。
        public DataTable List(Hashtable args)
        {
            DataSet ds = Call(_n.Get, new object[] { args, _container }) as DataSet;
            if (ds == null || !ds.Tables.Contains(MoGenArgs.Table))
            {
                return new DataTable(MoGenArgs.Table);
            }
            return ds.Tables[MoGenArgs.Table];
        }

        // 返回每行结果 {Id, Cnt, MoId, MoDId, MoCode}，下标 0.. 按处理顺序（即请求顺序）。
        public Hashtable Save(Hashtable args)
        {
            return Call(_n.Save, new object[] { args, _container }) as Hashtable;
        }

        static object Call(MethodInfo m, object[] args)
        {
            try
            {
                return m.Invoke(null, args);
            }
            catch (TargetInvocationException ex)
            {
                if (ex.InnerException != null)
                {
                    ExceptionDispatchInfo.Capture(ex.InnerException).Throw();
                }
                throw;
            }
        }

        static FieldInfo Field(Type t, string name)
        {
            FieldInfo f = t.GetField(name, BindingFlags.Public | BindingFlags.Instance);
            if (f == null)
            {
                throw new BridgeException(503, "com_unavailable", "U8 登录上下文缺少字段 " + name);
            }
            return f;
        }

        static Net Load()
        {
            lock (Gate)
            {
                if (_net == null)
                {
                    _net = Net.Build();
                }
                return _net;
            }
        }

        // 反射拿到的类型和成员；成功后缓存（进程内 U8 程序集不会换）。
        sealed class Net
        {
            public MethodInfo Get;
            public MethodInfo Save;
            public MethodInfo MinDate;
            public MethodInfo MaxDate;
            public MethodInfo SetProvider;
            public Type Entrance;
            public MethodInfo SetLogin;
            public PropertyInfo Token;
            public PropertyInfo Info;
            public PropertyInfo LoginInfo;
            public Type Container;
            public FieldInfo DbType;
            public MethodInfo SetSingle;
            public FieldInfo Provider;
            public PropertyInfo Courier;
            public MethodInfo CloseConn;
            public MethodInfo RemoveTrans;

            public static Net Build()
            {
                Assembly bf = Asm(Dispatcher);
                Assembly cs = Asm(ContextSrv);
                Assembly db = Asm(DbConn);
                Net n = new Net();
                Type mom = Need(bf, Dispatcher + ".MOMExecutor");
                Type common = Need(bf, Dispatcher + ".U8MCommonExecutor");
                Type factory = Need(bf, Dispatcher + ".BFTarget.TargetProviderFactory");
                n.Get = Proxy(mom, "BFMomProcess_GetMOGenBaseData", 2);
                n.Save = Proxy(mom, "BFBatchProcess_SaveMOGenQtyData", 2);
                n.MinDate = Proxy(common, "BFSystem_GetMinSystemDate", 1);
                n.MaxDate = Proxy(common, "BFSystem_GetMaxSystemDate", 1);
                n.SetProvider = Proxy(factory, "SetCustomBFProvider", 1);
                // 原来的自定义提供者没有公共 getter，读私有 [ThreadStatic] 字段（按当前线程取值）。
                n.Provider = factory.GetField("m_cstBFProvider", BindingFlags.NonPublic | BindingFlags.Static);
                n.Entrance = Need(bf, Dispatcher + ".BFTarget.U8MEntranceHelper");
                Type thread = Need(cs, ContextSrv + ".ThreadContext");
                n.SetLogin = Method(thread, "SetLoginInfo", new Type[] { typeof(string) });
                n.Token = Prop(thread, "ServerToken");
                n.Info = Prop(thread, "U8LoginInfo");
                n.LoginInfo = Prop(Need(cs, ContextSrv + ".UserContextGetter"), "U8LoginInfo");
                n.Container = Need(cs, ContextSrv + ".DefaultContextContainer");
                Type conn = Need(db, DbConn + ".ThreadDBConnInfo");
                n.DbType = conn.GetField("DBConnectionType", BindingFlags.Public | BindingFlags.Static);
                n.SetSingle = Method(conn, "SetSingleDBConnectionType", new Type[] { typeof(bool) });
                Type courier = Need(db, DbConn + ".ConnectionCourier");
                n.Courier = Prop(courier, "Instance");
                n.CloseConn = Instance(courier, "CloseCurrentConnection");
                n.RemoveTrans = Instance(courier, "RemoveCustomTransaction");
                if (n.DbType == null || n.Provider == null)
                {
                    throw Missing(n.DbType == null ? "ThreadDBConnInfo.DBConnectionType" : "TargetProviderFactory.m_cstBFProvider");
                }
                return n;
            }

            // 先用进程里已加载的同名程序集（U8 API 框架可能已从同一目录加载过），否则按短名加载，由 U8Resolve 在 U8 目录里找。
            static Assembly Asm(string name)
            {
                Assembly[] all = AppDomain.CurrentDomain.GetAssemblies();
                for (int i = 0; i < all.Length; i++)
                {
                    if (string.Equals(all[i].GetName().Name, name, StringComparison.OrdinalIgnoreCase))
                    {
                        return all[i];
                    }
                }
                return Assembly.Load(name);
            }

            static Type Need(Assembly asm, string name)
            {
                Type t = asm.GetType(name, false);
                if (t == null)
                {
                    throw Missing(name);
                }
                return t;
            }

            // 代理方法：公共静态、按名字和参数个数找（MOMExecutor 里同名方法没有重载）。
            static MethodInfo Proxy(Type t, string name, int args)
            {
                MethodInfo[] all = t.GetMethods(BindingFlags.Public | BindingFlags.Static);
                MethodInfo hit = null;
                for (int i = 0; i < all.Length; i++)
                {
                    if (all[i].Name == name && all[i].GetParameters().Length == args)
                    {
                        if (hit != null)
                        {
                            throw Missing(t.Name + "." + name + "（有重载）");
                        }
                        hit = all[i];
                    }
                }
                if (hit == null)
                {
                    throw Missing(t.Name + "." + name);
                }
                return hit;
            }

            static MethodInfo Method(Type t, string name, Type[] args)
            {
                MethodInfo m = t.GetMethod(name, BindingFlags.Public | BindingFlags.Static, null, args, null);
                if (m == null)
                {
                    throw Missing(t.Name + "." + name);
                }
                return m;
            }

            static MethodInfo Instance(Type t, string name)
            {
                MethodInfo m = t.GetMethod(name, BindingFlags.Public | BindingFlags.Instance, null, Type.EmptyTypes, null);
                if (m == null)
                {
                    throw Missing(t.Name + "." + name);
                }
                return m;
            }

            static PropertyInfo Prop(Type t, string name)
            {
                PropertyInfo p = t.GetProperty(name, BindingFlags.Public | BindingFlags.Static);
                if (p == null)
                {
                    throw Missing(t.Name + "." + name);
                }
                return p;
            }

            static BridgeException Missing(string what)
            {
                return new BridgeException(503, "com_unavailable", "U8 生产制造组件不可用：缺少 " + what);
            }
        }
    }
}
