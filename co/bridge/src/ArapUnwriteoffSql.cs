using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 取消核销的查询和写。往来明细表按 AR / AP 取（WriteoffSql.Detail）；调用方的值只进参数。
    // 写的 SQL 与 U8 应收（应付）「取消操作」取消 9P 时执行的语句和符号一致（实测核对），
    // 只把逐行拼值换成参数、把逐行执行换成按核销号的一条集合语句（结果相同）。
    // 按核销号读都带 cProcStyle=N'9P' 和 cFlag（索引 HXZD 是 cProcStyle, cCancelNo, cFlag，只有这样才能定位；
    // 只带 cCancelNo 是整索引扫描，事务里的 UPDLOCK, HOLDLOCK 会把整张往来明细的插入都挡住）。
    // HX 开头的核销号只出现在 9P 行上（U8 其他处理用别的前缀），所以不再读同号下的其他处理方式。
    internal static class UnwriteoffSql
    {
        const string BatchSql = "select convert(varchar(20), d.Auto_ID) as auto, d.cProcStyle as style, d.cFlag as flag, "
            + "d.cVouchType as vtype, d.cVouchID as vcode, d.cCoVouchType as cotype, d.cCoVouchID as cocode, "
            + "convert(varchar(20), isnull(d.iBVid,0)) as bvid, convert(varchar(20), isnull(d.iCoClosesID,0)) as coclose, "
            + "convert(varchar(10), d.iPeriod) as period, convert(varchar(10), d.dRegDate, 23) as regdate, d.cDwCode as dw, "
            + "d.cPZid as pz, d.cContractID as contract, d.cexch_name as cur, {rate} as rate, {df} as df, {cf} as cf "
            + "from {D} d with (UPDLOCK, HOLDLOCK) where d.cProcStyle=N'9P' and d.cCancelNo=? and d.cFlag=? order by d.Auto_ID";

        // 取消操作的 9P 分支：收付款单行余额按自身冲减行加回（-(借+贷)，原币、本币、数量）。
        const string CloseBillsSql = "update b set iRAmt_f=b.iRAmt_f+x.wb, iRAmt=b.iRAmt+x.je, iRAmt_s=b.iRAmt_s+x.sl "
            + "from Ap_CloseBills b inner join (select d.iCoClosesID as id, "
            + "sum(0-(isnull(d.iDAmount_f,0)+isnull(d.iCAmount_f,0))) as wb, sum(0-(isnull(d.iDAmount,0)+isnull(d.iCAmount,0))) as je, "
            + "sum(0-(isnull(d.iDAmount_s,0)+isnull(d.iCAmount_s,0))) as sl from {D} d where d.cProcStyle=N'9P' and d.cCancelNo=? "
            + "and d.cFlag=? and d.cCoVouchType like N'4%' and isnull(d.iCoClosesID,0)<>0 group by d.iCoClosesID) x on x.id=b.ID";

        // C2：应收单 / 应付单（R0 / P0）的 iRAmount* 加回 借+贷；iRAmount_s 按 U8 汇兑损益那段的比例公式，用加回后的原币余额。
        const string BillSql = "update v set iRAmount_f=v.iRAmount_f+x.f, iRAmount=v.iRAmount+x.n, "
            + "iRAmount_s=case when v.iAmount_f<>0 then v.iAmount_s*(v.iRAmount_f+x.f)/v.iAmount_f else v.iRAmount_f+x.f end "
            + "from Ap_Vouch v inner join (select d.cCoVouchType as t, d.cCoVouchID as c, "
            + "sum(isnull(d.iDAmount_f,0)+isnull(d.iCAmount_f,0)) as f, sum(isnull(d.iDAmount,0)+isnull(d.iCAmount,0)) as n "
            + "from {D} d where d.cProcStyle=N'9P' and d.cCancelNo=? and d.cFlag=? and d.cCoVouchType in (N'R0',N'P0') "
            + "group by d.cCoVouchType, d.cCoVouchID) x on v.cVouchType=x.t and v.cVouchID=x.c and v.cFlag=?";

        // F：收付款单各行都回到未核销（余额 = 金额）时清核销人、预收标志（U8 原句）。参数 (类型, 单号, AR|AP) × 2。
        const string CancelManSql = "IF NOT EXISTS (SELECT id FROM AP_CloseBill INNER JOIN AP_CloseBills "
            + "ON AP_CloseBill.iid=AP_CloseBills.iid WHERE cVouchType=? AND cVouchID=? AND cflag=? GROUP BY id "
            + "HAVING SUM(iRAmt_f-iAmt_f)<>0) UPDATE AP_CloseBill SET cCancelMan=NULL, bPrepay=0 "
            + "WHERE cVouchType=? AND cVouchID=? AND cflag=?";

        // B：删除原核销行（U8 原句，带代理进口的条件）。
        const string DeleteSql = "DELETE FROM {D} WHERE cProcStyle=N'9P' AND cCancelNo=? AND (cflag=? OR cbustype=N'代理进口')";

        // 「之后的处理」：处理方式不是单据本身的审核（Sign）行的都算——核销 9P、红票对冲 9N、汇兑损益、坏账、并账、
        // 票据等（9M / 9E / 9I / 9J / 9A / 9C / 9D / BZ …）。Sign 行的处理方式就是单据类型（26 / 27 / R0 / 48 / 49 / 01 / 02 / P0、
        // 期初 50 等，= cVouchType）。U8 自己的判断同样是除 Sign 外的任何后续行都拒绝。
        internal const string Blocker = "isnull(d.cProcStyle,N'') not in (N'26',N'27',N'R0',N'48',N'49',N'01',N'02',N'P0') "
            + "and isnull(d.cProcStyle,N'')<>isnull(d.cVouchType,N'')";

        // 本批之后（Auto_ID 更大、核销号不同）这些单据上的其他处理；{DOCS} 是每张单据一组（对方单据或本单据）条件。
        const string LaterSql = "select top 1 d.cProcStyle from {D} d where d.cFlag=? and d.Auto_ID>? "
            + "and isnull(d.cCancelNo,N'')<>? and " + Blocker + " and ({DOCS})";

        internal const string DocCond = "(d.cCoVouchType=? and d.cCoVouchID=?) or (d.cVouchType=? and d.cVouchID=?)";

        // 一条语句最多带的单据数（每张 4 个参数，远低于 SQL Server 的 2100 个参数上限）。
        internal const int DocChunk = 200;

        // 存储过程 AR_ExistUnAuditCloseBill 的正文（排除销售发票现结）：有一行就不让取消。
        const string UnauditedSql = "SELECT TOP 1 convert(varchar(1), 1) FROM Ap_CloseBill INNER JOIN Ap_CloseBills "
            + "ON Ap_CloseBills.iID = Ap_CloseBill.iID WHERE Ap_CloseBill.cFlag='AR' AND ISNULL(cCheckMan,N'')=N'' "
            + "AND iType<2 AND ISNULL(cCancelNo,'')=''";

        static string Of(string sql, string flag)
        {
            return sql.Replace("{D}", WriteoffSql.Detail(flag));
        }

        public static List<UnwriteoffRow> Batch(object conn, string flag, string cancelNo, int max)
        {
            string sql = Of(BatchSql, flag).Replace("{rate}", WriteoffSql.Dec("d.iExchRate", 10))
                .Replace("{df}", WriteoffSql.Dec("d.iDAmount_f", 2)).Replace("{cf}", WriteoffSql.Dec("d.iCAmount_f", 2));
            List<UnwriteoffRow> rows = new List<UnwriteoffRow>();
            foreach (Dictionary<string, object> r in Rows.Query(conn, sql, new object[] { cancelNo, flag }, max))
            {
                rows.Add(RowOf(r));
            }
            return rows;
        }

        internal static UnwriteoffRow RowOf(Dictionary<string, object> r)
        {
            UnwriteoffRow row = new UnwriteoffRow();
            row.Auto = CoRows.AsId(CoRows.Col(r, "auto"));
            row.Style = CoRows.Col(r, "style");
            row.Flag = CoRows.Col(r, "flag");
            row.VType = CoRows.Col(r, "vtype");
            row.VCode = CoRows.Col(r, "vcode");
            row.CoType = CoRows.Col(r, "cotype");
            row.CoCode = CoRows.Col(r, "cocode");
            row.BVid = CoRows.AsId(CoRows.Col(r, "bvid"));
            row.CoClose = CoRows.AsId(CoRows.Col(r, "coclose"));
            row.Period = CoRows.AsId(CoRows.Col(r, "period"));
            row.RegDate = CoRows.Col(r, "regdate");
            row.Dw = CoRows.Col(r, "dw");
            row.Pz = CoRows.Col(r, "pz");
            row.Contract = CoRows.Col(r, "contract");
            row.Cur = CoRows.Col(r, "cur");
            row.Rate = WriteoffSql.Num(CoRows.Col(r, "rate"));
            row.DF = WriteoffSql.Num(CoRows.Col(r, "df"));
            row.CF = WriteoffSql.Num(CoRows.Col(r, "cf"));
            return row;
        }

        public static int ReceiptId(object conn, string type, string code, string flag)
        {
            string sql = "select convert(varchar(20), h.iID) from Ap_CloseBill h where h.cVouchID=? and h.cVouchType=? and h.cFlag=?";
            return CoRows.AsId(Rows.Scalar(conn, sql, new object[] { code, type, flag }));
        }

        // 应收单 / 应付单表头的未核销余额（原币）。
        public static decimal BillRemain(object conn, string flag, string type, string code)
        {
            string sql = "select " + WriteoffSql.Dec("v.iRAmount_f", 2) + " from Ap_Vouch v with (UPDLOCK, HOLDLOCK) "
                + "where v.cVouchType=? and v.cVouchID=? and v.cFlag=?";
            return WriteoffSql.Num(Rows.Scalar(conn, sql, new object[] { type, code, flag }));
        }

        // 本批涉及的单据：收付款单和各被核销单据（类型、单号），去重。
        internal static List<string[]> Docs(UnwriteoffPlan plan)
        {
            List<string[]> docs = new List<string[]>();
            HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
            seen.Add(plan.ReceiptType + "|" + plan.ReceiptCode);
            docs.Add(new string[] { plan.ReceiptType, plan.ReceiptCode });
            foreach (UnwriteoffTarget t in plan.Targets)
            {
                if (seen.Add(t.VType + "|" + t.Code))
                {
                    docs.Add(new string[] { t.VType, t.Code });
                }
            }
            return docs;
        }

        // 本批的单据上有没有之后的处理（每 DocChunk 张单据一条语句）；有则返回其中一条的处理方式。
        public static string LaterStyle(object conn, UnwriteoffPlan plan)
        {
            List<string[]> docs = Docs(plan);
            for (int start = 0; start < docs.Count; start += DocChunk)
            {
                List<object> args = new List<object>();
                args.Add(plan.Flag);
                args.Add(plan.MaxAuto);
                args.Add(plan.CancelNo);
                string cond = DocArgs(docs, start, args);
                string style = Rows.Scalar(conn, Of(LaterSql, plan.Flag).Replace("{DOCS}", cond), args.ToArray());
                if (style != null)
                {
                    return style;
                }
            }
            return null;
        }

        // docs[start ..] 最多 DocChunk 张的 DocCond 条件（用 or 连起来），参数追加到 args。
        internal static string DocArgs(List<string[]> docs, int start, List<object> args)
        {
            StringBuilder cond = new StringBuilder();
            for (int i = start; i < docs.Count && i < start + DocChunk; i++)
            {
                cond.Append(cond.Length > 0 ? " or " : string.Empty).Append(DocCond);
                args.Add(docs[i][0]);
                args.Add(docs[i][1]);
                args.Add(docs[i][0]);
                args.Add(docs[i][1]);
            }
            return cond.ToString();
        }

        public static bool UnauditedCloseBill(object conn)
        {
            return Rows.Scalar(conn, UnauditedSql, new object[0]) != null;
        }

        public static int CountRows(object conn, string flag, string cancelNo)
        {
            string sql = "select convert(varchar(20), count(*)) from " + WriteoffSql.Detail(flag)
                + " d where d.cProcStyle=N'9P' and d.cCancelNo=? and d.cFlag=?";
            return CoRows.AsId(Rows.Scalar(conn, sql, new object[] { cancelNo, flag }));
        }

        public static void CloseBills(object conn, UnwriteoffPlan plan)
        {
            GlSql.Exec(conn, Of(CloseBillsSql, plan.Flag), new object[] { plan.CancelNo, plan.Flag });
        }

        public static void Bills(object conn, UnwriteoffPlan plan)
        {
            GlSql.Exec(conn, Of(BillSql, plan.Flag), new object[] { plan.CancelNo, plan.Flag, plan.Flag });
        }

        public static void CancelMan(object conn, UnwriteoffPlan plan)
        {
            object[] args = new object[] { plan.ReceiptType, plan.ReceiptCode, plan.Flag, plan.ReceiptType, plan.ReceiptCode, plan.Flag };
            GlSql.Exec(conn, CancelManSql, args);
        }

        public static void Delete(object conn, UnwriteoffPlan plan)
        {
            GlSql.Exec(conn, Of(DeleteSql, plan.Flag), new object[] { plan.CancelNo, plan.Flag });
        }

        // 不带参数的整段 SQL 直接走 Connection.Execute（不经 sp_executesql），建的 #临时表留在本连接上，
        // 之后带参数的语句和 U8 组件（同一连接）都看得见。
        public static void Run(object conn, string sql)
        {
            object rs = null;
            try
            {
                rs = ComUtil.Call(conn, "Execute", new object[] { sql });
            }
            finally
            {
                ComUtil.Final(rs);
            }
        }
    }
}
