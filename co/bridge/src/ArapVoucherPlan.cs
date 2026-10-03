using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 制单计划：合并同科目同辅助项的行（应收应付选项 bYPzKMHB：发票、应收应付单；bSPzKMHB：收付款单，缺省合并）、
    // 借方在前（同方向保持拼出的先后）、借贷必须平、摘要每行相同、凭证类别（没给时有现金 / 银行科目的借方「收」、贷方「付」，否则「转」）、
    // 现金流量（总账选项 bXJLL 开着、凭证里有现金流量科目时，给对方分录按「现金流量项目数据来源」GL_CashItemDataSource 取项目，
    // 取不出时用调用方的 cash_items，见 ArapCashItems）。
    internal static class ArapVoucherPlan
    {
        // 合并制单：每张单据各自拼分录、按选项合并（只在本单内合并，U8 多张收款单合成一张凭证时，同客户同科目的
        // 往来行也没有跨单据合并）、摘要各取各的，再整张排序（借方在前，同方向按单据顺序）。附单据数 = 单据张数。
        public static VoucherPlan Make(object conn, List<VoucherDoc> docs, string date, int year)
        {
            VoucherPlan plan = new VoucherPlan();
            plan.Docs = docs;
            plan.Doc = docs[0];
            plan.Date = date;
            plan.Key.Year = year;
            plan.Key.Period = DateTime.ParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture).Month;
            VoucherDoc doc = plan.Doc;
            string option = ArapVoucherDoc.IsReceipt(doc.Ask.Kind) ? "bSPzKMHB" : "bYPzKMHB";
            bool merge = Option(conn, doc.Flag, option);
            List<VoucherRow> rows = new List<VoucherRow>();
            for (int bill = 0; bill < docs.Count; bill++)
            {
                rows.AddRange(BillRows(conn, docs[bill], bill, merge, year));
            }
            rows = Order(rows);
            Balance(rows);
            plan.Key.Sign = Sign(conn, doc.Ask.Sign, rows);
            plan.Seq = GlState.SignSeq(conn, plan.Key.Sign);
            plan.Digest = rows[0].Line.Digest;
            plan.OutSign = ArapVoucherRule.OutSign(doc.Ask.Kind, doc.Flag);
            doc.Ask.CashItems.ByColumn = ArapVoucherRule.IsRefund(doc.Ask.Kind);
            Flows(conn, rows, year, date, doc.Ask.CashItems);
            ArapVoucherRefs.Check(conn, rows);
            Fill(plan, rows);
            return plan;
        }

        // 一张单据的分录行：拼分录、辅助核算、按选项合并，标上单据下标和本单的摘要。
        static List<VoucherRow> BillRows(object conn, VoucherDoc doc, int bill, bool merge, int year)
        {
            List<VoucherPart> parts = ArapVoucherBuild.Parts(conn, doc, year);
            List<VoucherRow> rows = ArapVoucherLines.Build(conn, doc, parts, year);
            if (merge)
            {
                rows = Merge(rows);
            }
            string digest = ArapVoucherDigest.Of(conn, doc);
            foreach (VoucherRow row in rows)
            {
                row.Bill = bill;
                row.Line.Digest = digest;
            }
            return rows;
        }

        static bool Option(object conn, string flag, string name)
        {
            string value = Rows.Scalar(conn, "select cValue from AccInformation where cSysID=? and cName=?", new object[] { flag, name });
            string text = value == null ? "" : value.Trim();
            return text.Length == 0 || text == "1" || text.Equals("true", StringComparison.OrdinalIgnoreCase);
        }

        static string MergeKey(VoucherRow row)
        {
            GlLine l = row.Line;
            return string.Join("\u0001", new string[]
            {
                l.Account.ToUpperInvariant(), l.Debit != 0 ? "D" : "C", l.Dept, l.Person, l.Customer, l.Supplier, l.ItemClass, l.Item,
                l.Settle, l.DocNo, l.DocDate, row.Op
            });
        }

        internal static List<VoucherRow> Merge(List<VoucherRow> rows)
        {
            Dictionary<string, VoucherRow> seen = new Dictionary<string, VoucherRow>(StringComparer.Ordinal);
            List<VoucherRow> merged = new List<VoucherRow>();
            foreach (VoucherRow row in rows)
            {
                string key = MergeKey(row);
                VoucherRow first;
                if (!seen.TryGetValue(key, out first))
                {
                    seen[key] = row;
                    merged.Add(row);
                    continue;
                }
                first.Line.Debit += row.Line.Debit;
                first.Line.Credit += row.Line.Credit;
                first.Marked = first.Marked || row.Marked;
                first.Aids.AddRange(row.Aids);
            }
            return merged;
        }

        // 借方在前，同方向保持原来的先后。借方红字（负数，退款单）也算借方。
        internal static List<VoucherRow> Order(List<VoucherRow> rows)
        {
            List<VoucherRow> ordered = new List<VoucherRow>();
            foreach (VoucherRow row in rows)
            {
                if (row.Line.Debit != 0)
                {
                    ordered.Add(row);
                }
            }
            foreach (VoucherRow row in rows)
            {
                if (row.Line.Debit == 0)
                {
                    ordered.Add(row);
                }
            }
            if (ordered.Count < 2 || ordered.Count > 200)
            {
                throw ArapVoucherDoc.Refuse("凭证分录必须是 2 到 200 行（实际 " + ordered.Count.ToString(CultureInfo.InvariantCulture) + " 行）");
            }
            return ordered;
        }

        // 借贷相等且有发生额。退款单的分录都在同一方（往来红字、结算正数），借贷合计都可以是 0，按金额绝对值判断有无发生额。
        internal static void Balance(List<VoucherRow> rows)
        {
            decimal debit = 0;
            decimal credit = 0;
            decimal gross = 0;
            foreach (VoucherRow row in rows)
            {
                debit += row.Line.Debit;
                credit += row.Line.Credit;
                gross += Math.Abs(row.Line.Debit) + Math.Abs(row.Line.Credit);
            }
            if (debit != credit || gross == 0)
            {
                throw ArapVoucherDoc.Refuse("按单据拼出的凭证借贷不平（借 " + ArapVoucherAcct.Text(debit) + "，贷 " + ArapVoucherAcct.Text(credit)
                    + "），请在 U8 客户端制单");
            }
        }

        static string Sign(object conn, string asked, List<VoucherRow> rows)
        {
            string sign = asked;
            if (sign.Length == 0)
            {
                sign = ArapVoucherRule.Transfer;
                foreach (VoucherRow row in rows)
                {
                    if (row.Cash)
                    {
                        sign = row.Line.Debit > 0 ? ArapVoucherRule.Receive : ArapVoucherRule.Pay;
                        break;
                    }
                }
            }
            string adjust = Rows.Scalar(conn, "select convert(varchar(4), isnull(iAdjustFlag,0)) from dsign where csign=?", new object[] { sign });
            if (adjust == null || adjust.Trim() == "1")
            {
                throw ArapVoucherDoc.Refuse("凭证类别 " + sign + " 不存在或是调整期凭证类别" + (asked.Length == 0 ? "，请用 sign 指定" : ""));
            }
            return sign;
        }

        // 现金流量项目（ArapCashItems：数据来源推不出时用调用方的 cash_items）。退款单按分录所在的列定方向（CashItemMap.ByColumn）。
        static void Flows(object conn, List<VoucherRow> rows, int year, string date, CashItemMap given)
        {
            List<GlLine> lines = new List<GlLine>();
            List<bool> cash = new List<bool>();
            foreach (VoucherRow row in rows)
            {
                lines.Add(row.Line);
                cash.Add(row.CashItem);
            }
            ArapCashItems.Flows(conn, lines, cash, year, date, given);
        }

        internal static void Fill(VoucherPlan plan, List<VoucherRow> rows)
        {
            plan.Draft.Sign = plan.Key.Sign;
            plan.Draft.Date = plan.Date;
            // 附单据数 = 单据张数（单张制单 idoc ≥ 1，N 张收款单合并的凭证 idoc = N）。
            plan.Draft.Attachments = plan.Docs.Count;
            for (int i = 0; i < rows.Count; i++)
            {
                VoucherRow row = rows[i];
                plan.Draft.Lines.Add(row.Line);
                plan.Bills.Add(row.Bill);
                int entry = i + 1;
                if (row.Op.Length > 0)
                {
                    plan.Operators[entry] = row.Op;
                }
                if (row.Controlled)
                {
                    plan.Controlled.Add(entry);
                }
                if (row.Marked)
                {
                    Remember(plan, row, entry);
                }
            }
        }

        // 回写明细 ino_id 的分录号：按明细行（Auto_ID）记，另记每张单据每个科目的第一行给本单的票据登记行用。
        static void Remember(VoucherPlan plan, VoucherRow row, int entry)
        {
            foreach (string aid in row.Aids)
            {
                plan.Entries[aid] = entry;
            }
            string key = VoucherPlan.AccountKey(row.Bill, row.Line.Account);
            if (!plan.ByAccount.ContainsKey(key))
            {
                plan.ByAccount[key] = entry;
            }
        }
    }
}
