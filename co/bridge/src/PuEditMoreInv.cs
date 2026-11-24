using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 采购发票修改（PuEditMore 分派）。闸门同删除（GateDelete：复核、应付审核、结算、现付、期初、退货、直运、应付明细、
    // 采购期间已结账）；改发票日期时新日期所在期间也不能已结账。Init 同删除：vt 4 + purbill / ppurbill。
    // 金额按生单公式重算（普通发票按 PriceSrc 税率 0），保存后核对入库行 iSumBillQuantity 恰好移动了本单该行数量的减少量。未经实测。
    // 红字发票（表头 bNegative=1）与蓝字同一路径：Init 的 bPositive 同删除（PositiveOf），数量按符号 −1 写负数，
    // 请求里的数量仍是正数；红字入库行的 iSumBillQuantity 为负，数量减少时朝 0 移动（与蓝字相反）。
    // 红蓝字行混合的发票 409；RdsId=0 的行（订单行直开的红字）不核对入库行，同蓝字。
    internal static partial class PuInv
    {
        const string EditLinesSql = "select convert(varchar(20), ID) as Id, convert(varchar(20), isnull(RdsId,0)) as RdsId"
            + " from PurBillVouchs where PBVID=?";
        // 表头红字标志和明细行数量的最小、最大值：判断符号，也挡住红蓝字行混合的发票。
        const string SignSql = "select convert(varchar(5), isnull(h.bNegative,0)) as Neg,"
            + " convert(varchar(40), min(isnull(b.iPBVQuantity,0))) as Lo, convert(varchar(40), max(isnull(b.iPBVQuantity,0))) as Hi"
            + " from PurBillVouch h inner join PurBillVouchs b on b.PBVID=h.PBVID where h.PBVID=? group by h.bNegative";
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
            int sign = SignOf(ctx.Conn, id);
            GateDelete(ctx.Conn, id, row);
            string date;
            if (req.Head.TryGetValue("dpbvdate", out date))
            {
                RequireOpen(ctx.Conn, date);
            }
            return EditSave(ctx, kind, id, req, new string[] { bill, billType }, sign);
        }

        // 红字发票 −1、蓝字 1；表头与行符号不一致（混合行）409。
        static int SignOf(object conn, int id)
        {
            Dictionary<string, object> row = Rows.One(conn, SignSql, new object[] { id });
            if (row == null)
            {
                throw new BridgeException(409, "state_mismatch", "采购发票没有明细行");
            }
            return PickSign(CoRows.Col(row, "Neg") == "1", Num(CoRows.Col(row, "Lo")), Num(CoRows.Col(row, "Hi")));
        }

        // red：表头 bNegative；lo / hi：明细行数量的最小、最大值。红字行都不大于 0，蓝字行都不小于 0。
        internal static int PickSign(bool red, decimal lo, decimal hi)
        {
            if (red ? hi > 0m : lo < 0m)
            {
                throw new BridgeException(409, "state_mismatch", "红蓝字行混合的采购发票不能修改");
            }
            return red ? -1 : 1;
        }

        // bills：{ Init 单据键 purbill / ppurbill, 发票类型 01 / 02 }；sign：红字 −1、蓝字 1。
        static ApiResult EditSave(WorkContext ctx, VoucherKind kind, int id, PuEditReq req, string[] bills, int sign)
        {
            object info = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                PuSession.UseStored(ctx, kind, id);
                Open(ctx, 4, bills[0], sign > 0 || RedOpPositive, out info, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                PurchaseCo.ReadPu(co, doms, id, ctx.Item);
                CoRows.RequireHead(doms[0], kind.IdColumn, id);
                PuEditMore.ApplyHead(ctx, doms[0], req.Head);
                Dictionary<int, decimal> drops = PuEditMore.ApplyLines(doms[1], req, EditShape(doms[0], bills[1], sign));
                PuEditMore.SaveTran(ctx, co, doms, id, BilledCheck(ctx, id, drops, sign));
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

        // 行主键 id；汇率取表头 cexchrate；属性对照同生单（AmountNames），普通发票按 PriceSrc 改成税率 0、含税价；红字写负数量、负金额。
        static PuEditShape EditShape(object head, string billType, int sign)
        {
            decimal exch = Rate(PuEditMore.HeadText(head, "cexchrate"));
            PuEditShape shape = new PuEditShape();
            shape.IdAttr = "id";
            shape.Sign = sign;
            shape.Amounts = delegate(object dom, object row, List<string> schema, decimal qty)
            {
                Dictionary<string, object> src = PriceSrc(PuEditMore.PriceOf(row), billType);
                Dictionary<string, string> amt = StockGen.PoAmounts(src, qty, exch);
                PuEditMore.WriteAmounts(dom, row, schema, amt, AmountNames);
            };
            return shape;
        }

        // 入库行累计开票数量随本单数量减少而减少（红字入库行为负，朝 0 移动，即 −sign）；没动的行也核对（应不变）。
        // 生单不核对订单行 iInvQTY，这里同样不核。
        static PuEditCheck BilledCheck(WorkContext ctx, int id, Dictionary<int, decimal> drops, int sign)
        {
            List<Dictionary<string, object>> lines = Rows.Query(ctx.Conn, EditLinesSql, new object[] { id }, 5000);
            Dictionary<int, decimal> delta = PuEditMore.BySource(lines, "Id", "RdsId", drops, -sign);
            string label = "入库单累计开票数量 iSumBillQuantity（发票 " + id.ToString(CultureInfo.InvariantCulture) + "）";
            return PuEditMore.SumCheck(ctx.Item, label, BilledLockSql, BilledSql, delta);
        }
    }
}
