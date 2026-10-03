using System;
using System.Collections.Generic;

namespace U8Co
{
    // 期间损益结转（GL_bautotran.itype=30）：每个转账序号两行，inid=1 是损益科目、inid=2 是本年利润科目，定义上没有公式
    // （两行的 bd_c 都是 0，不用；方向按余额定）。U8 的结转凭证照这样生成：
    // 损益类末级科目（带辅助核算的按辅助项）已记账的期末余额非零的，反向结平，差额进本年利润；
    // 贷方性质的科目（收入）一张凭证、借方性质的（费用）一张：收入凭证本年利润在最后一行（贷方），费用凭证在第一行（借方）。
    // 余额为反方向的写成同一边的负数。摘要「期间损益结转」，类别取定义上的 csign。
    // 定义里的科目不存在或不是末级、本年利润科目不合格：跳过这一对，写进 skipped。
    // 本期已有期间损益凭证时只补生成缺的那一类（GlTransferPnlDone）。
    internal static class GlTransferPnl
    {
        const string DefSql = "SELECT * FROM GL_bautotran WHERE itype=30";
        const int MaxDefs = 10000;
        internal const string Income = "income";
        internal const string Expense = "expense";

        public static void Build(object conn, GlTransferPlan plan, GlTransferCodes codes)
        {
            GlTransferAsk ask = plan.Ask;
            List<string[]> done = null;
            if (ask.Exclude)
            {
                GlTransferBal.Excluded(conn, plan, "");
            }
            else
            {
                done = GlTransferPnlDone.Existing(conn, ask, codes);
            }
            List<string[]> pairs = Pairs(Rows.Query(conn, DefSql, new object[0], MaxDefs), plan, codes);
            if (pairs.Count == 0)
            {
                return;
            }
            // 数据权限：定义里的科目在取数之前查（GlTransferPerm）。
            GlTransferPerm.Pairs(plan.Perm, pairs);
            Dictionary<string, GlTransferBalRow> bal = GlTransferBal.Group(GlTransferBal.Pnl(conn, ask), codes);
            Assemble(plan, pairs, bal, codes);
            if (done != null)
            {
                GlTransferPnlDone.Filter(plan, done);
            }
        }

        // 每个转账序号 → {序号, 类别, 损益科目, 本年利润科目}，按序号排序；不合格的写进 skipped。
        internal static List<string[]> Pairs(List<Dictionary<string, object>> rows, GlTransferPlan plan, GlTransferCodes codes)
        {
            Dictionary<string, string[]> map = new Dictionary<string, string[]>(StringComparer.Ordinal);
            foreach (Dictionary<string, object> raw in rows)
            {
                GlTransferDef def = GlTransferDef.Of(raw);
                string[] pair;
                if (!map.TryGetValue(def.TranId, out pair))
                {
                    pair = new string[] { def.TranId, def.Sign, "", "" };
                    map[def.TranId] = pair;
                }
                if (def.Inid == 1 || def.Inid == 2)
                {
                    pair[def.Inid + 1] = def.Code;
                }
            }
            List<string> ids = new List<string>(map.Keys);
            ids.Sort(GlTransferDef.Order);
            List<string[]> list = new List<string[]>();
            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (string id in ids)
            {
                string[] pair = map[id];
                string why = Problem(pair, codes, seen);
                if (why != null)
                {
                    plan.Skip(pair[0], pair[2], why);
                    continue;
                }
                seen.Add(pair[2]);
                list.Add(pair);
            }
            return list;
        }

        static string Problem(string[] pair, GlTransferCodes codes, HashSet<string> seen)
        {
            if (pair[1].Length == 0 || pair[2].Length == 0 || pair[3].Length == 0)
            {
                return "定义不完整（缺凭证类别、损益科目或本年利润科目）";
            }
            if (!codes.Leaf(pair[2]))
            {
                return "损益科目不存在或不是末级科目";
            }
            if (!codes.Leaf(pair[3]))
            {
                return "本年利润科目 " + pair[3] + " 不存在或不是末级科目";
            }
            if (TestAux(codes, pair[3]))
            {
                return "本年利润科目 " + pair[3] + " 带辅助核算，桥暂不支持";
            }
            return seen.Contains(pair[2]) ? "损益科目重复定义" : null;
        }

