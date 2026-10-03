using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 一张被核销单据（的一行）：过了闸门之后的实际行、单号、日期和核销前余额。
    internal sealed class WriteoffTarget
    {
        public WriteoffKind Kind;
        public int Id;
        // 发票是表体行主键（= 往来明细 iBVid）；应收单、应付单为 0（整单）。
        public int Line;
        public string Code;
        public string VType;
        public string Date;
        public decimal Amount;
        public decimal Before;
        // 表头（数据权限按它的往来单位、部门、业务员）。
        public Dictionary<string, object> Head;
    }

    // 过了闸门的核销计划：收付款单一行 + 若干目标。Head 是收付款单表头（数据权限按它）。
    internal sealed class WriteoffPlan
    {
        public string Flag;
        public int ReceiptId;
        public string ReceiptKind;
        public string ReceiptType;
        public string ReceiptCode;
        public string ReceiptDate;
        public string Dw;
        // 收付款单的币种（表头为空按本位币）和汇率；被核销单据必须同币种，汇率可以不同，核销行一律按收付款单的汇率。
        public string Currency;
        public decimal Rate;
        public int Line;
        public bool Prepay;
        public decimal RemainBefore;
        // Save 之前该收付款单最大的行主键：预收 / 预付行核销时 U8 可能另插 Ap_CloseBills 行，核对时把新行一并算上。
        public int LineMax;
        public decimal Sum;
        public string CancelNo;
        public Dictionary<string, object> Head;
        public List<WriteoffTarget> Targets;

        public string Side
        {
            get { return Flag == "AP" ? "应付" : "应收"; }
        }
    }

    // 核销的闸门：在事务里、带锁读收付款单和各目标单据之后，调用 U8 之前。
    // 以 AR / AP 登录时 U8 的 Save 不查日期（启用日期、结账）、期间、锁（只在 PO 登录时查），这些由桥自己查。
    // 单据不存在 404；line_id 不属于本单 400；审批流 409 workflow_enabled；其余拒绝 409 state_mismatch，
    // 包括币种与收付款单不一致。单据汇率与收付款单不同照样核销（同 U8：核销行按收付款单汇率，汇兑差额留给期末汇兑损益 9M，
    // 本接口不做 9M）。U8 的 Locked_by_Other（LockVouch 表）没有研究，不查（docs/limitations.md）。
    internal static class ArapWriteoffGate
    {
        public static WriteoffPlan Plan(object conn, WriteoffAsk ask, DateTime day, string acc)
        {
            string local = WriteoffSql.LocalCurrency(conn);
            WriteoffPlan plan = Receipt(conn, ask, day, local);
            HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (WriteoffAskItem item in ask.Items)
            {
                WriteoffTarget t = Target(conn, plan, item, day, local);
                if (!seen.Add(t.Kind.Name + ":" + t.Id.ToString(CultureInfo.InvariantCulture) + ":"
                    + t.Line.ToString(CultureInfo.InvariantCulture)))
                {
                    throw new BridgeException(400, "bad_request", "items 里有重复的单据行");
                }
                plan.Targets.Add(t);
                plan.Sum += t.Amount;
            }
            if (plan.Sum > plan.RemainBefore)
            {
                throw State("核销金额合计超过收付款单该行的未核销余额 " + Money(plan.RemainBefore));
            }
            OpenDay(conn, plan.Flag, plan.Side, day, acc);
            return plan;
        }

        // 核销日期不早于应收（应付）系统启用日期；所在会计期间按 UFSYSTEM..UA_Period 定（U8 Save 同一查询，
        // 不一定是自然月），GL_mend 上该期应收（应付）未结账。
        static void OpenDay(object conn, string flag, string side, DateTime day, string acc)
        {
            DateTime start;
            if (WriteoffSql.StartDate(conn, flag, out start) && day < start)
            {
                throw State("核销日期早于" + side + "系统启用日期 " + start.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));
            }
            int[] period = WriteoffSql.PeriodOf(conn, acc, day.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));
            if (period == null)
            {
                throw State("核销日期不在 U8 的会计期间内");
            }
            if (WriteoffSql.Closed(conn, flag, period[0], period[1]))
            {
                throw State(side + "已结账");
            }
        }

        // 审批流控制的单据 409 workflow_enabled，网络锁 409。
        public static void Usable(Dictionary<string, object> head, string what)
        {
            if (CoRows.FlagOf(head, "wf"))
            {
                throw new BridgeException(409, "workflow_enabled", what + "受审批流控制，不能通过接口核销");
            }
            if (CoRows.FlagOf(head, "locked"))
            {
                throw State(what + "正被其他操作锁定");
            }
        }

        static WriteoffPlan Receipt(object conn, WriteoffAsk ask, DateTime day, string local)
        {
            Dictionary<string, object> head = WriteoffSql.Receipt(conn, ask.ReceiptId);
            string vtype = ask.Flag == "AP" ? "49" : "48";
            if (head == null || CoRows.Col(head, "flag") != ask.Flag || CoRows.Col(head, "vtype") != vtype)
            {
                throw new BridgeException(404, "not_found", (ask.Flag == "AP" ? "付款单" : "收款单") + "不存在");
            }
            WriteoffPlan plan = new WriteoffPlan();
            plan.Flag = ask.Flag;
            plan.Head = head;
            plan.ReceiptId = ask.ReceiptId;
            plan.ReceiptKind = ask.ReceiptKind;
            plan.ReceiptType = vtype;
            plan.ReceiptCode = CoRows.Col(head, "code");
            plan.ReceiptDate = CoRows.Col(head, "vdate");
            plan.Dw = CoRows.Col(head, "cDwCode");
            plan.Currency = Cur(head, local);
            plan.Targets = new List<WriteoffTarget>();
            if (CoRows.Col(head, "auditor").Length == 0)
            {
                throw State("收付款单未审核");
            }
            Usable(head, "收付款单");
            plan.Rate = ReceiptRate(head, plan.Currency, local);
            NotBefore(plan.ReceiptDate, day, "收付款单");
            PickLine(conn, plan, ask.ReceiptLine);
            return plan;
        }

        static void PickLine(object conn, WriteoffPlan plan, int want)
        {
            Dictionary<string, object> hit = null;
            int open = 0;
            foreach (Dictionary<string, object> row in WriteoffSql.ReceiptRows(conn, plan.ReceiptId))
            {
                bool positive = WriteoffSql.Num(CoRows.Col(row, "rem_f")) > 0;
                if (want > 0 ? CoRows.AsId(CoRows.Col(row, "line")) == want : positive)
                {
                    hit = row;
                    open++;
                }
            }
            if (want > 0 && hit == null)
            {
                throw new BridgeException(400, "bad_request", "明细行不存在");
            }
            if (open > 1)
            {
                throw new BridgeException(400, "bad_request", "收付款单有多行未核销余额，请指定 receipt.line_id");
            }
            decimal remain = hit == null ? 0m : WriteoffSql.Num(CoRows.Col(hit, "rem_f"));
            if (remain <= 0)
            {
                throw State("收付款单没有未核销余额");
            }
            plan.Line = CoRows.AsId(CoRows.Col(hit, "line"));
            plan.Prepay = CoRows.FlagOf(hit, "prepay");
            plan.RemainBefore = remain;
        }

        static WriteoffTarget Target(object conn, WriteoffPlan plan, WriteoffAskItem item, DateTime day, string local)
        {
            WriteoffKind kind = WriteoffKind.Of(item.Kind);
            Dictionary<string, object> head = WriteoffSql.Head(conn, kind, item.Id);
            string vtype = CoRows.Col(head, "vtype");
            bool flagOk = kind.LineSql != null || CoRows.Col(head, "flag") == kind.Flag;
            if (head == null || !flagOk || Array.IndexOf(kind.Types, vtype) < 0)
            {
                throw new BridgeException(404, "not_found", kind.Title + "不存在");
            }
            WriteoffTarget t = new WriteoffTarget();
            t.Kind = kind;
            t.Id = item.Id;
            t.Amount = item.Amount;
            t.Code = CoRows.Col(head, "code");
            t.VType = vtype;
            t.Date = CoRows.Col(head, "vdate");
            t.Head = head;
            string partner = CoRows.Col(head, "auditor").Length == 0 ? null
                : WriteoffSql.Partner(conn, kind.Flag, vtype, t.Code);
            if (partner == null)
            {
                throw State(kind.Title + " " + t.Code + " 未" + plan.Side + "审核");
            }
            if (!string.Equals(partner.Trim(), plan.Dw, StringComparison.OrdinalIgnoreCase))
            {
                throw State(kind.Title + " " + t.Code + " 的往来单位与收付款单不一致");
            }
            Usable(head, kind.Title + " " + t.Code);
            Same(plan, head, kind.Title + " " + t.Code, local);
            NotBefore(t.Date, day, kind.Title + " " + t.Code);
            t.Line = TargetLine(conn, plan, t, item.Line);
            t.Before = WriteoffSql.Balance(conn, plan.Flag, t.VType, t.Code, plan.Dw, t.Line);
            if (t.Amount > t.Before)
            {
                throw State(kind.Title + " " + t.Code + " 的核销金额超过未核销余额 " + Money(t.Before));
            }
            return t;
        }

        // 应收单、应付单整单（0）；发票给了 line_id 要属于本单，没给时取唯一有余额的行。
        static int TargetLine(object conn, WriteoffPlan plan, WriteoffTarget t, int want)
        {
            if (t.Kind.LineSql == null)
            {
                return 0;
            }
            if (want > 0)
            {
                if (!WriteoffSql.LineOf(conn, t.Kind, want, t.Id))
                {
                    throw new BridgeException(400, "bad_request", "明细行不存在");
                }
                return want;
            }
            Dictionary<int, decimal> open = WriteoffSql.OpenLines(conn, plan.Flag, t.VType, t.Code, plan.Dw);
            if (open.Count > 1)
            {
                throw new BridgeException(400, "bad_request", t.Kind.Title + " " + t.Code + " 有多行未核销余额，请指定 line_id");
            }
            foreach (int line in open.Keys)
            {
                return line;
            }
            throw State(t.Kind.Title + " " + t.Code + " 没有未核销余额");
        }

        // 币种：表头为空按本位币。
        static string Cur(Dictionary<string, object> head, string local)
        {
            string cur = CoRows.Col(head, "cur");
            return cur.Length == 0 ? local : cur;
        }

        // 收付款单的汇率：本位币必须是 1，外币必须大于 0。
        static decimal ReceiptRate(Dictionary<string, object> head, string cur, string local)
        {
            decimal rate = WriteoffSql.Num(CoRows.Col(head, "rate"));
            bool home = string.Equals(cur, local, StringComparison.Ordinal);
            if (home ? rate != 1m : rate <= 0m)
            {
                throw State("收付款单的汇率无效（" + (home ? "本位币汇率应为 1" : "外币汇率应大于 0") + "）");
            }
            return rate;
        }

        // 被核销单据与收付款单同币种（汇率不比：单据汇率常与收付款单不同，U8 按收付款单汇率核销）。
        static void Same(WriteoffPlan plan, Dictionary<string, object> head, string what, string local)
        {
            if (!string.Equals(Cur(head, local), plan.Currency, StringComparison.Ordinal))
            {
                throw State(what + " 的币种与收付款单不一致");
            }
        }

        static void NotBefore(string date, DateTime day, string what)
        {
            DateTime at;
            if (DateTime.TryParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out at) && at > day)
            {
                throw State("核销日期早于" + what + "的日期 " + date);
            }
        }

        public static string Money(decimal value)
        {
            return value.ToString("0.00", CultureInfo.InvariantCulture);
        }

        public static BridgeException State(string message)
        {
            return new BridgeException(409, "state_mismatch", message);
        }
    }
}
