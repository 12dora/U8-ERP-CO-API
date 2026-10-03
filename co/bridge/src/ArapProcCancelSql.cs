using System.Collections.Generic;

namespace U8Co
{
    // 取消应收冲应付 / 应付冲应收 / 并账的查询和写。往来明细表按账取（WriteoffSql.Detail），处理方式、处理号只进参数。
    // 写的 SQL 与 U8「取消操作」取消 9I / 9J / BZ 时执行的一致（实测核对）：
    //   9I / 9J：收付款单行 iRAmt* 加回（同 U8 取消核销的加回）、应收应付单 iRAmount* 加回 借+贷、发票累计核销
    //   （ArapUnwriteoffBill，同 9P 那段）、再按处理方式和处理号删两边（不带 cflag）；
    //   BZ：成对 ± 行各单据净额为 0，余额不动，删本侧（带 cflag 或代理进口，同 9P 的删除原句）；
    //   9N（红票对冲）：应收应付单 iRAmount* 加回 -(借+贷)（BillRedSql）、发票累计按处理行原样加回（ArapRedSql）、删本侧同 BZ。
    // 按处理号读都带 cProcStyle（索引 HXZD 是 cProcStyle, cCancelNo, cFlag 的前缀，能定位）。
    internal static class ProcCancelSql
    {
        const string BatchSql = "select convert(varchar(20), d.Auto_ID) as auto, d.cProcStyle as style, d.cFlag as flag, "
            + "d.cVouchType as vtype, d.cVouchID as vcode, d.cCoVouchType as cotype, d.cCoVouchID as cocode, "
            + "convert(varchar(20), isnull(d.iBVid,0)) as bvid, convert(varchar(20), isnull(d.iCoClosesID,0)) as coclose, "
            + "convert(varchar(10), d.iFlag) as iflag, convert(varchar(10), d.iPeriod) as period, "
            + "convert(varchar(10), d.dRegDate, 23) as regdate, d.cDwCode, d.cDeptCode, d.cPerson, d.cPZid as pz, "
            + "d.cContractID as contract, d.cBusType as bustype, {df} as df, {cf} as cf "
            + "from {D} d with (UPDLOCK, HOLDLOCK) where d.cProcStyle=? and d.cCancelNo=? order by d.Auto_ID";

        // 收付款单行（48 / 49，iCoClosesID 是 Ap_CloseBills.ID）余额加回 借+贷（原币、本币、数量）。
        // 与 U8 原子相同，只是不算 iFlag=6 的「形成预收 / 预付款」行：它与同一单据上的 iFlag=1 行成对、不进余额口径（iFlag<3）。
        const string CloseBillsSql = "update b set iRAmt_f=b.iRAmt_f+x.wb, iRAmt=b.iRAmt+x.je, iRAmt_s=b.iRAmt_s+x.sl "
            + "from Ap_CloseBills b inner join (select d.iCoClosesID as id, "
            + "sum(isnull(d.iDAmount_f,0)+isnull(d.iCAmount_f,0)) as wb, sum(isnull(d.iDAmount,0)+isnull(d.iCAmount,0)) as je, "
            + "sum(isnull(d.iDAmount_s,0)+isnull(d.iCAmount_s,0)) as sl from {D} d where d.cProcStyle=? and d.cCancelNo=? "
            + "and d.cCoVouchType like N'4%' and isnull(d.iCoClosesID,0)<>0 and isnull(d.iFlag,0)<>6 "
            + "group by d.iCoClosesID) x on x.id=b.ID";

        // 应收单 / 应付单（R0 / P0）iRAmount* 加回 借+贷；iRAmount_s 同取消核销（UnwriteoffSql.BillSql）。
        const string BillSql = "update v set iRAmount_f=v.iRAmount_f+x.f, iRAmount=v.iRAmount+x.n, "
            + "iRAmount_s=case when v.iAmount_f<>0 then v.iAmount_s*(v.iRAmount_f+x.f)/v.iAmount_f else v.iRAmount_f+x.f end "
            + "from Ap_Vouch v inner join (select d.cCoVouchType as t, d.cCoVouchID as c, "
            + "sum(isnull(d.iDAmount_f,0)+isnull(d.iCAmount_f,0)) as f, sum(isnull(d.iDAmount,0)+isnull(d.iCAmount,0)) as n "
            + "from {D} d where d.cProcStyle=? and d.cCancelNo=? and d.cCoVouchType in (N'R0',N'P0') "
            + "group by d.cCoVouchType, d.cCoVouchID) x on v.cVouchType=x.t and v.cVouchID=x.c and v.cFlag=?";

