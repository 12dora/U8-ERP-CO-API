using System;

namespace U8Co
{
    // UFAPBO：先 clsAccount_AP.Init(login, 子系统)，返回 false 也能用。收付款单 clsCloseBill，应收/应付单 clsAPVouch，
    // 都在请求连接 ctx.Conn 上 Init。by-ref 下标按实测结果。
    internal sealed class ArapBo : IDisposable
    {
        object _acc;
        object _bo;
        ArapSpec _spec;

        public static ArapBo Open(WorkContext ctx, ArapSpec spec)
        {
            if (ctx == null || ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "缺少 U8 登录");
            }
            ArapBo bo = new ArapBo();
            bo._spec = spec;
            ctx.DropLogin();
            try
            {
                bo.Init(ctx.Session.Login, ctx.Conn);
                return bo;
            }
            catch
            {
                bo.Dispose();
                throw;
            }
        }

        void Init(object login, object conn)
        {
            _acc = Make("UFAPBO.clsAccount_AP");
            ComUtil.Call(_acc, "Init", new object[] { login, _spec.Flag });
            if (_spec.Close)
            {
                _bo = Make("UFAPBO.clsCloseBill");
                ComUtil.CallRef(_bo, "Init", new object[] { login, conn, _acc, _spec.Flag }, new int[] { 0, 1, 2, 3 });
                return;
            }
            _bo = Make("UFAPBO.clsAPVouch");
            ComUtil.CallRef(_bo, "Init", new object[] { login, conn, _acc }, new int[] { 0, 1, 2 });
        }

        static object Make(string progId)
        {
            object made = ComUtil.Create(progId);
            if (made == null)
            {
                throw new BridgeException(503, "com_unavailable", "组件无法创建 " + progId);
            }
            return made;
        }

        // GetVouchData(cond, h, b, vt, err) by-ref {1,2,3,4}。U8 换了 DOM 就放掉旧的。
        public bool GetData(string cond, object[] doms, out string err)
        {
            object[] args = new object[] { cond, doms[0], doms[1], 0, "" };
            object ret = ComUtil.CallRef(_bo, "GetVouchData", args, new int[] { 1, 2, 3, 4 });
            CoRows.Swap(doms, 0, args[1]);
            CoRows.Swap(doms, 1, args[2]);
            err = Values.Text(args[4]).Trim();
            return Values.Flag(ret);
        }

        public bool Save(object[] doms, out string err)
        {
            return Save(doms, true, out err);
        }

        // clsCloseBill.SaveVouch by-ref {0,1,2}；clsAPVouch 的两个 DOM 按值传，只有 err 是 {2}。
        // IsAdd=false 是修改（收款单已在测试账套实测：GetVouchData 后改金额，不写往来明细，留在请求事务里）。
        public bool Save(object[] doms, bool isAdd, out string err)
        {
            object[] args = new object[] { doms[0], doms[1], "", isAdd };
            int[] refs = _spec.Close ? new int[] { 0, 1, 2 } : new int[] { 2 };
            object ret = ComUtil.CallRef(_bo, "SaveVouch", args, refs);
            if (_spec.Close)
            {
                CoRows.Swap(doms, 0, args[0]);
                CoRows.Swap(doms, 1, args[1]);
            }
            err = Values.Text(args[2]).Trim();
            return Values.Flag(ret);
        }

        // Sign / CancelSign / DeleteVouch(cond, msg) by-ref {1}。DeleteVouch 尾部可选的 Ufts 不传。
        public bool Run(string method, string cond, out string msg)
        {
            object[] args = new object[] { cond, "" };
            object ret = ComUtil.CallRef(_bo, method, args, new int[] { 1 });
            msg = Values.Text(args[1]).Trim();
            return Values.Flag(ret);
        }

        public void Dispose()
        {
            object bo = _bo;
            object acc = _acc;
            _bo = null;
            _acc = null;
            ComUtil.Final(bo);
            ComUtil.Final(acc);
        }
    }
}
