using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 采购发票修改（PuEditMore 分派）。闸门同删除（GateDelete：复核、应付审核、结算、现付、期初、退货、直运、应付明细、
    // 采购期间已结账）；改发票日期时新日期所在期间也不能已结账。Init 同删除：vt 4 + purbill / ppurbill。
    // 金额按生单公式重算（普通发票按 PriceSrc 税率 0），保存后核对入库行 iSumBillQuantity 恰好减少了本单该行数量的减少量。未经实测。
    internal static partial class PuInv
    {
        const string EditLinesSql = "select convert(varchar(20), ID) as Id, convert(varchar(20), isnull(RdsId,0)) as RdsId"
            + " from PurBillVouchs where PBVID=?";
        const string RedSql = "select top 1 convert(varchar(20), h.PBVID) from PurBillVouch h where h.PBVID=?"
            + " and (isnull(h.bNegative,0)=1 or exists (select 1 from PurBillVouchs b where b.PBVID=h.PBVID and b.iPBVQuantity<0))";
        const string BilledLockSql = "select convert(varchar(40), isnull(iSumBillQuantity,0)) from rdrecords01 with (updlock, holdlock) where AutoID=?";

        internal static ApiResult EditUpdate(WorkContext ctx, VoucherKind kind, int id, PuEditReq req)
        {
            Dictionary<string, object> row = Rows.One(ctx.Conn, DelSql, new object[] { id });
            if (row == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            string billType = CoRows.Col(row, "cPBVBillType");
            string bill = BillKey(billType);
            if (CoRows.Col(row, "cBusType") != "普通采购")
            {
                throw new BridgeException(409, "state_mismatch", "只支持修改普通采购的发票");
            }
            // 红字发票（bNegative=1 或有负数量行）：改数量按蓝字写正数会翻转符号，本期不支持。
            if (Rows.Scalar(ctx.Conn, RedSql, new object[] { id }) != null)
            {
                throw new BridgeException(409, "state_mismatch", "红字采购发票暂不支持修改");
            }
            GateDelete(ctx.Conn, id, row);
            string date;
            if (req.Head.TryGetValue("dpbvdate", out date))
            {
                RequireOpen(ctx.Conn, date);
            }
            return EditSave(ctx, kind, id, req, new string[] { bill, billType });
        }

        // bills：{ Init 单据键 purbill / ppurbill, 发票类型 01 / 02 }。
        static ApiResult EditSave(WorkContext ctx, VoucherKind kind, int id, PuEditReq req, string[] bills)
        {
            object info = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                PuSession.UseStored(ctx, kind, id);
                Open(ctx, 4, bills[0], out info, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                PurchaseCo.ReadPu(co, doms, id, ctx.Item);
                CoRows.RequireHead(doms[0], kind.IdColumn, id);
                PuEditMore.ApplyHead(ctx, doms[0], req.Head);
                Dictionary<int, decimal> drops = PuEditMore.ApplyLines(doms[1], req, EditShape(doms[0], bills[1]));
                PuEditMore.SaveTran(ctx, co, doms, id, BilledCheck(ctx, id, drops));
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

        // 行主键 id；汇率取表头 cexchrate；属性对照同生单（AmountNames），普通发票按 PriceSrc 改成税率 0、含税价。
        static PuEditShape EditShape(object head, string billType)
        {
            decimal exch = Rate(PuEditMore.HeadText(head, "cexchrate"));
            PuEditShape shape = new PuEditShape();
            shape.IdAttr = "id";
            shape.Sign = 1;
            shape.Amounts = delegate(object dom, object row, List<string> schema, decimal qty)
            {
                Dictionary<string, object> src = PriceSrc(PuEditMore.PriceOf(row), billType);
                Dictionary<string, string> amt = StockGen.PoAmounts(src, qty, exch);
                PuEditMore.WriteAmounts(dom, row, schema, amt, AmountNames);
            };
            return shape;
        }

        // 入库行累计开票数量随本单数量减少而减少；没动的行也核对（应不变）。生单不核对订单行 iInvQTY，这里同样不核。
        static PuEditCheck BilledCheck(WorkContext ctx, int id, Dictionary<int, decimal> drops)
        {
            List<Dictionary<string, object>> lines = Rows.Query(ctx.Conn, EditLinesSql, new object[] { id }, 5000);
            Dictionary<int, decimal> delta = PuEditMore.BySource(lines, "Id", "RdsId", drops, -1);
            string label = "入库单累计开票数量 iSumBillQuantity（发票 " + id.ToString(CultureInfo.InvariantCulture) + "）";
            return PuEditMore.SumCheck(ctx.Item, label, BilledLockSql, BilledSql, delta);
        }
    }
}