        // 红票对冲（9N）的应收单 / 应付单加回 -(借+贷)；其余同 BillSql。
        const string BillRedSql = "update v set iRAmount_f=v.iRAmount_f-x.f, iRAmount=v.iRAmount-x.n, "
            + "iRAmount_s=case when v.iAmount_f<>0 then v.iAmount_s*(v.iRAmount_f-x.f)/v.iAmount_f else v.iRAmount_f-x.f end "
            + "from Ap_Vouch v inner join (select d.cCoVouchType as t, d.cCoVouchID as c, "
            + "sum(isnull(d.iDAmount_f,0)+isnull(d.iCAmount_f,0)) as f, sum(isnull(d.iDAmount,0)+isnull(d.iCAmount,0)) as n "
            + "from {D} d where d.cProcStyle=? and d.cCancelNo=? and d.cCoVouchType in (N'R0',N'P0') "
            + "group by d.cCoVouchType, d.cCoVouchID) x on v.cVouchType=x.t and v.cVouchID=x.c and v.cFlag=?";

        // 9I / 9J 的删除（U8 原句，不带 cflag）；并账、红票对冲同 9P 的删除原句。
        const string DeleteSql = "DELETE FROM {D} WHERE cProcStyle=? AND cCancelNo=?";
        const string DeleteFlagSql = "DELETE FROM {D} WHERE cProcStyle=? AND cCancelNo=? AND (cflag=? OR cbustype=N'代理进口')";

        // 本批之后（Auto_ID 更大、处理号不同）这些单据上除审核（Sign）以外的处理；条件同取消核销（UnwriteoffSql.Blocker）。
        const string LaterSql = "select top 1 d.cProcStyle from {D} d where d.cFlag=? and d.Auto_ID>? "
            + "and isnull(d.cCancelNo,N'')<>? and " + UnwriteoffSql.Blocker + " and ({DOCS})";

        // 收付款单行：取消前余额、金额（带锁），行必须属于（类型、单号、账）那张收付款单。
        const string LineSql = "select {rem} as rem, {amt} as amt from Ap_CloseBills b with (UPDLOCK, HOLDLOCK) "
            + "inner join Ap_CloseBill h on h.iID=b.iID where b.ID=? and h.cVouchType=? and h.cVouchID=? and h.cFlag=?";

        internal static string Of(string sql, string ledger)
        {
            return sql.Replace("{D}", WriteoffSql.Detail(ledger));
        }

        public static List<ProcRow> Batch(object conn, string ledger, string style, string cancelNo, int max)
        {
            string sql = Of(BatchSql, ledger).Replace("{df}", WriteoffSql.Dec("d.iDAmount_f", 2))
                .Replace("{cf}", WriteoffSql.Dec("d.iCAmount_f", 2));
            List<ProcRow> rows = new List<ProcRow>();
            foreach (Dictionary<string, object> r in Rows.Query(conn, sql, new object[] { style, cancelNo }, max))
            {
                rows.Add(RowOf(ledger, r));
            }
            return rows;
        }

        internal static ProcRow RowOf(string ledger, Dictionary<string, object> r)
        {
            ProcRow row = new ProcRow();
            row.Ledger = ledger;
            row.Auto = CoRows.AsId(CoRows.Col(r, "auto"));
            row.Style = CoRows.Col(r, "style");
            row.Flag = CoRows.Col(r, "flag");
            row.VType = CoRows.Col(r, "vtype");
            row.VCode = CoRows.Col(r, "vcode");
            row.CoType = CoRows.Col(r, "cotype");
            row.CoCode = CoRows.Col(r, "cocode");
            row.BVid = CoRows.AsId(CoRows.Col(r, "bvid"));
            row.CoClose = CoRows.AsId(CoRows.Col(r, "coclose"));
            string iflag = CoRows.Col(r, "iflag");
            row.IFlag = iflag.Length == 0 ? -1 : CoRows.AsId(iflag);
            row.Period = CoRows.AsId(CoRows.Col(r, "period"));
            row.RegDate = CoRows.Col(r, "regdate");
            row.Pz = CoRows.Col(r, "pz");
            row.Contract = CoRows.Col(r, "contract");
            row.BusType = CoRows.Col(r, "bustype");
            row.DF = WriteoffSql.Num(CoRows.Col(r, "df"));
            row.CF = WriteoffSql.Num(CoRows.Col(r, "cf"));
            row.Head = r;
            return row;
        }

