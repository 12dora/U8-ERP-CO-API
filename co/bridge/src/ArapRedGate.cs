using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 红票对冲的闸门（事务里、带锁读各单据之后，调 U8 之前；U8 FrmRed / FrmRedTJ 的检查）：
    // 对冲日期（= 登录日期）不早于该系统启用日期、所在期间未结账；往来单位存在；
    // 每张单据存在、已审核（有审核行）、往来单位就是 partner、不受审批流控制、没被锁、没被 U8 客户端占用（LockVouch）、
    // 币种与对冲币种相同、日期不晚于对冲日期、没有关联合同；红字一侧余额为负（审核行 cSign=F），蓝字一侧余额为正（cSign=Z），
    // 金额不超过可对冲余额。外币时两侧折本币合计必须相等。
    // 单据不存在 404；line_id 不属于本单 400；审批流 409 workflow_enabled；其余 409 state_mismatch。
    internal static class ArapRedGate
    {
        public static RedPlan Plan(WorkContext ctx, RedAsk ask)
        {
            object conn = ctx.Conn;
            RedPlan plan = Start(conn, ctx, ask);
            DateTime day = ArapWriteoff.Day(plan.Date);
            foreach (TransferAskLine line in ask.Red)
            {
                plan.Red.Add(Doc(conn, plan, line, true, day));
            }
            foreach (TransferAskLine line in ask.Blue)
            {
                plan.Blue.Add(Doc(conn, plan, line, false, day));
            }
            plan.Rate = plan.Red[0].Core.Rate;
            NativeEqual(plan);
            return plan;
        }

        static RedPlan Start(object conn, WorkContext ctx, RedAsk ask)
        {
            RedPlan plan = new RedPlan();
            plan.Flag = ask.Flag;
            plan.Date = ctx.Item.Date;
            plan.Partner = ask.Partner;
            plan.Sum = ask.Sum;
            plan.Local = WriteoffSql.LocalCurrency(conn);
            plan.Currency = ask.Currency.Length == 0 ? plan.Local : ask.Currency;
            plan.Home = string.Equals(plan.Currency, plan.Local, StringComparison.Ordinal);
            OpenDay(conn, plan, ArapWriteoff.Day(plan.Date), ctx.Item.Acc);
            if (TransferSql.PartnerName(conn, plan.Flag, plan.Partner) == null)
            {
                throw new BridgeException(404, "not_found", (plan.Flag == "AP" ? "供应商 " : "客户 ") + plan.Partner + " 不存在");
            }
            return plan;
        }

        static void OpenDay(object conn, RedPlan plan, DateTime day, string acc)
        {
            int[] period = WriteoffSql.PeriodOf(conn, acc, plan.Date);
            if (period == null)
            {
                throw State("对冲日期不在 U8 的会计期间内");
            }
            plan.Year = period[0];
            plan.Period = period[1];
            string title = plan.Flag == "AP" ? "应付" : "应收";
            DateTime start;
            if (WriteoffSql.StartDate(conn, plan.Flag, out start) && day < start)
            {
                throw State("对冲日期早于" + title + "系统启用日期 " + start.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));
            }
            if (WriteoffSql.Closed(conn, plan.Flag, plan.Year, plan.Period))
            {
                throw State("对冲日期所在期间" + title + "已结账");
            }
        }

        static RedDoc Doc(object conn, RedPlan plan, TransferAskLine line, bool red, DateTime day)
        {
            RedDoc d = new RedDoc();
            d.Red = red;
            TransferDoc core = new TransferDoc();
            core.Ask = line;
            core.Side = plan.Flag;
            core.Kind = ArapTransferRule.KindOf(line.Type);
            core.Title = ArapTransferRule.TitleOf(line.Type);
            core.Head = Head(conn, core);
            core.Id = core.Head == null ? 0 : CoRows.AsId(CoRows.Col(core.Head, "id"));
            if (core.Id == 0)
            {
                throw new BridgeException(404, "not_found", core.Label + " 不存在");
            }
            d.Core = core;
            Audited(conn, plan, core);
            Usable(conn, core);
            Same(plan, core, day);
            core.Rate = Rate(plan, core);
            List<RedSlot> slots = Slots(conn, plan, d);
            List<RedPiece> pieces = ArapRedRule.Spread(red, slots, line.Amount, plan.Home, core.Rate);
            if (pieces == null)
            {
                throw State(d.Label + " 的对冲金额超过可对冲余额 " + ArapTransferRule.Money(ArapRedRule.Available(red, slots)));
            }
            d.Pieces = pieces;
            Before(conn, plan, d);
            return d;
        }

        // 按（单号、类型）带锁读表头；应收应付单的应收 / 应付标志、类型对不上当作不存在。
        static Dictionary<string, object> Head(object conn, TransferDoc d)
        {
            Dictionary<string, object> head = Rows.One(conn, WriteoffKind.Of(d.Kind).CodeSql, new object[] { d.Ask.Code, d.Ask.Type });
            if (head == null || CoRows.Col(head, "vtype") != d.Ask.Type)
            {
                return null;
            }
            bool bill = d.Kind == "ar_bill" || d.Kind == "ap_bill";
            return !bill || CoRows.Col(head, "flag") == d.Side ? head : null;
        }

        static void Audited(object conn, RedPlan plan, TransferDoc d)
        {
            string partner = CoRows.Col(d.Head, "auditor").Length == 0 ? null
                : WriteoffSql.Partner(conn, d.Side, d.Ask.Type, d.Ask.Code);
            if (partner == null)
            {
                throw State(d.Label + " 未" + (d.Side == "AP" ? "应付" : "应收") + "审核");
            }
            if (!string.Equals(partner.Trim(), plan.Partner, StringComparison.OrdinalIgnoreCase))
            {
                throw State(d.Label + " 的往来单位不是" + (d.Side == "AP" ? "供应商 " : "客户 ") + plan.Partner);
            }
        }

        static void Usable(object conn, TransferDoc d)
        {
            if (CoRows.FlagOf(d.Head, "wf"))
            {
                throw new BridgeException(409, "workflow_enabled", d.Label + " 受审批流控制，不能通过接口红票对冲");
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

        static void Same(RedPlan plan, TransferDoc d, DateTime day)
        {
            string cur = CoRows.Col(d.Head, "cur");
            if (cur.Length == 0)
            {
                cur = plan.Local;
            }
            if (!string.Equals(cur, plan.Currency, StringComparison.Ordinal))
            {
                throw State(d.Label + " 的币种是 " + cur + "，与对冲币种 " + plan.Currency + " 不一致");
            }
            DateTime at;
            string date = CoRows.Col(d.Head, "vdate");
            if (DateTime.TryParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out at) && at > day)
            {
                throw State("对冲日期早于" + d.Label + " 的日期 " + date);
            }
        }

        static decimal Rate(RedPlan plan, TransferDoc d)
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

        // 可对冲的行：给了 line_id 只取那一行（不属于本单 400）；应收应付单只取整单（行 0）。这一侧一行都没有时说明是红蓝放反了。
        static List<RedSlot> Slots(object conn, RedPlan plan, RedDoc d)
        {
            TransferDoc core = d.Core;
            int want = core.Ask.Line;
            if (want > 0 && !WriteoffSql.LineOf(conn, WriteoffKind.Of(core.Kind), want, core.Id))
            {
                throw new BridgeException(400, "bad_request", "明细行不存在", "line_id");
            }
            bool bill = ArapMergeReq.IsBill(core.Ask.Type);
            List<RedSlot> slots = new List<RedSlot>();
            foreach (RedSlot s in ArapRedSql.Slots(conn, core, plan.Partner))
            {
                if (Hit(bill, want, s.Line) && ArapRedRule.Open(d.Red, s.SignedF) > 0m)
                {
                    slots.Add(s);
                }
            }
            if (slots.Count == 0)
            {
                throw State(d.Label + (want > 0 ? " 的行 " + want.ToString(CultureInfo.InvariantCulture) : string.Empty)
                    + (d.Red ? " 没有红字（负数）余额，不能放在 red 一侧" : " 没有蓝字（正数）余额，不能放在 blue 一侧"));
            }
            return slots;
        }

        static bool Hit(bool bill, int want, int line)
        {
            if (bill)
            {
                return line == 0;
            }
            return line > 0 && (want == 0 || line == want);
        }

        // 每一行都要有审核行（U8 从它复制处理行），符号与这一侧一致，没有关联合同；记下发票行累计核销、应收应付单表头余额，写后核对用。
        static void Before(object conn, RedPlan plan, RedDoc d)
        {
            TransferDoc core = d.Core;
            string want = d.Red ? "F" : "Z";
            foreach (RedPiece p in d.Pieces)
            {
                Dictionary<string, object> sign = ArapRedSql.SignRow(conn, core, p.Line, plan.Partner);
                string at = d.Label + (p.Line > 0 ? " 的行 " + p.Line.ToString(CultureInfo.InvariantCulture) : string.Empty);
                if (sign == null)
                {
                    throw State(at + " 没有审核记录，不能红票对冲");
                }
                string csign = CoRows.Col(sign, "csign").Trim();
                if (csign.Length > 0 && csign != want)
                {
                    throw State(at + (d.Red ? " 是蓝字单据，不能放在 red 一侧" : " 是红字单据，不能放在 blue 一侧"));
                }
                if (CoRows.Col(sign, "contract").Trim().Length > 0)
                {
                    throw State(d.Label + " 关联了合同，请在 U8 客户端红票对冲");
                }
                p.Sign = sign;
                if (p.Line > 0)
                {
                    decimal[] acc = TransferSql.InvoiceAcc(conn, core.Ask.Type, p.Line);
                    p.AccA = acc[0];
                    p.AccB = acc[1];
                }
            }
            if (ArapMergeReq.IsBill(core.Ask.Type))
            {
                d.HeadBefore = UnwriteoffSql.BillRemain(conn, core.Side, core.Ask.Type, core.Ask.Code);
            }
        }

        // 外币：两侧折本币合计必须相等，否则处理行借贷不平（差额是期末汇兑损益的事，请先在 U8 客户端处理）。
        internal static void NativeEqual(RedPlan plan)
        {
            if (plan.Home)
            {
                return;
            }
            decimal red = Native(plan.Red);
            decimal blue = Native(plan.Blue);
            if (red != blue)
            {
                throw State("外币红票对冲两侧折合本币不等（红字 " + ArapTransferRule.Money(red) + "，蓝字 " + ArapTransferRule.Money(blue)
                    + "），请在 U8 客户端处理");
            }
        }

        static decimal Native(List<RedDoc> docs)
        {
            decimal sum = 0m;
            foreach (RedDoc d in docs)
            {
                foreach (RedPiece p in d.Pieces)
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
