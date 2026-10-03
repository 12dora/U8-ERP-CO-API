using System;
using System.Collections.Generic;

namespace U8Co
{
    // 采购退货单的读取/审核入口辅助与删除。删除只删未审核、没有下游（报检、入库）的退货单；
    // Delete 在 CoTrans 里执行，提交前按 Checks 核对来源行的回写已减回。
    internal static partial class PuRet
    {
        const string LinesSql = "select convert(varchar(20), isnull(s.iCorId,0)) as CorId, convert(varchar(20), isnull(s.iPOsID,0)) as PoLine,"
            + " convert(varchar(40), isnull(s.iQuantity,0)) as Qty from PU_ArrivalVouchs s where s.ID=?";

        // 读取、审核前核对类型：purchase_return 只认 iBillType=1 的到货单（HeadRow 的 bill_type）。
        internal static void RequireRed(VoucherKind kind, Dictionary<string, object> row)
        {
            if (kind == null || kind.Name != "purchase_return" || row == null)
            {
                return;
            }
            if (CoRows.Col(row, "bill_type") != "1")
            {
                throw new BridgeException(400, "bad_request", "不是采购退货单");
            }
        }

        public static ApiResult Delete(WorkContext ctx, VoucherKind kind, int id)
        {
            Gate(ctx, kind, id);
            // 预演：登记来源到货单 / 采购订单（退货累计回退）。
            DocMark.Upstream(ctx.Conn, kind, id);
            List<RetSnap> snaps = Plan(DeleteKeys(ctx.Conn, id));
            object info = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                PuSession.UseStored(ctx, kind, id);
                Open(ctx, false, false, out info, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                PurchaseCo.ReadPu(co, doms, id, ctx.Item);
                CoRows.RequireHead(doms[0], kind.IdColumn, id);
                DeleteTran(ctx, co, doms, snaps);
                return Gone(ctx, kind, id);
            }
            finally
            {
                ComUtil.Final(doms[1]);
                ComUtil.Final(doms[0]);
                ComUtil.Final(co);
                ComUtil.Final(info);
            }
        }

        // 同到货单删除：存在、是退货单、未启用审批流、未审核、未报检未入库。
        static void Gate(WorkContext ctx, VoucherKind kind, int id)
        {
            Dictionary<string, object> row = CoRows.HeadRow(ctx.Conn, kind, id);
            if (row == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            RequireRed(kind, row);
            PurchaseCo.RefuseFlow(ctx.Conn, kind, row);
            if (CoRows.Col(row, "verifier").Length > 0 || CoRows.Col(row, "verify_date").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核");
            }
            PurchaseCo.RefuseArrival(ctx.Conn, id, true);
        }

        // 每行：iCorId 有值按到货单来源核对，否则有 iPOsID 按订单来源核对；数量取绝对值。
        static List<RetKey> DeleteKeys(object conn, int id)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, LinesSql, new object[] { id }, 5000);
            List<RetKey> keys = new List<RetKey>();
            for (int i = 0; rows != null && i < rows.Count; i++)
            {
                RetKey key = new RetKey();
                int cor = CoRows.AsId(CoRows.Col(rows[i], "CorId"));
                key.Po = CoRows.AsId(CoRows.Col(rows[i], "PoLine"));
                key.Source = cor > 0 ? "arrival" : "purchase_order";
                key.Src = cor > 0 ? cor : key.Po;
                key.Qty = Math.Abs(PuInv.Num(CoRows.Col(rows[i], "Qty")));
                keys.Add(key);
            }
            return keys;
        }

        // Delete(h, b) 引用 {0,1}：返回空为成功。
        static void DeleteTran(WorkContext ctx, object co, object[] doms, List<RetSnap> snaps)
        {
            bool open = false;
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                ReadBefore(ctx.Conn, snaps);
                object[] args = new object[] { doms[0], doms[1] };
                object ret = ComUtil.CallRef(co, "Delete", args, new int[] { 0, 1 });
                CoRows.Swap(doms, 0, args[0]);
                CoRows.Swap(doms, 1, args[1]);
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                string msg = ret == null ? "" : Convert.ToString(ret).Trim();
                CoRows.Note(ctx.Item, "Delete " + (msg.Length == 0 ? "ok" : msg));
                if (msg.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", msg);
                }
                StockCall.AfterCheck(ctx, delegate(object conn) { RequireWritten(conn, ctx.Item, snaps, -1); }, "删除采购退货单");
                CoTrans.CommitSeen(ctx.Conn);
                open = false;
            }
            catch (Exception)
            {
                CoRows.CatchTran(ctx.Conn, ctx.Item, open);
                throw;
            }
        }

        // 已提交：新连接上回读，单据还在是 409；回读本身失败（连不上、查询出错）是 504，结果要调用方核对。
        static ApiResult Gone(WorkContext ctx, VoucherKind kind, int id)
        {
            object conn = null;
            try
            {
                Dictionary<string, object> row;
                try
                {
                    conn = ctx.OpenFresh();
                    row = CoRows.HeadRow(conn, kind, id);
                }
                catch (Exception ex)
                {
                    CoRows.Note(ctx.Item, "删除后回读失败 " + ex.Message);
                    throw new BridgeException(504, "outcome_unknown",
                        "已删除但回读失败，需要人工核对：采购退货单 " + id.ToString(System.Globalization.CultureInfo.InvariantCulture));
                }
                if (row != null)
                {
                    throw new BridgeException(409, "state_mismatch", "U8 返回成功但回读状态不符");
                }
                Dictionary<string, object> body = new Dictionary<string, object>();
                body["ok"] = true;
                body["type"] = kind.Name;
                body["id"] = id;
                body["deleted"] = true;
                return ApiResult.Ok(body);
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }
    }
}
