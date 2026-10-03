using System;
using System.Collections.Generic;

namespace U8Co
{
    // 应收冲应付 / 应付冲应收的写（请求连接的事务里），顺序同 U8：取号 → 两张往来明细的处理行（应收在前）→ 扣余额。
    // 余额：应收单 / 应付单 Ap_Vouch.iRAmount*；采购发票 PurBillVouchs.iOriTotal / iTotal 加本次；
    // 销售发票经 U8 的 clsWrite2Bill.UpdateBillForAR（#ap_SaleBillVouchHXdata 放本次金额，组件加到累计并连带订单、发货单）。
    // 写完在事务里核对：行数、每行余额 = 写前 - 本次、表头余额、发票累计 = 写前 + 本次；不符回滚、409。不在这里制单。
    internal static class ArapTransferWrite
    {
        const decimal Tolerance = 0.005m;

        public static void Write(WorkContext ctx, TransferPlan plan)
        {
            object conn = ctx.Conn;
            Number(conn, plan);
            foreach (TransferDoc d in plan.Docs)
            {
                foreach (TransferPiece p in d.Pieces)
                {
                    Row(conn, plan, p);
                }
            }
            Remainders(ctx, plan);
            string problem = Mismatch(conn, plan);
            if (problem != null)
            {
                throw new BridgeException(409, "u8_rejected", ArapTransferRule.Title(plan.Flag) + "后核对不符，已回滚：" + problem);
            }
        }

        // 票据背书（NotesProcEndorse）：号已由调用方取好，只写各单据的处理行、扣余额、逐张核对余额；不符返回原因，符合返回 null。
        internal static string ApplyDocs(WorkContext ctx, TransferPlan plan)
        {
            foreach (TransferDoc d in plan.Docs)
            {
                foreach (TransferPiece p in d.Pieces)
                {
                    Row(ctx.Conn, plan, p);
                }
            }
            Remainders(ctx, plan);
            foreach (TransferDoc d in plan.Docs)
            {
                string problem = DocMismatch(ctx.Conn, plan, d);
                if (problem != null)
                {
                    return problem;
                }
            }
            return null;
        }

        static void Number(object conn, TransferPlan plan)
        {
            string no = TransferSql.CancelNo(conn, plan.Flag);
            if (!ArapTransferRule.NoValid(plan.Flag, no))
            {
                throw new BridgeException(409, "u8_rejected", "U8 没有给出有效的处理号（Ap_Proc_CancelNo 返回 " + (no ?? "空") + "）");
            }
            if (TransferSql.Used(conn, plan.Style, no))
            {
                throw new BridgeException(409, "u8_rejected", "处理号 " + no + " 已被占用，请检查 Ap_CancelNo");
            }
            plan.CancelNo = no;
        }

        static void Row(object conn, TransferPlan plan, TransferPiece p)
        {
            List<object> args = new List<object>();
            string sql = TransferSql.Insert(plan, p, plan.DwOf(p.Doc.Side), args);
            GlSql.Exec(conn, sql, args.ToArray());
        }

        static void Remainders(WorkContext ctx, TransferPlan plan)
        {
            object conn = ctx.Conn;
            List<TransferPiece> sale = new List<TransferPiece>();
            foreach (TransferDoc d in plan.Docs)
            {
                if (ArapTransferRule.Mode(d.Ask.Type) == ArapTransferRule.WholeDoc)
                {
                    TransferSql.Bill(conn, d, SumF(d), SumN(d));
                    continue;
                }
                foreach (TransferPiece p in d.Pieces)
                {
                    if (d.Kind == "purchase_invoice")
                    {
                        TransferSql.PurLine(conn, p);
                    }
                    else
                    {
                        sale.Add(p);
                    }
                }
            }
            if (sale.Count > 0)
            {
                Sale(ctx, plan, sale);
            }
        }

        static void Sale(WorkContext ctx, TransferPlan plan, List<TransferPiece> pieces)
        {
            object conn = ctx.Conn;
            UnwriteoffSql.Run(conn, ArapUnwriteoffBill.SaleTmp);
            foreach (TransferPiece p in pieces)
            {
                TransferSql.SaleLine(conn, p);
            }
            ArapUnwriteoffBill.Write2(ctx, "处理号 " + plan.CancelNo);
            UnwriteoffSql.Run(conn, "DROP TABLE #ap_SaleBillVouchHXdata");
        }

        static decimal SumF(TransferDoc d)
        {
            decimal sum = 0m;
            foreach (TransferPiece p in d.Pieces)
            {
                sum += p.F;
            }
            return sum;
        }

        static decimal SumN(TransferDoc d)
        {
            decimal sum = 0m;
            foreach (TransferPiece p in d.Pieces)
            {
                sum += p.N;
            }
            return sum;
        }

        // 不符返回原因，符合返回 null。
        static string Mismatch(object conn, TransferPlan plan)
        {
            foreach (string side in new string[] { "AR", "AP" })
            {
                int want = Expected(side == "AP" ? plan.Ap : plan.Ar);
                if (TransferSql.CountRows(conn, side, plan.Style, plan.CancelNo) != want)
                {
                    return (side == "AP" ? "应付" : "应收") + "往来明细的处理行数不对（应为 " + want + "）";
                }
            }
            foreach (TransferDoc d in plan.Docs)
            {
                string problem = DocMismatch(conn, plan, d);
                if (problem != null)
                {
                    return problem;
                }
            }
            return null;
        }

        // 每一行（整单）一条处理行。
        internal static int Expected(List<TransferDoc> docs)
        {
            int n = 0;
            foreach (TransferDoc d in docs)
            {
                n += d.Pieces.Count;
            }
            return n;
        }

        static string DocMismatch(object conn, TransferPlan plan, TransferDoc d)
        {
            string mode = ArapTransferRule.Mode(d.Ask.Type);
            string dw = plan.DwOf(d.Side);
            foreach (TransferPiece p in d.Pieces)
            {
                decimal now = TransferSql.Remain(conn, d, p.Line, dw).RemainF;
                if (Math.Abs(now - (p.BeforeF - p.F)) > Tolerance)
                {
                    return d.Label + " 余额 " + ArapTransferRule.Money(now);
                }
                if (mode == ArapTransferRule.ByInvoiceLine && !AccOk(conn, d, p))
                {
                    return d.Label + " 行 " + p.Line + " 的累计核销不符";
                }
            }
            if (mode == ArapTransferRule.WholeDoc)
            {
                decimal head = UnwriteoffSql.BillRemain(conn, d.Side, d.Ask.Type, d.Ask.Code);
                if (Math.Abs(head - (d.HeadBefore - SumF(d))) > Tolerance)
                {
                    return d.Label + " 表头余额 " + ArapTransferRule.Money(head);
                }
            }
            return null;
        }

        static bool AccOk(object conn, TransferDoc d, TransferPiece p)
        {
            decimal[] now = TransferSql.InvoiceAcc(conn, d.Ask.Type, p.Line);
            return Math.Abs(now[0] - (p.BillA + p.F)) <= Tolerance && Math.Abs(now[1] - (p.BillB + p.N)) <= Tolerance;
        }
    }
}
