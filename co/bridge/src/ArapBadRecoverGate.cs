using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 坏账收回的计划：公共闸门、收款单（表头、唯一的表体行）、币种、金额（原币即请求的 amount，本币取收款单行的本币余额）。
    internal sealed class BadRecoverPlan
    {
        public BadOpen Open;
        public Dictionary<string, object> Head;
        public int Id;
        public int Line;
        public string Currency;
        public decimal Rate;
        public string Digest;
        public decimal AmountF;
        public decimal AmountN;
        public string CancelNo = "";
        public decimal RemainAfter;
    }

    // 坏账收回的闸门（事务里、带锁读，写之前）。U8「坏账收回」界面只让选：本客户、同币种、未审核（cCheckMan 为空）、
    // 未核销、款项类型为应收款（iType=0）的收款单（48）。桥另拒绝：审批流控制（409 workflow_enabled）、U8 客户端占用、
    // 票据 / 网银 / 其他单据生成或期初的收款单、已制单、已有往来明细、多行收款单（U8 只挂一条 9H 行，多行时的写法没有核对），
    // 金额不等于收款单金额（U8 收回整张收款单：取消时把余额恢复成原金额）。收款单不存在 404，其余 409 state_mismatch。
    internal static class ArapBadRecoverGate
    {
        const string HeadSql = "select convert(varchar(20), h.iID) as id, h.cVouchID as code, h.cDwCode, h.cDeptCode, h.cPerson,"
            + " convert(varchar(10), h.dVouchDate, 23) as vdate, h.cexch_name as cur, {rate} as rate,"
            + " isnull(h.cCheckMan,N'') as auditor, h.IsWfControlled as wf, h.cNoteNo as note_no, h.cSrcFlag as src_flag,"
            + " h.bFromBank as from_bank, h.bToBank as to_bank, h.cCoVouchType as co_type, h.bStartFlag as start_flag,"
            + " h.cPzID as voucher, h.cCancelMan as settler"
            + " from Ap_CloseBill h with (UPDLOCK, HOLDLOCK) where h.cVouchID=? and h.cVouchType=N'48' and h.cFlag=N'AR'";
        const string LineSql = "select convert(varchar(20), b.ID) as line, convert(varchar(10), isnull(b.iType,0)) as itype,"
            + " {amt} as amt, {amtf} as amt_f, {rem} as rem, {remf} as rem_f"
            + " from Ap_CloseBills b with (UPDLOCK, HOLDLOCK) where b.iID=? order by b.ID";
        const string DetailSql = "select top 1 convert(varchar(20), d.Auto_ID) from Ar_Detail d where d.cFlag=N'AR'"
            + " and ((d.cVouchType=N'48' and d.cVouchID=?) or (d.cCoVouchType=N'48' and d.cCoVouchID=?))";

        public static BadRecoverPlan Plan(WorkContext ctx, ArapBadAsk ask)
        {
            object conn = ctx.Conn;
            BadRecoverPlan plan = new BadRecoverPlan();
            plan.Open = ArapBad.Open(ctx, ask);
            if (TransferSql.PartnerName(conn, "AR", ask.Customer) == null)
            {
                throw new BridgeException(404, "not_found", "客户 " + ask.Customer + " 不存在");
            }
            string local = WriteoffSql.LocalCurrency(conn);
            plan.Currency = ask.Currency.Length == 0 ? local : ask.Currency;
            plan.Digest = ask.Digest.Length == 0 ? ArapBadRule.DefaultRecoverDigest : ask.Digest;
            plan.Head = Head(conn, ask.Receipt);
            plan.Id = CoRows.AsId(CoRows.Col(plan.Head, "id"));
            if (CoRows.FlagOf(plan.Head, "wf"))
            {
                throw new BridgeException(409, "workflow_enabled", "收款单 " + ask.Receipt + " 受审批流控制，不能通过接口做坏账收回");
            }
            string why = Refusal(conn, plan, ask, local);
            if (why != null)
            {
                throw ArapBad.State(why);
            }
            Line(conn, plan, ask);
            plan.Rate = string.Equals(plan.Currency, local, StringComparison.Ordinal) ? 1m
                : WriteoffSql.Num(CoRows.Col(plan.Head, "rate"));
            if (plan.Rate <= 0m)
            {
                throw ArapBad.State("收款单 " + ask.Receipt + " 的汇率无效（外币汇率应大于 0）");
            }
            return plan;
        }

        // 表头、U8 客户端占用、已有往来明细；不合用返回原因。
        static string Refusal(object conn, BadRecoverPlan plan, ArapBadAsk ask, string local)
        {
            string why = HeadRefusal(plan.Head, ask, plan.Currency, local, plan.Open.Date);
            if (why == null)
            {
                why = Occupied(conn, ask.Receipt, plan.Id);
            }
            if (why == null && Rows.Scalar(conn, DetailSql, new object[] { ask.Receipt, ask.Receipt }) != null)
            {
                why = "收款单 " + ask.Receipt + " 已有往来明细（做过审核、核销或其他处理），不能用于坏账收回";
            }
            return why;
        }

        static Dictionary<string, object> Head(object conn, string code)
        {
            string sql = HeadSql.Replace("{rate}", WriteoffSql.Dec("h.iExchRate", 10));
            List<Dictionary<string, object>> rows = Rows.Query(conn, sql, new object[] { code }, 2);
            if (rows.Count == 0)
            {
                throw new BridgeException(404, "not_found", "收款单 " + code + " 不存在");
            }
            if (rows.Count > 1)
            {
                throw ArapBad.State("收款单号 " + code + " 不唯一，请在 U8 客户端处理");
            }
            return rows[0];
        }

        static string Occupied(object conn, string code, int id)
        {
            string ws = TransferSql.LockedBy(conn, "48", code, id);
            if (ws == null)
            {
                return null;
            }
            return "收款单 " + code + " 正在 U8 客户端被占用" + (ws.Trim().Length > 0 ? "（" + ws.Trim() + "）" : string.Empty) + "，请关闭后再试";
        }

        // 表头不合用时的原因，合用返回 null（审批流另查）。纯函数，--selftest 用。
        internal static string HeadRefusal(Dictionary<string, object> head, ArapBadAsk ask, string currency, string local, string date)
        {
            string label = "收款单 " + ask.Receipt;
            if (CoRows.Col(head, "auditor").Length > 0)
            {
                return label + " 已审核；坏账收回要选未审核的收款单（U8 在收回时审核它）";
            }
            if (!string.Equals(CoRows.Col(head, "cDwCode"), ask.Customer, StringComparison.OrdinalIgnoreCase))
            {
                return label + " 的客户是 " + CoRows.Col(head, "cDwCode") + "，不是 " + ask.Customer;
            }
            string state = StateRefusal(head, label);
            if (state != null)
            {
                return state;
            }
            string cur = CoRows.Col(head, "cur");
            cur = cur.Length == 0 ? local : cur;
            if (!string.Equals(cur, currency, StringComparison.Ordinal))
            {
                return label + " 的币种是 " + cur + "，与坏账收回币种 " + currency + " 不一致";
            }
            string vdate = CoRows.Col(head, "vdate");
            return string.CompareOrdinal(vdate, date) > 0 ? "登记日期早于" + label + " 的日期 " + vdate : null;
        }

        // 来源（票据、网银、其他单据生成、期初）、已制单、已核销。
        static string StateRefusal(Dictionary<string, object> head, string label)
        {
            string src = CoRows.Col(head, "src_flag");
            bool elsewhere = (src.Length > 0 && src != "A") || CoRows.Col(head, "note_no").Length > 0
                || CoRows.FlagOf(head, "from_bank") || CoRows.FlagOf(head, "to_bank");
            if (elsewhere || CoRows.Col(head, "co_type").Length > 0 || CoRows.FlagOf(head, "start_flag"))
            {
                return label + " 不是手工录入的收款单（来自票据、网银、其他单据或期初），不能用于坏账收回";
            }
            if (CoRows.Col(head, "voucher").Length > 0)
            {
                return label + " 已生成凭证，不能用于坏账收回";
            }
            return CoRows.Col(head, "settler").Length > 0 ? label + " 已核销，不能用于坏账收回" : null;
        }

        static void Line(object conn, BadRecoverPlan plan, ArapBadAsk ask)
        {
            string sql = LineSql.Replace("{amt}", WriteoffSql.Dec("b.iAmt", 2)).Replace("{amtf}", WriteoffSql.Dec("b.iAmt_f", 2))
                .Replace("{rem}", WriteoffSql.Dec("b.iRAmt", 2)).Replace("{remf}", WriteoffSql.Dec("b.iRAmt_f", 2));
            List<Dictionary<string, object>> lines = Rows.Query(conn, sql, new object[] { plan.Id }, 21);
            string why = LinesRefusal(lines, ask);
            if (why != null)
            {
                throw ArapBad.State(why);
            }
            plan.Line = CoRows.AsId(CoRows.Col(lines[0], "line"));
            plan.AmountF = ask.Amount;
            plan.AmountN = WriteoffSql.Num(CoRows.Col(lines[0], "rem"));
        }

        // 表体不合用时的原因，合用返回 null。纯函数，--selftest 用。
        internal static string LinesRefusal(List<Dictionary<string, object>> lines, ArapBadAsk ask)
        {
            string label = "收款单 " + ask.Receipt;
            if (lines == null || lines.Count == 0)
            {
                return label + " 没有表体行";
            }
            if (lines.Count > 1)
            {
                return "坏账收回只支持单行收款单（" + label + " 有 " + lines.Count.ToString(CultureInfo.InvariantCulture) + " 行）";
            }
            Dictionary<string, object> line = lines[0];
            if (CoRows.Col(line, "itype") != "0")
            {
                return label + " 的款项类型不是应收款（预收款等不能用于坏账收回）";
            }
            decimal remF = WriteoffSql.Num(CoRows.Col(line, "rem_f"));
            if (remF != WriteoffSql.Num(CoRows.Col(line, "amt_f")) || CoRows.Col(line, "rem") != CoRows.Col(line, "amt"))
            {
                return label + " 已部分核销，不能用于坏账收回";
            }
            if (remF <= 0m)
            {
                return label + " 的金额不是正数，不能用于坏账收回";
            }
            return remF == ask.Amount ? null : "坏账收回金额须等于收款单金额 " + ArapBadRule.Money(remF);
        }
    }
}
