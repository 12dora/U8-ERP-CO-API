using System.Collections.Generic;

namespace U8Co
{
    // 票据退回（9C）的 SQL（与 U8 退回写入的行逐列核对）。新应收单（R0）本身经 UFAPBO clsAPVouch.SaveVouch
    // 生成（NotesProcReturnBo，主键、单号由 U8 分配；Ap_Vouch.Auto_ID 不是自增列，桥不能自己 INSERT），保存后在同一事务里补上 U8 退回
    // 写而 SaveVouch 不写的列：cCoVouchType = 50、cSrcNo = 票据号、iRAmount / iRAmount_f = 0，以及审核列（审核人 = 操作员、审核日期 = 处理日期、
    // 审核时间 = 当前时间；退回生成的应收单直接是已审核状态，但没有审核产生的往来明细行）、创建时间（空时）、模板号和条码（空时）。
    // 往来明细的应收单行：借方、iFlag 0、iExchRate 1、csign Z、科目 = 应收控制科目，带部门、业务员，审核人 = 操作员。
    // 应付票据（按应收对称推断、未经实测）：生成应付单 P0（cFlag AP、卡片 AP04），往来明细写 Ap_Detail、应付单行记贷方；
    // 条码按应收的规则推断为「||app0|单号」。票据行（50，应收贷方 / 应付借方）与其他处理同一条语句（NotesProcSql.InsertDetail）。调用方的值只进参数。
    internal static class NotesProcReturnSql
    {
        const string M = "convert(decimal(28,2), ?)";
        const string Day = "CAST(CONVERT(date, ?, 23) AS datetime)";

        // 保存后补写的列（只改本次保存的那一张、还没有标记过的）。
        internal static readonly string StampSql = "update Ap_Vouch set cCoVouchType=N'50', cSrcNo=?, iRAmount=0, iRAmount_f=0, cCheckMan=?, "
            + "dverifydate=" + Day + ", dverifysystime=getdate(), dcreatesystime=isnull(dcreatesystime, getdate()), "
            + "VT_ID=case when isnull(VT_ID,0)=0 then ? else VT_ID end, "
            + "csysbarcode=case when isnull(csysbarcode,N'')=N'' then ? else csysbarcode end "
            + "where Auto_ID=? and cFlag=? and cVouchType=? and isnull(cCoVouchType,N'')=N''";

        const string StampedSql = "select convert(varchar(10), count(*)) from Ap_Vouch where Auto_ID=? and cFlag=? and cVouchType=? "
            + "and cCoVouchType=N'50' and cSrcNo=? and isnull(iRAmount,0)=0 and isnull(iRAmount_f,0)=0 and cCheckMan=?";

        internal static readonly string DetailSql = "insert into {D} (iPeriod, cVouchType, cVouchSType, cVouchID, dVouchDate, "
            + "dRegDate, cDwCode, cDeptCode, cPerson, cInvCode, iBVid, cCode, cItem_Class, cItemCode, csign, isignseq, ino_id, cDigest, "
            + "iPrice, cexch_name, iExchRate, iDAmount, iCAmount, iDAmount_f, iCAmount_f, iDAmount_s, iCAmount_s, cProcStyle, cCancelNo, "
            + "cPZid, bPrePay, iFlag, cCoVouchType, cCoVouchID, cFlag, iClosesID, iCoClosesID, cOperator, cCheckMan) values (?, ?, "
            + "NULL, ?, " + Day + ", " + Day + ", ?, ?, ?, NULL, 0, ?, NULL, NULL, N'Z', NULL, NULL, ?, 0, ?, 1, " + M + ", " + M + ", "
            + M + ", " + M + ", 0, 0, N'9C', ?, NULL, 0, 0, ?, ?, ?, 0, 0, ?, ?)";

        // 登记生成的收款单（48）应收款行（iType 0；应付票据是付款单 49 的应付款行）的科目，即往来控制科目（如应收 112201）。
        const string ReceiptKmSql = "select top 1 isnull(b.cKm,N'') from Ap_CloseBills b where b.iID=? and isnull(b.iType,0)=0 "
            + "and isnull(b.cKm,N'')<>N'' order by b.ID";