        public static int CountRows(object conn, string ledger, string style, string cancelNo)
        {
            string sql = "select convert(varchar(20), count(*)) from " + WriteoffSql.Detail(ledger)
                + " d where d.cProcStyle=? and d.cCancelNo=?";
            return CoRows.AsId(Rows.Scalar(conn, sql, new object[] { style, cancelNo }));
        }

        // 收付款单行的 [余额, 金额]（原币）；行不属于那张收付款单返回 null。
        public static decimal[] Line(object conn, ProcRemain line)
        {
            string sql = LineSql.Replace("{rem}", WriteoffSql.Dec("b.iRAmt_f", 2)).Replace("{amt}", WriteoffSql.Dec("b.iAmt_f", 2));
            Dictionary<string, object> row = Rows.One(conn, sql, new object[] { line.Line, line.VType, line.Code, line.Ledger });
            if (row == null)
            {
                return null;
            }
            return new decimal[] { WriteoffSql.Num(CoRows.Col(row, "rem")), WriteoffSql.Num(CoRows.Col(row, "amt")) };
        }

        // 本账上这批涉及的单据在本批之后有没有别的处理；有则返回其中一条的处理方式。
        public static string LaterStyle(object conn, ProcCancelPlan plan, string ledger)
        {
            List<string[]> docs = plan.DocKeys(ledger);
            for (int start = 0; start < docs.Count; start += UnwriteoffSql.DocChunk)
            {
                List<object> args = LaterArgs(plan, ledger);
                string cond = UnwriteoffSql.DocArgs(docs, start, args);
                string style = Rows.Scalar(conn, Of(LaterSql, ledger).Replace("{DOCS}", cond), args.ToArray());
                if (style != null)
                {
                    return style;
                }
            }
            return null;
        }

        internal static List<object> LaterArgs(ProcCancelPlan plan, string ledger)
        {
            List<object> args = new List<object>();
            args.Add(ledger);
            args.Add(plan.MaxAuto(ledger));
            args.Add(plan.CancelNo);
            return args;
        }

        // 供自检核对占位符个数。
        internal static string LaterText(string ledger)
        {
            return Of(LaterSql, ledger);
        }

        public static void CloseBills(object conn, ProcCancelPlan plan, string ledger)
        {
            GlSql.Exec(conn, Of(CloseBillsSql, ledger), new object[] { plan.Kind.Style, plan.CancelNo });
        }

        public static void Bills(object conn, ProcCancelPlan plan, string ledger)
        {
            GlSql.Exec(conn, Of(plan.Kind.Negate ? BillRedSql : BillSql, ledger), new object[] { plan.Kind.Style, plan.CancelNo, ledger });
        }

        public static void Delete(object conn, ProcCancelPlan plan, string ledger)
        {
            if (plan.Kind.FlagDelete)
            {
                GlSql.Exec(conn, Of(DeleteFlagSql, ledger), new object[] { plan.Kind.Style, plan.CancelNo, ledger });
                return;
            }
            GlSql.Exec(conn, Of(DeleteSql, ledger), new object[] { plan.Kind.Style, plan.CancelNo });
        }

        // 表头取数（带锁）：收付款单按（类型、单号、账）找 Ap_CloseBill，其余按 WriteoffKind 的 CodeSql。找不到返回 null。
        public static Dictionary<string, object> DocHead(object conn, ProcDoc doc)
        {
            if (doc.IsReceipt)
            {
                int id = UnwriteoffSql.ReceiptId(conn, doc.VType, doc.Code, doc.Ledger);
                return id > 0 ? WriteoffSql.Receipt(conn, id) : null;
            }
            WriteoffKind kind = WriteoffKind.OfType(doc.Ledger, doc.VType);
            return Rows.One(conn, kind.CodeSql, new object[] { doc.Code, doc.VType });
        }

        // 占位符个数（自检核对 SQL 与参数个数一致）。
        internal static int Marks(string sql)
        {
            int n = 0;
            foreach (char c in sql)
            {
                if (c == '?')
                {
                    n++;
                }
            }
            return n;
        }

        internal static string[] WriteTexts(string ledger)
        {
            return new string[]
            {
                Of(CloseBillsSql, ledger), Of(BillSql, ledger), Of(DeleteSql, ledger), Of(DeleteFlagSql, ledger), Of(BillRedSql, ledger)
            };
        }

        internal static int[] WriteArgCounts()
        {
            return new int[] { 2, 3, 2, 3, 3 };
        }
    }
}
