using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 过了闸门的取消票据处理计划。Ledger 是票据所在的账（= flag）；Rows 是该账上本批的往来明细（票据行 50，9C 另有生成的应收单行）；
    // Side 是背书（9E）对方账的取消计划（余额加回同应收冲应付，ArapProcCancel.Write），其余为 null；Bill 是退回（9C）生成的单据；
    // Ranges 是分包票据取消后的可用区间（不分包为 null）。Back / BackLocal 是票据要加回的余额（本批票据行的金额，贴现为票面）。
    internal sealed class NoteUndoPlan
    {
        public ProcCancelKind Kind;
        public string Flag;
        public string CancelNo;
        public string Ledger;
        public NoteUndoSub Sub;
        public NoteUndoHead Head;
        public List<ProcRow> Rows = new List<ProcRow>();
        public ProcCancelPlan Side;
        public NoteUndoBill Bill;
        public List<NoteAvailRange> Ranges;
        public decimal Back;
        public decimal BackLocal;
    }

    // 取消票据处理的闸门（事务里，带锁读，写之前）。处理号不存在 404；其余 409 state_mismatch，用语同取消操作（ArapProcCancelGate）。
    internal static class NotesUndoGate
    {
        const decimal Tolerance = 0.005m;
        internal const string Shape = "处理记录形状不符，请在 U8 客户端取消";
        internal const string Prepaid = "该背书形成了预付款，请在 U8 客户端取消";

        public static NoteUndoPlan Plan(object conn, ProcCancelAsk ask, string acc)
        {
            NoteUndoPlan plan = new NoteUndoPlan();
            plan.Kind = ask.Kind;
            plan.Flag = ask.Flag;
            plan.CancelNo = ask.CancelNo;
            plan.Ledger = ask.Kind.Ledgers[0];
            plan.Sub = OneSub(NotesUndoSql.Subs(conn, ask.Kind.Style, ask.CancelNo), plan);
            plan.Rows = ProcCancelSql.Batch(conn, plan.Ledger, ask.Kind.Style, ask.CancelNo, 11);
            CheckRows(plan);
            NoPrepay(conn, ask);
            decimal[] amount = NotesUndoSql.Amount(conn, plan.Ledger, ask.Kind.Style, ask.CancelNo);
            plan.Back = amount[0];
            plan.BackLocal = amount[1];
            plan.Head = NotesUndoSql.Head(conn, plan.Sub.Link);
            CheckHead(plan);
            if (NotesUndoSql.Later(conn, plan.Sub.Link, plan.Sub.Id) != null)
            {
                throw State(ArapProcCancelGate.Later);
            }
            OpenPeriod(conn, plan, acc);
            if (NotesUndoSql.KeepClue(conn, plan.Flag))
            {
                throw State("账套打开了「取消操作保留线索」选项，本接口不支持，请在 U8 客户端取消");
            }
            if (ask.Kind.Style == "9C")
            {
                plan.Bill = Bill(conn, plan);
            }
            if (plan.Head.Split)
            {
                plan.Ranges = Ranges(plan, NotesUndoSql.Ranges(conn, plan.Head.Link));
            }
            if (ask.Kind.Ledgers.Length > 1)
            {
                plan.Side = Side(conn, ask, acc);
            }
            return plan;
        }

        // 一个处理号对应一条票据处理行（U8 每次结算、贴现、背书、退回一张票据编一个号）。
        internal static NoteUndoSub OneSub(List<NoteUndoSub> subs, NoteUndoPlan plan)
        {
            if (subs.Count == 0)
            {
                throw new BridgeException(404, "not_found", "处理号 " + plan.CancelNo + " 不存在");
            }
            if (subs.Count > 1)
            {
                throw State("处理号 " + plan.CancelNo + " 下有多条票据处理记录，请在 U8 客户端取消");
            }
            NoteUndoSub sub = subs[0];
            if (sub.Flag != plan.Flag)
            {
                throw State(Shape);
            }
            if (sub.Pz.Length > 0)
            {
                throw State(ArapProcCancelGate.Vouchered);
            }
            return sub;
        }

        // 票据所在账上本批的往来明细（只按行本身判断，不查库）：本种处理、本账、没制单、没合同、同一期间；
        // 恰好一行票据行（50）；退回（9C）另恰好一行生成单据的行（类型、单号 = 处理行的 cCoVouchType / cCoVouchID），其余不认。
        internal static void CheckRows(NoteUndoPlan plan)
        {
            if (plan.Rows.Count == 0 || plan.Rows.Count > 10)
            {
                throw State("处理号 " + plan.CancelNo + " 的往来明细不完整，请在 U8 客户端取消");
            }
            int notes = 0;
            int bills = 0;
            foreach (ProcRow row in plan.Rows)
            {
                RowOk(plan, row);
                notes += row.VType == "50" && row.CoType == "50" ? 1 : 0;
                bills += IsBillRow(plan, row) ? 1 : 0;
            }
            bool returned = plan.Kind.Style == "9C";
            if (notes != 1 || bills != (returned ? 1 : 0) || notes + bills != plan.Rows.Count)
            {
                throw State(Shape);
            }
        }

        static void RowOk(NoteUndoPlan plan, ProcRow row)
        {
            ProcRow first = plan.Rows[0];
            if (row.Style != plan.Kind.Style || row.Flag != plan.Ledger || row.Period != first.Period || row.RegDate != first.RegDate)
            {
                throw State(Shape);
            }
            if (row.Pz.Length > 0)
            {
                throw State(ArapProcCancelGate.Vouchered);
            }
            if (row.Contract.Length > 0)
            {
                throw State("处理涉及合同，请在 U8 客户端取消");
            }
        }

        static bool IsBillRow(NoteUndoPlan plan, ProcRow row)
        {
            return plan.Kind.Style == "9C" && plan.Sub.CoId.Length > 0 && row.VType == plan.Sub.CoType && row.CoType == plan.Sub.CoType
                && row.VCode == plan.Sub.CoId && row.CoCode == plan.Sub.CoId;
        }

        // U8 做的背书冲应付超出应付单据时生成付款单（形成预付款，对方账有 49 行），取消要删这张付款单，桥不做，写之前拒绝。
        static void NoPrepay(object conn, ProcCancelAsk ask)
        {
            if (ask.Kind.Ledgers.Length > 1 && NotesUndoSql.Prepay(conn, ask.Kind.Ledgers[1], ask.Kind.Style, ask.CancelNo) != null)
            {
                throw State(Prepaid);
            }
        }

        // 票据存在、属于本账；加回后余额不超过票面。
        internal static void CheckHead(NoteUndoPlan plan)
        {
            NoteUndoHead head = plan.Head;
            if (head == null || head.Id == 0)
            {
                throw State("处理记录指向的票据不存在，请在 U8 客户端取消");
            }
            if (head.Flag != plan.Flag)
            {
                throw State(Shape);
            }
            if (plan.Back <= 0m || head.Remain + plan.Back > head.Face + Tolerance)
            {
                throw State("票据 " + head.Code + " 的余额与处理记录不符（余额 " + ArapWriteoffGate.Money(head.Remain) + "，加回 "
                    + ArapWriteoffGate.Money(plan.Back) + "，票面 " + ArapWriteoffGate.Money(head.Face) + "），请在 U8 客户端取消");
            }
        }

        // 处理登记期间票据所在的账未结账（对方账由 Side 的 ArapProcCancelGate.Plan 查）。
        static void OpenPeriod(object conn, NoteUndoPlan plan, string acc)
        {
            ProcRow first = plan.Rows[0];
            int[] found = WriteoffSql.PeriodOf(conn, acc, first.RegDate);
            int year = found != null ? found[0] : YearOf(first.RegDate);
            if (WriteoffSql.Closed(conn, plan.Ledger, year, first.Period))
            {
                throw State(ArapProcCancelGate.Side(plan.Ledger) + "已结账，不能取消该期间的处理");
            }
        }

        static int YearOf(string date)
        {
            DateTime day;
            if (DateTime.TryParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out day))
            {
                return day.Year;
            }
            throw State("处理记录没有登记日期");
        }

        // 退回生成的单据：存在、由票据生成（cCoVouchType 50）、没制单、除本批外没有被往来明细引用（核销、转账等）。
        static NoteUndoBill Bill(object conn, NoteUndoPlan plan)
        {
            NoteUndoSub sub = plan.Sub;
            NoteUndoBill bill = NotesUndoSql.Bill(conn, plan.Flag, sub.CoType, sub.CoId);
            string name = "退回生成的单据 " + sub.CoId;
            if (bill == null || bill.Id == 0)
            {
                throw State(name + " 不存在，请在 U8 客户端取消");
            }
            if (bill.CoType != "50")
            {
                throw State(name + " 不是由票据生成的，请在 U8 客户端取消");
            }
            if (bill.Pz.Length > 0)
            {
                throw State(name + " 已制单，请先取消制单");
            }
            if (NotesUndoSql.BillUsed(conn, plan.Ledger, bill, plan.Kind.Style, plan.CancelNo) != null)
            {
                throw State(name + " 已有核销或其他处理，请先取消这些处理");
            }
            return bill;
        }

        // 分包票据取消后的可用区间：现有区间合计必须等于票据余额，把本次占用的子票区间加回、相邻的合并；
        // 与现有区间重叠或超出票据的子票区间说明记录不一致，拒绝。
        internal static List<NoteAvailRange> Ranges(NoteUndoPlan plan, List<NoteAvailRange> existing)
        {
            NoteUndoSub sub = plan.Sub;
            NoteUndoHead head = plan.Head;
            if (!sub.Ranged || sub.Start <= 0 || sub.End < sub.Start || sub.Start < head.Start || sub.End > head.End)
            {
                throw State("分包票据 " + head.Code + " 的处理记录没有有效的子票区间，请在 U8 客户端取消");
            }
            decimal sum = 0m;
            foreach (NoteAvailRange r in existing)
            {
                sum += r.Amount;
            }
            if (Math.Abs(sum - head.Remain) > Tolerance)
            {
                throw State("分包票据 " + head.Code + " 的可用区间与余额不一致，请在 U8 客户端取消");
            }
            NoteAvailRange add = new NoteAvailRange();
            add.Start = sub.Start;
            add.End = sub.End;
            add.Amount = plan.Back;
            add.Local = plan.BackLocal;
            List<NoteAvailRange> merged = Merge(existing, add);
            if (merged == null)
            {
                throw State("分包票据 " + head.Code + " 的子票区间与现有可用区间重叠，请在 U8 客户端取消");
            }
            return merged;
        }

        // 按起点排序后插入 add，首尾相接（前一段止号 + 1 = 后一段起号）的合并、金额相加；有重叠返回 null。
        internal static List<NoteAvailRange> Merge(List<NoteAvailRange> existing, NoteAvailRange add)
        {
            List<NoteAvailRange> all = new List<NoteAvailRange>();
            foreach (NoteAvailRange r in existing)
            {
                all.Add(Copy(r));
            }
            all.Add(Copy(add));
            all.Sort(delegate(NoteAvailRange a, NoteAvailRange b) { return a.Start.CompareTo(b.Start); });
            List<NoteAvailRange> merged = new List<NoteAvailRange>();
            foreach (NoteAvailRange r in all)
            {
                NoteAvailRange last = merged.Count == 0 ? null : merged[merged.Count - 1];
                if (last != null && r.Start <= last.End)
                {
                    return null;
                }
                if (last != null && r.Start == last.End + 1)
                {
                    last.End = r.End;
                    last.Amount += r.Amount;
                    last.Local += r.Local;
                    continue;
                }
                merged.Add(r);
            }
            return merged;
        }

        static NoteAvailRange Copy(NoteAvailRange r)
        {
            NoteAvailRange c = new NoteAvailRange();
            c.Start = r.Start;
            c.End = r.End;
            c.Amount = r.Amount;
            c.Local = r.Local;
            return c;
        }

        // 背书（9E）对方账：被背书单位的应付单据（采购发票、应付单、付款单）余额加回同取消应收冲应付。
        // 走 ArapProcCancelGate.Plan：带锁读处理行、单据表头、取消前余额、对方账期间、之后的处理、（应收）未审核收款单。
        static ProcCancelPlan Side(object conn, ProcCancelAsk ask, string acc)
        {
            string other = ask.Kind.Ledgers[1];
            if (ProcCancelSql.CountRows(conn, other, ask.Kind.Style, ask.CancelNo) == 0)
            {
                throw State("处理号 " + ask.CancelNo + " 的记录不完整（缺" + ArapProcCancelGate.Side(other) + "一侧），请在 U8 客户端取消");
            }
            ProcCancelAsk side = new ProcCancelAsk();
            side.Flag = ask.Flag;
            side.CancelNo = ask.CancelNo;
            side.Kind = SideKind(ask.Kind, other);
            return ArapProcCancelGate.Plan(conn, side, acc);
        }

        internal static ProcCancelKind SideKind(ProcCancelKind kind, string other)
        {
            ProcCancelKind k = new ProcCancelKind();
            k.Prefix = kind.Prefix;
            k.Style = kind.Style;
            k.Flag = kind.Flag;
            k.Name = kind.Name;
            k.Title = kind.Title;
            k.Ledgers = new string[] { other };
            k.Restore = true;
            k.FlagDelete = false;
            return k;
        }

        static BridgeException State(string message)
        {
            return ArapProcCancelGate.State(message);
        }
    }
}
