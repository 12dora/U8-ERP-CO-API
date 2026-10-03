using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 一张单据一行（应收单整单为 0）的可处理余额：原币、本币。
    internal sealed class BadSlot
    {
        public int Line;
        public decimal RemainF;
        public decimal RemainN;
    }

    // 落到一行上的坏账发生金额：写一条 9G 处理行（贷方），扣该行余额。BillA / BillB 是销售发票行写之前的累计核销
    // （iExchSum / iMoneySum），写后核对用。
    internal sealed class BadPiece
    {
        public BadDoc Doc;
        public int Line;
        public decimal F;
        public decimal N;
        public decimal BeforeF;
        public decimal BillA;
        public decimal BillB;
    }

    // 请求里的一项过了闸门之后：单据主键、表头（数据权限按它）、汇率、分摊到各行的金额。
    internal sealed class BadDoc
    {
        public ArapBadLine Ask;
        public int Id;
        public decimal Rate;
        public Dictionary<string, object> Head;
        public List<BadPiece> Pieces = new List<BadPiece>();

        public string Label
        {
            get { return ArapBadRule.TitleOf(Ask.Type) + " " + Ask.Id; }
        }
    }

    // 坏账发生的计划：公共闸门（BadOpen）、币种、各单据的分摊。
    internal sealed class BadOccurPlan
    {
        public BadOpen Open;
        public string Local;
        public string Currency;
        public bool Home;
        public string Digest;
        public decimal Rate;
        public List<BadDoc> Docs = new List<BadDoc>();
        public string CancelNo = "";
        public decimal RemainAfter;

        public decimal TotalF
        {
            get { return Sum(true); }
        }

        public decimal TotalN
        {
            get { return Sum(false); }
        }

        decimal Sum(bool f)
        {
            decimal sum = 0m;
            foreach (BadPiece p in Pieces)
            {
                sum += f ? p.F : p.N;
            }
            return sum;
        }

        public IEnumerable<BadPiece> Pieces
        {
            get
            {
                foreach (BadDoc d in Docs)
                {
                    foreach (BadPiece p in d.Pieces)
                    {
                        yield return p;
                    }
                }
            }
        }
    }

    // 坏账发生的固定规则（纯函数，--selftest 核对）。依据 U8 界面坏账发生的处理结果（测试账套实测）：
    // 可选单据 26 / 27 / 28 / 29（销售发票，按行 iBVid）和 R0–R9（应收单，整单），不含 RZ；只扣正余额；处理行记贷方、iFlag=0。
    internal static class ArapBadRule
    {
        public const string OccurStyle = "9G";
        public const string RecoverStyle = "9H";
        public const string DefaultOccurDigest = "坏账发生";
        public const string DefaultRecoverDigest = "坏账收回";
        // 一次坏账发生最多写多少条处理行（发票按行分摊后）。
        public const int MaxPieces = 500;

        public static bool Pickable(string type)
        {
            if (type == "26" || type == "27" || type == "28" || type == "29")
            {
                return true;
            }
            return type != null && type.Length == 2 && type[0] == 'R' && type[1] >= '0' && type[1] <= '9';
        }

        // 应收单（Ap_Vouch）按整单，发票按行。
        public static bool WholeDoc(string type)
        {
            return type != null && type.StartsWith("R", StringComparison.Ordinal);
        }

        public static string KindOf(string type)
        {
            return WholeDoc(type) ? "ar_bill" : "sale_invoice";
        }

        public static string TitleOf(string type)
        {
            return WholeDoc(type) ? "应收单" : "销售发票";
        }

        // 原币折本币：本位币即原币；外币把一行余额全部处理掉时取该行的本币余额（不留尾差），否则按单据汇率折算、两位小数。
        public static decimal Native(decimal f, BadSlot slot, bool home, decimal rate)
        {
            if (home)
            {
                return f;
            }
            if (f == slot.RemainF)
            {
                return slot.RemainN;
            }
            return decimal.Round(f * rate, 2, MidpointRounding.AwayFromZero);
        }

        // 按行主键从小到大（先开的行先处理）分摊 amount，跳过余额不大于 0 的行；余额不够返回 null。
        public static List<BadPiece> Spread(BadDoc doc, List<BadSlot> slots, decimal amount, bool home)
        {
            List<BadSlot> sorted = new List<BadSlot>(slots);
            sorted.Sort(delegate(BadSlot a, BadSlot b) { return a.Line.CompareTo(b.Line); });
            List<BadPiece> pieces = new List<BadPiece>();
            decimal left = amount;
            foreach (BadSlot slot in sorted)
            {
                if (left <= 0m)
                {
                    break;
                }
                if (slot.RemainF <= 0m)
                {
                    continue;
                }
                BadPiece p = new BadPiece();
                p.Doc = doc;
                p.Line = slot.Line;
                p.BeforeF = slot.RemainF;
                p.F = Math.Min(left, slot.RemainF);
                p.N = Native(p.F, slot, home, doc == null ? 1m : doc.Rate);
                pieces.Add(p);
                left -= p.F;
            }
            return left > 0m ? null : pieces;
        }

        // 外币：各单据汇率必须相同（U8 坏账发生界面只有一个汇率）。本位币不比。纯函数。
        public static bool SameRate(List<BadDoc> docs, bool home)
        {
            if (home)
            {
                return true;
            }
            for (int i = 1; i < docs.Count; i++)
            {
                if (docs[i].Rate != docs[0].Rate)
                {
                    return false;
                }
            }
            return true;
        }

        // 处理号的形状：HZAR + 13 位数字（如 HZAR0000000000001），共 17 位。
        public static bool NoValid(string no)
        {
            if (no == null || no.Length != 17 || !no.StartsWith("HZAR", StringComparison.Ordinal))
            {
                return false;
            }
            for (int i = 4; i < no.Length; i++)
            {
                if (no[i] < '0' || no[i] > '9')
                {
                    return false;
                }
            }
            return true;
        }

        public static string Money(decimal value)
        {
            return value.ToString("0.00", CultureInfo.InvariantCulture);
        }

        public static string Rate(decimal value)
        {
            return decimal.Round(value, 10).ToString("0.##########", CultureInfo.InvariantCulture);
        }
    }

    // 坏账发生的单据闸门（事务里、带锁读，写之前）：单据存在（404）、已审核（有审核行）、客户对、不受审批流控制、没被 U8 客户端占用、
    // 币种与请求相同、单据日期不晚于登记日期、没有关联合同（U8 会另写合同的预收预付，本接口不做），金额不超过可处理余额（只扣正余额）。
    internal static class ArapBadOccurPlan
    {
        public static BadOccurPlan Plan(WorkContext ctx, ArapBadAsk ask)
        {
            object conn = ctx.Conn;
            BadOccurPlan plan = new BadOccurPlan();
            plan.Open = ArapBad.Open(ctx, ask);
            if (TransferSql.PartnerName(conn, "AR", ask.Customer) == null)
            {
                throw new BridgeException(404, "not_found", "客户 " + ask.Customer + " 不存在");
            }
            plan.Local = WriteoffSql.LocalCurrency(conn);
            plan.Currency = ask.Currency.Length == 0 ? plan.Local : ask.Currency;
            plan.Home = string.Equals(plan.Currency, plan.Local, StringComparison.Ordinal);
            plan.Digest = ask.Digest.Length == 0 ? ArapBadRule.DefaultOccurDigest : ask.Digest;
            Refs(conn, ask, plan.Open.Date);
            int pieces = 0;
            foreach (ArapBadLine line in ask.Lines)
            {
                BadDoc d = Doc(conn, plan, ask, line);
                pieces += d.Pieces.Count;
                plan.Docs.Add(d);
            }
            if (pieces > ArapBadRule.MaxPieces)
            {
                throw ArapBad.State("本次坏账发生要写 " + pieces.ToString(CultureInfo.InvariantCulture) + " 条处理行，超过 "
                    + ArapBadRule.MaxPieces.ToString(CultureInfo.InvariantCulture) + " 条，请分批处理");
            }
            if (!ArapBadRule.SameRate(plan.Docs, plan.Home))
            {
                throw new BridgeException(400, "bad_request", "外币坏账发生的各单据汇率必须相同，请按汇率分批处理", "lines");
            }
            plan.Rate = plan.Home || plan.Docs.Count == 0 ? 1m : plan.Docs[0].Rate;
            return plan;
        }

        // 部门（存在、末级）、业务员（存在）同应收单据新增（ArapArch.Refs，400）；另拒绝登记日期当天或之前已停用的部门、业务员。
        static void Refs(object conn, ArapBadAsk ask, string date)
        {
            Dictionary<string, string> map = new Dictionary<string, string>();
            map["dept"] = ask.Dept;
            map["person"] = ask.Person;
            ArapArch.Refs(conn, map, "dept", "person", new HashSet<string>(StringComparer.OrdinalIgnoreCase));
            string ended = " is not null and {c}<=cast(convert(date, ?, 23) as datetime)";
            if (ask.Dept.Length > 0 && Rows.Scalar(conn, "select top 1 'x' from Department where cDepCode=? and dDepEndDate"
                + ended.Replace("{c}", "dDepEndDate"), new object[] { ask.Dept, date }) != null)
            {
                throw new BridgeException(400, "bad_request", "部门已停用 " + ask.Dept, "dept");
            }
            if (ask.Person.Length > 0 && Rows.Scalar(conn, "select top 1 'x' from Person where cPersonCode=? and dPInValidDate"
                + ended.Replace("{c}", "dPInValidDate"), new object[] { ask.Person, date }) != null)
            {
                throw new BridgeException(400, "bad_request", "业务员已停用 " + ask.Person, "person");
            }
        }

        static BadDoc Doc(object conn, BadOccurPlan plan, ArapBadAsk ask, ArapBadLine line)
        {
            BadDoc d = new BadDoc();
            d.Ask = line;
            d.Head = Head(conn, line);
            d.Id = d.Head == null ? 0 : CoRows.AsId(CoRows.Col(d.Head, "id"));
            if (d.Id == 0)
            {
                throw new BridgeException(404, "not_found", d.Label + " 不存在");
            }
            Audited(conn, d, ask.Customer);
            Usable(conn, d);
            Same(plan, d);
            d.Rate = Rate(plan, d);
            if (line.LineId > 0 && !WriteoffSql.LineOf(conn, WriteoffKind.Of("sale_invoice"), line.LineId, d.Id))
            {
                throw new BridgeException(400, "bad_request", d.Label + " 没有明细行 " + line.LineId.ToString(CultureInfo.InvariantCulture),
                    "lines");
            }
            List<BadSlot> slots = ArapBadOccurSql.Slots(conn, d, ask.Customer);
            List<BadPiece> pieces = ArapBadRule.Spread(d, slots, line.Amount, plan.Home);
            if (pieces == null)
            {
                throw ArapBad.State(d.Label + " 的坏账金额超过可处理余额 " + ArapBadRule.Money(Positive(slots))
                    + "（只处理余额为正的单据行）");
            }
            d.Pieces = pieces;
            ArapBadOccurSql.Before(conn, d, ask.Customer);
            return d;
        }

        static decimal Positive(List<BadSlot> slots)
        {
            decimal sum = 0m;
            foreach (BadSlot s in slots)
            {
                sum += Math.Max(0m, s.RemainF);
            }
            return sum;
        }

        // 按（单号、类型）带锁读表头：发票 SaleBillVouch，应收单 Ap_Vouch（cFlag 必须是 AR）。类型对不上当作不存在。
        static Dictionary<string, object> Head(object conn, ArapBadLine line)
        {
            WriteoffKind kind = WriteoffKind.Of(ArapBadRule.KindOf(line.Type));
            Dictionary<string, object> head = Rows.One(conn, kind.CodeSql, new object[] { line.Id, line.Type });
            if (head == null || CoRows.Col(head, "vtype") != line.Type)
            {
                return null;
            }
            return ArapBadRule.WholeDoc(line.Type) && CoRows.Col(head, "flag") != "AR" ? null : head;
        }

        // 已审核：表头有审核人，往来明细上有审核行（cProcStyle = cVouchType），审核行的客户就是请求的客户。
        static void Audited(object conn, BadDoc d, string customer)
        {
            string partner = CoRows.Col(d.Head, "auditor").Length == 0 ? null
                : WriteoffSql.Partner(conn, "AR", d.Ask.Type, d.Ask.Id);
            if (partner == null)
            {
                throw ArapBad.State(d.Label + " 未审核");
            }
            if (!string.Equals(partner.Trim(), customer, StringComparison.OrdinalIgnoreCase))
            {
                throw ArapBad.State(d.Label + " 的客户是 " + partner.Trim() + "，不是 " + customer);
            }
        }

        static void Usable(object conn, BadDoc d)
        {
            if (CoRows.FlagOf(d.Head, "wf"))
            {
                throw new BridgeException(409, "workflow_enabled", d.Label + " 受审批流控制，不能通过接口做坏账发生");
            }
            if (CoRows.FlagOf(d.Head, "locked"))
            {
                throw ArapBad.State(d.Label + " 正被其他操作锁定");
            }
            string ws = TransferSql.LockedBy(conn, d.Ask.Type, d.Ask.Id, d.Id);
            if (ws != null)
            {
                throw ArapBad.State(d.Label + " 正在 U8 客户端被占用" + (ws.Trim().Length > 0 ? "（" + ws.Trim() + "）" : string.Empty)
                    + "，请关闭后再试");
            }
        }

        // 币种与请求相同（表头为空按本位币）；单据日期不晚于登记日期。
        static void Same(BadOccurPlan plan, BadDoc d)
        {
            string cur = CoRows.Col(d.Head, "cur");
            if (cur.Length == 0)
            {
                cur = plan.Local;
            }
            if (!string.Equals(cur, plan.Currency, StringComparison.Ordinal))
            {
                throw ArapBad.State(d.Label + " 的币种是 " + cur + "，与坏账发生币种 " + plan.Currency + " 不一致");
            }
            string date = CoRows.Col(d.Head, "vdate");
            if (string.CompareOrdinal(date, plan.Open.Date) > 0)
            {
                throw ArapBad.State("登记日期早于" + d.Label + " 的日期 " + date);
            }
        }

        // 外币按单据表头的汇率（必须大于 0）；本位币不用汇率。
        static decimal Rate(BadOccurPlan plan, BadDoc d)
        {
            if (plan.Home)
            {
                return 1m;
            }
            decimal rate = WriteoffSql.Num(CoRows.Col(d.Head, "rate"));
            if (rate <= 0m)
            {
                throw ArapBad.State(d.Label + " 的汇率无效（外币汇率应大于 0）");
            }
            return rate;
        }
    }
}
