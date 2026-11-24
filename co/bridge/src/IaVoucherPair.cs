using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 调拨、形态转换、组装、拆卸的成对单据（同 U8 制单：其他入库单 08、其他出库单 09 按 Left(cBusType, 2) + cBusCode 成对）。
    // 制单单元是业务单据：只选了一边时把另一边按业务单号补进来，两边一起制单、一起跳过、一起回写表头。
    // U8 在两边金额不等（sum(入库金额 − 出库金额 + 借方差异 − 贷方差异) ≠ 0）时另拼一行差额分录，桥不拼、拒绝。
    internal static class IaVoucherPair
    {
        const int Chunk = 200;
        const string PairFilter = "left(isnull(s.cBusType,N''),2) in (N'调拨',N'转换',N'组装',N'拆卸') and s.cBusCode in ";

        // 制单单元键：成对业务「前两个字 + 业务单号」（业务单号不分大小写，同 SQL Server 排序规则），其他空串。
        internal static string Key(IaRow row)
        {
            return row.Pair.Length == 0 ? "" : row.Pair + "\u0001" + row.BusCode.ToUpperInvariant();
        }

        // 凭证分录的外部单号（GL_accvouch.coutid）：成对业务是业务单号 cBusCode，其他是单据号（同 U8 制单临时表的 cVouCode）。
        internal static string OutId(IaRow row)
        {
            return row.Pair.Length > 0 ? row.BusCode : row.Code;
        }

        // 把选中的成对业务的另一边（及同业务单号的其他行）补进来；已有的行不重复。
        public static List<IaRow> Pull(object conn, int year, List<IaRow> rows)
        {
            HashSet<string> keys = new HashSet<string>(StringComparer.Ordinal);
            HashSet<string> have = new HashSet<string>(StringComparer.Ordinal);
            List<object> codes = Codes(rows, keys, have);
            List<IaRow> all = new List<IaRow>(rows);
            foreach (string code in new string[] { "08", "09" })
            {
                for (int start = 0; start < codes.Count; start += Chunk)
                {
                    List<object> part = codes.GetRange(start, Math.Min(Chunk, codes.Count - start));
                    string filter = PairFilter + "(?" + new StringBuilder().Insert(0, ",?", part.Count - 1) + ")";
                    foreach (IaRow row in IaVoucherLoad.Query(conn, IaType.Find(code), filter, GlSql.With(new object[] { year }, part.ToArray()), 0))
                    {
                        if (keys.Contains(Key(row)) && have.Add(row.Aid))
                        {
                            all.Add(row);
                        }
                    }
                }
            }
            if (all.Count > IaVoucherLoad.RowMax)
            {
                throw IaVoucherLoad.Refuse("选中的存货明细账（含成对单据的另一边）超过 " + IaVoucherLoad.RowMax + " 行；请用 types 或 ids 分批制单");
            }
            return all;
        }

        // 已选行的 AutoID 记进 have，成对业务的单元键记进 keys；返回要补查的业务单号（不分大小写去重）。
        static List<object> Codes(List<IaRow> rows, HashSet<string> keys, HashSet<string> have)
        {
            List<object> codes = new List<object>();
            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (IaRow row in rows)
            {
                have.Add(row.Aid);
                if (row.Pair.Length > 0 && row.BusCode.Length > 0)
                {
                    keys.Add(Key(row));
                    if (seen.Add(row.BusCode))
                    {
                        codes.Add(row.BusCode);
                    }
                }
            }
            return codes;
        }

        // 按制单单元分组，保持先后：成对业务同键的单据一组，其他单据各自一组。
        public static List<List<IaBill>> Units(List<IaBill> bills)
        {
            List<List<IaBill>> units = new List<List<IaBill>>();
            Dictionary<string, List<IaBill>> byKey = new Dictionary<string, List<IaBill>>(StringComparer.Ordinal);
            foreach (IaBill bill in bills)
            {
                List<IaBill> unit;
                if (bill.Unit.Length > 0 && byKey.TryGetValue(bill.Unit, out unit))
                {
                    unit.Add(bill);
                    continue;
                }
                unit = new List<IaBill>();
                unit.Add(bill);
                units.Add(unit);
                if (bill.Unit.Length > 0)
                {
                    byKey[bill.Unit] = unit;
                }
            }
            return units;
        }

        // 成对单元的闸门（各张单据的闸门过了之后）：业务单号齐全、行的业务与单据一致、入库出库两边都在、两边金额相等。合格返回 null。
        public static string Problem(List<IaBill> unit)
        {
            if (unit[0].Unit.Length == 0)
            {
                return null;
            }
            decimal net = 0;
            bool hasIn = false;
            bool hasOut = false;
            foreach (IaBill bill in unit)
            {
                string bad = RowsProblem(bill, ref net);
                if (bad != null)
                {
                    return bad;
                }
                hasIn |= bill.Type.In;
                hasOut |= !bill.Type.In;
            }
            IaRow first = unit[0].Rows[0];
            string name = IaType.PairDigest(first.Pair) + " " + first.BusCode;
            if (!hasIn || !hasOut)
            {
                return name + " 缺少另一边（未记账或不在登录年度），暂不支持制单";
            }
            if (net != 0)
            {
                return name + " 的出入库金额不等（差 " + net.ToString("0.00", CultureInfo.InvariantCulture) + "），暂不支持制单";
            }
            return null;
        }

        // 一张单据的行：有业务单号、与单据同一单元；金额并进 net（入库加、出库减）。
        static string RowsProblem(IaBill bill, ref decimal net)
        {
            foreach (IaRow row in bill.Rows)
            {
                if (row.BusCode.Length == 0)
                {
                    return "调拨、形态转换、组装、拆卸业务的存货明细账行没有业务单号，暂不支持制单";
                }
                if (Key(row) != bill.Unit)
                {
                    return "表体行的业务类型或业务单号不一致，暂不支持制单";
                }
                net += row.Type.In ? row.Amount : -row.Amount;
            }
            return null;
        }

        // 按月份选时，成对单元补进来的另一边记在别的月份、且该月晚于制单日期或存货核算已结账：整个单元跳过，原因进 skipped
        // （按单据选时由结账、制单日期闸门 409）。返回新跳过的单元数。
        public static int Strays(object conn, IaJob job)
        {
            // 制单日期不在登录年度时不跳过，交给 IaVoucherPlan.DateCheck 报错。
            string year = job.Year.ToString(CultureInfo.InvariantCulture) + "-";
            if (job.Ask.Month == 0 || !job.Date.StartsWith(year, StringComparison.Ordinal))
            {
                return 0;
            }
            int skipped = 0;
            foreach (List<IaBill> unit in Units(job.Bills))
            {
                IaBill culprit = null;
                string problem = unit[0].Skip.Length > 0 ? null : Stray(conn, job, unit, out culprit);
                if (problem != null)
                {
                    IaVoucherLoad.SkipUnit(unit, culprit, problem);
                    skipped++;
                }
            }
            return skipped;
        }

        static string Stray(object conn, IaJob job, List<IaBill> unit, out IaBill culprit)
        {
            int last = IaVoucherPlan.Month(job.Date);
            foreach (IaBill bill in unit)
            {
                culprit = bill;
                foreach (IaRow row in bill.Rows)
                {
                    if (row.Month == job.Ask.Month)
                    {
                        continue;
                    }
                    if (row.Month > last)
                    {
                        return "记账月份（" + IaVoucherLoad.Int(row.Month) + " 月）晚于制单日期 " + job.Date + "，成对业务整单跳过";
                    }
                    if (IaVoucherLoad.Closed(conn, row.Year, row.Month))
                    {
                        return "记账月份（" + IaVoucherLoad.Int(row.Month) + " 月）存货核算已结账，成对业务整单跳过";
                    }
                }
            }
            culprit = null;
            return null;
        }
    }
}
