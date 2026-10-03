using System;

namespace U8Co
{
    // 销售订单和发货单共用的 SA 登录与读单。审核也走这里。
    internal static class SaSession
    {
        public static void OpenSa(object conn, object login, int vt, out object sys, out object co)
        {
            sys = ComUtil.Create("USSAServer.clsSystem");
            co = ComUtil.Create("VoucherCO_Sa.ClsVoucherCO_SA");
            if (sys == null || co == null)
            {
                throw new BridgeException(503, "com_unavailable", "U8 组件未注册");
            }
            // bManualTrans 写在 VoucherCO.Init 之后，和销售订单审核同一顺序。
            ComUtil.Call(sys, "Init", new object[] { login });
            ComUtil.Call(sys, "INIMySAInfor", new object[0]);
            ComUtil.Call(co, "Init", new object[] { vt, login, conn, "CS", sys });
            ComUtil.Set(sys, "bManualTrans", true);
        }

        public static void CloseSa(object sys)
        {
            if (sys == null)
            {
                return;
            }
            try
            {
                ComUtil.Call(sys, "CloseSys", new object[0]);
            }
            catch (Exception)
            {
            }
        }

        public static int SaVt(VoucherKind kind)
        {
            if (kind == null)
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持");
            }
            if (kind.SaVt != 0)
            {
                return kind.SaVt;
            }
            if (kind.Name == "dispatch")
            {
                return 9;
            }
            if (kind.Name == "sale_order")
            {
                return 12;
            }
            throw new BridgeException(400, "bad_request", "该单据类型不支持");
        }

        public static void Release(object sys, object co, object[] doms)
        {
            ComUtil.Final(doms[1]);
            ComUtil.Final(doms[0]);
            ComUtil.Final(co);
            CloseSa(sys);
            ComUtil.Final(sys);
        }

        public static string ReadSa(object co, object[] doms, int id, WorkItem item)
        {
            object[] args = new object[] { doms[0], doms[1], id, true, "", (short)0 };
            object ret = ComUtil.CallRef(co, "GetVoucherData", args, new int[] { 0, 1, 2, 3, 4, 5 });
            CoRows.Swap(doms, 0, args[0]);
            CoRows.Swap(doms, 1, args[1]);
            string msg = ret == null ? "" : Convert.ToString(ret);
            CoRows.Note(item, "GetVoucherData " + (msg.Length == 0 ? "ok" : msg));
            return msg;
        }
    }
}
