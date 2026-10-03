using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 核销号下的一条往来明细（事务里带锁读出的快照；U8 的「取消操作」同样先取整批再写）。金额是原币。
    internal sealed class UnwriteoffRow
    {
        public int Auto;
        public string Style;
        public string Flag;
        public string VType;
        public string VCode;
        public string CoType;
        public string CoCode;
        public int BVid;
        public int CoClose;
        public int Period;
        public string RegDate;
        public string Dw;
        public string Pz;
        public string Contract;
        public string Cur;
        public decimal Rate;
        public decimal DF;
        public decimal CF;
    }

    // 收付款单的一行（Ap_CloseBills.ID）：取消前余额和要加回的金额（自身冲减行 -(借+贷) 之和）。
    internal sealed class UnwriteoffLine
    {
        public int Line;
        public decimal Before;
        public decimal Back;
    }

    // 被核销单据的一行（应收应付单整单为 0）：Amount 是余额要加回的数（应收 贷-借、应付 借-贷），
    // Raise 是 借+贷（应收应付单 iRAmount_f 按它加回）；Before / BillBefore 是取消前的余额和应收应付单的 iRAmount_f。
    internal sealed class UnwriteoffTarget
    {
        public WriteoffKind Kind;
        public int Id;
        public int Line;
        public string Code;
        public string VType;
        public string Dw;
        public decimal Amount;
        public decimal Raise;
        public decimal Before;
        public decimal BillBefore;
        public Dictionary<string, object> Head;
    }

    // 过了闸门的取消计划。Head 是收付款单表头（数据权限按它和各单据表头）。
    internal sealed class UnwriteoffPlan
    {
        public string Flag;
        public string CancelNo;
        public string ReceiptType;
        public string ReceiptKind;
        public string ReceiptCode;
        public int ReceiptId;
        public int Period;
        public string RegDate;
        public int MaxAuto;
        public Dictionary<string, object> Head;
        public List<UnwriteoffRow> Rows = new List<UnwriteoffRow>();
        public List<UnwriteoffLine> Lines = new List<UnwriteoffLine>();
        public List<UnwriteoffTarget> Targets = new List<UnwriteoffTarget>();

        public string Side
        {
            get { return Flag == "AP" ? "应付" : "应收"; }
        }

        public bool Has(string kind)
        {
            foreach (UnwriteoffTarget t in Targets)
            {
                if (t.Kind.Name == kind)
                {
                    return true;
                }
            }
            return false;
        }
    }

    // 取消核销的闸门：事务里带锁读整批核销行、收付款单、各单据之后，写之前。核销号不存在 404；其余 409 state_mismatch。
    // 只收本接口做得出的那种批次：一张收付款单（48 / 49）对销售发票、应收单（采购发票、应付单），没有制单、没有合同、
    // 同一币种同一汇率、同一期间。
    internal static class ArapUnwriteoffGate
    {
        internal const int MaxRows = 500;
        internal const string Later = "单据在本次核销之后还有其他处理，请先取消后面的处理";
        internal const string Unaudited = "账套里有未审核的收款单，U8 不允许取消核销，请先审核或删除这些收款单";

        public static UnwriteoffPlan Plan(object conn, UnwriteoffAsk ask, string acc)
        {
            List<UnwriteoffRow> rows = UnwriteoffSql.Batch(conn, ask.Flag, ask.CancelNo, MaxRows + 1);
            if (rows.Count == 0)
            {
                throw new BridgeException(404, "not_found", "核销号不存在");
            }
            UnwriteoffPlan plan = FromRows(ask, rows, WriteoffSql.LocalCurrency(conn));
            Receipt(conn, plan);
            Targets(conn, plan);
            OpenPeriod(conn, plan, acc);
            NothingLater(conn, plan);
            // U8 取消操作前调 AR_ExistUnAuditCloseBill（全账套扫描，只在应收一侧）：有未审核的收款单（不含现结）就不让取消。
            if (plan.Flag == "AR" && UnwriteoffSql.UnauditedCloseBill(conn))
            {
                throw State(Unaudited);
            }
            return plan;
        }

        // 只按核销行本身判断（不查库）：行数、全是本侧 9P、同一张收付款单、没制单、没合同、同币种同汇率、同一期间，并归好行和单据。
        // 取消核销和核销记录查询（reports/arap_writeoffs 的 cancellable）共用。
        internal static UnwriteoffPlan FromRows(UnwriteoffAsk ask, List<UnwriteoffRow> rows, string local)
        {
            if (rows.Count > MaxRows)
            {
                throw State("核销记录超过 500 行，请在 U8 客户端取消");
            }
            UnwriteoffPlan plan = Start(ask, rows);
            foreach (UnwriteoffRow row in rows)
            {
                Shape(plan, row);
                Plain(plan, row, local);
                Add(plan, row);
            }
            return plan;
        }

        static UnwriteoffPlan Start(UnwriteoffAsk ask, List<UnwriteoffRow> rows)
        {
            UnwriteoffPlan plan = new UnwriteoffPlan();
            plan.Flag = ask.Flag;
            plan.CancelNo = ask.CancelNo;
            plan.ReceiptType = ask.Flag == "AP" ? "49" : "48";
            plan.ReceiptKind = ask.Flag == "AP" ? "ap_payment" : "ar_receipt";
            plan.ReceiptCode = rows[0].VCode;
            plan.Period = rows[0].Period;
            plan.RegDate = rows[0].RegDate;
            plan.Rows = rows;
            foreach (UnwriteoffRow row in rows)
            {
                plan.MaxAuto = Math.Max(plan.MaxAuto, row.Auto);
            }
            return plan;
        }

        // 全是本侧的 9P 行，都挂在同一张收付款单上。
        static void Shape(UnwriteoffPlan plan, UnwriteoffRow row)
        {
            if (row.Style != "9P" || row.Flag != plan.Flag)
            {
                throw State("核销号 " + plan.CancelNo + " 下有不是" + plan.Side + "核销的记录，请在 U8 客户端取消");
            }
            if (row.VType != plan.ReceiptType || row.VCode != plan.ReceiptCode)
            {
                throw State("该核销不是一张" + (plan.Flag == "AP" ? "付款单" : "收款单") + "对单据的核销，请在 U8 客户端取消");
            }
        }

        static void Plain(UnwriteoffPlan plan, UnwriteoffRow row, string local)
        {
            if (row.Pz.Length > 0)
            {
                throw State("核销已制单，请先删除凭证");
            }
            if (row.Contract.Length > 0)
            {
                throw State("核销涉及合同，请在 U8 客户端取消");
            }
            if (!SameFx(row, plan.Rows[0], local))
            {
                throw State("核销记录的币种或汇率不一致，请在 U8 客户端取消");
            }
            if (row.Period != plan.Period)
            {
                throw State("核销记录跨会计期间，请在 U8 客户端取消");
            }
        }

        // 外币核销（9P）照样能取消：U8 的取消 SQL 原币、本币分开加回（单据一侧按 9P 行上的本币加回，与单据自己的汇率无关）。
        // 9P 行一律是收付款单的汇率，所以整批必须同一币种、同一汇率（U8 的 9P 一个核销号内不混用）；
        // 币种为空按本位币，本位币汇率必须是 1。
        static bool SameFx(UnwriteoffRow row, UnwriteoffRow first, string local)
        {
            string cur = row.Cur.Length == 0 ? local : row.Cur;
            string head = first.Cur.Length == 0 ? local : first.Cur;
            bool home = string.Equals(cur, local, StringComparison.Ordinal);
            bool valid = home ? row.Rate == 1m : row.Rate > 0m;
            return valid && string.Equals(cur, head, StringComparison.Ordinal) && row.Rate == first.Rate;
        }

        // 自身冲减行（对方是收付款单自己、iCoClosesID 是行）归到行；其余按（单据类型、单号、行）归到被核销单据。
        static void Add(UnwriteoffPlan plan, UnwriteoffRow row)
        {
            if (row.CoType == plan.ReceiptType)
            {
                if (row.CoCode != plan.ReceiptCode || row.CoClose <= 0)
                {
                    throw State("该核销涉及收付款单之间的对冲，请在 U8 客户端取消");
                }
                LineOf(plan, row.CoClose).Back -= row.DF + row.CF;
                return;
            }
            WriteoffKind kind = WriteoffKind.OfType(plan.Flag, row.CoType);
            if (kind == null)
            {
                throw State("该核销涉及本接口不支持的单据类型 " + row.CoType + "，请在 U8 客户端取消");
            }
            UnwriteoffTarget t = TargetOf(plan, kind, row);
            t.Amount += plan.Flag == "AP" ? row.DF - row.CF : row.CF - row.DF;
            t.Raise += row.DF + row.CF;
        }

        static UnwriteoffLine LineOf(UnwriteoffPlan plan, int line)
        {
            foreach (UnwriteoffLine one in plan.Lines)
            {
                if (one.Line == line)
                {
                    return one;
                }
            }
            UnwriteoffLine add = new UnwriteoffLine();
            add.Line = line;
            plan.Lines.Add(add);
            return add;
        }

        static UnwriteoffTarget TargetOf(UnwriteoffPlan plan, WriteoffKind kind, UnwriteoffRow row)
        {
            int line = kind.LineSql == null ? 0 : row.BVid;
            foreach (UnwriteoffTarget one in plan.Targets)
            {
                if (one.VType == row.CoType && one.Code == row.CoCode && one.Line == line)
                {
                    return one;
                }
            }
            UnwriteoffTarget t = new UnwriteoffTarget();
            t.Kind = kind;
            t.VType = row.CoType;
            t.Code = row.CoCode;
            t.Line = line;
            t.Dw = row.Dw;
            plan.Targets.Add(t);
            return t;
        }

        // 收付款单表头（带锁）和各行取消前的余额；核销行指向的行必须属于这张收付款单。
        static void Receipt(object conn, UnwriteoffPlan plan)
        {
            plan.ReceiptId = UnwriteoffSql.ReceiptId(conn, plan.ReceiptType, plan.ReceiptCode, plan.Flag);
            plan.Head = plan.ReceiptId > 0 ? WriteoffSql.Receipt(conn, plan.ReceiptId) : null;
            Dictionary<int, decimal> remain = new Dictionary<int, decimal>();
            if (plan.Head != null)
            {
                foreach (Dictionary<string, object> row in WriteoffSql.ReceiptRows(conn, plan.ReceiptId))
                {
                    remain[CoRows.AsId(CoRows.Col(row, "line"))] = WriteoffSql.Num(CoRows.Col(row, "rem_f"));
                }
            }
            CheckReceipt(plan, remain);
        }

        // 收付款单存在；核销行指向的行都属于它（remain：行 id → 余额，顺带填 Before）。
        internal static void CheckReceipt(UnwriteoffPlan plan, Dictionary<int, decimal> remain)
        {
            if (plan.Head == null)
            {
                throw State("收付款单 " + plan.ReceiptCode + " 不存在");
            }
            if (plan.Lines.Count == 0)
            {
                throw State("核销号下没有收付款单自身的冲减记录，请在 U8 客户端取消");
            }
            foreach (UnwriteoffLine line in plan.Lines)
            {
                if (!remain.TryGetValue(line.Line, out line.Before))
                {
                    throw State("核销记录指向的行不属于收付款单 " + plan.ReceiptCode);
                }
            }
        }

        // 各单据表头（带锁，按单号找）、取消前的余额；采购发票被网络锁定时 409。
        static void Targets(object conn, UnwriteoffPlan plan)
        {
            if (plan.Targets.Count == 0)
            {
                throw State("核销号下没有被核销的单据，请在 U8 客户端取消");
            }
            foreach (UnwriteoffTarget t in plan.Targets)
            {
                t.Head = Rows.One(conn, t.Kind.CodeSql, new object[] { t.Code, t.VType });
                CheckTarget(t);
                t.Before = WriteoffSql.Balance(conn, plan.Flag, t.VType, t.Code, t.Dw, t.Line);
                t.BillBefore = t.Kind.LineSql == null ? UnwriteoffSql.BillRemain(conn, plan.Flag, t.VType, t.Code) : 0m;
            }
        }

        // 单据存在（表头 id）、没有被网络锁定（表头 locked）；填 Id。
        internal static void CheckTarget(UnwriteoffTarget t)
        {
            t.Id = t.Head == null ? 0 : CoRows.AsId(CoRows.Col(t.Head, "id"));
            if (t.Id == 0)
            {
                throw State(t.Kind.Title + " " + t.Code + " 不存在");
            }
            if (CoRows.FlagOf(t.Head, "locked"))
            {
                throw State(t.Kind.Title + " " + t.Code + " 正被其他操作锁定");
            }
        }

        // 核销行的期间（iPeriod）应收（应付）未结账（U8：GL_mend bFlag_AR / bFlag_AP = 1 拒绝）；年度按核销时的登记日期
        // 在 UFSYSTEM..UA_Period 上所在的年度（找不到按日期的年份）。
        internal static void OpenPeriod(object conn, UnwriteoffPlan plan, string acc)
        {
            int[] found = WriteoffSql.PeriodOf(conn, acc, plan.RegDate);
            int year = found != null ? found[0] : YearOf(plan.RegDate);
            if (WriteoffSql.Closed(conn, plan.Flag, year, plan.Period))
            {
                throw State(plan.Side + "已结账，不能取消该期间的核销");
            }
        }

        static int YearOf(string date)
        {
            DateTime day;
            if (DateTime.TryParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out day))
            {
                return day.Year;
            }
            throw State("核销记录没有登记日期");
        }

        // 收付款单和各单据在本批之后（Auto_ID 更大、核销号不同）又有除审核（Sign）以外的任何处理时拒绝
        // （核销、红票对冲、汇兑损益、坏账、并账、票据等；先取消后面的）。条件见 UnwriteoffSql.Blocker。
        internal static void NothingLater(object conn, UnwriteoffPlan plan)
        {
            if (UnwriteoffSql.LaterStyle(conn, plan) != null)
            {
                throw State(Later);
            }
        }

        static BridgeException State(string message)
        {
            return ArapWriteoffGate.State(message);
        }
    }
}
