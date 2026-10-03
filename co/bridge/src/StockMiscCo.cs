using System.Collections.Generic;

namespace U8Co
{
    // 形态转换单（15）、调拨申请单（62）、盘点单（18）的新增、审核、弃审、删除（StockMisc 分派）。
    // 同调拨单：USERPCO.VoucherCO 的 Insert 13 参、Delete / UnVerify 9 参，都在请求连接的 CoTrans 里（StockCall.RunAt）。
    // 审核：15、18 生成其他出入库单，走调拨同款 12 参 Verify + Scripting.Dictionary（15 已在测试账套核对）；62 不生单，9 参。
    internal static partial class StockCo
    {
        internal static ApiResult MiscCreate(WorkContext ctx, VoucherKind kind,
            Dictionary<string, object> head, object[] lines)
        {
            RequireSt(kind);
            object co = null;
            object domH = null;
            object domB = null;
            object pos = null;
            object msg = null;
            try
            {
                string maker = ctx.Session.OperatorName == null ? "" : ctx.Session.OperatorName.Trim();
                string billDate = ctx.Item == null || ctx.Item.Date == null ? "" : ctx.Item.Date.Trim();
                domH = StockDom.MiscHead(ctx.Conn, kind, head, maker, billDate);
                Dictionary<string, object> seeds = HeadSeeds(domH);
                string wh = StockMsg.Col(seeds, "cWhCode");
                if (kind.StType == "18")
                {
                    StockDom.RefuseOpenCheck(ctx.Conn, wh);
                }
                domB = StockDom.MiscBody(ctx.Conn, kind, lines, wh);
                pos = Rows.NewDom();
                msg = Rows.NewDom();
                co = StockCall.OpenCo(ctx);
                string code = StockCall.NewCode(ctx, co, kind, seeds);
                StockDom.SetHeadValue(domH, kind.CodeColumn, code);
                int[] refs;
                object[] args = StockCall.InsertArgs(kind, StockCall.Forms(domH, domB, pos, ctx.Conn, msg), out refs);
                StockCall.RunCo(ctx, co, "Insert", args, refs, null);
                return Inserted(ctx, kind, code, args[6]);
            }
            finally
            {
                ComUtil.Final(msg);
                ComUtil.Final(pos);
                ComUtil.Final(domB);
                ComUtil.Final(domH);
                ComUtil.Final(co);
            }
        }

        internal static ApiResult MiscVerify(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            RequireSt(kind);
            // 盘点单：登录前 Requests.GuardVerify 已拒绝，这里再挡一次，任何 COM 调用之前（StockMisc.NoCheckVerify）。
            if (kind.StType == "18")
            {
                throw new BridgeException(400, "bad_request", StockMisc.NoCheckVerify);
            }
            if (action != "verify" && action != "unverify")
            {
                throw new BridgeException(400, "bad_request", "action 只能是 verify 或 unverify");
            }
            bool undo = action == "unverify";
            Dictionary<string, object> before = StockMisc.Head(ctx.Conn, kind, id);
            if (before == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            RefuseBefore(before, action, kind);
            if (undo)
            {
                StockMisc.RefuseUndo(ctx.Conn, kind, before);
            }
            Gate(ctx.Conn, kind, before);
            string ufts = StockCall.Ufts(ctx.Conn, kind, id);
            if (!undo && kind.StType != "62")
            {
                return MiscMake(ctx, kind, id, ufts);
            }
            return MiscPlain(ctx, kind, id, action, ufts);
        }

        // 12 参 Verify：MakeWheres 槽传 DispatchWrapper(null)，字典的键是生成的 09/08 主键（同调拨）。
        static ApiResult MiscMake(WorkContext ctx, VoucherKind kind, int id, string ufts)
        {
            object co = null;
            object msg = null;
            object dict = null;
            object made = null;
            object wheres = null;
            try
            {
                co = StockCall.OpenCo(ctx);
                msg = Rows.NewDom();
                int[] refs;
                object[] args = StockCall.TransferVerifyArgs(kind, id, ctx.Conn, ufts, msg, out refs);
                dict = args[11];
                wheres = args[9];
                StockCall.RunCo(ctx, co, "Verify", args, refs, ufts);
                made = args[11];
                wheres = args[9];
                List<Dictionary<string, object>> generated = ReadGenerated(ctx, made == null ? dict : made);
                return MiscDone(ctx, kind, id, "verify", generated);
            }
            finally
            {
                ReleaseSlot(wheres, ctx.Conn);
                if (made != null && !object.ReferenceEquals(made, dict))
                {
                    ComUtil.Final(made);
                }
                ComUtil.Final(dict);
                ComUtil.Final(msg);
                ComUtil.Final(co);
            }
        }

        static ApiResult MiscPlain(WorkContext ctx, VoucherKind kind, int id, string action, string ufts)
        {
            object co = null;
            object msg = null;
            try
            {
                co = StockCall.OpenCo(ctx);
                msg = Rows.NewDom();
                int[] refs;
                object[] args = StockCall.VerifyArgs(kind, id, ctx.Conn, ufts, msg, out refs);
                StockCall.RunCo(ctx, co, action == "verify" ? "Verify" : "UnVerify", args, refs, ufts);
                return MiscDone(ctx, kind, id, action, null);
            }
            finally
            {
                ComUtil.Final(msg);
                ComUtil.Final(co);
            }
        }

        // 提交后在新连接上回读审核人；读不到按 RefuseAfter 报 409（同 StockCo.Verify）。
        static ApiResult MiscDone(WorkContext ctx, VoucherKind kind, int id, string action,
            List<Dictionary<string, object>> generated)
        {
            Dictionary<string, object> after = Reread(ctx, kind, id);
            RefuseAfter(after, action, ctx.Session.OperatorName, kind);
            return StockMsg.Verified(ctx.Item, kind, id, action, after, generated);
        }

        // 只删未审核的；调拨申请单另要未关闭、没有生成调拨单。有货位记录时同 StockCo.Delete 先 ClearPosition。
        internal static ApiResult MiscDelete(WorkContext ctx, VoucherKind kind, int id)
        {
            RequireSt(kind);
            Dictionary<string, object> before = StockMisc.Head(ctx.Conn, kind, id);
            if (before == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (StockMsg.Col(before, kind.VerifierColumn).Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核");
            }
            StockMisc.RefuseEdit(kind, before);
            string ufts = StockCall.Ufts(ctx.Conn, kind, id);
            object co = null;
            object msg = null;
            try
            {
                co = StockCall.OpenCo(ctx);
                msg = Rows.NewDom();
                int[] refs;
                object[] args = StockCall.VerifyArgs(kind, id, ctx.Conn, ufts, msg, out refs);
                StockAt at = StockCall.AtFor("Delete", args, refs, ufts);
                StockPosGuard.ClearOnDelete(ctx, co, at, kind, id);
                StockCall.RunAt(ctx, co, at);
                if (Reread(ctx, kind, id) != null)
                {
                    throw new BridgeException(409, "state_mismatch", "U8 返回成功但回读状态不符");
                }
                return StockMsg.Gone(kind, id);
            }
            finally
            {
                ComUtil.Final(msg);
                ComUtil.Final(co);
            }
        }
    }
}
