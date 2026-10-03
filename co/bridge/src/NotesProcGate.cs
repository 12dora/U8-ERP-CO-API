using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 票据处理的闸门：在事务里、带锁读票据之后，写之前（同 U8 票据管理界面的校验）。
    // 处理日期（= 登录日期）在会计期间内、不早于应收（应付）启用日期、所在期间未结账（背书另查应付）；票据存在、本位币、没换过票、
    // 没退回过、有票据科目、有余额、处理日期不早于签发日期和收票日期、登记生成的收付款单已审核、没被 U8 客户端占用；
    // 非分包票据整张处理、分包票据按可用子票区间；结算、贴现的银行科目是末级银行科目；贴现净额大于 0；
    // 背书的应付单据过转账同一套闸门（ArapTransferGate.Doc）；退回另查分包票据退回全部可用区间、之后的处理、应收控制科目、应收单模板（NotesProcReturn）。
    // 不存在 404；其余 409 state_mismatch。
    internal static class NotesProcGate
    {
        public static NotesProcPlan Plan(WorkContext ctx, NotesProcAsk ask)
        {
            object conn = ctx.Conn;
            NotesProcPlan plan = Start(conn, ctx, ask);
            DateTime day = ArapWriteoff.Day(plan.Date);
            plan.Note = Note(conn, ask, plan.Local);
            Usable(conn, plan, day);
            Amount(conn, plan, ask);
            if (plan.Op == "settle" || plan.Op == "discount")
            {
                Bank(conn, plan, ask);
            }
            Discount(plan, ask);
            string name = TransferSql.PartnerName(conn, plan.Flag, plan.Note.Partner);
            plan.PartnerName = string.IsNullOrEmpty(name) ? plan.Note.Partner : name;
            if (plan.Endorse)
            {
                NotesProcEndorse.Plan(ctx, plan, ask, day);
            }
            if (plan.Return)
            {
                NotesProcReturn.WholeRemainder(plan);
                NotesProcReturn.Plan(conn, plan);
            }
            plan.Digest = ask.Digest.Length > 0 ? ask.Digest : DefaultDigest(plan);
            return plan;
        }

        static string DefaultDigest(NotesProcPlan plan)
        {
            if (plan.Return)
            {
                return NotesProcRule.ReturnDigest(plan.PartnerName);
            }
            return NotesProcRule.Digest(plan.Op, plan.Flag, plan.PartnerName, plan.VendorName);
        }

        static NotesProcPlan Start(object conn, WorkContext ctx, NotesProcAsk ask)
        {
            NotesProcPlan plan = new NotesProcPlan();
            plan.Flag = ask.Flag;
            plan.Op = ask.Op;
            plan.Date = ctx.Item.Date;
            plan.Operator = ctx.OperatorName;
            plan.Local = WriteoffSql.LocalCurrency(conn);
            int[] period = WriteoffSql.PeriodOf(conn, ctx.Item.Acc, plan.Date);
            if (period == null)
            {
                throw State("处理日期不在 U8 的会计期间内");
            }
            plan.Year = period[0];
            plan.Period = period[1];
            OpenDay(conn, plan, plan.Flag);
            if (plan.Endorse && plan.Flag != "AP")
            {
                OpenDay(conn, plan, "AP");
            }
            return plan;
        }

        // 启用日期、结账（GL_mend 的 bFlag_AR / bFlag_AP，同 U8）。
        static void OpenDay(object conn, NotesProcPlan plan, string side)
        {
            string title = side == "AP" ? "应付" : "应收";
            DateTime start;
            if (WriteoffSql.StartDate(conn, side, out start) && ArapWriteoff.Day(plan.Date) < start)
            {
                throw State("处理日期早于" + title + "系统启用日期 " + start.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));
            }
            if (WriteoffSql.Closed(conn, side, plan.Year, plan.Period))
            {
                throw State("处理日期所在期间" + title + "已结账");
            }
        }

        static NoteHead Note(object conn, NotesProcAsk ask, string local)
        {
            NoteHead note = NotesProcSql.Head(conn, ask.Flag, ask.NoteCode, ask.NoteId);
            if (note == null || note.Id == 0)
            {
                string key = ask.NoteCode ?? ask.NoteId.ToString(CultureInfo.InvariantCulture);
                throw new BridgeException(404, "not_found", (ask.Flag == "AP" ? "应付" : "应收") + "票据 " + key + " 不存在");
            }
            HeadOk(note, local);
            return note;
        }

        // 票据表头本身的条件：本位币、有往来单位、有票据科目（登记时 COM 保存后才补科目，补失败的票据不能处理，否则 50 行科目为空）。
        internal static void HeadOk(NoteHead note, string local)
        {
            if (note.Currency.Length == 0)
            {
                note.Currency = local;
            }
            if (!string.Equals(note.Currency, local, StringComparison.Ordinal))
            {
                throw State("票据 " + note.Code + " 是外币票据（" + note.Currency + "），请在 U8 客户端处理");
            }
            if (note.Partner.Length == 0)
            {
                throw State("票据 " + note.Code + " 没有往来单位");
            }
            if (note.Km.Length == 0)
            {
                throw State("票据 " + note.Code + " 没有票据科目，请在 U8 里补全");
            }
        }

        static void Usable(object conn, NotesProcPlan plan, DateTime day)
        {
            NoteHead note = plan.Note;
            string label = "票据 " + note.Code;
            if (note.ChangeType != 0)
            {
                throw State(label + " 已换票，请在 U8 客户端处理");
            }
            if (NotesProcSql.Returned(conn, note.Link))
            {
                throw State(label + " 已退回");
            }
            if (note.Remain <= 0m)
            {
                throw State(label + " 已处理完（余额为 0）");
            }
            NotBefore(day, note.SignDate, label + " 的签发日期");
            NotBefore(day, note.ReceiptDate, label + " 的收票日期");
            if (note.SignDate.Length == 0)
            {
                note.SignDate = note.ReceiptDate.Length > 0 ? note.ReceiptDate : plan.Date;
            }
            CloseBillAudited(conn, plan);
            string ws = TransferSql.LockedBy(conn, note.VouchType, note.Code, note.Id);
            if (ws != null)
            {
                throw State(label + " 正在 U8 客户端被占用" + (ws.Trim().Length > 0 ? "（" + ws.Trim() + "）" : string.Empty) + "，请关闭后再试");
            }
        }

        static void NotBefore(DateTime day, string date, string what)
        {
            DateTime at;
            if (DateTime.TryParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out at) && day < at)
            {
                throw State("处理日期早于" + what + " " + date);
            }
        }

        // 登记时生成的收款单（48）/ 付款单（49）要已审核：没审核时票据科目还没入账。期初票据没有这张单。
        static void CloseBillAudited(object conn, NotesProcPlan plan)
        {
            if (plan.Note.Opening || plan.Note.CloseId <= 0)
            {
                return;
            }
            Dictionary<string, object> bill = NotesProcSql.CloseBill(conn, plan.Note.CloseId);
            if (bill != null && CoRows.Col(bill, "auditor").Trim().Length == 0)
            {
                throw State("票据 " + plan.Note.Code + " 登记生成的" + (plan.Flag == "AP" ? "付款单 " : "收款单 ")
                    + CoRows.Col(bill, "code").Trim() + " 未审核");
            }
        }

        // 本次金额：分包票据按子票区间（给了区间用它，否则从可用区间的最小号起取）；非分包票据只能整张处理（Whole）。
        static void Amount(object conn, NotesProcPlan plan, NotesProcAsk ask)
        {
            decimal? want = ask.Amount;
            if (want == null && ask.ApLines != null)
            {
                want = NotesProcReq.Total(ask.ApLines);
            }
            if (!plan.Note.Sub)
            {
                plan.Amount = Whole(plan.Note, want, ask.SubStart);
                return;
            }
            SubRange(conn, plan, ask, want);
        }

        // 非分包票据（bsubpackage=0）一次处理全部余额：缺省取余额，给了金额必须等于余额（同 U8，非分包票据整张处理）。
        internal static decimal Whole(NoteHead note, decimal? want, long subStart)
        {
            if (subStart > 0)
            {
                throw State("票据 " + note.Code + " 不是分包票据，不能指定子票区间");
            }
            if (want != null && want.Value != note.Remain)
            {
                throw State("整张票据须一次处理全部金额（余额 " + NotesProcRule.Money(note.Remain) + "）");
            }
            return note.Remain;
        }

        static void SubRange(object conn, NotesProcPlan plan, NotesProcAsk ask, decimal? want)
        {
            NoteHead note = plan.Note;
            List<NoteRange> avail = NotesProcSql.Avail(conn, note.Link);
            if (avail.Count == 0 || NotesProcRule.Sum(avail) != note.Remain)
            {
                throw State("票据 " + note.Code + " 的可用子票区间与余额不符，请在 U8 客户端处理");
            }
            NoteRange used = NotesProcRule.Pick(avail, want, ask.SubStart, ask.SubEnd);
            if (used == null)
            {
                throw State(ask.SubStart > 0 ? "子票区间不在票据 " + note.Code + " 的可用区间内"
                    : "处理金额超过票据的可用子票区间（第一段可用 " + NotesProcRule.Money(FirstAmount(avail)) + "）");
            }
            plan.Range = used;
            plan.Left = NotesProcRule.Leftover(avail, used);
            plan.Amount = NotesProcRule.RangeAmount(used.Start, used.End);
        }

        static decimal FirstAmount(List<NoteRange> avail)
        {
            NoteRange first = avail[0];
            foreach (NoteRange r in avail)
            {
                if (r.Start < first.Start)
                {
                    first = r;
                }
            }
            return NotesProcRule.RangeAmount(first.Start, first.End);
        }

        // 结算 / 贴现的银行科目：本年度的末级银行科目。银行名称缺省取上一级科目的名称（末级多是币种户）。
        static void Bank(object conn, NotesProcPlan plan, NotesProcAsk ask)
        {
            Dictionary<string, object> code = NotesProcSql.Code(conn, plan.Year, ask.BankCode);
            if (code == null)
            {
                throw new BridgeException(404, "not_found", "科目 " + ask.BankCode + " 不存在");
            }
            if (CoRows.Col(code, "bend") != "1")
            {
                throw State("科目 " + ask.BankCode + " 不是末级科目");
            }
            if (CoRows.Col(code, "bbank") != "1")
            {
                throw State("科目 " + ask.BankCode + " 不是银行科目");
            }
            plan.BankCode = CoRows.Col(code, "ccode").Trim();
            plan.BankName = ask.BankName;
            if (plan.BankName.Length == 0)
            {
                int grade = CoRows.AsId(CoRows.Col(code, "grade"));
                string parent = grade > 2 ? NotesProcSql.ParentName(conn, plan.Year, plan.BankCode, grade) : "";
                plan.BankName = parent.Length > 0 ? parent : CoRows.Col(code, "name").Trim();
            }
        }

        static void Discount(NotesProcPlan plan, NotesProcAsk ask)
        {
            plan.Net = plan.Amount;
            if (plan.Op != "discount")
            {
                return;
            }
            plan.Expense = ask.Expense;
            plan.Interest = ask.Interest;
            plan.Rate = ask.Rate;
            plan.Net = NotesProcRule.Net(plan.Amount, plan.Interest, plan.Expense);
            if (plan.Net <= 0m)
            {
                throw State("贴现净额必须大于 0（金额 " + NotesProcRule.Money(plan.Amount) + "，费用 " + NotesProcRule.Money(plan.Expense) + "）");
            }
        }

        internal static BridgeException State(string message)
        {
            return new BridgeException(409, "state_mismatch", message);
        }
    }
}
