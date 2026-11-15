using System;

namespace U8Co
{
    // 不良品处理单（QM05 / QM06）的 UFQMCo 组件，登录子系统 QM。实测（测试账套，见 docs/u8-notes.md「不良品处理单」）：
    // Init() 无参数；属性 bOutAuth、LoadTemp 设 True，bOutTrans 设 False（U8 自己开、自己提交保存 / 审核 / 弃审 / 删除的事务，
    // 多条语句的保存不会半截落库；设 True 时没有人包这个事务。False 时四个动作测试账套均已实测）；GetVtidList(login, False) 引用 {0,1}，返回的记录集
    // 在用完组件前不放（不要赋给 VO.TemplateData，报 91）；VO 用 UFQMVOCom.clsVoucherVO，GetVTID(login, VT, 0, vo, False) 引用 {0,2,3}。
    // 新增：InitByXml(空白表头, 空白表体) → AddNewVoucher(login, vo, "") {0,1} → AddVoucherByRef(conn, vo, 检验单记录集) {0,1,2}
    // → 改 domHead / domBody 后再 InitByXml → AddVoucher(login, vo) {0,1}。审核、弃审、删除：GetTheVoucher(login, vo, id) {0,1}
    // 之后 AuditVoucher / UnAuditVoucher / DelVoucher(login, vo) {0,1}。保存、审核、弃审、删除都在 U8 自己的连接上提交，
    // 请求连接的事务回滚不了。U8 的拒绝在 COM 异常的 ErrBagList 里（QmCo.BagText）。
    // 其他报检单、其他检验单（QmOthSpec）用同一族 VO 接口：类型库里两个组件都没有 Init，其他报检单组件也没有
    // bOutAuth / bOutTrans（只有 LoadTemp），由 Open 的 init / outFlags 开关控制。
    internal sealed class QmRejCo : IDisposable
    {
        const string VoProgId = "UFQMVOCom.clsVoucherVO";
        const int AdUseClient = 3;
        const int AdOpenStatic = 3;
        const int AdLockBatchOptimistic = 4;
        const string Missing = "指定单据不存在";

        readonly WorkContext _ctx;
        object _co;
        object _vo;
        object _vt;

        QmRejCo(WorkContext ctx)
        {
            _ctx = ctx;
        }

        public object Vo
        {
            get { return _vo; }
        }

        public static QmRejCo Open(WorkContext ctx, QmRejSpec spec)
        {
            return Open(ctx, spec.ProgId, spec.Vt, true, true);
        }

        // init：调 Init()；outFlags：设 bOutAuth=True、bOutTrans=False。LoadTemp 总是设 True。
        public static QmRejCo Open(WorkContext ctx, string progId, int vtId, bool init, bool outFlags)
        {
            if (ctx == null || ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "没有 U8 登录");
            }
            QmRejCo made = new QmRejCo(ctx);
            try
            {
                made._co = Need(ComUtil.Create(progId), progId);
                // GetVtidList、GetVTID 及之后的调用都把登录按引用交给组件：本次登录不放回缓存。
                ctx.DropLogin();
                made.Prime(init, outFlags);
                object[] args = new object[] { ctx.Session.Login, false };
                made._vt = ComUtil.CallRef(made._co, "GetVtidList", args, new int[] { 0, 1 });
                made.LoginBack(args[0]);
                made._vo = Need(ComUtil.Create(VoProgId), VoProgId);
                object[] vt = new object[] { ctx.Session.Login, vtId, 0, made._vo, false };
                ComUtil.CallRef(made._co, "GetVTID", vt, new int[] { 0, 2, 3 });
                made.LoginBack(vt[0]);
                made.VoBack(vt[3]);
                return made;
            }
            catch
            {
                made.Dispose();
                throw;
            }
        }

        void Prime(bool init, bool outFlags)
        {
            if (init)
            {
                ComUtil.Call(_co, "Init", new object[0]);
            }
            if (outFlags)
            {
                ComUtil.Set(_co, "bOutAuth", true);
                ComUtil.Set(_co, "bOutTrans", false);
            }
            ComUtil.Set(_co, "LoadTemp", true);
        }

        static object Need(object com, string progId)
        {
            if (com == null)
            {
                throw new BridgeException(503, "com_unavailable", "组件无法创建 " + progId);
            }
            return com;
        }

        // 把表头、表体 DOM 交给 VO（空白模板，或改过的 domHead / domBody）。
        public void Push(object head, object body)
        {
            ComUtil.Call(_vo, "InitByXml", new object[] { head, body });
        }

        public object Head()
        {
            return ComUtil.Get(_vo, "domHead");
        }

        public object Body()
        {
            return ComUtil.Get(_vo, "domBody");
        }

        public int RowCount()
        {
            return CoRows.AsId(Values.Text(ComUtil.Get(_vo, "RowCount")));
        }

        // 生单前两步：AddNewVoucher 盖上制单日期、制单人、单据类型；AddVoucherByRef 按检验单记录集带入表头。
        // 这两步不写库，U8 拒绝时 409 u8_rejected。
        public void FromCheck(string viewSql, int checkId, string checkType)
        {
            AddNew();
            FromSource(viewSql, new object[] { checkId, checkType }, "检验单不可参照（已关闭或不存在）");
        }

        // AddNewVoucher(login, vo, "")：盖上制单日期、制单人、单据类型，不写库。
        public void AddNew()
        {
            Refuse(Run("AddNewVoucher", new object[] { _ctx.Session.Login, _vo, "" }, 0));
        }