        // 应收单的单据模板：卡片 AR04（应付单 AP04）的缺省模板（本账套 vouchers.DEF_ID），要在模板表里存在。
        // SaveVouch 没写 VT_ID 时补它。
        const string VtSql = "select top 1 convert(varchar(20), v.DEF_ID) from vouchers v where v.CardNumber=? "
            + "and exists (select 1 from vouchertemplates_base t where t.VT_ID=v.DEF_ID)";

        // 票据在退回日期之后的处理（不含保留线索的 *L 和 H* 行，同 NotesUndoSql.LaterSql）：U8 的 Return_Ticket_* 要求退回日期不早于它们。
        const string LaterSql = "select top 1 convert(varchar(10), s.dDate, 23) from AP_Note_Sub s where s.cLink=? and s.dDate>" + Day
            + " and s.cProcStyle not like N'%L' and s.cProcStyle not like N'H%' order by s.dDate desc";

        // 补写要用到的列在本账套的 Ap_Vouch 上都要有（U8 标准表结构都有）；缺了说明表结构不同，写之前拒绝。
        internal const string StampCols = "ccovouchtype,csrcno,iramount,iramount_f,ccheckman,dverifydate,dverifysystime,dcreatesystime,"
            + "vt_id,csysbarcode";

        const string ColsSql = "select convert(varchar(10), count(*)) from sys.columns c where c.object_id=object_id(N'Ap_Vouch') "
            + "and charindex(N',' + lower(c.name) + N',', ?)>0";

        const string MaxIdSql = "select convert(varchar(20), isnull(max(Auto_ID),0)) from Ap_Vouch";

        const string CodeSql = "select cVouchID as code from Ap_Vouch where Auto_ID=? and cFlag=? and cVouchType=?";

        // SaveVouch 没把主键、单号写回 DOM 时的兜底：保存前最大主键之后、同往来单位同摘要、还没标记的唯一一张。
        const string FreshSql = "select convert(varchar(20), max(Auto_ID)) as id, convert(varchar(10), count(*)) as n from Ap_Vouch "
            + "where Auto_ID>? and cFlag=? and cVouchType=? and cDwCode=? and cDigest=? and isnull(cCoVouchType,N'')=N''";

        const string BillRowsSql = "select convert(varchar(20), (select count(*) from Ap_Vouch where cLink=? and cVouchType=? "
            + "and cFlag=?) + (select count(*) from Ap_Vouchs where cLink=?))";

        // 退回生成的单据类型：应收票据 R0（应收单），应付票据 P0（应付单）。
        public static string BillType(string flag)
        {
            return flag == "AP" ? "P0" : "R0";
        }

        // 单据卡片号：应收单 AR04，应付单 AP04。
        public static string Card(string flag)
        {
            return flag == "AP" ? "AP04" : "AR04";
        }

        public static string ReceiptKm(object conn, int closeId)
        {
            if (closeId <= 0)
            {
                return "";
            }
            string km = Rows.Scalar(conn, ReceiptKmSql, new object[] { closeId });
            return km == null ? "" : km.Trim();
        }

        public static int VtId(object conn, string flag)
        {
            return CoRows.AsId(Rows.Scalar(conn, VtSql, new object[] { Card(flag) }));
        }

        // 退回日期之后最近的处理日期；没有返回 null。
        public static string Later(object conn, string link, string date)
        {
            return Rows.Scalar(conn, LaterSql, new object[] { link, date });
        }

        // 补写要用的列是否都在。
        public static bool StampReady(object conn)
        {
            string want = "," + StampCols + ",";
            int n = CoRows.AsId(Rows.Scalar(conn, ColsSql, new object[] { want }));
            return n == StampCols.Split(',').Length;
        }

        public static int MaxId(object conn)
        {
            return CoRows.AsId(Rows.Scalar(conn, MaxIdSql, new object[0]));
        }

