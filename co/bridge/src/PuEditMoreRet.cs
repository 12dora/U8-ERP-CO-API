using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 采购退货单修改（PuEditMore 分派）。Init 同读取和删除：Open(generate=false) 的红字参数（OpPositive / OpBill）。
    // 请求里的数量是正的退货数量，只能减少；写回 U8 取负，金额按生单公式重算（负数）。
    // 保存后按 Checks 核对来源回写：数量减少 d 相当于删掉 d，fRetQuantity / fPoRetQuantity 减 d、iArrQTY 加 d。未经实测。
    internal static partial class PuRet
    {
        const string EditLinesSql = "select convert(varchar(20), s.Autoid) as Autoid, convert(varchar(20), isnull(s.iCorId,0)) as CorId,"
            + " convert(varchar(20), isnull(s.iPOsID,0)) as PoLine from PU_ArrivalVouchs s where s.ID=?";

        internal static ApiResult EditUpdate(WorkContext ctx, VoucherKind kind, int id, PuEditReq req)
        {
            Dictionary<string, object> row = CoRows.HeadRow(ctx.Conn, kind, id);
            if (row == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            RequireRed(kind, row);
            PuEditMore.GateArrival(ctx, kind, id, row);
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
                PuEditMore.ApplyHead(ctx, doms[0], req.Head);
                Dictionary<int, decimal> drops = PuEditMore.ApplyLines(doms[1], req, PuArr.EditShape(doms[0], -1));
                PuEditMore.SaveTran(ctx, co, doms, id, EditCheck(ctx, id, drops));
                return PuEditMore.AfterSaved(ctx, kind, id);
            }
            finally
            {
                ComUtil.Final(doms[1]);
                ComUtil.Final(doms[0]);
                ComUtil.Final(co);
                ComUtil.Final(info);
            }
        }

        // 核对键同删除（iCorId 有值按到货单来源，否则按订单来源），数量是本行退货数量的减少量（没动的行 0，也核对）。
        // 保存前先 UPDLOCK 锁住来源行再读基准，方向 −1 同删除。
        static PuEditCheck EditCheck(WorkContext ctx, int id, Dictionary<int, decimal> drops)
        {
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, EditLinesSql, new object[] { id }, 5000);
            List<RetKey> keys = new List<RetKey>();
            for (int i = 0; rows != null && i < rows.Count; i++)
            {
                RetKey key = new RetKey();
                int cor = CoRows.AsId(CoRows.Col(rows[i], "CorId"));
                key.Po = CoRows.AsId(CoRows.Col(rows[i], "PoLine"));
                key.Source = cor > 0 ? "arrival" : "purchase_order";
                key.Src = cor > 0 ? cor : key.Po;
                drops.TryGetValue(CoRows.AsId(CoRows.Col(rows[i], "Autoid")), out key.Qty);
                keys.Add(key);
            }
            List<RetSnap> snaps = Plan(keys);
            WorkItem item = ctx.Item;
            PuEditCheck check = new PuEditCheck();
            check.Label = "修改采购退货单 " + id.ToString(CultureInfo.InvariantCulture);
            check.Before = delegate(object conn)
            {
                LockSnaps(conn, snaps);
                ReadBefore(conn, snaps);
            };
            check.After = delegate(object conn) { RequireWritten(conn, item, snaps, -1); };
            return check;
        }

        // 来源到货行用 ArrLeftSql、订单行用 PoLeftSql（都带 UPDLOCK, HOLDLOCK），只为加锁，值不用。
        static void LockSnaps(object conn, List<RetSnap> snaps)
        {
            for (int i = 0; i < snaps.Count; i++)
            {
                bool arrival = snaps[i].Check.Source == "arrival" && snaps[i].Check.Key == "src";
                Rows.Scalar(conn, arrival ? ArrLeftSql : PoLeftSql, new object[] { snaps[i].Id });
            }
        }
    }
}
