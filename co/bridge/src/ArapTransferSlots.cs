using System.Collections.Generic;

namespace U8Co
{
    // 转账单据的可转账余额（事务里带锁读）和写之前的快照。
    // 发票按行：往来明细上该行（iBVid）的余额，口径同核销；应收单 / 应付单整单（iBVid 为 0）。
    internal static class ArapTransferSlots
    {
        // 各行余额（含不大于 0 的行：分摊时跳过，红字判定要用）；给了 line_id 时只有那一行（不属于本单 400「明细行不存在」）。
        public static List<TransferSlot> Of(object conn, TransferDoc d, string dw)
        {
            List<TransferSlot> slots = new List<TransferSlot>();
            if (ArapTransferRule.Mode(d.Ask.Type) == ArapTransferRule.WholeDoc)
            {
                slots.Add(TransferSql.Remain(conn, d, 0, dw));
                return slots;
            }
            if (d.Ask.Line > 0)
            {
                if (!WriteoffSql.LineOf(conn, WriteoffKind.Of(d.Kind), d.Ask.Line, d.Id))
                {
                    throw new BridgeException(400, "bad_request", "明细行不存在");
                }
                slots.Add(TransferSql.Remain(conn, d, d.Ask.Line, dw));
                return slots;
            }
            foreach (int line in WriteoffSql.OpenLines(conn, d.Side, d.Ask.Type, d.Ask.Code, dw).Keys)
            {
                slots.Add(TransferSql.Remain(conn, d, line, dw));
            }
            return slots;
        }

        // 红字单据（蓝字转账只扣正余额）：各行余额没有大于 0 而有小于 0 的；按整单分摊的发票只列出正余额行，没有时看整单合计。
        public static bool Red(object conn, TransferDoc d, string dw, List<TransferSlot> slots)
        {
            return ArapTransferRule.Red(slots) || (slots.Count == 0 && TransferSql.DocRemain(conn, d, dw) < 0m);
        }

        // 每一行都要有审核行（处理行从它复制），且没有关联合同；记下应收应付单表头余额、发票行累计核销，写后核对用。
        public static void Before(object conn, TransferDoc d, string dw)
        {
            string mode = ArapTransferRule.Mode(d.Ask.Type);
            foreach (TransferPiece p in d.Pieces)
            {
                Dictionary<string, object> sign = TransferSql.SignRow(conn, d, p.Line, dw);
                if (sign == null)
                {
                    throw ArapTransferGate.State(d.Label + (p.Line > 0 ? " 的行 " + p.Line : string.Empty) + " 没有审核记录，不能转账");
                }
                if (CoRows.Col(sign, "contract").Trim().Length > 0)
                {
                    throw ArapTransferGate.State(d.Label + " 关联了合同，请在 U8 客户端转账");
                }
                if (mode == ArapTransferRule.ByInvoiceLine)
                {
                    decimal[] acc = TransferSql.InvoiceAcc(conn, d.Ask.Type, p.Line);
                    p.BillA = acc[0];
                    p.BillB = acc[1];
                }
            }
            if (mode == ArapTransferRule.WholeDoc)
            {
                d.HeadBefore = UnwriteoffSql.BillRemain(conn, d.Side, d.Ask.Type, d.Ask.Code);
            }
        }
    }
}
