using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    internal delegate ApiResult Handler(WorkContext ctx);

    internal sealed class WorkContext : IDisposable
    {
        object _conn;
        public WorkItem Item;
        public U8Session Session;
        public BridgeConfig Config;
        public WfSnap WfBefore;
        public string WfNote;
        // 读线程没有 Session，连接串和操作员姓名来自登录缓存。
        public string ConnString;
        public string AuthName;
        // 操作员权限：读路由由 PermGate 在处理函数之前填好；写路由按需 PermCheck.Of 取。
        public PermContext Perm;
        public PermRule PermRule;
        // 登录复用（LoginCache）：by-ref 把登录交给 U8 组件 Init 的路径调用 DropLogin，本次登录用完即关、不放回缓存。
        public bool KeepLogin = true;

        public void DropLogin()
        {
            KeepLogin = false;
        }

        // 请求连接是否已经打开过（StaExec.KeepAfter 用它避免为查 @@TRANCOUNT 专门开连接）。
        public bool ConnOpened
        {
            get { return _conn != null; }
        }

        // 账套缺省值，一个请求只查一次（AccDefaults）。读的是本请求的 Conn。
        string _home;
        string _purchaseType;

        public string HomeCurrency
        {
            get
            {
                if (_home == null)
                {
                    _home = AccDefaults.Home(Conn);
                }
                return _home;
            }
        }

        // 给了值用给的，空才取本位币（不给就不查库）。
        public string HomeCurrencyOr(string value)
        {
            return value != null && value.Length > 0 ? value : HomeCurrency;
        }

        // 默认采购类型；账套没设时是空串。
        public string DefaultPurchaseType
        {
            get
            {
                if (_purchaseType == null)
                {
                    _purchaseType = AccDefaults.PurchaseType(Conn);
                }
                return _purchaseType;
            }
        }

        // 采购 CO 会话 Init 用的采购类型（PuSession.InitType）：生单、新增时调用方在 Open 之前填来源单据或请求的 cPTCode；
        // 已有单据的读取、修改、删除、审核、关闭经 PuSession.UseStored 填该单存的 cPTCode。
        public string PuInitType;

        // 来源单据的采购类型；来源上为空时取默认采购类型（同 U8 与无来源采购入库），都没有返回空串。
        public string PurchaseTypeOr(string code)
        {
            string pt = code == null ? "" : code.Trim();
            return pt.Length > 0 ? pt : DefaultPurchaseType;
        }

        public object Conn
        {
            get
            {
                if (_conn == null)
                {
                    _conn = AdoXml.Open(ConnText());
                }
                return _conn;
            }
        }

        // 操作员姓名（cUserName）。有登录用登录对象上的，读线程用缓存里的。
        public string OperatorName
        {
            get
            {
                if (Session != null)
                {
                    return Session.OperatorName ?? "";
                }
                return AuthName ?? "";
            }
        }

        public object OpenFresh()
        {
            object conn = AdoXml.Open(ConnText());
            try
            {
                CoTrans.LockWait(conn);
                return conn;
            }
            catch
            {
                AdoXml.Close(conn);
                throw;
            }
        }

        string ConnText()
        {
            if (Session != null)
            {
                return AdoXml.ConnectionString(Session.Login, Config);
            }
            if (ConnString != null && ConnString.Length > 0)
            {
                return ConnString;
            }
            throw new BridgeException(500, "internal", "没有可用的数据库连接");
        }

        public void Dispose()
        {
            object conn = _conn;
            _conn = null;
            AdoXml.Close(conn);
        }

        public void Scrub(ApiResult result)
        {
            try
            {
                string token = TokenText();
                if (token.Length == 0)
                {
                    return;
                }
                if (Item != null && Item.Detail != null)
                {
                    Item.Detail = Item.Detail.Replace(token, "***");
                }
                ScrubResult(result, token);
            }
            catch (Exception)
            {
            }
        }

        public static Exception ScrubEx(WorkContext ctx, Exception ex)
        {
            if (ctx == null || ex == null || ex.Message == null)
            {
                return ex;
            }
            string token = ctx.TokenText();
            if (token.Length == 0 || ex.Message.IndexOf(token, StringComparison.Ordinal) < 0)
            {
                return ex;
            }
            string message = ex.Message.Replace(token, "***");
            BridgeException bridge = ex as BridgeException;
            if (bridge != null)
            {
                return new BridgeException(bridge.Status, bridge.Code, message, bridge.Field, bridge.Hint);
            }
            return new Exception(message);
        }

        string TokenText()
        {
            if (Session == null)
            {
                return "";
            }
            string token = Session.LoadedToken();
            return token ?? "";
        }

        static void ScrubResult(ApiResult result, string token)
        {
            if (result == null)
            {
                return;
            }
            if (result.AuditMessage != null)
            {
                result.AuditMessage = result.AuditMessage.Replace(token, "***");
            }
            ScrubObject(result.Body, token);
        }

        static void ScrubMap(Dictionary<string, object> map, string token)
        {
            string[] keys = new string[map.Count];
            map.Keys.CopyTo(keys, 0);
            for (int i = 0; i < keys.Length; i++)
            {
                string text = map[keys[i]] as string;
                if (text != null)
                {
                    map[keys[i]] = text.Replace(token, "***");
                    continue;
                }
                ScrubObject(map[keys[i]], token);
            }
        }

        static void ScrubObject(object value, string token)
        {
            Dictionary<string, object> map = value as Dictionary<string, object>;
            if (map != null)
            {
                ScrubMap(map, token);
                return;
            }
            IList list = value as IList;
            if (list == null)
            {
                return;
            }
            for (int i = 0; i < list.Count; i++)
            {
                string text = list[i] as string;
                if (text != null)
                {
                    list[i] = text.Replace(token, "***");
                    continue;
                }
                ScrubObject(list[i], token);
            }
        }
    }
}
