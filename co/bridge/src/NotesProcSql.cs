using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 票据处理的 SQL（与 U8 票据管理写入的行一致，已在测试账套逐列核对）。
    // 调用方的值只进参数；金额、号段、汇率以不变区域的文本传入、在 SQL 里转换。往来明细表按票据的 AR / AP 取。
    internal static class NotesProcSql
    {
        const string M = "convert(decimal(28,2), ?)";
        const string Day = "CAST(CONVERT(date, ?, 23) AS datetime)";

        const string HeadSql = "select convert(varchar(20), n.Auto_ID) as id, n.cLink as link, n.cVouchID as code, "
            + "n.cVouchType as vtype, isnull(n.cEndorser,N'') as endorser, isnull(n.cDwCode,N'') as dw, "
            + "isnull(n.cDeptCode,N'') as cdeptcode, isnull(n.cPerson,N'') as cperson, isnull(n.cCode,N'') as km, "
            + "{REM} as remain, {REML} as remain_local, isnull(n.cexch_name,N'') as cur, "
            + "convert(varchar(40), convert(decimal(28,10), isnull(n.nfrat,1))) as nfrat, "
            + "convert(varchar(10), n.dSignDate, 23) as sign_date, convert(varchar(10), n.dReceiptDate, 23) as receipt_date, "
            + "case when isnull(n.bStartFlag,0)<>0 then 1 else 0 end as opening, convert(varchar(20), isnull(n.iCloseID,0)) as close_id, "
            + "case when isnull(n.bsubpackage,0)<>0 then 1 else 0 end as sub, convert(varchar(20), isnull(n.iChangeType,0)) as change, "
            + "isnull(n.cSettleCode,N'') as settle "
            + "from AP_Note n with (UPDLOCK, HOLDLOCK) where n.cFlag=? and (n.cVouchID=? or n.Auto_ID=?)";

        const string AvailSql = "select convert(varchar(20), r.cavailstart) as s, convert(varchar(20), r.cavailend) as e "
            + "from Ap_Note_AvailRange r with (UPDLOCK, HOLDLOCK) where r.cNoteLink=? order by r.cavailstart";

        const string CodeSql = "select c.ccode, isnull(c.ccode_name,N'') as name, convert(varchar(10), isnull(c.igrade,0)) as grade, "
            + "convert(int, isnull(c.bend,0)) as bend, convert(int, isnull(c.bbank,0)) as bbank from code c where c.iyear=? and c.ccode=?";

        const string ParentSql = "select top 1 isnull(c.ccode_name,N'') from code c where c.iyear=? and c.igrade=? "
            + "and left(?, len(c.ccode))=c.ccode";

        // U8 的取号过程（自带 BEGIN TRAN / COMMIT，嵌在桥的事务里回滚时号一并撤销；号可能跳过，与 U8 一样无妨）。
        const string NumberSql = "SET NOCOUNT ON; DECLARE @n nvarchar(30); "
            + "EXEC Ap_Proc_CancelNo @cType=?, @cFlag=?, @cID=N'', @iNum=@n OUTPUT; SELECT @n AS no";

        const string SubSql = "insert into AP_Note_Sub (cLink, cProcStyle, dDate, iIntrest, iExpense, iAmount, cBank, iDisctIntrest, "
            + "cCode, cOperator, cCancelNo, cCoVouchType, cCoVouchID, cPzID, cFlag, iBsType, dHideDate, cTrustReceiver, iAmount_Local, "
            + "iIntrest_Local, iExpense_Local, iOverAmount, nfrat, csbnstart, csbnend, icoid) values (?, ?, " + Day + ", " + M + ", "
            + M + ", " + M + ", ?, convert(real, ?), ?, ?, ?, ?, ?, NULL, ?, NULL, NULL, ?, " + M + ", " + M + ", " + M
            + ", 0, convert(float, ?), convert(bigint, ?), convert(bigint, ?), NULL)";

        const string HeadUpdateSql = "update AP_Note set iRAmount=iRAmount-" + M + ", iRAmount_Local=iRAmount_Local-" + M
            + " where Auto_ID=? and cFlag=?";

        // 往来明细的票据处理行（50 行）：iFlag=3、iExchRate=0、部门业务员为空、未制单，日期是票据的签发日期，登记日期是处理日期。
        const string DetailSql = "insert into {D} (iPeriod, cVouchType, cVouchSType, cVouchID, dVouchDate, dRegDate, cDwCode, "
            + "cDeptCode, cPerson, cInvCode, iBVid, cCode, cItem_Class, cItemCode, csign, isignseq, ino_id, cDigest, iPrice, cexch_name, "
            + "iExchRate, iDAmount, iCAmount, iDAmount_f, iCAmount_f, iDAmount_s, iCAmount_s, cOrderNo, cSSCode, cPayCode, cProcStyle, "
            + "cCancelNo, cPZid, bPrePay, iFlag, cCoVouchType, cCoVouchID, cFlag, iClosesID, iCoClosesID, cOperator, cCheckMan) "
            + "values (?, ?, ?, ?, " + Day + ", " + Day + ", ?, NULL, NULL, NULL, 0, ?, NULL, NULL, NULL, NULL, NULL, ?, 0, ?, 0, "
            + M + ", " + M + ", " + M + ", " + M + ", 0, 0, NULL, NULL, NULL, ?, ?, NULL, 0, 3, ?, ?, ?, 0, 0, ?, ?)";

        public static NoteHead Head(object conn, string flag, string code, int id)
        {
            string sql = HeadSql.Replace("{REM}", WriteoffSql.Dec("n.iRAmount", 2)).Replace("{REML}", WriteoffSql.Dec("n.iRAmount_Local", 2));
            Dictionary<string, object> row = Rows.One(conn, sql, new object[] { flag, code ?? "", id });
            if (row == null)
            {
                return null;
            }
            NoteHead h = new NoteHead();
            h.Row = row;
            h.Id = CoRows.AsId(CoRows.Col(row, "id"));
            h.Link = CoRows.Col(row, "link");
            h.Code = CoRows.Col(row, "code");
            h.VouchType = CoRows.Col(row, "vtype");
            string endorser = CoRows.Col(row, "endorser").Trim();
            h.Partner = endorser.Length > 0 ? endorser : CoRows.Col(row, "dw").Trim();
            h.Dept = CoRows.Col(row, "cdeptcode");
            h.Person = CoRows.Col(row, "cperson");
            h.Km = CoRows.Col(row, "km").Trim();
            h.Remain = WriteoffSql.Num(CoRows.Col(row, "remain"));
            h.RemainLocal = WriteoffSql.Num(CoRows.Col(row, "remain_local"));
            h.Currency = CoRows.Col(row, "cur").Trim();
            h.Nfrat = CoRows.Col(row, "nfrat");
            h.SignDate = CoRows.Col(row, "sign_date");
            h.ReceiptDate = CoRows.Col(row, "receipt_date");
            h.Opening = CoRows.Col(row, "opening") == "1";
            h.CloseId = CoRows.AsId(CoRows.Col(row, "close_id"));
            h.Sub = CoRows.Col(row, "sub") == "1";
            h.ChangeType = CoRows.AsId(CoRows.Col(row, "change"));
            h.Settle = CoRows.Col(row, "settle").Trim();
            return h;
        }

        // 数据权限用的行：往来单位、部门、业务员。
        public static Dictionary<string, object> PermRow(NoteHead note)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["cDwCode"] = note.Partner;
            row["cDeptCode"] = note.Dept;
            row["cPerson"] = note.Person;
            return row;
        }

        public static List<NoteRange> Avail(object conn, string link)
        {
            List<NoteRange> list = new List<NoteRange>();
            foreach (Dictionary<string, object> row in Rows.Query(conn, AvailSql, new object[] { link }, 1001))
            {
                list.Add(new NoteRange(Long(CoRows.Col(row, "s")), Long(CoRows.Col(row, "e"))));
            }
            return list;
        }

        static long Long(string text)
        {
            long value;
            return long.TryParse(text == null ? "" : text.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out value) ? value : 0;
        }

        // 收付款单（登记时生成的 48 / 49）的单号和审核人；不存在返回 null。
        public static Dictionary<string, object> CloseBill(object conn, int id)
        {
            return Rows.One(conn, "select h.cVouchID as code, isnull(h.cCheckMan,N'') as auditor from Ap_CloseBill h where h.iID=?",
                new object[] { id });
        }

        // 已退回（9C）过的票据。
        public static bool Returned(object conn, string link)
        {
            return Rows.Scalar(conn, "select top 1 'x' from AP_Note_Sub where cLink=? and cProcStyle=N'9C'", new object[] { link }) != null;
        }

        public static Dictionary<string, object> Code(object conn, int year, string code)
        {
            return Rows.One(conn, CodeSql, new object[] { year, code });
        }

        // 科目上一级的名称（如 100201 取 1002 的名称）；没有返回空串。
        public static string ParentName(object conn, int year, string code, int grade)
        {
            string name = Rows.Scalar(conn, ParentSql, new object[] { year, grade - 1, code });
            return name == null ? "" : name.Trim();
        }

        // 应付票据：Ap_CancelNo 可能没有 PJJ / CL + AP 的行，第一次用时先补一行 0（同汇兑损益，未经实测
        // Ap_Proc_CancelNo 自己能否补）；应收票据的行都在，不动。
        const string SeedSql = "if not exists (select 1 from Ap_CancelNo with (UPDLOCK, HOLDLOCK) where cType=? and cFlag=?) "
            + "insert into Ap_CancelNo (cType, cFlag, iCancelNo) values (?, ?, 0)";

        public static string Number(object conn, string op, string flag)
        {
            string type = NotesProcRule.NoType(op);
            if (flag == "AP")
            {
                GlSql.Exec(conn, SeedSql, new object[] { type, flag, type, flag });
            }
            string no = Rows.Scalar(conn, NumberSql, new object[] { type, flag });
            return no == null ? null : no.Trim();
        }

        // 号已用在票据处理表或往来明细的同一处理方式上。
        public static bool Used(object conn, string style, string no)
        {
            string sql = "select top 1 'x' from AP_Note_Sub where cCancelNo=? union all select top 1 'x' from Ar_Detail "
                + "where cProcStyle=? and cCancelNo=? union all select top 1 'x' from Ap_Detail where cProcStyle=? and cCancelNo=?";
            return Rows.Scalar(conn, sql, new object[] { no, style, no, style, no }) != null;
        }

        public static void InsertSub(object conn, NotesProcPlan plan)
        {
            GlSql.Exec(conn, SubSql, SubArgs(plan));
        }

        // 各 op 的列值（按 U8 的 PJJ / PJT / PJB / CL 各类批次核对）：
        // 结算 cCode = 银行科目、cTrustReceiver = 银行名称；贴现 iAmount = 净额、iExpense / iIntrest / iDisctIntrest、
        // cBank = 银行名称、cCode = cCoVouchID = 银行科目；背书 cBank = 被背书供应商、cCode = 应付往来科目；
        // 退回 cCode = 往来控制科目、cBank 空。cCoVouchType：退回是生成的应收单 R0 / 应付单 P0（cCoVouchID = 单号），其余是标记 48
        // （应付票据的结算同样写 48，按应收票据推断）。
        internal static object[] SubArgs(NotesProcPlan plan)
        {
            bool discount = plan.Op == "discount";
            string amount = NotesProcRule.Money(discount ? plan.Net : plan.Amount);
            string interest = NotesProcRule.Money(discount ? plan.Interest : 0m);
            string expense = NotesProcRule.Money(discount ? plan.Expense : 0m);
            string bank = discount ? plan.BankName : (plan.Endorse ? plan.Vendor : null);
            return new object[]
            {
                plan.Note.Link, plan.Style, plan.Date, interest, expense, amount, bank,
                (discount ? plan.Rate : 0m).ToString(CultureInfo.InvariantCulture), SubCode(plan), plan.Operator, plan.CancelNo,
                plan.Return ? NotesProcReturnSql.BillType(plan.Flag) : "48", CoId(plan), plan.Flag,
                plan.Op == "settle" ? plan.BankName : null, amount, interest, expense,
                plan.Note.Nfrat, RangeArg(plan, true), RangeArg(plan, false)
            };
        }

        static string CoId(NotesProcPlan plan)
        {
            if (plan.Return)
            {
                return plan.BillCode;
            }
            return plan.Op == "discount" ? plan.BankCode : null;
        }

        static string SubCode(NotesProcPlan plan)
        {
            if (plan.Return)
            {
                return plan.CtrlKm;
            }
            return plan.Endorse ? plan.ApKm : plan.BankCode;
        }

        static string RangeArg(NotesProcPlan plan, bool start)
        {
            if (plan.Range == null)
            {
                return null;
            }
            return (start ? plan.Range.Start : plan.Range.End).ToString(CultureInfo.InvariantCulture);
        }

        public static void UpdateHead(object conn, NotesProcPlan plan)
        {
            string money = NotesProcRule.Money(plan.Amount);
            GlSql.Exec(conn, HeadUpdateSql, new object[] { money, money, plan.Note.Id, plan.Flag });
        }

        public static void InsertDetail(object conn, NotesProcPlan plan)
        {
            GlSql.Exec(conn, DetailSql.Replace("{D}", WriteoffSql.Detail(plan.Flag)), DetailArgs(plan));
        }

        // 应收票据的处理行记贷方，应付票据记借方（应付票据没有参考样本，按对称推断）。
        internal static object[] DetailArgs(NotesProcPlan plan)
        {
            string money = NotesProcRule.Money(plan.Amount);
            bool credit = plan.Flag != "AP";
            string debit = credit ? "0.00" : money;
            string creditMoney = credit ? money : "0.00";
            string id = plan.DetailId;
            return new object[]
            {
                plan.Period, plan.Note.VouchType, plan.Note.VouchType, id, plan.Note.SignDate, plan.Date, plan.Note.Partner,
                plan.Note.Km, plan.Digest, plan.Note.Currency, debit, creditMoney, debit, creditMoney, plan.Style, plan.CancelNo,
                plan.Note.VouchType, id, plan.Flag, plan.Operator, plan.Operator
            };
        }

        // 分包票据：删掉可用区间再按剩下的重写（同 U8，实测）。全部用完时不留行。
        public static void RewriteAvail(object conn, NotesProcPlan plan)
        {
            GlSql.Exec(conn, "delete from Ap_Note_AvailRange where cNoteLink=?", new object[] { plan.Note.Link });
            foreach (NoteRange r in plan.Left)
            {
                string money = NotesProcRule.Money(NotesProcRule.RangeAmount(r.Start, r.End));
                GlSql.Exec(conn, "insert into Ap_Note_AvailRange (cNoteLink, cavailstart, cavailend, iavailamount, iavailamount_local) "
                    + "values (?, convert(bigint, ?), convert(bigint, ?), " + M + ", " + M + ")", new object[]
                    {
                        plan.Note.Link, r.Start.ToString(CultureInfo.InvariantCulture), r.End.ToString(CultureInfo.InvariantCulture),
                        money, money
                    });
            }
        }

        // 背书的应付往来科目：取本次写的第一条应付处理行的科目（从该单据的审核行抄来，如 220201）。
        public static string ApKm(object conn, string no)
        {
            string km = Rows.Scalar(conn, "select top 1 isnull(cCode,N'') from Ap_Detail where cProcStyle=N'9E' and cCancelNo=? "
                + "and cFlag=N'AP' order by Auto_ID", new object[] { no });
            return km == null ? "" : km.Trim();
        }

        public static decimal Remain(object conn, int id)
        {
            return WriteoffSql.Num(Rows.Scalar(conn, "select " + WriteoffSql.Dec("n.iRAmount", 2) + " from AP_Note n where n.Auto_ID=?",
                new object[] { id }));
        }

        public static int SubRows(object conn, string style, string no)
        {
            return CoRows.AsId(Rows.Scalar(conn, "select convert(varchar(20), count(*)) from AP_Note_Sub where cCancelNo=? and cProcStyle=?",
                new object[] { no, style }));
        }

        // 可用区间的合计（金额）与行数。
        public static decimal[] AvailTotal(object conn, string link)
        {
            Dictionary<string, object> row = Rows.One(conn, "select " + WriteoffSql.Dec("sum(r.iavailamount)", 2) + " as v, "
                + "convert(varchar(20), count(*)) as n from Ap_Note_AvailRange r where r.cNoteLink=?", new object[] { link });
            return new decimal[] { WriteoffSql.Num(CoRows.Col(row, "v")), CoRows.AsId(CoRows.Col(row, "n")) };
        }
    }
}
