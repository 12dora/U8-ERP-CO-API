using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 应收冲应付 / 应付冲应收的闸门：在事务里、带锁读各单据之后，写之前（同 U8 转账界面的校验）。
    // 转账日期（= 登录日期）不早于应收、应付启用日期，所在期间应收、应付都未结账；客户、供应商存在；
    // 每张单据存在、已审核（有审核行）、往来单位对、不受审批流控制、没被锁、没被 U8 客户端占用（LockVouch）、币种与转账币种相同、
    // 日期不晚于转账日期、没有关联合同（U8 会另写合同的预收预付，本接口不做）、不是红字（走红票对冲），金额不超过可转账余额。
    // 单据不存在 404；line_id 不属于本单 400；审批流 409 workflow_enabled；其余 409 state_mismatch。
    internal static class ArapTransferGate
    {
        public static TransferPlan Plan(WorkContext ctx, TransferAsk ask)
        {
            object conn = ctx.Conn;
            TransferPlan plan = Start(conn, ctx, ask);
            DateTime day = ArapWriteoff.Day(plan.Date);
            foreach (TransferAskLine line in ask.Ar)
            {
                plan.Ar.Add(Doc(conn, plan, line, day));
            }
            foreach (TransferAskLine line in ask.Ap)
            {
                plan.Ap.Add(Doc(conn, plan, line, day));
            }
            NativeEqual(plan);
            return plan;
        }

        static TransferPlan Start(object conn, WorkContext ctx, TransferAsk ask)
        {
            TransferPlan plan = new TransferPlan();
            plan.Flag = ask.Flag;
            plan.Date = ctx.Item.Date;
            plan.Customer = ask.Customer;
            plan.Vendor = ask.Vendor;
            plan.Sum = ask.Sum;
            plan.Operator = ctx.OperatorName;
            plan.Local = WriteoffSql.LocalCurrency(conn);
            plan.Currency = ask.Currency.Length == 0 ? plan.Local : ask.Currency;
            plan.Home = string.Equals(plan.Currency, plan.Local, StringComparison.Ordinal);
            OpenDay(conn, plan, ArapWriteoff.Day(plan.Date), ctx.Item.Acc);
            string cus = TransferSql.PartnerName(conn, "AR", ask.Customer);
            if (cus == null)
            {
                throw new BridgeException(404, "not_found", "客户 " + ask.Customer + " 不存在");
            }
            string ven = TransferSql.PartnerName(conn, "AP", ask.Vendor);
            if (ven == null)
            {
                throw new BridgeException(404, "not_found", "供应商 " + ask.Vendor + " 不存在");
            }
            plan.Digest = Digest(ask, cus, ven);
            return plan;
        }

        // 缺省摘要同 U8 界面：「应收冲应付 供应商名」/「应付冲应收 客户名」（对方单位的名称），最多 120 字。
        internal static string Digest(TransferAsk ask, string cus, string ven)
        {
            if (ask.Digest.Length > 0)
            {
                return ask.Digest;
            }
            string other = ask.Flag == "AP" ? cus : ven;
            if (other.Length == 0)
            {
                other = ask.Flag == "AP" ? ask.Customer : ask.Vendor;
            }
            string text = ArapTransferRule.Title(ask.Flag) + " " + other;
            return text.Length > ArapVoucherReq.DigestMax ? text.Substring(0, ArapVoucherReq.DigestMax) : text;
        }

        // 转账同时动应收、应付两边的往来，两个系统的启用日期、结账都要查（U8 查 GL_mend 的 bFlag_AR、bFlag_AP）。
        static void OpenDay(object conn, TransferPlan plan, DateTime day, string acc)
        {
            int[] period = WriteoffSql.PeriodOf(conn, acc, plan.Date);
            if (period == null)
            {
                throw State("转账日期不在 U8 的会计期间内");
            }
            plan.Year = period[0];
            plan.Period = period[1];
            foreach (string side in new string[] { "AR", "AP" })
            {
                string title = side == "AP" ? "应付" : "应收";
                DateTime start;
                if (WriteoffSql.StartDate(conn, side, out start) && day < start)
                {
                    throw State("转账日期早于" + title + "系统启用日期 " + start.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));
                }
                if (WriteoffSql.Closed(conn, side, plan.Year, plan.Period))
                {
                    throw State("转账日期所在期间" + title + "已结账");
                }
            }
        }

        // 票据背书的应付单据（NotesProcEndorse）同样经这里过闸门。
        internal static TransferDoc Doc(object conn, TransferPlan plan, TransferAskLine line, DateTime day)
        {
            TransferDoc d = new TransferDoc();
            d.Ask = line;
            d.Side = ArapTransferRule.SideOf(line.Type);
            d.Kind = ArapTransferRule.KindOf(line.Type);
            d.Title = ArapTransferRule.TitleOf(line.Type);
            d.Head = Head(conn, d);
            d.Id = d.Head == null ? 0 : CoRows.AsId(CoRows.Col(d.Head, "id"));
            if (d.Id == 0)
            {
                throw new BridgeException(404, "not_found", d.Label + " 不存在");
            }
            string dw = plan.DwOf(d.Side);
            Audited(conn, d, dw);
            Usable(conn, d);
            Same(plan, d, day);
            d.Rate = Rate(plan, d);
            List<TransferSlot> slots = ArapTransferSlots.Of(conn, d, dw);
            List<TransferPiece> pieces = ArapTransferRule.Spread(d, slots, line.Amount, plan.Home);
            if (pieces == null && ArapTransferSlots.Red(conn, d, dw, slots))
            {
                throw State(d.Label + " 是红字单据，请用红票对冲");
            }
            if (pieces == null)
            {
                throw State(d.Label + " 的转账金额超过可转账余额 " + ArapTransferRule.Money(Total(slots)));
            }
            d.Pieces = pieces;
            ArapTransferSlots.Before(conn, d, dw);
            return d;
        }

        static decimal Total(List<TransferSlot> slots)
        {
            decimal sum = 0m;
            foreach (TransferSlot s in slots)
            {
                sum += Math.Max(0m, s.RemainF);
            }
            return sum;
        }

        // 按（单号、类型）带锁读表头。类型或应收 / 应付标志对不上当作不存在。
        static Dictionary<string, object> Head(object conn, TransferDoc d)
        {
            Dictionary<string, object> head = Rows.One(conn, WriteoffKind.Of(d.Kind).CodeSql, new object[] { d.Ask.Code, d.Ask.Type });
            bool flagOk = head == null || d.Kind == "sale_invoice" || d.Kind == "purchase_invoice"
                || CoRows.Col(head, "flag") == d.Side;
            return flagOk && head != null && CoRows.Col(head, "vtype") == d.Ask.Type ? head : null;
        }

        // 已审核：表头有审核人，往来明细上有审核行（cProcStyle = cVouchType），审核行的往来单位就是请求的客户 / 供应商。
        static void Audited(object conn, TransferDoc d, string dw)
        {
            string side = d.Side == "AP" ? "应付" : "应收";
            string partner = CoRows.Col(d.Head, "auditor").Length == 0 ? null
                : WriteoffSql.Partner(conn, d.Side, d.Ask.Type, d.Ask.Code);
            if (partner == null)
            {
                throw State(d.Label + " 未" + side + "审核");
            }
            if (!string.Equals(partner.Trim(), dw, StringComparison.OrdinalIgnoreCase))
            {
                throw State(Elsewhere(conn, d, dw, partner.Trim()));
            }
        }

        // 往来单位不符的说明，点出余额现在所在的单位：并账转入请求的单位时处理行无从抄起，请在 U8 客户端转账。
        static string Elsewhere(object conn, TransferDoc d, string dw, string partner)
        {
            string who = d.Side == "AP" ? "供应商 " : "客户 ";
            string holder = TransferSql.Holder(conn, d);
            if (holder == null)
            {
                return d.Label + " 的往来单位是" + who + partner + "，不是 " + dw;
            }
            if (string.Equals(holder, dw, StringComparison.OrdinalIgnoreCase))
            {
                return d.Label + " 由并账转入" + who + dw + "，请在 U8 客户端转账";
            }
            return d.Label + " 的余额在" + who + holder + " 名下，不是 " + dw;
        }

        static void Usable(object conn, TransferDoc d)
        {
            if (CoRows.FlagOf(d.Head, "wf"))
            {
                throw new BridgeException(409, "workflow_enabled", d.Label + " 受审批流控制，不能通过接口转账");
            }
            if (CoRows.FlagOf(d.Head, "locked"))
            {
                throw State(d.Label + " 正被其他操作锁定");
            }
            string ws = TransferSql.LockedBy(conn, d.Ask.Type, d.Ask.Code, d.Id);
            if (ws != null)
            {
                throw State(d.Label + " 正在 U8 客户端被占用" + (ws.Trim().Length > 0 ? "（" + ws.Trim() + "）" : string.Empty)
                    + "，请关闭后再试");
            }
        }

        // 币种与转账币种相同（表头为空按本位币）；单据日期不晚于转账日期。
        static void Same(TransferPlan plan, TransferDoc d, DateTime day)
        {
            string cur = CoRows.Col(d.Head, "cur");
            if (cur.Length == 0)
            {
                cur = plan.Local;
            }
            if (!string.Equals(cur, plan.Currency, StringComparison.Ordinal))
            {
                throw State(d.Label + " 的币种是 " + cur + "，与转账币种 " + plan.Currency + " 不一致");
            }
            string date = CoRows.Col(d.Head, "vdate");
            DateTime at;
            if (DateTime.TryParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out at) && at > day)
            {
                throw State("转账日期早于" + d.Label + " 的日期 " + date);
            }
        }

        // 外币按单据表头的汇率折本币（必须大于 0）；本位币不用汇率。
        static decimal Rate(TransferPlan plan, TransferDoc d)
        {
            if (plan.Home)
            {
                return 1m;
            }
            decimal rate = WriteoffSql.Num(CoRows.Col(d.Head, "rate"));
            if (rate <= 0m)
            {
                throw State(d.Label + " 的汇率无效（外币汇率应大于 0）");
            }
            return rate;
        }

        // 外币转账：两侧折合本币的合计必须相等，否则转账凭证借贷不平（差额是期末汇兑损益的事，请先在 U8 客户端处理）。
        internal static void NativeEqual(TransferPlan plan)
        {
            if (plan.Home)
            {
                return;
            }
            decimal ar = Native(plan.Ar);
            decimal ap = Native(plan.Ap);
            if (ar != ap)
            {
                throw State("外币转账两侧折合本币不等（应收 " + ArapTransferRule.Money(ar) + "，应付 " + ArapTransferRule.Money(ap)
                    + "），请在 U8 客户端处理");
            }
        }

        static decimal Native(List<TransferDoc> docs)
        {
            decimal sum = 0m;
            foreach (TransferDoc d in docs)
            {
                foreach (TransferPiece p in d.Pieces)
                {
                    sum += p.N;
                }
            }
            return sum;
        }

        internal static BridgeException State(string message)
        {
            return new BridgeException(409, "state_mismatch", message);
        }
    }
}
