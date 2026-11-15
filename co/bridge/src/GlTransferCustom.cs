using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 一个自定义转账定义（同一 ctran_id 的各行，按 inid）解析后的样子。
    internal sealed class GlCustomDef
    {
        public string TranId;
        public string Sign;
        public string Digest;
        public List<GlTransferDef> Rows = new List<GlTransferDef>();
        public List<GlFormula> Formulas = new List<GlFormula>();
    }

    // 自定义转账（GL_bautotran.itype=10）：每个转账序号一张凭证，行的科目是 ccode、方向是 bd_c、金额按 cformula 计算
    // （QM 取数用公式里的科目，可以不是本行科目；CE() 是借贷差额）。金额为 0 的行不写。
    // 跳过（写进 skipped）：本期已生成（同期、coutsign「自定义转账」、摘要相同的未作废凭证）、按年取数（不给 tran_id 时）、
    // 科目不存在或不是末级、行科目要辅助核算而定义和公式都给不出、多于一行 CE()、算出来全为 0。
    // 公式用了桥不支持的函数、或给了 tran_id 而该定义按年取数：400，写明函数名或转账序号（不猜）。不给 tran_id 而后面的定义要取前面定义本次生成的科目时 409（Chain）。
    internal static class GlTransferCustom
    {
        const string DefSql = "SELECT * FROM GL_bautotran WHERE itype=10";
        const string DoneSql = "SELECT TOP 1 csign + N'-' + CONVERT(varchar(12), ino_id) k FROM GL_accvouch"
            + " WHERE iyear=? AND iperiod=? AND coutsign=N'" + GlTransferReq.CustomSign + "' AND cdigest=? AND ISNULL(iflag,0)<>1"
            + " ORDER BY csign, ino_id";
        const int MaxDefs = 10000;

        public static void Build(object conn, GlTransferPlan plan, GlTransferCodes codes)
        {
            List<GlCustomDef> defs = Load(conn, plan.Ask);
            if (defs.Count == 0)
            {
                throw GlState.Refuse(plan.Ask.TranId.Length > 0 ? "没有转账序号为 " + plan.Ask.TranId + " 的自定义转账定义"
                    : "账套没有自定义转账定义");
            }
            // 本次已生成的分录科目：{转账序号, 科目}（Chain 用）。
            List<string[]> made = new List<string[]>();
            foreach (GlCustomDef def in defs)
            {
                string why = Gate(conn, plan, def, codes);
                if (why != null)
                {
                    plan.Skip(def.TranId, "", why);
                    continue;
                }
                Chain(plan.Ask, def, made);
                // 数据权限：定义里的科目和辅助项在取数之前查（GlTransferPerm）。
                GlTransferPerm.Def(plan.Perm, def, codes);
                if (plan.Ask.Exclude)
                {
                    GlTransferBal.Excluded(conn, plan, def.Digest);
                }
                GlTransferVoucher v = GlTransferCustomCalc.Voucher(conn, plan, def, codes);
                if (v == null)
                {
                    plan.Skip(def.TranId, "", "没有需要结转的金额");
                    continue;
                }
                plan.Vouchers.Add(v);
                foreach (GlLine line in v.Draft.Lines)
                {
                    made.Add(new string[] { def.TranId, line.Account });
                }
            }
        }

        // 不给 tran_id 时 U8 按转账序号逐个结转，后一个在前一个生成（并记账）之后取数；桥一次只能取同一份已记账余额。
        // 所以后面的定义 QM 取数的科目（含下级）与前面定义本次生成的分录科目重叠时 409，请用 tran_id 逐个生成、审核、记账。
        // 只给一个 tran_id、或前面的定义没生成分录时不查；核对模式（exclude_existing）不查：余额里留着序号在前的已有结转凭证。
        internal static void Chain(GlTransferAsk ask, GlCustomDef def, List<string[]> made)
        {
            if (ask.TranId.Length > 0 || ask.Exclude || made.Count == 0)
            {
                return;
            }
            foreach (GlFormula f in def.Formulas)
            {
                foreach (GlQm qm in f.Atoms)
                {
                    string[] hit = Overlap(qm.Code, made);
                    if (hit != null)
                    {
                        throw GlState.Refuse("自定义转账 " + def.TranId + " 的 QM(" + qm.Code + ") 要取转账序号 " + hit[0]
                            + " 本次生成的科目 " + hit[1] + " 的余额：U8 按序号逐个结转，后一个在前一个记账之后取数。"
                            + "请用 tran_id 逐个生成，每生成一个先审核、记账，再生成下一个");
                    }
                }
            }
        }

        static string[] Overlap(string code, List<string[]> made)
        {
            foreach (string[] one in made)
            {
                if (one[1].StartsWith(code, StringComparison.OrdinalIgnoreCase) || code.StartsWith(one[1], StringComparison.OrdinalIgnoreCase))
                {
                    return one;
                }
            }
            return null;
        }

        // 读定义、按转账序号分组，解析公式（不支持的函数在这里 400）。
        static List<GlCustomDef> Load(object conn, GlTransferAsk ask)
        {
            Dictionary<string, GlCustomDef> map = new Dictionary<string, GlCustomDef>(StringComparer.Ordinal);
            foreach (Dictionary<string, object> raw in Rows.Query(conn, DefSql, new object[0], MaxDefs))
            {
                GlTransferDef row = GlTransferDef.Of(raw);
                if (ask.TranId.Length > 0 && row.TranId != ask.TranId)
                {
                    continue;
                }
                GlCustomDef def;
                if (!map.TryGetValue(row.TranId, out def))
                {
                    def = new GlCustomDef();
                    def.TranId = row.TranId;
                    map[row.TranId] = def;
                }
                def.Rows.Add(row);
            }
            List<string> ids = new List<string>(map.Keys);
            ids.Sort(GlTransferDef.Order);
            List<GlCustomDef> list = new List<GlCustomDef>();
            foreach (string id in ids)
            {
                list.Add(Prepare(map[id]));
            }
            return list;
        }

        static GlCustomDef Prepare(GlCustomDef def)
        {
            def.Rows.Sort(delegate(GlTransferDef a, GlTransferDef b) { return a.Inid.CompareTo(b.Inid); });
            def.Sign = def.Rows[0].Sign;
            def.Digest = "";
            foreach (GlTransferDef row in def.Rows)
            {
                if (def.Digest.Length == 0)
                {
                    def.Digest = row.Digest.Length > 0 ? row.Digest : "";
                }
                string where = "自定义转账 " + def.TranId + " 第 " + row.Inid.ToString(CultureInfo.InvariantCulture) + " 行：";
                def.Formulas.Add(GlTransferFormula.Parse(row.Formula, where));
            }
            if (def.Digest.Length == 0)
            {
                def.Digest = def.Rows[0].Text.Length > 0 ? def.Rows[0].Text : GlTransferReq.CustomSign;
            }
            return def;
        }

        // 按年取数 QM(…,年,…)（尤其带借 / 贷方向）的口径未与 U8 结转凭证逐项对照。给了 tran_id 时 400 点名该定义；不给时跳过（全部跳过由调用方 409）。
        internal static string YearGate(GlTransferAsk ask, GlCustomDef def)
        {
            foreach (GlFormula f in def.Formulas)
            {
                if (!GlTransferFormula.UsesYear(f))
                {
                    continue;
                }
                if (ask.TranId.Length > 0)
                {
                    throw new BridgeException(400, "bad_request", "转账定义 " + def.TranId
                        + " 用到按年取数（QM(…,年,…)），暂不支持，请在 U8 客户端生成");
                }
                return "用到按年取数（QM(…,年,…)），暂不支持，请在 U8 客户端生成";
            }
            return null;
        }

        // 定义级的闸门；不合格返回原因（跳过），合格返回 null。
        static string Gate(object conn, GlTransferPlan plan, GlCustomDef def, GlTransferCodes codes)
        {
            GlTransferAsk ask = plan.Ask;
            string year = YearGate(ask, def);
            if (year != null)
            {
                return year;
            }
            if (def.Sign.Length == 0)
            {
                return "定义没有凭证类别";
            }
            int ce = 0;
            foreach (GlFormula f in def.Formulas)
            {
                ce += f.Ce ? 1 : 0;
            }
            if (ce > 1)
            {
                return "多于一行 CE()，桥不支持";
            }
            string bad = GlTransferCustomCalc.Accounts(def, codes);
            if (bad != null)
            {
                return bad;
            }
            if (ask.Exclude)
            {
                return null;
            }
            string done = Rows.Scalar(conn, DoneSql, new object[] { ask.Year, ask.Period, def.Digest });
            return done == null ? null : "本期已生成（" + done.Trim() + "），如需重做请先作废并删除该凭证";
        }
    }
}
