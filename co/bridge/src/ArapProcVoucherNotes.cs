using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 一条票据处理行（AP_Note_Sub，处理制单时带锁读出）。金额是本币（与往来明细的 iDAmount / iCAmount 同口径）；
    // Code 是结算 / 贴现的银行科目，Note 是票据号（AP_Note.cVouchID，凭证行的 coutid）。
    internal sealed class ProcNoteSub
    {
        public int Id;
        public string CancelNo = "";
        public string Flag = "";
        public string Code = "";
        public decimal Amount;
        public decimal Expense;
        public decimal Interest;
        public string Pz = "";
        public string CoType = "";
        public string CoId = "";
        public string Note = "";
    }

    // 票据处理制单（coutsign PJ）：结算 9A、贴现 9D、背书 9E、退回 9C 的批次（与 U8 的 PJ 凭证核对）。
    // 分录：票据行（往来明细 50，科目 = 票据科目 cCode，如 112101）同方向；
    // - 结算 / 贴现另配银行行（AP_Note_Sub.cCode），在票据行的对方、金额 = 处理行金额（贴现为净额）；
    //   贴现费用行（基本科目设置 pjyfrzkm，或请求的 expense_code）与票据行同方向、金额 = -费用（如费用科目 660399 记贷方负数）；
    // - 背书：被背书单位的应付明细（01 / P0 / 49，iFlag=6 的留底行不出分录）同方向，同应收冲应付；
    // - 退回：票据行和生成的应收单行都换到对方、金额取负（例：应收控制科目 112201 贷 -100000、票据科目 112101 借 -100000）。
    // 顺序：银行 / 费用 / 对方单据行在前（各自保持生成顺序），票据行在后（同 U8）。银行、费用行不回写明细的分录号（Pl）。
    // 凭证行 coutbillsign：票据相关行 50、对方单据行为其单据类型；coutid：票据相关行为票据号，其余为单据号
    // （U8 的 coutid 是票据号后接内部序号，不是可还原的键，桥写票据号，是否影响 U8 联查未经实测）。
    // 回写：往来明细同其他处理，另写 AP_Note_Sub.cPzID；退回生成的应收单写 cPZid、cPZNum、doutbilldate（同 U8）。
    internal static class ArapProcVoucherNotes
    {
        const decimal Tolerance = 0.005m;
        const string SubCols = "convert(varchar(20), s.ID) as id, s.cCancelNo as cno, isnull(s.cFlag,N'') as fl, isnull(s.cCode,N'') as code, "
            + "{amt} as amt, {exp} as exp, {intr} as intr, isnull(s.cPzID,N'') as pz, isnull(s.cCoVouchType,N'') as cot, "
            + "isnull(s.cCoVouchID,N'') as coid, isnull(n.cVouchID,N'') as note";

        public static string SubSql(int count)
        {
            string inList = "(?" + new StringBuilder().Insert(0, ",?", count - 1).ToString() + ")";
            string cols = SubCols.Replace("{amt}", WriteoffSql.Dec("isnull(s.iAmount_Local,s.iAmount)", 2))
                .Replace("{exp}", WriteoffSql.Dec("isnull(s.iExpense_Local,s.iExpense)", 2))
                .Replace("{intr}", WriteoffSql.Dec("isnull(s.iIntrest_Local,s.iIntrest)", 2));
            return "select " + cols + " from AP_Note_Sub s with (UPDLOCK, HOLDLOCK) left join AP_Note n on n.cLink=s.cLink "
                + "where s.cProcStyle=? and s.cCancelNo in " + inList + " order by s.ID";
        }

        // Prepare 事务里：带锁读票据处理行、逐批核对、防重复制单。不是票据处理什么也不做。
        public static void Prepare(object conn, ProcPlan plan)
        {
            ProcVoucherAsk ask = plan.Ask;
            if (!ask.Notes)
            {
                return;
            }
            object[] nos = ask.CancelNos.ToArray();
            foreach (Dictionary<string, object> r in Rows.Query(conn, SubSql(nos.Length), GlSql.With(new object[] { ask.Style }, nos), 101))
            {
                plan.NoteSubs.Add(SubOf(r));
            }
            Check(ask, plan.Rows, plan.NoteSubs);
            NoOrphan(conn, ask, plan.NoteSubs);
        }

        static ProcNoteSub SubOf(Dictionary<string, object> r)
        {
            ProcNoteSub sub = new ProcNoteSub();
            sub.Id = CoRows.AsId(CoRows.Col(r, "id"));
            sub.CancelNo = CoRows.Col(r, "cno");
            sub.Flag = CoRows.Col(r, "fl");
            sub.Code = CoRows.Col(r, "code");
            sub.Amount = WriteoffSql.Num(CoRows.Col(r, "amt"));
            sub.Expense = WriteoffSql.Num(CoRows.Col(r, "exp"));
            sub.Interest = WriteoffSql.Num(CoRows.Col(r, "intr"));
            sub.Pz = CoRows.Col(r, "pz");
            sub.CoType = CoRows.Col(r, "cot");
            sub.CoId = CoRows.Col(r, "coid");
            sub.Note = CoRows.Col(r, "note");
            return sub;
        }

        // 每个批次：恰好一条票据处理行（flag 一致、未制单、有票据）；票据所在账上恰好一行票据行（50）；
        // 金额对得上（处理行金额 = 票据行，贴现是净额 + 费用 = 票据行，贴现利息不为 0 不支持）；结算 / 贴现有银行科目；
        // 退回：生成单据的那行存在。
        internal static void Check(ProcVoucherAsk ask, List<ProcVoucherRow> rows, List<ProcNoteSub> subs)
        {
            foreach (string no in ask.CancelNos)
            {
                ProcNoteSub sub = OneSub(ask, no, subs);
                ProcVoucherRow note = NoteRow(ask, no, rows);
                decimal face = note.Dm + note.Cm;
                bool bank = ask.Style == "9A" || ask.Style == "9D";
                Need(!bank || sub.Code.Length > 0, "批次 " + no + " 的处理记录没有银行科目");
                Need(ask.Style != "9D" || sub.Interest == 0m, "批次 " + no + " 有贴现利息，请在 U8 客户端制单");
                decimal applied = ask.Style == "9D" ? sub.Amount + sub.Expense : sub.Amount;
                Need(Math.Abs(Math.Abs(face) - applied) <= Tolerance, "批次 " + no + " 的处理金额与往来明细不一致，请在 U8 客户端制单");
                Need(ask.Style != "9C" || BillRow(no, sub, rows) != null, "批次 " + no + " 缺退回生成的单据行，请在 U8 客户端制单");
            }
        }

        static ProcNoteSub OneSub(ProcVoucherAsk ask, string no, List<ProcNoteSub> subs)
        {
            ProcNoteSub found = null;
            foreach (ProcNoteSub sub in subs)
            {
                if (sub.CancelNo != no)
                {
                    continue;
                }
                Need(found == null, "批次 " + no + " 有多条票据处理记录，请在 U8 客户端制单");
                found = sub;
            }
            Need(found != null, "批次 " + no + " 没有票据处理记录（AP_Note_Sub），请在 U8 客户端制单");
            Need(found.Flag == ask.Flag && found.Note.Length > 0, "批次 " + no + " 的票据处理记录不完整，请在 U8 客户端制单");
            Need(found.Pz.Length == 0, "批次 " + no + " 已制单（外部业务号 " + found.Pz + "），不能重复制单");
            return found;
        }

        static ProcVoucherRow NoteRow(ProcVoucherAsk ask, string no, List<ProcVoucherRow> rows)
        {
            ProcVoucherRow found = null;
            foreach (ProcVoucherRow row in rows)
            {
                if (row.CancelNo == no && row.Ledger == ask.Flag && row.VType == "50")
                {
                    Need(found == null, "批次 " + no + " 有多行票据往来明细，请在 U8 客户端制单");
                    found = row;
                }
            }
            Need(found != null && (found.Dm == 0m) != (found.Cm == 0m), "批次 " + no + " 的票据往来明细不完整，请在 U8 客户端制单");
            return found;
        }

        static ProcVoucherRow BillRow(string no, ProcNoteSub sub, List<ProcVoucherRow> rows)
        {
            foreach (ProcVoucherRow row in rows)
            {
                if (row.CancelNo == no && row.Ledger == sub.Flag && row.VType == sub.CoType && row.VId == sub.CoId)
                {
                    return row;
                }
            }
            return null;
        }

        // 贴现费用科目：请求的 expense_code，否则基本科目设置（Ap_InputCode cNote_f = pjyfrzkm，按会计年度）。非贴现或费用全为 0 返回空串。
        public static string ExpenseAccount(object conn, ProcPlan plan, int year)
        {
            ProcVoucherAsk ask = plan.Ask;
            bool any = false;
            foreach (ProcNoteSub sub in plan.NoteSubs)
            {
                any = any || sub.Expense != 0m;
            }
            if (ask.Style != "9D" || !any)
            {
                return "";
            }
            if (ask.ExpenseCode.Length > 0)
            {
                return ask.ExpenseCode;
            }
            bool ar = ask.Flag == "AR";
            string code = Rows.Scalar(conn, "select top 1 isnull(" + (ar ? "cArCode" : "cApCode") + ",N'') from Ap_InputCode "
                + "where iyear=? and cFlag=? and cNote_f=N'pjyfrzkm'", new object[] { year, ar ? "R" : "P" });
            code = code == null ? "" : code.Trim();
            Need(code.Length > 0, "基本科目设置里没有贴现费用科目（pjyfrzkm），请用 expense_code 指定");
            return code;
        }

        // 拼分录（纯计算）：按批次，先银行 / 费用行，再本批往来明细（规则见类注释）。
        public static List<ProcPart> Parts(ProcPlan plan, string expenseCode)
        {
            ProcVoucherAsk ask = plan.Ask;
            List<ProcPart> parts = new List<ProcPart>();
            foreach (string no in ask.CancelNos)
            {
                ProcNoteSub sub = OneSub(ask, no, plan.NoteSubs);
                ProcVoucherRow note = NoteRow(ask, no, plan.Rows);
                string digest = ArapProcVoucherParts.DigestOf(note, ask.Style, ask.Digest);
                bool noteDebit = note.Dm != 0m;
                if (ask.Style == "9A" || ask.Style == "9D")
                {
                    parts.Add(Extra(note, sub, sub.Code, !noteDebit, sub.Amount, digest));
                }
                if (ask.Style == "9D" && sub.Expense != 0m)
                {
                    parts.Add(Extra(note, sub, expenseCode, noteDebit, -sub.Expense, digest));
                }
                foreach (ProcVoucherRow row in plan.Rows)
                {
                    if (row.CancelNo == no)
                    {
                        RowParts(parts, row, sub, ask, digest);
                    }
                }
            }
            return parts;
        }

        static ProcPart Extra(ProcVoucherRow note, ProcNoteSub sub, string account, bool debit, decimal amount, string digest)
        {
            ProcPart part = NotePart(note, sub, account, debit, amount, digest);
            part.Pl = true;
            return part;
        }

        // 一行往来明细：退回换方向取负；没有科目的 iFlag=6 留底行和金额为 0 的行不出分录，其余缺科目 409。
        static void RowParts(List<ProcPart> parts, ProcVoucherRow row, ProcNoteSub sub, ProcVoucherAsk ask, string digest)
        {
            bool empty = row.Dm == 0m && row.Cm == 0m;
            if (empty || (row.Code.Length == 0 && row.IFlag == 6))
            {
                return;
            }
            Need(row.Code.Length > 0, "批次 " + row.CancelNo + " 的往来明细（" + row.VType + " " + row.VId + "）缺科目，请在 U8 客户端制单");
            bool flip = ask.Style == "9C";
            bool note = row.VType == "50";
            if (row.Dm != 0m)
            {
                parts.Add(Detail(row, sub, !flip, flip ? -row.Dm : row.Dm, note, digest));
            }
            if (row.Cm != 0m)
            {
                parts.Add(Detail(row, sub, flip, flip ? -row.Cm : row.Cm, note, digest));
            }
        }

        static ProcPart Detail(ProcVoucherRow row, ProcNoteSub sub, bool debit, decimal amount, bool note, string digest)
        {
            if (note)
            {
                return NotePart(row, sub, row.Code, debit, amount, digest);
            }
            ProcPart part = new ProcPart();
            part.Row = row;
            part.Account = row.Code;
            part.Debit = debit;
            part.Amount = amount;
            part.Digest = digest;
            return part;
        }

        // 票据相关的分录：来源单据 50 + 票据号。
        static ProcPart NotePart(ProcVoucherRow row, ProcNoteSub sub, string account, bool debit, decimal amount, string digest)
        {
            ProcPart part = new ProcPart();
            part.Row = row;
            part.Account = account;
            part.Debit = debit;
            part.Amount = amount;
            part.Digest = digest;
            part.VType = "50";
            part.VId = sub.Note;
            return part;
        }

        // 顺序：不回写的附加行（银行、费用）和对方单据行在前，票据行在后，各自保持原来的先后；其余核对同 ArapProcVoucherParts.Order。
        public static List<ProcLine> Order(List<ProcLine> rows)
        {
            List<ProcLine> ordered = new List<ProcLine>();
            for (int pass = 0; pass < 2; pass++)
            {
                foreach (ProcLine row in rows)
                {
                    bool tail = !row.Pl && row.VType == "50";
                    if (tail == (pass == 1))
                    {
                        ordered.Add(row);
                    }
                }
            }
            ArapProcVoucherParts.Balanced(ordered);
            return ordered;
        }

        // 现金流量（同 arap/voucher 的 ArapVoucherPlan.Flows，与 U8 的 PJ 凭证核对）：总账选项 bXJLL 开着、凭证里既有现金流量科目
        // （银行、票据科目）又有别的科目时，别的科目每行挂一个项目（贴现费用 660399 → 07、背书的应付 220201 → 04、退回的应收 1122 → 01），
        // 金额、方向同该行；全是现金流量科目（结算）不挂。数据来源推不出时用调用方的 cash_items（ArapCashItems）。
        public static void Flows(object conn, List<ProcLine> lines, int year, string date, CashItemMap given)
        {
            List<GlLine> gl = new List<GlLine>();
            List<bool> cash = new List<bool>();
            foreach (ProcLine line in lines)
            {
                gl.Add(line.Line);
                cash.Add(line.CashItem);
            }
            ArapCashItems.Flows(conn, gl, cash, year, date, given);
        }

        // 防重复制单（补 ArapProcVoucherLoad.NoOrphan：票据行的 coutid 是票据号而不是往来明细的单号）：总账里已有本系统生成、
        // coutsign PJ、来源 50 + 这些票据号、外部业务号却没有任何往来明细和票据处理行引用的凭证——上次制单 504 留下的。
        static void NoOrphan(object conn, ProcVoucherAsk ask, List<ProcNoteSub> subs)
        {
            const string Sql = "select top 1 isnull(g.coutno_id,N'') as pz from GL_accvouch g where g.coutsysname=? and g.coutsign=N'PJ' "
                + "and g.coutbillsign=N'50' and g.coutid=? and not exists (select 1 from Ar_Detail d where d.cPZid=g.coutno_id) "
                + "and not exists (select 1 from Ap_Detail d where d.cPZid=g.coutno_id) "
                + "and not exists (select 1 from AP_Note_Sub s where s.cPzID=g.coutno_id)";
            HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (ProcNoteSub sub in subs)
            {
                if (!seen.Add(sub.Note))
                {
                    continue;
                }
                string pz = Rows.Scalar(conn, Sql, new object[] { ask.Flag, sub.Note });
                Need(pz == null, "票据 " + sub.Note + " 已有一张没有回写的票据处理凭证（外部业务号 " + pz + "），可能是上次制单结果不明时留下的；"
                    + "请先核对，确认后用 arap/voucher/delete 或在 U8 客户端删除该凭证再制单");
            }
        }

        // 回写：票据处理行 cPzID；退回生成的应收单 / 应付单 cPZid、cPZNum、doutbilldate（只写还没有凭证号的）。
        public static void Mark(object conn, ProcPlan plan)
        {
            VoucherPlan gl = plan.Gl;
            foreach (ProcNoteSub sub in plan.NoteSubs)
            {
                GlSql.Exec(conn, "update AP_Note_Sub set cPzID=? where ID=? and cCancelNo=? and isnull(cPzID,N'')=N''",
                    new object[] { gl.PzId, sub.Id, sub.CancelNo });
                if (plan.Ask.Style == "9C")
                {
                    GlSql.Exec(conn, "update Ap_Vouch set cPZid=?, cPZNum=?, doutbilldate=cast(convert(date, ?, 23) as datetime) "
                        + "where cVouchType=? and cVouchID=? and cFlag=? and isnull(cPZid,N'')=N''",
                        new object[] { gl.PzId, gl.PzNum(), gl.Date, sub.CoType, sub.CoId, sub.Flag });
                }
            }
        }

        // 提交前核对：票据处理行（退回另加生成的单据）全部带上本凭证号。不符返回原因。
        public static string Mismatch(object conn, ProcPlan plan)
        {
            if (plan.NoteSubs.Count == 0)
            {
                return null;
            }
            int subs = ArapProcVoucherBack.Count(conn, "select convert(varchar(12), count(*)) from AP_Note_Sub where cPzID=?",
                new object[] { plan.Gl.PzId });
            if (subs != plan.NoteSubs.Count)
            {
                return "票据处理行回写了 " + subs.ToString(CultureInfo.InvariantCulture) + " 行，应为 "
                    + plan.NoteSubs.Count.ToString(CultureInfo.InvariantCulture) + " 行";
            }
            if (plan.Ask.Style == "9C")
            {
                int bills = ArapProcVoucherBack.Count(conn, "select convert(varchar(12), count(*)) from Ap_Vouch where cPZid=?",
                    new object[] { plan.Gl.PzId });
                if (bills != plan.NoteSubs.Count)
                {
                    return "退回生成的单据回写了 " + bills.ToString(CultureInfo.InvariantCulture) + " 张";
                }
            }
            return null;
        }

        // 清除（补偿、取消制单）：票据处理行和由票据退回生成的单据上引用该外部业务号的凭证号。
        public static void Clear(object conn, string pzId)
        {
            GlSql.Exec(conn, "update AP_Note_Sub set cPzID=null where cPzID=?", new object[] { pzId });
            GlSql.Exec(conn, "update Ap_Vouch set cPZid=null, cPZNum=null, doutbilldate=null where cPZid=? and cCoVouchType=N'50'",
                new object[] { pzId });
        }

        // 清除后核对用：仍引用该外部业务号的票据处理行数。
        public static int Left(object conn, string pzId)
        {
            return ArapProcVoucherBack.Count(conn, "select convert(varchar(12), count(*)) from AP_Note_Sub where cPzID=?", new object[] { pzId });
        }

        static void Need(bool ok, string message)
        {
            if (!ok)
            {
                throw ArapVoucherDoc.Refuse(message);
            }
        }
    }
}
