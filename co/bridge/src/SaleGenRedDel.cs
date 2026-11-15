using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 红字销售发票的复核、弃复、删除。只放行参照退货单生成的红字发票：表头 iDisp=1、没有发货单反指本发票
    // （DispatchList.SBVID，先开票），且每一行都指向退货单（bReturnFlag=1、cVouchType 05）的行。其余红字发票
    // （先开票、蓝字发货单上的 0 数量红票）400「仅支持蓝字销售发票和参照退货单生成的红字销售发票」；红字发票的修改一律照旧拒绝（SaleEditMoreGate）。
    // 删除：VT 1 / 3 读入、每行 editprop=D、CoTrans 里带 UPDLOCK 记退货行 iSettleQuantity 与订单行 iKPQuantity 基准，
    // CO Delete 之后同一事务核对二者按发票数量退回（退货行 +qty 回到 0 方向，订单行 +qty），不符 409 回滚。
    // 红冲蓝字发票生成的红字发票（每一行 iSBVID 都指向蓝字发票行，SaleGenBlueRed）同样放行复核、弃复、删除；
    // 其回写未实测，删除只核对单据已删除，不核对退货行 / 订单行累计；蓝字发票所挂发货行与订单行累计的前后值同生单记审计。
    // 分类见 RedShape：形如参照退货单生成的红字发票按退货单处理（核对回写），不走红冲蓝字的删除。
    internal static partial class SaleGen
    {
        const string RInvShapeSql = "select isnull(h.iDisp,0) as disp, "
            + "(select count(*) from DispatchList d where d.SBVID=h.SBVID) as back, "
            + "(select count(*) from SaleBillVouchs b where b.SBVID=h.SBVID) as n, "
            + "(select count(*) from SaleBillVouchs b inner join DispatchLists l on l.iDLsID=b.iDLsID "
            + "inner join DispatchList d on d.DLID=l.DLID where b.SBVID=h.SBVID and d.bReturnFlag=1 and d.cVouchType='05') as r, "
            + "(select count(*) from SaleBillVouchs b inner join SaleBillVouchs s on s.AutoID=b.iSBVID "
            + "inner join SaleBillVouch sh on sh.SBVID=s.SBVID where b.SBVID=h.SBVID and isnull(sh.bReturnFlag,0)=0) as blue "
            + "from SaleBillVouch h where h.SBVID=?";
        // 同蓝字 SaleEdit.StaleCheckSql，退货行的 fVeriBillQty 与发票数量都是负数，按绝对值比。
        const string RInvStaleSql = "select top 1 convert(varchar(20), l.iDLsID) from DispatchLists l "
            + "where l.iDLsID in (select s.iDLsID from SaleBillVouchs s where s.SBVID=? and isnull(s.iDLsID,0)<>0) "
            + "and abs(isnull(l.fVeriBillQty,0)) > abs(isnull((select sum(case when isnull(d2.iTB,0)=0 "
            + "then isnull(s2.iQuantity,0) else isnull(s2.TBQuantity,0) end) from SaleBillVouchs s2 "
            + "inner join SaleBillVouch h2 on h2.SBVID=s2.SBVID inner join DispatchLists d2 on d2.iDLsID=s2.iDLsID "
            + "where s2.iDLsID=l.iDLsID and isnull(h2.cChecker,N'')<>N''),0)) + 0.000001";
        // 红字发票行 iSBVID 指向的蓝字发票（红冲蓝字只参照一张）。
        const string RedBlueIdSql = "select top 1 convert(varchar(20), s.SBVID) from SaleBillVouchs b "
            + "inner join SaleBillVouchs s on s.AutoID=b.iSBVID where b.SBVID=? and isnull(b.iSBVID,0)<>0";
        // 订单行只认退货行的 iSOsID，同生单（退货行为空、发票行挂了订单行的红票不核对）。
        const string RInvDelLinesSql = "select b.iDLsID, l.iSOsID as iSOsID, "
            + "convert(varchar(40), -isnull(b.iQuantity,0)) as qty from SaleBillVouchs b "
            + "left join DispatchLists l on l.iDLsID=b.iDLsID where b.SBVID=?";

        // 被红冲的蓝字发票：有红字发票行的 iSBVID 指向它的行（SaleGenBlueRed 写的关联）。
        const string BlueRedLinkSql = "select top 1 convert(varchar(20), r.AutoID) from SaleBillVouchs r "
            + "inner join SaleBillVouch rh on rh.SBVID=r.SBVID where isnull(rh.bReturnFlag,0)=1 "
            + "and r.iSBVID in (select b.AutoID from SaleBillVouchs b where b.SBVID=?)";

        // 蓝字发票的弃复、修改、删除用：已被红冲的 409，免得红字发票指向的蓝字行被改掉或删掉。
        internal static void RefuseRedLinked(object conn, int blueId)
        {
            if (Rows.Scalar(conn, BlueRedLinkSql, new object[] { blueId }) != null)
            {
                throw new BridgeException(409, "state_mismatch", "蓝字发票已被红冲，请先删除红字发票");
            }
        }

        // SaleInvoice.Verify 用：红字发票过闸后，复核 / 弃复的 VT 由蓝字的 0 / 2 换成红字的 1 / 3。
        internal static int RedVerifyVt(object conn, int id, int blueVt)
        {
            RequireRedFromReturn(conn, id);
            return blueVt + 1;
        }

        // SaleEdit.DeleteInvoice 用：审批流、复核、应收审核已在调用方挡过。
        internal static ApiResult DeleteRedInvoice(WorkContext ctx, VoucherKind kind, int id, Dictionary<string, object> snap)
        {
            bool fromBlue = RequireRedFromReturn(ctx.Conn, id);
            if (Rows.Scalar(ctx.Conn, RInvStaleSql, new object[] { id }) != null)
            {
                throw new BridgeException(409, "state_mismatch",
                    "发票复核后又弃复，U8 未回退退货单的已复核开票数量，删除后累计开票数无法回退，请在 U8 客户端处理");
            }
            int vt = RedVt(CoRows.Col(snap, "vouch_type"));
            object sys = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                SaSession.OpenSa(ctx.Conn, ctx.Session.Login, vt, out sys, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                string err = SaSession.ReadSa(co, doms, id, ctx.Item);
                if (err.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", err);
                }
                CoRows.RequireHead(doms[0], kind.IdColumn, id);
                List<object> rows = DomRows.RowsOf(doms[1]);
                for (int i = 0; i < rows.Count; i++)
                {
                    DomRows.Set(doms[1], rows[i], "editprop", "D");
                }
                RedDeleteTran(ctx, co, doms, id, fromBlue);
                return SaleOrderCo.Gone(ctx, kind, id);
            }
            finally
            {
                SaSession.Release(sys, co, doms);
            }
        }

        // 返回 true 表示红冲蓝字发票生成的红字发票（每行 iSBVID 指向蓝字发票行）。
        static bool RequireRedFromReturn(object conn, int id)
        {
            Dictionary<string, object> shape = Rows.One(conn, RInvShapeSql, new object[] { id });
            if (shape == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            int kind = RedShape(PuInv.Num(CoRows.Col(shape, "n")), PuInv.Num(CoRows.Col(shape, "r")),
                PuInv.Num(CoRows.Col(shape, "blue")), PuInv.Num(CoRows.Col(shape, "disp")), PuInv.Num(CoRows.Col(shape, "back")));
            if (kind < 0)
            {
                throw new BridgeException(400, "bad_request", "仅支持蓝字销售发票和参照退货单生成的红字销售发票");
            }
            return kind == RedFromBlue;
        }

        internal const int RedFromReturn = 0;
        internal const int RedFromBlue = 1;

        // 红字发票分类（不连库，自检可调）：n 行数、r 指向退货单行的行数、blue 的 iSBVID 指向蓝字行的行数、disp 表头 iDisp、
        // back 反指本发票的发货单数。参照退货单生成的（iDisp=1、无反指、每行都指向退货单行）优先按退货单处理（核对回写）；
        // 否则每行都指向蓝字行的按红冲蓝字（实测：U8 保存红冲时行上可能挂着它自己生成的退货单行，r 也等于 n）；其余 −1。
        internal static int RedShape(decimal n, decimal r, decimal blue, decimal disp, decimal back)
        {
            if (n <= 0m)
            {
                return -1;
            }
            if (r == n && disp == 1m && back == 0m)
            {
                return RedFromReturn;
            }
            return blue == n ? RedFromBlue : -1;
        }

        // 26 → VT 1，27 → VT 3（未经实测，类型库序号推断）。
        static int RedVt(string text)
        {
            decimal n;
            if (decimal.TryParse(text, NumberStyles.Number, CultureInfo.InvariantCulture, out n))
            {
                if (n == 26m)
                {
                    return 1;
                }
                if (n == 27m)
                {
                    return 3;
                }
            }
            throw new BridgeException(400, "bad_request", "该发票类型不支持");
        }

        static void RedDeleteTran(WorkContext ctx, object co, object[] doms, int id, bool fromBlue)
        {
            bool open = false;
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                RedMoves moves = fromBlue ? new RedMoves() : RedDeleteBase(ctx.Conn, id);
                int blueId = fromBlue ? CoRows.AsId(Rows.Scalar(ctx.Conn, RedBlueIdSql, new object[] { id })) : 0;
                string before = BlueSnapNote(ctx.Conn, blueId);
                object[] args = new object[] { doms[0], doms[1] };
                object ret = ComUtil.CallRef(co, "Delete", args, new int[] { 0, 1 });
                CoRows.Swap(doms, 0, args[0]);
                CoRows.Swap(doms, 1, args[1]);
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                string msg = ret == null ? "" : Convert.ToString(ret);
                CoRows.Note(ctx.Item, "Delete " + (msg.Length == 0 ? "ok" : msg));
                if (msg.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", msg);
                }
                StockCall.AfterCheck(ctx, delegate(object conn)
                {
                    RequireRedMoved(conn, ctx.Item, moves);
                    NoteBlueDelete(conn, ctx.Item, blueId, before);
                }, "红字发票 " + Id(id));
                CoTrans.CommitSeen(ctx.Conn);
                open = false;
            }
            catch (Exception)
            {
                CoRows.CatchTran(ctx.Conn, ctx.Item, open);
                throw;
            }
        }

        // 红冲蓝字发票删除的回写快照（发货行累计开票 / 已复核开票 / 订单行累计开票），读失败记「?」，不影响删除。
        static string BlueSnapNote(object conn, int blueId)
        {
            if (blueId <= 0)
            {
                return "";
            }
            try
            {
                return BlueSnap(conn, blueId);
            }
            catch (Exception)
            {
                return "?";
            }
        }

        // 只记审计：同生单的「红冲回写」，暂不核对。
        static void NoteBlueDelete(object conn, WorkItem item, int blueId, string before)
        {
            if (blueId <= 0)
            {
                return;
            }
            CoRows.Note(item, "红冲删除回写 蓝字发票 " + Id(blueId) + " st/vb/kp " + before + "→" + BlueSnapNote(conn, blueId));
        }

        // 发票行数量为负，qty 取正：退货行 iSettleQuantity 应变 +qty，订单行 iKPQuantity 应变 +qty。
        static RedMoves RedDeleteBase(object conn, int id)
        {
            RedMoves moves = new RedMoves();
            List<Dictionary<string, object>> lines = Rows.Query(conn, RInvDelLinesSql, new object[] { id }, 500);
            for (int i = 0; i < lines.Count; i++)
            {
                decimal qty = PuInv.Num(CoRows.Col(lines[i], "qty"));
                int lineId = CoRows.AsId(CoRows.Col(lines[i], "iDLsID"));
                if (lineId > 0)
                {
                    RedLock(conn, moves.Settle, lineId, RInvSetLockSql, qty, "退货单行不存在");
                }
                int soLine = CoRows.AsId(CoRows.Col(lines[i], "iSOsID"));
                if (RedCheckSoKp && soLine > 0)
                {
                    RedLock(conn, moves.Kp, soLine, RInvKpLockSql, qty, "销售订单行不存在");
                }
            }
            return moves;
        }
    }
}
