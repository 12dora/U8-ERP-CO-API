using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 一行处理往来明细（Ar_Detail / Ap_Detail 上 cProcStyle 是 9I / 9J / BZ / 9M 等的行，含坏账 9G / 9H），带锁读出。
    internal sealed class ProcVoucherRow
    {
        // 所在的表：AR = Ar_Detail，AP = Ap_Detail（应收冲应付一批两张表都有行）。
        public string Ledger = "";
        public string Aid = "";
        public string Style = "";
        public string CancelNo = "";
        public string Flag = "";
        public int IFlag;
        public string VType = "";
        public string VId = "";
        public string Dw = "";
        public string Code = "";
        public string Dept = "";
        public string Person = "";
        public string ItemClass = "";
        public string ItemCode = "";
        public string Inv = "";
        public string Digest = "";
        public string Cur = "";
        public string Pz = "";
        public string RegDate = "";
        public int Period;
        public int RegYear;
        public decimal Dm;
        public decimal Cm;
        // 计提坏账 9F：批次行由坏账准备参数行拼出（没有往来明细，Aid 为空），ParaYear 是参数行的 iYear。
        public int ParaYear;

        // 回写分录号用的键：表 + Auto_ID（两张表的 Auto_ID 各自编号）。
        public string Key
        {
            get { return Ledger + ":" + Aid; }
        }

        public static ProcVoucherRow Of(Dictionary<string, object> r)
        {
            ProcVoucherRow row = new ProcVoucherRow();
            row.Ledger = CoRows.Col(r, "led");
            row.Aid = CoRows.Col(r, "aid");
            row.Style = CoRows.Col(r, "ps");
            row.CancelNo = CoRows.Col(r, "cno");
            row.Flag = CoRows.Col(r, "fl");
            row.IFlag = CoRows.AsId(CoRows.Col(r, "iflag"));
            row.VType = CoRows.Col(r, "vt");
            row.VId = CoRows.Col(r, "vid");
            row.Dw = CoRows.Col(r, "cDwCode");
            row.Code = CoRows.Col(r, "cCode");
            row.Dept = CoRows.Col(r, "cDeptCode");
            row.Person = CoRows.Col(r, "cPerson");
            row.ItemClass = CoRows.Col(r, "cItem_Class");
            row.ItemCode = CoRows.Col(r, "cItemCode");
            row.Inv = CoRows.Col(r, "cInvCode");
            row.Digest = CoRows.Col(r, "cDigest");
            row.Cur = CoRows.Col(r, "cur");
            row.Pz = CoRows.Col(r, "pz");
            row.RegDate = CoRows.Col(r, "rd");
            row.Period = CoRows.AsId(CoRows.Col(r, "per"));
            row.RegYear = CoRows.AsId(CoRows.Col(r, "ry"));
            row.Dm = WriteoffSql.Num(CoRows.Col(r, "dm"));
            row.Cm = WriteoffSql.Num(CoRows.Col(r, "cm"));
            return row;
        }
    }

    // 拼分录前的一行：科目、方向、金额，辅助核算的来源（往来明细行），来源单据（coutbillsign / coutid）。
    internal sealed class ProcPart
    {
        public string Account = "";
        public bool Debit;
        public decimal Amount;
        public ProcVoucherRow Row;
        // 汇兑损益科目行（9M 的对方）；不回写明细的 ino_id。票据处理的银行、贴现费用行同样用它（ArapProcVoucherNotes）。
        public bool Pl;
        public string Digest = "";
        // 凭证行来源单据（coutbillsign / coutid）的覆盖值；空串取明细行的单据类型、单号（票据处理的票据相关行写 50 + 票据号）。
        public string VType = "";
        public string VId = "";
    }

    // 一行分录连同来源、回写信息。
    internal sealed class ProcLine
    {
        public GlLine Line;
        public string VType = "";
        public string VId = "";
        public bool Pl;
        // 往来科目核算外币时的币种（9M：U8 的凭证行写币种、原币 0），保存后补到凭证行上。
        public string Fc = "";
        public bool Controlled;
        // 科目是现金流量科目（bCashItem）。只有票据处理放行这类科目，并给其余分录挂现金流量项目（ArapProcVoucherNotes.Flows）。
        public bool CashItem;
        public string Op = "";
        public List<string> Rows = new List<string>();
    }

    // 处理制单的纯计算部分（不连库，--selftest 直接核对）：明细行 → 分录草稿、合并、排序、来源单据号。
    // 照 U8 的制单（已在测试账套核对）：
    // - 分录方向与往来明细相同（明细借方 → 凭证借方），带明细的符号：并账、红票对冲、汇兑损益的红字行照记负数，不换方向
    //   （BZ 凭证出方、入方借方各记 -x / +x，9N 凭证同理，9M 的汇兑损益科目全在借方）；借贷为 0 的行不出分录。
    // - 科目取明细的 cCode；处理时形成的预收 / 预付行（iFlag=6）没有科目，不出分录（含留底行的凭证仍是两行）。
    // - 汇兑损益（9M）每行明细再配一行汇兑损益科目（pl_code），在借方、金额 = 贷 - 借。
    // - 合并（bYPzKMHB 开着）：科目、方向、辅助项、来源单据都相同的行合成一行（多行明细可按科目 + 方向合并）。
    internal static class ArapProcVoucherParts
    {
        public static List<ProcPart> Build(List<ProcVoucherRow> rows, string style, string plCode, string digest)
        {
            List<ProcPart> parts = new List<ProcPart>();
            foreach (ProcVoucherRow row in rows)
            {
                bool empty = row.Dm == 0 && row.Cm == 0;
                if (row.Code.Length == 0)
                {
                    if (row.IFlag == 6 || empty)
                    {
                        continue;
                    }
                    throw ArapVoucherDoc.Refuse("批次 " + row.CancelNo + " 的往来明细（" + row.VType + " " + row.VId + "）缺科目，请在 U8 客户端制单");
                }
                if (!empty)
                {
                    RowParts(parts, row, style, plCode, DigestOf(row, style, digest));
                }
            }
            return parts;
        }

        // 一行明细的分录：借方金额记借方、贷方金额记贷方，都带原来的符号（红字照记负数，不换方向）。
        // 汇兑损益另配一行 pl_code，总在借方，金额 = 贷 - 借（U8 生成的凭证汇兑损益科目全在借方，收益为负；应付侧同式，未经实测）。
        static void RowParts(List<ProcPart> parts, ProcVoucherRow row, string style, string plCode, string digest)
        {
            if (row.Dm != 0)
            {
                parts.Add(Part(row, row.Code, true, row.Dm, digest));
            }
            if (row.Cm != 0)
            {
                parts.Add(Part(row, row.Code, false, row.Cm, digest));
            }
            if (style == "9M" && row.Cm - row.Dm != 0)
            {
                ProcPart pl = Part(row, plCode, true, row.Cm - row.Dm, digest);
                pl.Pl = true;
                parts.Add(pl);
            }
        }

        static ProcPart Part(ProcVoucherRow row, string account, bool debit, decimal amount, string digest)
        {
            ProcPart part = new ProcPart();
            part.Row = row;
            part.Account = account;
            part.Debit = debit;
            part.Amount = amount;
            part.Digest = digest;
            return part;
        }

        // 摘要：调用方给了就用；否则取明细上的摘要（处理时写的；红票对冲同 U8 一律「红票对冲」）；都没有用处理类型名。最多 120 字。
        public static string DigestOf(ProcVoucherRow row, string style, string digest)
        {
            string text = digest.Length > 0 ? digest : (style == ArapRedRule.Style ? "" : row.Digest);
            if (text.Length == 0)
            {
                text = ArapProcVoucherReq.Title(style);
            }
            return text.Length > ArapProcVoucherReq.DigestMax ? text.Substring(0, ArapProcVoucherReq.DigestMax) : text;
        }

        static string MergeKey(ProcLine row)
        {
            GlLine l = row.Line;
            return string.Join("\u0001", new string[]
            {
                l.Account.ToUpperInvariant(), l.Debit != 0 ? "D" : "C", l.Dept, l.Person, l.Customer, l.Supplier, l.ItemClass, l.Item,
                l.Currency ?? "", row.VType, row.VId, row.Pl ? "P" : "", row.Op
            });
        }

        public static List<ProcLine> Merge(List<ProcLine> rows)
        {
            Dictionary<string, ProcLine> seen = new Dictionary<string, ProcLine>(StringComparer.Ordinal);
            List<ProcLine> merged = new List<ProcLine>();
            foreach (ProcLine row in rows)
            {
                string key = MergeKey(row);
                ProcLine first;
                if (!seen.TryGetValue(key, out first))
                {
                    seen[key] = row;
                    merged.Add(row);
                    continue;
                }
                first.Line.Debit += row.Line.Debit;
                first.Line.Credit += row.Line.Credit;
                first.Rows.AddRange(row.Rows);
            }
            return merged;
        }

        // 顺序：往来科目的借方行、往来科目的贷方行、汇兑损益科目行（同 U8：往来行在前），各自保持原来的先后；2 到 200 行；
        // 借方合计必须等于贷方合计（红字行带负数，并账、红票对冲、汇兑损益的凭证合计可以是 0），且不能全为 0。
        public static List<ProcLine> Order(List<ProcLine> rows)
        {
            List<ProcLine> ordered = new List<ProcLine>();
            for (int pass = 0; pass < 3; pass++)
            {
                foreach (ProcLine row in rows)
                {
                    int at = row.Pl ? 2 : (row.Line.Debit != 0 ? 0 : 1);
                    if (at == pass)
                    {
                        ordered.Add(row);
                    }
                }
            }
            Balanced(ordered);
            return ordered;
        }

        // 2 到 200 行；借方合计等于贷方合计且不全为 0。票据处理的排序（ArapProcVoucherNotes.Order）共用。
        internal static void Balanced(List<ProcLine> rows)
        {
            decimal debit = 0;
            decimal credit = 0;
            decimal gross = 0;
            foreach (ProcLine row in rows)
            {
                debit += row.Line.Debit;
                credit += row.Line.Credit;
                gross += Math.Abs(row.Line.Debit) + Math.Abs(row.Line.Credit);
            }
            Count(rows.Count);
            if (debit != credit || gross == 0)
            {
                throw ArapVoucherDoc.Refuse("按处理明细拼出的凭证借贷不平（借 " + ArapVoucherAcct.Text(debit) + "，贷 " + ArapVoucherAcct.Text(credit)
                    + "），请在 U8 客户端制单");
            }
        }

        static void Count(int n)
        {
            if (n < 2)
            {
                throw ArapVoucherDoc.Refuse("凭证分录不足 2 行（各行金额为 0），请在 U8 客户端处理");
            }
            if (n > 200)
            {
                throw ArapVoucherDoc.Refuse("凭证分录超过 200 行（实际 " + n.ToString(CultureInfo.InvariantCulture) + " 行），请分几次制单");
            }
        }

        // 凭证行的 coutid：单据号；汇兑损益在单据号后加空格和分录号（同 U8：coutid = '{cVouchID} {inid}'）。
        public static string OutId(string style, string vid, int entry)
        {
            return style == "9M" ? vid + " " + entry.ToString(CultureInfo.InvariantCulture) : vid;
        }

        // 排好序的分录写进计划：草稿、每行来源、明细行 → 分录号（汇兑损益科目行不回写）、业务员、受控科目、外币行。
        public static void Fill(ProcPlan plan, List<ProcLine> rows)
        {
            VoucherPlan gl = plan.Gl;
            gl.Draft.Sign = gl.Key.Sign;
            gl.Draft.Date = gl.Date;
            gl.Draft.Attachments = plan.Ask.CancelNos.Count;
            for (int i = 0; i < rows.Count; i++)
            {
                ProcLine row = rows[i];
                int entry = i + 1;
                gl.Draft.Lines.Add(row.Line);
                plan.Sources.Add(new string[] { row.VType, OutId(plan.Ask.Style, row.VId, entry) });
                if (row.Op.Length > 0)
                {
                    gl.Operators[entry] = row.Op;
                }
                if (row.Controlled)
                {
                    gl.Controlled.Add(entry);
                }
                if (row.Fc.Length > 0)
                {
                    plan.Fc[entry] = row.Fc;
                }
                if (row.Pl)
                {
                    continue;
                }
                foreach (string key in row.Rows)
                {
                    gl.Entries[key] = entry;
                }
            }
        }
    }

    // 处理制单计划：请求、全部明细行（两张表）、凭证计划（ArapVoucherSave 用它保存、核对）、每行分录的来源、外币行。
    internal sealed class ProcPlan
    {
        public ProcVoucherAsk Ask;
        public List<ProcVoucherRow> Rows = new List<ProcVoucherRow>();
        public VoucherPlan Gl = new VoucherPlan();
        // 分录号 - 1 → [coutbillsign, coutid]。
        public List<string[]> Sources = new List<string[]>();
        // 分录号 → 币种（往来科目核算外币的 9M 行）。
        public Dictionary<int, string> Fc = new Dictionary<int, string>();
        // 票据处理批次的 AP_Note_Sub 行（ArapProcVoucherNotes.Prepare 读入；其余处理为空）。
        public List<ProcNoteSub> NoteSubs = new List<ProcNoteSub>();

        // 涉及的表（AR / AP）。
        public List<string> Ledgers()
        {
            List<string> list = new List<string>();
            foreach (ProcVoucherRow row in Rows)
            {
                if (!list.Contains(row.Ledger))
                {
                    list.Add(row.Ledger);
                }
            }
            return list;
        }
    }
}
