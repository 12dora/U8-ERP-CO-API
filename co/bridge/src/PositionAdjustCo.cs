using System.Collections.Generic;

namespace U8Co
{
    // 货位调整单（19）的新增、审核、弃审、删除（PositionAdjust 分派）。同形态转换单：USERPCO.VoucherCO 的 Insert 13 参、
    // Verify / UnVerify / Delete 9 参，都在请求连接的 CoTrans 里（StockCall.RunAt）。审核不生单（9 参），U8 在审核时写货位台账，
    // 提交前由 PositionAdjustCheck 核对审核人、台账行数和结存变动；新增提交前由 PositionAdjustSaved 回读整张单据；删除只删未审核的，有货位记录时照库存删除先 ClearPosition（StockPosGuard）。
    internal static partial class StockCo
    {
        internal static ApiResult AdjustCreate(WorkContext ctx, VoucherKind kind,
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
                domH = StockDom.AdjustHead(ctx.Conn, kind, head, maker, billDate);
                Dictionary<string, object> seeds = HeadSeeds(domH);
                List<Dictionary<string, string>> rows;
                domB = StockDom.AdjustBody(ctx.Conn, kind, lines, out rows);
                // 任何 COM 调用之前：仓库是货位管理、货位是本仓库的末级货位、存货管理方式、调出货位结存够。
                string wh = StockMsg.Col(seeds, "cWhCode");
                List<BinLine> want = PositionAdjustBins.FromDom(rows);
                PositionAdjustBins.CheckNew(ctx.Conn, wh, want);
                pos = Rows.NewDom();
                msg = Rows.NewDom();
                co = StockCall.OpenCo(ctx);
                string code = StockCall.NewCode(ctx, co, kind, seeds);
                StockDom.SetHeadValue(domH, kind.CodeColumn, code);
                int[] refs;
                object[] args = StockCall.InsertArgs(kind, StockCall.Forms(domH, domB, pos, ctx.Conn, msg), out refs);
                StockAt at = StockCall.AtFor("Insert", args, refs, null);
                PositionAdjustSaved saved = new PositionAdjustSaved(ctx, at, wh, code, want);
                at.Before = saved.Before;
                at.After = saved.After;
                StockCall.RunAt(ctx, co, at);
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

        internal static ApiResult AdjustVerify(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            RequireSt(kind);
            if (action != "verify" && action != "unverify")
            {
                throw new BridgeException(400, "bad_request", "action 只能是 verify 或 unverify");
            }
            bool undo = action == "unverify";
            Dictionary<string, object> before = ReadHead(ctx.Conn, kind, id, false, false);
            if (before == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            RefuseBefore(before, action, kind);
            Gate(ctx.Conn, kind, before);
            string ufts = StockCall.Ufts(ctx.Conn, kind, id);
            PositionAdjustCheck check = new PositionAdjustCheck(id, undo, ctx.Session.OperatorName);
            object co = null;
            object msg = null;
            try
            {
                co = StockCall.OpenCo(ctx);
                msg = Rows.NewDom();
                int[] refs;
                object[] args = StockCall.VerifyArgs(kind, id, ctx.Conn, ufts, msg, out refs);
                StockAt at = StockCall.AtFor(undo ? "UnVerify" : "Verify", args, refs, ufts);
                at.Before = check.Before;
                at.After = check.After;
                StockCall.RunAt(ctx, co, at);
                Dictionary<string, object> after = Reread(ctx, kind, id);
                RefuseAfter(after, action, ctx.Session.OperatorName, kind);
                ApiResult result = StockMsg.Verified(ctx.Item, kind, id, action, after, null);
                check.Describe(result.Body);
                return result;
            }
            finally
            {
                ComUtil.Final(msg);
                ComUtil.Final(co);
            }
        }

        internal static ApiResult AdjustDelete(WorkContext ctx, VoucherKind kind, int id)
        {
            RequireSt(kind);
            Dictionary<string, object> before = ReadHead(ctx.Conn, kind, id, false, false);
            if (before == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (StockMsg.Col(before, kind.VerifierColumn).Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核");
            }
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
                // 提交前在本连接上确认表头、表体都已删掉（ClearOnDelete 包在它外面，先清货位、后核对货位台账）。
                at.After = delegate(object conn) { RequireGone(conn, kind, id); };
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

        static void RequireGone(object conn, VoucherKind kind, int id)
        {
            string sql = "select (select count(*) from AdjustPVouch where Id=?) + (select count(*) from AdjustPVouchs where ID=?) as n";
            Dictionary<string, object> row = Rows.One(conn, sql, new object[] { id, id });
            if (row == null || CoRows.AsId(CoRows.Col(row, "n")) != 0)
            {
                throw new BridgeException(409, "u8_rejected", kind.Title + "删除后表头或表体仍在，已回滚");
            }
        }
    }
}
