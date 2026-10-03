using System;

namespace U8Co
{
    // 采购 Init 的推断参数只在 Bind 里赋值。登录子系统用 VoucherKind.SubId（PU）。
    internal static class PuSession
    {
        public static void Open(WorkContext ctx, int voucherType, bool verify, out object info, out object co)
        {
            info = ComUtil.Create("Info_PU.ClsS_Infor");
            co = ComUtil.Create("VoucherCO_PU.clsVoucherCO_PU");
            if (info == null || co == null)
            {
                throw new BridgeException(503, "com_unavailable", "U8 组件未注册");
            }
            Bind(ctx, info, co, voucherType, verify);
        }

        static void Bind(WorkContext ctx, object info, object co, int voucherType, bool verify)
        {
            string bus = "普通采购";
            string pt = InitType(ctx);
            bool positive = true;
            string bill = "";
            int mode = 0;
            object login = ctx.Session.Login;
            object[] infoArgs = new object[] { login, bus, pt };
            object infoRet = ComUtil.CallRef(info, "Init", infoArgs, new int[] { 0 });
            CoRows.LoginBack(ctx, login, infoArgs[0]);
            string infoMsg = Values.Text(infoRet).Trim();
            CoRows.Note(ctx.Item, "ClsS_Infor.Init " + infoMsg);
            if (infoMsg.Length > 0)
            {
                throw new BridgeException(409, "u8_rejected", infoMsg);
            }
            object[] initArgs = new object[] { voucherType, login, ctx.Conn, info, positive, bill, bus, mode, "", pt };
            if (!TryInit(ctx, co, initArgs))
            {
                Fallback(ctx, co, info, initArgs);
            }
            ComUtil.Set(co, "bOutTrans", true);
            // 审核/弃审才开审核模式。Load 不调用。Init 的其余实参保持现场已验证的值。
            if (verify)
            {
                ComUtil.Call(co, "SetVerifyMode", new object[] { true });
                CoRows.Note(ctx.Item, "SetVerifyMode 1 vt=" + voucherType.ToString());
            }
        }

        // Info_PU.Init / VoucherCO_PU.Init 的最后一个实参与 U8 客户端一致（实测），是采购类型编码（原来写死某账套的 "CG"）。
        // 取 ctx.PuInitType（来源单据或请求的 cPTCode），没有取账套默认采购类型，都没有传空串。
        internal static string InitType(WorkContext ctx)
        {
            return ctx.PurchaseTypeOr(ctx.PuInitType);
        }

        // 已有单据（采购订单、到货单 / 采购退货单、采购发票）在 Open 之前调用：PuInitType 取该单表头存的 cPTCode，
        // 为空时 InitType 照旧退回账套默认。请购单没有采购类型，不查、保持账套默认。
        internal static void UseStored(WorkContext ctx, VoucherKind kind, int id)
        {
            if (kind == null || kind.Name == "purchase_requisition")
            {
                return;
            }
            string sql = "select convert(nvarchar(30), cPTCode) from " + CoRows.Ident(kind.HeadTable)
                + " where " + CoRows.Ident(kind.IdColumn) + "=?";
            string pt = Rows.Scalar(ctx.Conn, sql, new object[] { id });
            ctx.PuInitType = pt == null ? "" : pt.Trim();
        }

        static bool TryInit(WorkContext ctx, object co, object[] args)
        {
            object login = ctx.Session.Login;
            object conn = ctx.Conn;
            object info = args[3];
            try
            {
                ComUtil.CallRef(co, "Init", args, new int[] { 1, 2, 3, 4, 5, 6, 7 });
            }
            catch (Exception ex)
            {
                // Init 失败要退回 InitBySysInfo：登录状态不明，本次登录不放回缓存。
                ctx.DropLogin();
                CoRows.Note(ctx.Item, "Init " + ex.Message);
                NoteSwap(ctx, args);
                ReleaseRefs(ctx, login, conn, info, args);
                return false;
            }
            NoteSwap(ctx, args);
            ReleaseRefs(ctx, login, conn, info, args);
            CoRows.Note(ctx.Item, "Init bill=" + Values.Text(args[5]) + " bus=" + Values.Text(args[6]));
            return true;
        }

        static void ReleaseRefs(WorkContext ctx, object login, object conn, object info, object[] args)
        {
            if (args[3] != null && !object.ReferenceEquals(info, args[3]))
            {
                CoRows.Note(ctx.Item, "Init 更换了 info");
            }
            Restore(login, args, 1);
            Restore(conn, args, 2);
            Restore(info, args, 3);
        }

        // Init 失败时 ByRef 槽里可能是已释放的替换对象。放回原登录和连接再交给 InitBySysInfo。
        static void Restore(object original, object[] args, int index)
        {
            CoRows.ReleaseIfNew(original, args[index]);
            args[index] = original;
        }

        static void NoteSwap(WorkContext ctx, object[] args)
        {
            // U8 在 by-ref 登录槽里换了对象：原登录可能已被改动，本次登录不放回缓存（LoginCache）。
            if (args[1] != null && !(args[1] is DBNull) && !object.ReferenceEquals(ctx.Session.Login, args[1]))
            {
                ctx.DropLogin();
            }
            if (args[2] != null && !object.ReferenceEquals(ctx.Conn, args[2]))
            {
                CoRows.Note(ctx.Item, "Init 更换了连接");
            }
        }

        static void Fallback(WorkContext ctx, object co, object info, object[] opts)
        {
            object sysInfo = ComUtil.Get(info, "Information");
            object[] alt = new object[] { opts[0], sysInfo, opts[1], opts[2], opts[4], opts[5], opts[6], opts[7] };
            try
            {
                ComUtil.CallRef(co, "InitBySysInfo", alt, new int[] { 1, 2, 3, 4, 5, 6, 7 });
                CoRows.ReleaseIfNew(sysInfo, alt[1]);
                CoRows.ReleaseIfNew(opts[1], alt[2]);
                CoRows.ReleaseIfNew(ctx.Conn, alt[3]);
                CoRows.Note(ctx.Item, "InitBySysInfo bill=" + Values.Text(alt[5]) + " bus=" + Values.Text(alt[6]));
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "InitBySysInfo " + ex.Message);
                CoRows.ReleaseIfNew(sysInfo, alt[1]);
                CoRows.ReleaseIfNew(opts[1], alt[2]);
                CoRows.ReleaseIfNew(ctx.Conn, alt[3]);
                throw;
            }
            finally
            {
                ComUtil.ReleaseOne(sysInfo);
            }
        }
    }
}