        // 主键对应的单号（同类型、同 cFlag）；没有返回空串。
        public static string CodeOf(object conn, string flag, int id)
        {
            Dictionary<string, object> row = Rows.One(conn, CodeSql, new object[] { id, flag, BillType(flag) });
            return row == null ? "" : CoRows.Col(row, "code").Trim();
        }

        // 兜底找新单的主键；不是恰好一张返回 0。
        public static int Fresh(object conn, NotesProcPlan plan, int before)
        {
            Dictionary<string, object> row = Rows.One(conn, FreshSql, new object[]
            {
                before, plan.Flag, BillType(plan.Flag), plan.Note.Partner, HeadDigest(plan.Note.Code)
            });
            if (row == null || CoRows.Col(row, "n") != "1")
            {
                return 0;
            }
            return CoRows.AsId(CoRows.Col(row, "id"));
        }

        // 补写处理标记和审核列，再读回确认恰好一张对上。
        public static bool Stamp(object conn, NotesProcPlan plan)
        {
            GlSql.Exec(conn, StampSql, StampArgs(plan));
            object[] args = new object[] { plan.BillId, plan.Flag, BillType(plan.Flag), plan.Note.Code, plan.Operator };
            return Rows.Scalar(conn, StampedSql, args) == "1";
        }

        public static void InsertDetail(object conn, NotesProcPlan plan)
        {
            GlSql.Exec(conn, DetailSql.Replace("{D}", WriteoffSql.Detail(plan.Flag)), DetailArgs(plan));
        }

        // 应收（应付）单表头、表体的行数（应为 2）。
        public static int BillRows(object conn, NotesProcPlan plan)
        {
            string link = Link(plan);
            return CoRows.AsId(Rows.Scalar(conn, BillRowsSql, new object[] { link, BillType(plan.Flag), plan.Flag, link }));
        }

        internal static string Link(NotesProcPlan plan)
        {
            return BillType(plan.Flag) + plan.BillCode;
        }

        // 单据条码：「||」+ 小写的 cFlag 与单据类型 +「|」+ 单号（同票据「||ar50|号」、收款单「||ar48|号」；应收单「||arr0|号」）。
        internal static string BarCode(string flag, string code)
        {
            return "||" + (flag + BillType(flag)).ToLowerInvariant() + "|" + code;
        }

        internal static object[] StampArgs(NotesProcPlan plan)
        {
            return new object[]
            {
                plan.Note.Code, plan.Operator, plan.Date, plan.VtId, BarCode(plan.Flag, plan.BillCode), plan.BillId, plan.Flag,
                BillType(plan.Flag)
            };
        }

        // 往来明细的单据行：应收单记借方（iDAmount），应付单记贷方（iCAmount）。
        internal static object[] DetailArgs(NotesProcPlan plan)
        {
            string money = NotesProcRule.Money(plan.Amount);
            bool ap = plan.Flag == "AP";
            string debit = ap ? "0.00" : money;
            string credit = ap ? money : "0.00";
            string type = BillType(plan.Flag);
            NoteHead note = plan.Note;
            return new object[]
            {
                plan.Period, type, plan.BillCode, plan.Date, plan.Date, note.Partner, Opt(note.Dept), Opt(note.Person), plan.CtrlKm,
                plan.Digest, note.Currency, debit, credit, debit, credit, plan.CancelNo, type, plan.BillCode, plan.Flag, plan.Operator,
                plan.Operator
            };
        }

        // 表头摘要：「转出票据」+ 票据号（同 U8，不带子票区间），最多 120 字。
        internal static string HeadDigest(string noteCode)
        {
            string text = "转出票据" + noteCode;
            return text.Length > ArapVoucherReq.DigestMax ? text.Substring(0, ArapVoucherReq.DigestMax) : text;
        }

        // 空串写 NULL（U8 没有部门、业务员时这两列为空）。
        internal static object Opt(string value)
        {
            return string.IsNullOrEmpty(value) || value.Trim().Length == 0 ? null : (object)value.Trim();
        }

        // 占位符个数（自检核对）：补写、往来明细。
        internal static string[] Texts()
        {
            return new string[] { StampSql, DetailSql };
        }
    }
}
