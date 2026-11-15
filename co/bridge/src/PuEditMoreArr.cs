using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 到货单修改（PuEditMore 分派）。Init 同生单：PuInv.Open(vt 2, "0")——sBillType 传空串时 VoucherSave2 报「类型不匹配」
    // （实测核对）。改数量后 U8 按差额回写 PO_Podetails.iArrQTY（实测 5→3 时 −2）、不重算金额；桥按生单的
    // StockGen.PoAmounts 重算，保存后在同一事务里核对每个订单行的 iArrQTY 恰好减少了本单该行数量的减少量。
    internal static partial class PuArr
    {
        const string EditLinesSql = "select convert(varchar(20), Autoid) as Autoid, convert(varchar(20), isnull(iPOsID,0)) as PoLine"
            + " from PU_ArrivalVouchs where ID=?";
        const string ArrivedLockSql = "select convert(varchar(40), isnull(iArrQTY,0)) from PO_Podetails with (updlock, holdlock) where ID=?";

        internal static ApiResult EditUpdate(WorkContext ctx, VoucherKind kind, int id, PuEditReq req)
        {
            Dictionary<string, object> row = CoRows.HeadRow(ctx.Conn, kind, id);
            if (row == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            string bill = CoRows.Col(row, "bill_type");
            if (bill.Length > 0 && bill != "0")
            {
                throw new BridgeException(400, "bad_request", "仅支持蓝字到货单");
            }
            PuEditMore.GateArrival(ctx, kind, id, row);
            object info = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                PuSession.UseStored(ctx, kind, id);
                PuInv.Open(ctx, 2, "0", out info, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                PurchaseCo.ReadPu(co, doms, id, ctx.Item);
                CoRows.RequireHead(doms[0], kind.IdColumn, id);
                PuEditMore.ApplyHead(ctx, doms[0], req.Head);
                Dictionary<int, decimal> drops = PuEditMore.ApplyLines(doms[1], req, EditShape(doms[0], 1));
                PuEditMore.SaveTran(ctx, co, doms, id, ArrivedCheck(ctx, id, drops));
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

        // 到货单、退货单（sign −1）共用：行主键 autoid；金额按表头汇率、行上价格和税率（行上没有时用表头税率）重算，
        // 属性对照同生单（AmountNames）。退货单数量、金额为负，单价仍为正。实收 / 合格数量随数量改（PuEditMore.Follow）。
        internal static PuEditShape EditShape(object head, int sign)
        {
            decimal exch = Rate(PuEditMore.HeadText(head, "iexchrate"));
            string headRate = PuEditMore.HeadText(head, "itaxrate");
            PuEditShape shape = new PuEditShape();
            shape.IdAttr = "autoid";
            shape.Sign = sign;
            shape.Follow = true;
            shape.Amounts = delegate(object dom, object row, List<string> schema, decimal qty)
            {
                Dictionary<string, string> amt = StockGen.PoAmounts(PuEditMore.PriceOf(row), qty, exch, headRate);
                PuEditMore.WriteAmounts(dom, row, schema, amt, AmountNames);
            };
            return shape;
        }

        // 订单行累计到货数量随本单数量减少而减少；没动的行也核对（应不变）。
        static PuEditCheck ArrivedCheck(WorkContext ctx, int id, Dictionary<int, decimal> drops)
        {
            List<Dictionary<string, object>> lines = Rows.Query(ctx.Conn, EditLinesSql, new object[] { id }, 5000);
            Dictionary<int, decimal> delta = PuEditMore.BySource(lines, "Autoid", "PoLine", drops, -1);
            string label = "采购订单行累计到货数量 iArrQTY（到货单 " + id.ToString(CultureInfo.InvariantCulture) + "）";
            return PuEditMore.SumCheck(ctx.Item, label, ArrivedLockSql, ArrivedSql, delta);
        }
    }
}
