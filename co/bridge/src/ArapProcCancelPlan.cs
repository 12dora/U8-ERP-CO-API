using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 处理号下的一条往来明细（事务里带锁读出的快照，U8 的「取消操作」同样先取整批再写）。Ledger 是所在的账（Ar_Detail 为 AR）；
    // 金额是原币；Head 是原始行（数据权限按其中的 cDwCode、cDeptCode、cPerson）。
    internal sealed class ProcRow
    {
        public string Ledger;
        public int Auto;
        public string Style;
        public string Flag;
        public string VType;
        public string VCode;
        public string CoType;
        public string CoCode;
        public int BVid;
        public int CoClose;
        public int IFlag;
        public int Period;
        public string RegDate;
        public string Pz;
        public string Contract;
        public string BusType;
        public decimal DF;
        public decimal CF;
        public Dictionary<string, object> Head;
    }

    // 处理涉及的一张单据（按账、类型、单号）。Name 是 Kinds 里的类型名。
    internal sealed class ProcDoc
    {
        public string Ledger;
        public string VType;
        public string Code;
        public string Name;
        public string Title;
        public int Id;
        public Dictionary<string, object> Head;

        public bool IsReceipt
        {
            get { return VType == "48" || VType == "49"; }
        }
    }

    // 要加回余额的一处：收付款单行（Line = Ap_CloseBills.ID）或应收应付单整单（Line = 0）。Back 是要加回的原币，
    // Before / Amount 是取消前的余额和收付款单行的金额（应收应付单不用 Amount）。
    internal sealed class ProcRemain
    {
        public string Ledger;
        public string VType;
        public string Code;
        public int Line;
        public decimal Back;
        public decimal Before;
        public decimal Amount;
    }

    // 过了闸门的取消计划。
    internal sealed class ProcCancelPlan
    {
        public ProcCancelKind Kind;
        public string Flag;
        public string CancelNo;
        public int Period;
        public string RegDate;
        public List<ProcRow> Rows = new List<ProcRow>();
        public List<ProcDoc> Docs = new List<ProcDoc>();
        public List<ProcRemain> Lines = new List<ProcRemain>();
        public List<ProcRemain> Bills = new List<ProcRemain>();

        public int MaxAuto(string ledger)
        {
            int max = 0;
            foreach (ProcRow row in Rows)
            {
                if (row.Ledger == ledger)
                {
                    max = Math.Max(max, row.Auto);
                }
            }
            return max;
        }

        public int Count(string ledger)
        {
            int n = 0;
            foreach (ProcRow row in Rows)
            {
                n += row.Ledger == ledger ? 1 : 0;
            }
            return n;
        }

        // 该账上对方单据类型以 head 开头（"2" 销售发票、"0" 采购发票）的行是否存在。
        public bool HasCo(string ledger, string head)
        {
            foreach (ProcRow row in Rows)
            {
                if (row.Ledger == ledger && row.CoType.StartsWith(head, StringComparison.Ordinal))
                {
                    return true;
                }
            }
            return false;
        }

        public bool HasRemain(List<ProcRemain> list, string ledger)
        {
            foreach (ProcRemain r in list)
            {
                if (r.Ledger == ledger)
                {
                    return true;
                }
            }
            return false;
        }

        // 该账上涉及的单据（类型、单号），供「之后的处理」查询。
        public List<string[]> DocKeys(string ledger)
        {
            List<string[]> keys = new List<string[]>();
            foreach (ProcDoc doc in Docs)
            {
                if (doc.Ledger == ledger)
                {
                    keys.Add(new string[] { doc.VType, doc.Code });
                }
            }
            return keys;
        }

        public ProcDoc DocOf(string ledger, string type, string code)
        {
            foreach (ProcDoc doc in Docs)
            {
                if (doc.Ledger == ledger && doc.VType == type && doc.Code == code)
                {
                    return doc;
                }
            }
            return null;
        }
    }

    // 取消处理的闸门：事务里带锁读整批处理行、各单据之后，写之前。处理号不存在 404；其余 409 state_mismatch。
    internal static class ArapProcCancelGate
    {
        internal const int MaxRows = 500;
        internal const string Later = "单据在本次处理之后还有其他处理，请先取消后面的处理";
        internal const string Unaudited = "账套里有未审核的收款单，U8 不允许取消操作，请先审核或删除这些收款单";
        internal const string Vouchered = "已制单，请先取消制单";
        internal const string Incomplete = "处理记录指向的单据行不完整，请在 U8 客户端取消";

        public static ProcCancelPlan Plan(object conn, ProcCancelAsk ask, string acc)
        {
            List<ProcRow> rows = new List<ProcRow>();
            foreach (string ledger in ask.Kind.Ledgers)
            {
                rows.AddRange(ProcCancelSql.Batch(conn, ledger, ask.Kind.Style, ask.CancelNo, MaxRows + 1));
            }
            ProcCancelPlan plan = FromRows(ask, rows);
            Heads(conn, plan);
            Befores(conn, plan);
            OpenPeriod(conn, plan, acc);
            foreach (string ledger in ask.Kind.Ledgers)
            {
                if (ProcCancelSql.LaterStyle(conn, plan, ledger) != null)
                {
                    throw State(Later);
                }
            }
            // U8 在应收系统的取消操作前调 AR_ExistUnAuditCloseBill（同取消核销）。
            if (ask.Flag == "AR" && UnwriteoffSql.UnauditedCloseBill(conn))
            {
                throw State(Unaudited);
            }
            return plan;
        }

        // 只按处理行本身判断（不查库）：行数、处理方式、所在的账、没制单、没合同、不是代理进口、同一期间、单据类型，
        // 并归好单据和要加回的余额。
        internal static ProcCancelPlan FromRows(ProcCancelAsk ask, List<ProcRow> rows)
        {
            if (rows.Count == 0)
            {
                throw new BridgeException(404, "not_found", "处理号 " + ask.CancelNo + " 不存在");
            }
            if (rows.Count > MaxRows)
            {
                throw State("处理记录超过 500 行，请在 U8 客户端取消");
            }
            ProcCancelPlan plan = new ProcCancelPlan();
            plan.Kind = ask.Kind;
            plan.Flag = ask.Flag;
            plan.CancelNo = ask.CancelNo;
            plan.Period = rows[0].Period;
            plan.RegDate = rows[0].RegDate;
            plan.Rows = rows;
            foreach (ProcRow row in rows)
            {
                Shape(plan, row);
                Plain(plan, row);
                Add(plan, row);
            }
            foreach (string ledger in ask.Kind.Ledgers)
            {
                if (ask.Kind.Both && plan.Count(ledger) == 0)
                {
                    throw State("处理号 " + plan.CancelNo + " 的记录不完整（缺" + Side(ledger) + "一侧），请在 U8 客户端取消");
                }
            }
            return plan;
        }

        // 本种处理、在它应在的账上、对方单据就是单据本身（9I / 9J / BZ 的行都是如此）。
        static void Shape(ProcCancelPlan plan, ProcRow row)
        {
            bool known = Array.IndexOf(plan.Kind.Ledgers, row.Ledger) >= 0;
            if (row.Style != plan.Kind.Style || !known || row.Flag != row.Ledger)
            {
                throw State("处理号 " + plan.CancelNo + " 下有不是" + plan.Kind.Title + "的记录，请在 U8 客户端取消");
            }
            if (row.VType != row.CoType || row.VCode != row.CoCode)
            {
                throw State("处理号 " + plan.CancelNo + " 的记录形状不符，请在 U8 客户端取消");
            }
        }

        static void Plain(ProcCancelPlan plan, ProcRow row)
        {
            if (row.Pz.Length > 0)
            {
                throw State(Vouchered);
            }
            if (row.Contract.Length > 0)
            {
                throw State("处理涉及合同，请在 U8 客户端取消");
            }
            if (row.BusType == "代理进口")
            {
                throw State("处理涉及代理进口，请在 U8 客户端取消");
            }
            if (row.Period != plan.Period || row.RegDate != plan.RegDate)
            {
                throw State("处理记录的日期不一致，请在 U8 客户端取消");
            }
        }

        // 归单据；需要加回余额的处理（Kind.Restore）再归收付款单行（iFlag=6 的成对行不算）和应收应付单（Back）。发票的累计核销由
        // ArapUnwriteoffBill 按处理行整批回写，不在这里归。
        static void Add(ProcCancelPlan plan, ProcRow row)
        {
            string name = DocName(row.Ledger, row.CoType);
            if (name == null)
            {
                throw State("处理涉及本接口不支持的单据类型 " + row.CoType + "，请在 U8 客户端取消");
            }
            if (plan.DocOf(row.Ledger, row.CoType, row.CoCode) == null)
            {
                ProcDoc doc = new ProcDoc();
                doc.Ledger = row.Ledger;
                doc.VType = row.CoType;
                doc.Code = row.CoCode;
                doc.Name = name;
                doc.Title = DocTitle(name);
                plan.Docs.Add(doc);
            }
            if (plan.Kind.Restore)
            {
                Back(plan, row);
            }
        }

        // 要加回余额的一处。9I / 9J 加回 借+贷；9N（Kind.Negate）加回 -(借+贷)。
        // 收付款单行缺 iCoClosesID（iFlag=6 的成对行除外）、发票行缺 iBVid 的处理行加回不了，拒绝（不能只删行）。
        static void Back(ProcCancelPlan plan, ProcRow row)
        {
            bool receipt = row.CoType.StartsWith("4", StringComparison.Ordinal);
            Linked(plan, row, receipt);
            decimal back = plan.Kind.Negate ? -(row.DF + row.CF) : row.DF + row.CF;
            if (receipt && row.IFlag != 6)
            {
                RemainOf(plan.Lines, row, row.CoClose).Back += back;
            }
            else if (row.CoType == "R0" || row.CoType == "P0")
            {
                RemainOf(plan.Bills, row, 0).Back += back;
            }
        }

        static void Linked(ProcCancelPlan plan, ProcRow row, bool receipt)
        {
            if (receipt && plan.Kind.Negate)
            {
                throw State("红票对冲涉及收付款单，请在 U8 客户端取消");
            }
            if (receipt && row.CoClose <= 0 && row.IFlag != 6)
            {
                throw State(Incomplete);
            }
            bool invoice = row.CoType.StartsWith("2", StringComparison.Ordinal) || row.CoType.StartsWith("0", StringComparison.Ordinal);
            if (invoice && row.BVid <= 0)
            {
                throw State(Incomplete);
            }
        }

        static ProcRemain RemainOf(List<ProcRemain> list, ProcRow row, int line)
        {
            foreach (ProcRemain r in list)
            {
                if (r.Ledger == row.Ledger && r.VType == row.CoType && r.Code == row.CoCode && r.Line == line)
                {
                    return r;
                }
            }
            ProcRemain add = new ProcRemain();
            add.Ledger = row.Ledger;
            add.VType = row.CoType;
            add.Code = row.CoCode;
            add.Line = line;
            list.Add(add);
            return add;
        }

        // 账上的单据类型 → Kinds 里的类型名：应收 26 / 27、R0、48，应付 01 / 02、P0、49；其余返回 null。
        internal static string DocName(string ledger, string type)
        {
            if (type == "48" || type == "49")
            {
                bool match = (type == "48") == (ledger == "AR");
                return match ? (ledger == "AR" ? "ar_receipt" : "ap_payment") : null;
            }
            WriteoffKind kind = WriteoffKind.OfType(ledger, type);
            return kind == null ? null : kind.Name;
        }

        static string DocTitle(string name)
        {
            if (name == "ar_receipt")
            {
                return "收款单";
            }
            return name == "ap_payment" ? "付款单" : WriteoffKind.Of(name).Title;
        }

        // 各单据表头（带锁）：存在、没有被网络锁定；填 Id、Head。
        static void Heads(object conn, ProcCancelPlan plan)
        {
            foreach (ProcDoc doc in plan.Docs)
            {
                doc.Head = ProcCancelSql.DocHead(conn, doc);
                CheckHead(doc);
            }
        }

        internal static void CheckHead(ProcDoc doc)
        {
            doc.Id = doc.Head == null ? 0 : CoRows.AsId(CoRows.Col(doc.Head, "id"));
            if (doc.Id == 0)
            {
                throw State(doc.Title + " " + doc.Code + " 不存在");
            }
            if (CoRows.FlagOf(doc.Head, "locked"))
            {
                throw State(doc.Title + " " + doc.Code + " 正被其他操作锁定");
            }
        }

        // 取消前的余额：收付款单行（必须属于那张收付款单）、应收应付单 iRAmount_f。
        static void Befores(object conn, ProcCancelPlan plan)
        {
            foreach (ProcRemain line in plan.Lines)
            {
                decimal[] now = ProcCancelSql.Line(conn, line);
                if (now == null)
                {
                    throw State("处理记录指向的行不属于收付款单 " + line.Code);
                }
                line.Before = now[0];
                line.Amount = now[1];
            }
            foreach (ProcRemain bill in plan.Bills)
            {
                bill.Before = UnwriteoffSql.BillRemain(conn, bill.Ledger, bill.VType, bill.Code);
            }
        }

        // 处理行的期间在涉及的每个账上都未结账（U8：GL_mend bFlag_AR / bFlag_AP = 1 拒绝；9I / 9J 两个账都查，并账查本侧）。
        // 年度按登记日期在 UFSYSTEM..UA_Period 上所在的年度（找不到按日期的年份）。
        static void OpenPeriod(object conn, ProcCancelPlan plan, string acc)
        {
            int[] found = WriteoffSql.PeriodOf(conn, acc, plan.RegDate);
            int year = found != null ? found[0] : YearOf(plan.RegDate);
            foreach (string ledger in plan.Kind.Ledgers)
            {
                if (WriteoffSql.Closed(conn, ledger, year, plan.Period))
                {
                    throw State(Side(ledger) + "已结账，不能取消该期间的处理");
                }
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

        internal static string Side(string ledger)
        {
            return ledger == "AP" ? "应付" : "应收";
        }

        // 收付款单行加回后的余额应在 0 和金额之间（同号）；越界说明加回的口径与建立时不一致。
        internal static bool Within(decimal remain, decimal amount, decimal tolerance)
        {
            if (amount >= 0m)
            {
                return remain >= -tolerance && remain <= amount + tolerance;
            }
            return remain <= tolerance && remain >= amount - tolerance;
        }

        internal static BridgeException State(string message)
        {
            return ArapWriteoffGate.State(message);
        }
    }
}
