using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 自动核销的候选查询。表名、列名、单据类型都是这里的常量，调用方的值（往来单位、日期、id）只进参数。
    // 候选只做粗筛（已审核、币种汇率有效、不受审批流控制、未网络锁、日期不晚于 date_to、有未核销余额），
    // 每一批真正核销前还要过手工核销同一道闸门（ArapWriteoffGate.Plan，带锁重读）。
    // 币种随行带出（cur 为空按本位币），配对时只在同币种之间进行（ArapAutoWriteoffPlan.Allocate）；单据汇率不比，
    // 核销按收付款单汇率（同 U8）。
    internal static class AutoWriteoffSql
    {
        // 收款单（付款单）有余额的行：按单据日期、主键、行主键排序（先来先核）。
        // 预收 / 预付行（bPrePay=1）缺省不参加，请求 include_prepay 为 true 时参加（报文带 bprepay="1"）。
        const string ReceiptSql = "select top (?) convert(varchar(20), h.iID) as id, convert(varchar(20), b.ID) as line, "
            + "h.cVouchID as code, convert(varchar(10), h.dVouchDate, 23) as vdate, {rem} as rem, "
            + "isnull(h.cexch_name,N'') as cur "
            + "from Ap_CloseBill h inner join Ap_CloseBills b on b.iID=h.iID "
            + "where h.cFlag=? and h.cVouchType=? and h.cDwCode=? and b.iRAmt_f>0{prepay} "
            + "and ltrim(rtrim(isnull(h.cCheckMan,N'')))<>N'' and {fx} and isnull(h.IsWfControlled,0)=0 "
            + "and h.dVouchDate<=cast(convert(date, ?, 23) as datetime)";

        // 币种汇率有效：本位币（或空）汇率为 1，外币汇率大于 0。参数：本位币 × 2。
        static string Fx(string rate)
        {
            return "(((h.cexch_name=? or isnull(h.cexch_name,N'')=N'') and isnull(h." + rate + ",0)=1) "
                + "or (isnull(h.cexch_name,N'') not in (?,N'') and isnull(h." + rate + ",0)>0))";
        }

        public static List<Dictionary<string, object>> Receipts(object conn, AutoWriteoffAsk ask, string local, int top)
        {
            string sql = ReceiptSql.Replace("{rem}", WriteoffSql.Dec("b.iRAmt_f", 2))
                .Replace("{fx}", Fx("iExchRate"))
                .Replace("{prepay}", ask.IncludePrepay ? "" : " and isnull(b.bPrePay,0)=0");
            List<object> args = new List<object>();
            args.Add(top);
            args.Add(ask.Flag);
            args.Add(ask.Flag == "AP" ? "49" : "48");
            args.Add(ask.Partner);
            args.Add(local);
            args.Add(local);
            args.Add(ask.Cutoff);
            if (ask.ReceiptId > 0)
            {
                sql += " and h.iID=?";
                args.Add(ask.ReceiptId);
            }
            if (ask.ReceiptLine > 0)
            {
                sql += " and b.ID=?";
                args.Add(ask.ReceiptLine);
            }
            sql += " order by h.dVouchDate, h.iID, b.ID";
            return Rows.Query(conn, sql, args.ToArray(), top);
        }

        // 被核销单据有余额的行：与 U8 Save 同一口径的往来明细余额（cCoVouchType / cCoVouchID / iBVid，iFlag<3，往来单位），
        // 按单据日期、单号、行排序。targets 给了就只查这些单据。
        public static List<Dictionary<string, object>> Targets(object conn, AutoWriteoffAsk ask, string local, int top)
        {
            List<object> args = new List<object>();
            string types = ask.Flag == "AP" ? "N'01',N'02',N'P0'" : "N'26',N'27',N'R0'";
            string open = ask.Flag == "AP" ? "isnull(d.iCAmount_f,0)-isnull(d.iDAmount_f,0)"
                : "isnull(d.iDAmount_f,0)-isnull(d.iCAmount_f,0)";
            StringBuilder sql = new StringBuilder();
            sql.Append("with o as (select d.cCoVouchType as t, d.cCoVouchID as c, d.iBVid as l, sum(" + open + ") as v from ")
                .Append(WriteoffSql.Detail(ask.Flag))
                .Append(" d where d.cFlag=? and d.cDwCode=? and d.iFlag<3 and d.iBVid is not null and d.cCoVouchType in (")
                .Append(types).Append(") group by d.cCoVouchType, d.cCoVouchID, d.iBVid having sum(").Append(open).Append(")>0) ")
                .Append("select top (?) x.kind, x.id, x.code, x.vtype, convert(varchar(20), x.ln) as line, x.bal, ")
                .Append("convert(varchar(10), x.dd, 23) as vdate, x.cur from (");
            args.Add(ask.Flag);
            args.Add(ask.Partner);
            args.Add(top);
            string[] kinds = ask.Flag == "AP" ? new string[] { "purchase_invoice", "ap_bill" }
                : new string[] { "sale_invoice", "ar_bill" };
            int used = 0;
            foreach (string kind in kinds)
            {
                List<int> ids = IdsOf(ask, kind);
                if (ids != null && ids.Count == 0)
                {
                    continue;
                }
                sql.Append(used > 0 ? " union all " : "").Append(Branch(kind, ask.Flag, ids));
                args.Add(local);
                args.Add(local);
                args.Add(ask.Cutoff);
                foreach (int id in ids ?? new List<int>())
                {
                    args.Add(id);
                }
                used++;
            }
            sql.Append(") x order by x.dd, x.code, x.ln");
            return Rows.Query(conn, sql.ToString(), args.ToArray(), top);
        }

        // targets 没给时返回 null（不限）；给了返回该类型的 id（可能为空，这一类就不查）。
        static List<int> IdsOf(AutoWriteoffAsk ask, string kind)
        {
            if (ask.Targets == null || ask.Targets.Count == 0)
            {
                return null;
            }
            List<int> ids = new List<int>();
            foreach (AutoWriteoffTarget t in ask.Targets)
            {
                if (t.Kind == kind)
                {
                    ids.Add(t.Id);
                }
            }
            return ids;
        }

        // 一类单据：列 kind、id、code、vtype、ln（行，= iBVid）、bal（余额）、dd（单据日期）、cur（币种）。
        // 参数：本位币 × 2、date_to、[id…]。
        static string Branch(string kind, string flag, List<int> ids)
        {
            string[] c = Cols(kind, flag);
            string sql = "select N'" + kind + "' as kind, convert(varchar(20), h." + c[0] + ") as id, o.c as code, o.t as vtype, "
                + "o.l as ln, " + WriteoffSql.Dec("o.v", 2) + " as bal, h." + c[4] + " as dd, isnull(h.cexch_name,N'') as cur "
                + "from o inner join " + c[1]
                + " h on h." + c[2] + "=o.c and h." + c[3] + "=o.t" + c[7]
                + " where o.t in (" + c[8] + ") and ltrim(rtrim(isnull(h." + c[5] + ",N'')))<>N'' "
                + "and " + Fx(c[6]) + " "
                + "and h." + c[4] + "<=cast(convert(date, ?, 23) as datetime) and " + c[9];
            if (ids != null)
            {
                string[] marks = new string[ids.Count];
                for (int i = 0; i < marks.Length; i++)
                {
                    marks[i] = "?";
                }
                sql += " and h." + c[0] + " in (" + string.Join(",", marks) + ")";
            }
            return sql;
        }

        // 主键、表、单号列、类型列、日期列、审核人列、汇率列、另加的连接条件、类型、另加的筛选（审批流、网络锁、整单行 0）。
        static string[] Cols(string kind, string flag)
        {
            switch (kind)
            {
                case "sale_invoice":
                    return new string[] { "SBVID", "SaleBillVouch", "cSBVCode", "cVouchType", "dDate", "cVerifier", "iExchRate", "",
                        "N'26',N'27'", "isnull(h.iswfcontrolled,0)=0" };
                case "purchase_invoice":
                    return new string[] { "PBVID", "PurBillVouch", "cPBVCode", "cPBVBillType", "dPBVDate", "cPBVVerifier",
                        "cExchRate", "", "N'01',N'02'", "isnull(h.IsWfControlled,0)=0 and isnull(h.iNetLock,0)=0" };
            }
            // 应收单、应付单（Ap_Vouch）按整单核销：往来明细 iBVid 为 0。
            return new string[] { "Auto_ID", "Ap_Vouch", "cVouchID", "cVouchType", "dVouchDate", "cCheckMan", "iExchRate",
                " and h.cFlag=N'" + (flag == "AP" ? "AP" : "AR") + "'", flag == "AP" ? "N'P0'" : "N'R0'",
                "isnull(h.IsWfControlled,0)=0 and o.l=0" };
        }

        // 账套的核销规则选项：iHxRule（按订单 / 合同 / 存货等核销）不为 0，或 bAPAutoCancelWithHxRule 为真时，
        // U8 的自动核销要按规则配对，桥只实现按往来单位的规则，返回开启的选项名；都关着返回 null。
        public static string RuleOption(object conn, string flag)
        {
            string[] names = new string[] { "iHxRule", "bAPAutoCancelWithHxRule" };
            foreach (string name in names)
            {
                string sql = "select top 1 ltrim(rtrim(isnull(cValue,N''))) as v from AccInformation where cSysID=? and cName=?";
                string value = Rows.Scalar(conn, sql, new object[] { flag, name });
                if (On(value))
                {
                    return name;
                }
            }
            return null;
        }

        static bool On(string value)
        {
            string text = value == null ? "" : value.Trim();
            return text.Length > 0 && text != "0" && !string.Equals(text, "false", StringComparison.OrdinalIgnoreCase);
        }
    }
}