        static bool TestAux(GlTransferCodes codes, string code)
        {
            return GlTransferCodes.Any(codes.Dims(code));
        }

        // 组凭证：按（类别、本年利润科目、收入 / 支出）分组，收入在前；组内按转账序号、辅助项。
        internal static void Assemble(GlTransferPlan plan, List<string[]> pairs, Dictionary<string, GlTransferBalRow> bal,
            GlTransferCodes codes)
        {
            List<string> order = new List<string>();
            Dictionary<string, List<GlTransferBalRow>> groups = new Dictionary<string, List<GlTransferBalRow>>(StringComparer.Ordinal);
            Dictionary<string, string[]> heads = new Dictionary<string, string[]>(StringComparer.Ordinal);
            foreach (string[] pair in pairs)
            {
                string pack = codes.DebitNature(pair[2]) ? Expense : Income;
                foreach (GlTransferBalRow row in Of(bal, pair[2]))
                {
                    string key = (pack == Income ? "0" : "1") + "\n" + pair[1] + "\n" + pair[3];
                    if (!groups.ContainsKey(key))
                    {
                        groups[key] = new List<GlTransferBalRow>();
                        heads[key] = new string[] { pair[1], pair[3], pack };
                        order.Add(key);
                    }
                    groups[key].Add(row);
                }
            }
            order.Sort(StringComparer.Ordinal);
            foreach (string key in order)
            {
                plan.Vouchers.Add(Voucher(heads[key], groups[key]));
            }
        }

        // 某损益科目的非零余额行，按辅助项排序。
        static List<GlTransferBalRow> Of(Dictionary<string, GlTransferBalRow> bal, string code)
        {
            List<GlTransferBalRow> list = new List<GlTransferBalRow>();
            foreach (GlTransferBalRow row in bal.Values)
            {
                if (row.Value != 0 && row.Code.Equals(code, StringComparison.OrdinalIgnoreCase))
                {
                    list.Add(row);
                }
            }
            list.Sort(delegate(GlTransferBalRow a, GlTransferBalRow b)
            {
                return string.CompareOrdinal(string.Join("\n", a.Aux), string.Join("\n", b.Aux));
            });
            return list;
        }

        // head：{类别, 本年利润科目, 收入 / 支出}。
        static GlTransferVoucher Voucher(string[] head, List<GlTransferBalRow> rows)
        {
            GlTransferVoucher v = new GlTransferVoucher();
            v.Sign = head[0];
            v.Pack = head[2];
            v.Digest = GlTransferReq.PnlDigest;
            v.Draft = new GlDraft();
            v.Draft.Sign = v.Sign;
            v.Draft.Date = "";
            bool income = v.Pack == Income;
            decimal total = 0;
            List<GlLine> lines = new List<GlLine>();
            foreach (GlTransferBalRow row in rows)
            {
                // 借正贷负的余额：收入（贷方余额）借记 -余额，费用（借方余额）贷记余额。
                decimal amount = income ? -row.Value : row.Value;
                total += amount;
                lines.Add(GlTransferLines.Line(row.Code, v.Digest, income, amount, row.Aux));
            }
            // 正负相抵为 0 时不写本年利润行（U8 不写金额为 0 的分录）。
            GlLine profit = GlTransferLines.Line(head[1], v.Digest, !income, total, null);
            if (total != 0)
            {
                lines.Insert(income ? lines.Count : 0, profit);
            }
            v.Draft.Lines = lines;
            return v;
        }
    }

    // 结转分录行。
    internal static class GlTransferLines
    {
        // aux 下标同 GlTransferBal.AuxCols；null 为不带辅助项。
        public static GlLine Line(string account, string digest, bool debit, decimal amount, string[] aux)
        {
            GlLine line = new GlLine();
            line.Account = account;
            line.Digest = digest;
            line.Debit = debit ? amount : 0;
            line.Credit = debit ? 0 : amount;
            string[] a = aux ?? new string[6];
            line.Dept = a[0] ?? "";
            line.Person = a[1] ?? "";
            line.Customer = a[2] ?? "";
            line.Supplier = a[3] ?? "";
            line.ItemClass = a[4] ?? "";
            line.Item = a[5] ?? "";
            line.Settle = "";
            line.DocNo = "";
            line.DocDate = "";
            line.Currency = "";
            return line;
        }
    }
}