        // AddVoucherByRef(conn, vo, rs)：按来源记录集带入，不写库。args 只收 int（adInteger）和 string（adVarWChar）；
        // 记录集没有行时 409 state_mismatch（missing 是消息）。
        public void FromSource(string sql, object[] args, string missing)
        {
            object rs = null;
            object cmd = null;
            try
            {
                rs = OpenSource(sql, args, missing, out cmd);
                Refuse(Run("AddVoucherByRef", new object[] { _ctx.Conn, _vo, rs }, -1));
            }
            finally
            {
                CloseRs(rs);
                ComUtil.Final(cmd);
            }
        }

        // 自己提交的调用：结果交给调用方回读确认。
        public QmOutcome Save()
        {
            return Run("AddVoucher", new object[] { _ctx.Session.Login, _vo }, 0);
        }

        // 按 ID 载入已有单据（审核、弃审、删除之前）。U8「指定单据不存在或没有该单据数据权限」→ 404。
        public void Load(int id)
        {
            QmOutcome o = Run("GetTheVoucher", new object[] { _ctx.Session.Login, _vo, id }, 0);
            if (o.Error.IndexOf(Missing, StringComparison.Ordinal) >= 0)
            {
                throw new BridgeException(404, "not_found", o.Error);
            }
            Refuse(o);
        }

        // AuditVoucher / UnAuditVoucher / DelVoucher，自己提交。
        public QmOutcome Act(string method)
        {
            return Run(method, new object[] { _ctx.Session.Login, _vo }, 0);
        }

        // 调用一次：ErrBag 记为 Error，其他 COM 异常记为 Lost，返回 False 记为 Error「U8 返回失败」。
        // loginAt 是登录槽下标（-1 表示没有）；登录、VO 两个槽都按引用。
        QmOutcome Run(string method, object[] args, int loginAt)
        {
            QmOutcome outcome = new QmOutcome();
            int[] refs = loginAt < 0 ? new int[] { 0, 1, 2 } : new int[] { 0, 1 };
            try
            {
                object ret = ComUtil.CallRef(_co, method, args, refs);
                if (loginAt >= 0)
                {
                    LoginBack(args[loginAt]);
                }
                VoBack(args[1]);
                if (ret is bool && !(bool)ret)
                {
                    outcome.Error = "U8 返回失败（" + method + "）";
                }
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                string bag = QmCo.BagText(ex.Message);
                if (bag.Length > 0)
                {
                    outcome.Error = bag;
                }
                else
                {
                    outcome.Lost = ex;
                }
            }
            if (!outcome.Ok)
            {
                CoRows.Note(_ctx.Item, method + " " + QmGen.Text(outcome));
            }
            return outcome;
        }

        // 不写库的调用失败：U8 原文 409；其他异常照常抛（500）。
        static void Refuse(QmOutcome outcome)
        {
            if (outcome.Lost != null)
            {
                throw outcome.Lost;
            }
            if (outcome.Error.Length > 0)
            {
                throw new BridgeException(409, "u8_rejected", outcome.Error);
            }
        }

        // 来源记录集：客户端游标（U8 参照生单同样用 adUseClient、adLockBatchOptimistic），参数化，条件与 U8 参照相同（未关闭）。
        object OpenSource(string sql, object[] args, string missing, out object cmd)
        {
            cmd = Need(ComUtil.Create("ADODB.Command"), "ADODB.Command");
            ComUtil.Set(cmd, "ActiveConnection", _ctx.Conn);
            ComUtil.Set(cmd, "CommandText", sql);
            ComUtil.Set(cmd, "CommandTimeout", 45);
            for (int i = 0; i < args.Length; i++)
            {
                string text = args[i] as string;
                if (text != null)
                {
                    Param(cmd, text, 202, Math.Max(20, text.Length));
                }
                else
                {
                    Param(cmd, args[i], 3, 4);
                }
            }
            object rs = Need(ComUtil.Create("ADODB.Recordset"), "ADODB.Recordset");
            try
            {
                ComUtil.Set(rs, "CursorLocation", AdUseClient);
                ComUtil.Call(rs, "Open", new object[] { cmd, Type.Missing, AdOpenStatic, AdLockBatchOptimistic, -1 });
                if (CoRows.AsId(Values.Text(ComUtil.Get(rs, "RecordCount"))) < 1)
                {
                    throw new BridgeException(409, "state_mismatch", missing);
                }
                return rs;
            }
            catch
            {
                CloseRs(rs);
                throw;
            }
        }

        static void Param(object cmd, object value, int adoType, int size)
        {
            object param = ComUtil.Call(cmd, "CreateParameter", new object[] { "", adoType, 1, size, value });
            object list = null;
            try
            {
                list = ComUtil.Get(cmd, "Parameters");
                ComUtil.Call(list, "Append", new object[] { param });
            }
            finally
            {
                ComUtil.ReleaseOne(list);
                ComUtil.ReleaseOne(param);
            }
        }

        static void CloseRs(object rs)
        {
            if (rs == null)
            {
                return;
            }
            try
            {
                if (CoRows.AsId(Values.Text(ComUtil.Get(rs, "State"))) != 0)
                {
                    ComUtil.Call(rs, "Close", new object[0]);
                }
            }
            catch (Exception)
            {
            }
            ComUtil.Final(rs);
        }

        void LoginBack(object returned)
        {
            CoRows.LoginBack(_ctx, _ctx.Session.Login, returned);
        }

        void VoBack(object returned)
        {
            if (returned == null || returned is DBNull || object.ReferenceEquals(returned, _vo))
            {
                return;
            }
            ComUtil.Final(_vo);
            _vo = returned;
        }

        public void Dispose()
        {
            object vo = _vo;
            object vt = _vt;
            object co = _co;
            _vo = null;
            _vt = null;
            _co = null;
            ComUtil.Final(vo);
            CloseRs(vt);
            ComUtil.Final(co);
        }
    }
}
