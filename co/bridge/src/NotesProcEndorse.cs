using System;
using System.Collections.Generic;

namespace U8Co
{
    // 背书冲应付（9E）的应付一侧：照 U8，每张应付单据（行）照它的审核行在应付往来明细上插一条借方处理行
    // （iFlag=0，cProcStyle=9E，处理号同票据），并扣余额（采购发票 PurBillVouchs 累计、应付单 Ap_Vouch.iRAmount*）。
    // 这和应收冲应付（9I）的应付一侧是同一套写法，直接用转账的代码（ArapTransferGate.Doc、ArapTransferWrite.ApplyDocs），
    // 处理方式换成 9E。付款单（49，U8 会另写预付）不收（NotesProcReq）。
    internal static class NotesProcEndorse
    {
        // 供应商存在；各应付单据过转账的闸门（存在、已审核、往来单位是该供应商、不受审批流控制、没被占用、本位币、日期不晚于处理日期、余额够）。
        public static void Plan(WorkContext ctx, NotesProcPlan plan, NotesProcAsk ask, DateTime day)
        {
            object conn = ctx.Conn;
            string ven = TransferSql.PartnerName(conn, "AP", ask.Vendor);
            if (ven == null)
            {
                throw new BridgeException(404, "not_found", "供应商 " + ask.Vendor + " 不存在");
            }
            plan.Vendor = ask.Vendor;
            plan.VendorName = ven.Length > 0 ? ven : ask.Vendor;
            if (NotesProcReq.Total(ask.ApLines) != plan.Amount)
            {
                throw NotesProcGate.State("ap_lines 的金额合计 " + NotesProcRule.Money(NotesProcReq.Total(ask.ApLines))
                    + " 与背书金额 " + NotesProcRule.Money(plan.Amount) + " 不等");
            }
            TransferPlan ap = new TransferPlan();
            ap.Flag = "AR";
            ap.FixedStyle = NotesProcRule.Style(plan.Op);
            ap.Date = plan.Date;
            ap.Year = plan.Year;
            ap.Period = plan.Period;
            ap.Customer = plan.Note.Partner;
            ap.Vendor = plan.Vendor;
            ap.Local = plan.Local;
            ap.Currency = plan.Local;
            ap.Home = true;
            ap.Operator = plan.Operator;
            ap.Sum = plan.Amount;
            foreach (TransferAskLine line in ask.ApLines)
            {
                ap.Ap.Add(ArapTransferGate.Doc(conn, ap, line, day));
            }
            plan.Ap = ap;
        }

        // 数据权限：每张应付单据按供应商规则（同转账）。
        public static void Allowed(WorkContext ctx, NotesProcPlan plan)
        {
            if (plan.Ap == null)
            {
                return;
            }
            PermContext p = PermCheck.Of(ctx);
            foreach (TransferDoc d in plan.Ap.Ap)
            {
                if (!PermCheck.RowAllowed(p, PermRegistry.TransferRowRule("AP"), d.Head))
                {
                    throw new BridgeException(403, "no_permission", PermCheck.DeniedData);
                }
            }
        }

        // 写应付处理行、扣余额、逐张核对余额（不符返回原因）；然后取应付往来科目给票据处理表。
        public static string Write(WorkContext ctx, NotesProcPlan plan)
        {
            plan.Ap.CancelNo = plan.CancelNo;
            plan.Ap.Digest = plan.Digest;
            string problem = ArapTransferWrite.ApplyDocs(ctx, plan.Ap);
            if (problem != null)
            {
                return problem;
            }
            plan.ApKm = NotesProcSql.ApKm(ctx.Conn, plan.CancelNo);
            return plan.ApKm.Length == 0 ? "应付处理行没有往来科目" : null;
        }

        public static int ApRows(NotesProcPlan plan)
        {
            return plan.Ap == null ? 0 : ArapTransferWrite.Expected(plan.Ap.Ap);
        }

        // 响应里的应付各行：单据、行、本次金额和处理后的余额（同转账）。
        public static List<object> RowsOut(NotesProcPlan plan)
        {
            List<object> rows = new List<object>();
            if (plan.Ap == null)
            {
                return rows;
            }
            foreach (TransferDoc d in plan.Ap.Ap)
            {
                foreach (TransferPiece p in d.Pieces)
                {
                    Dictionary<string, object> one = new Dictionary<string, object>();
                    one["type"] = d.Ask.Type;
                    one["id"] = d.Ask.Code;
                    one["doc_id"] = d.Id;
                    one["line_id"] = p.Line > 0 ? (object)p.Line : null;
                    one["amount"] = p.F;
                    one["remaining"] = p.BeforeF - p.F;
                    rows.Add(one);
                }
            }
            return rows;
        }
    }
}
