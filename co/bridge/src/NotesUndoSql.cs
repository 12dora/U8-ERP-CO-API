using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 一条票据处理行（AP_Note_Sub，带锁读出）。分包票据的 Start / End 是本次处理占用的子票区间（csbnstart / csbnend）。
    internal sealed class NoteUndoSub
    {
        public int Id;
        public string Link = "";
        public string Flag = "";
        public string Pz = "";
        public string CoType = "";
        public string CoId = "";
        public bool Ranged;
        public long Start;
        public long End;
    }

    // 票据表头（AP_Note，带锁读出）：票面、余额（原币 / 本币）、是否分包及子票区间。
    internal sealed class NoteUndoHead
    {
        public int Id;
        public string Link = "";
        public string Code = "";
        public string Flag = "";
        public decimal Face;
        public decimal Remain;
        public decimal RemainLocal;
        public bool Split;
        public long Start;
        public long End;
    }

    // 分包票据的一段可用子票区间（Ap_Note_AvailRange）。
    internal sealed class NoteAvailRange
    {
        public long Start;
        public long End;
        public decimal Amount;
        public decimal Local;
    }

    // 退回（9C）生成的应收单 / 应付单（Ap_Vouch，带锁读出）。
    internal sealed class NoteUndoBill
    {
        public int Id;
        public string Link = "";
        public string VType = "";
        public string Code = "";
        public string Pz = "";
        public string CoType = "";
    }

    // 取消票据处理的查询和写。写与 U8 票据管理撤销时执行的 SQL 一致（实测核对）：按处理方式和处理号删往来明细、删 AP_Note_Sub、
    // 票据 iRAmount* 加回、lAcctID 清空、分包票据删掉再重插可用区间；9C 另删它生成的应收单（表体按 cLink、表头按主键）。
    // 表名、列名是常量，处理方式、处理号和金额只进参数。
    internal static class NotesUndoSql
    {
        const string SubSql = "select convert(varchar(20), s.ID) as id, s.cLink as link, isnull(s.cFlag,N'') as flag, "
            + "isnull(s.cPzID,N'') as pz, isnull(s.cCoVouchType,N'') as cotype, isnull(s.cCoVouchID,N'') as coid, "
            + "convert(varchar(20), s.csbnstart) as sb, convert(varchar(20), s.csbnend) as se "
            + "from AP_Note_Sub s with (UPDLOCK, HOLDLOCK) where s.cProcStyle=? and s.cCancelNo=? order by s.ID";

        const string HeadSql = "select convert(varchar(20), n.Auto_ID) as id, n.cLink as link, n.cVouchID as code, "
            + "isnull(n.cFlag,N'') as flag, {face} as face, {rem} as rem, {reml} as reml, "
            + "convert(varchar(4), isnull(n.bsubpackage,0)) as split, convert(varchar(20), n.csubnostart) as sb, "
            + "convert(varchar(20), n.csubnoend) as se from AP_Note n with (UPDLOCK, HOLDLOCK) where n.cLink=?";

        // 本批票据行（cVouchType 50）的原币、本币合计和行数。
        const string AmountSql = "select {f} as f, {n} as n, convert(varchar(10), count(*)) as c from {D} d "
            + "where d.cProcStyle=? and d.cCancelNo=? and d.cVouchType=N'50'";

        // 同一张票据在本批之后的处理（U8：不含保留线索的 *L 和 H* 行）。icoid 为空时分包分支等同按 ID 比较。
        const string LaterSql = "select top 1 s.cProcStyle from AP_Note_Sub s where s.cLink=? and s.ID>? "
            + "and s.cProcStyle not like N'%L' and s.cProcStyle not like N'H%'";

        const string BillSql = "select convert(varchar(20), v.Auto_ID) as id, v.cLink as link, isnull(v.cPZid,N'') as pz, "
            + "isnull(v.cCoVouchType,N'') as cot from Ap_Vouch v with (UPDLOCK, HOLDLOCK) where v.cVouchType=? and v.cVouchID=? and v.cFlag=?";

        // 退回生成的单据除本批以外还被往来明细引用（核销、转账等）。
        const string BillUsedSql = "select top 1 d.cProcStyle from {D} d where ((d.cVouchType=? and d.cVouchID=?) "
            + "or (d.cCoVouchType=? and d.cCoVouchID=?)) and not (d.cProcStyle=? and d.cCancelNo=?)";

        // 背书在对方账上形成的预付款（U8 背书冲应付超出应付单据时生成付款单，往来明细 cVouchType 49 的 iFlag 1 / 6 行）。
        const string PrepaySql = "select top 1 d.cVouchID from {D} d where d.cProcStyle=? and d.cCancelNo=? and d.cVouchType=N'49'";

        const string RangesSql = "select convert(varchar(20), r.cavailstart) as s, convert(varchar(20), r.cavailend) as e, "
            + "{a} as a, {l} as l from Ap_Note_AvailRange r with (UPDLOCK, HOLDLOCK) where r.cNoteLink=? order by r.cavailstart";

        const string Money = "convert(decimal(28,2), ?)";

        public static List<NoteUndoSub> Subs(object conn, string style, string cancelNo)
        {
            List<NoteUndoSub> list = new List<NoteUndoSub>();
            foreach (Dictionary<string, object> r in Rows.Query(conn, SubSql, new object[] { style, cancelNo }, 3))
            {
                NoteUndoSub sub = new NoteUndoSub();
                sub.Id = CoRows.AsId(CoRows.Col(r, "id"));
                sub.Link = CoRows.Col(r, "link");
                sub.Flag = CoRows.Col(r, "flag");
                sub.Pz = CoRows.Col(r, "pz");
                sub.CoType = CoRows.Col(r, "cotype");
                sub.CoId = CoRows.Col(r, "coid");
                string sb = CoRows.Col(r, "sb");
                string se = CoRows.Col(r, "se");
                sub.Ranged = sb.Length > 0 && se.Length > 0;
                sub.Start = Long(sb);
                sub.End = Long(se);
                list.Add(sub);
            }
            return list;
        }

        public static NoteUndoHead Head(object conn, string link)
        {
            string sql = HeadSql.Replace("{face}", WriteoffSql.Dec("n.iAmount", 2)).Replace("{rem}", WriteoffSql.Dec("n.iRAmount", 2))
                .Replace("{reml}", WriteoffSql.Dec("n.iRAmount_Local", 2));
            Dictionary<string, object> r = Rows.One(conn, sql, new object[] { link });
            if (r == null)
            {
                return null;
            }
            NoteUndoHead head = new NoteUndoHead();
            head.Id = CoRows.AsId(CoRows.Col(r, "id"));
            head.Link = CoRows.Col(r, "link");
            head.Code = CoRows.Col(r, "code");
            head.Flag = CoRows.Col(r, "flag");
            head.Face = WriteoffSql.Num(CoRows.Col(r, "face"));
            head.Remain = WriteoffSql.Num(CoRows.Col(r, "rem"));
            head.RemainLocal = WriteoffSql.Num(CoRows.Col(r, "reml"));
            head.Split = CoRows.Col(r, "split") == "1";
            head.Start = Long(CoRows.Col(r, "sb"));
            head.End = Long(CoRows.Col(r, "se"));
            return head;
        }

        // [原币, 本币, 行数]。
        public static decimal[] Amount(object conn, string ledger, string style, string cancelNo)
        {
            string sql = AmountSql.Replace("{D}", WriteoffSql.Detail(ledger))
                .Replace("{f}", WriteoffSql.Dec("sum(isnull(d.iDAmount_f,0)+isnull(d.iCAmount_f,0))", 2))
                .Replace("{n}", WriteoffSql.Dec("sum(isnull(d.iDAmount,0)+isnull(d.iCAmount,0))", 2));
            Dictionary<string, object> r = Rows.One(conn, sql, new object[] { style, cancelNo });
            return new decimal[]
            {
                WriteoffSql.Num(CoRows.Col(r, "f")), WriteoffSql.Num(CoRows.Col(r, "n")), CoRows.AsId(CoRows.Col(r, "c"))
            };
        }

        public static string Later(object conn, string link, int subId)
        {
            return Rows.Scalar(conn, LaterSql, new object[] { link, subId });
        }

        // 账套选项「取消操作保留线索」（AccInformation bAR2Cancel / bAP2Cancel）是否打开。
        public static bool KeepClue(object conn, string flag)
        {
            string value = Rows.Scalar(conn, "select top 1 cValue from AccInformation where cSysID=? and cName=?",
                new object[] { flag, "b" + flag + "2Cancel" });
            string text = value == null ? "" : value.Trim();
            return text == "1" || text.Equals("true", StringComparison.OrdinalIgnoreCase);
        }

        public static NoteUndoBill Bill(object conn, string flag, string type, string code)
        {
            Dictionary<string, object> r = Rows.One(conn, BillSql, new object[] { type, code, flag });
            if (r == null)
            {
                return null;
            }
            NoteUndoBill bill = new NoteUndoBill();
            bill.Id = CoRows.AsId(CoRows.Col(r, "id"));
            bill.Link = CoRows.Col(r, "link");
            bill.VType = type;
            bill.Code = code;
            bill.Pz = CoRows.Col(r, "pz");
            bill.CoType = CoRows.Col(r, "cot");
            return bill;
        }

        public static string BillUsed(object conn, string ledger, NoteUndoBill bill, string style, string cancelNo)
        {
            string sql = BillUsedSql.Replace("{D}", WriteoffSql.Detail(ledger));
            return Rows.Scalar(conn, sql, new object[] { bill.VType, bill.Code, bill.VType, bill.Code, style, cancelNo });
        }

        public static string Prepay(object conn, string ledger, string style, string cancelNo)
        {
            return Rows.Scalar(conn, PrepaySql.Replace("{D}", WriteoffSql.Detail(ledger)), new object[] { style, cancelNo });
        }

        public static List<NoteAvailRange> Ranges(object conn, string link)
        {
            string sql = RangesSql.Replace("{a}", WriteoffSql.Dec("r.iavailamount", 2)).Replace("{l}", WriteoffSql.Dec("r.iavailamount_local", 2));
            List<NoteAvailRange> list = new List<NoteAvailRange>();
            foreach (Dictionary<string, object> r in Rows.Query(conn, sql, new object[] { link }, 501))
            {
                NoteAvailRange one = new NoteAvailRange();
                one.Start = Long(CoRows.Col(r, "s"));
                one.End = Long(CoRows.Col(r, "e"));
                one.Amount = WriteoffSql.Num(CoRows.Col(r, "a"));
                one.Local = WriteoffSql.Num(CoRows.Col(r, "l"));
                list.Add(one);
            }
            return list;
        }

        public static void DeleteDetail(object conn, string ledger, string style, string cancelNo)
        {
            GlSql.Exec(conn, "DELETE FROM " + WriteoffSql.Detail(ledger) + " WHERE cProcStyle=? AND cCancelNo=?", new object[] { style, cancelNo });
        }

        public static void DeleteSub(object conn, string style, string cancelNo)
        {
            GlSql.Exec(conn, "DELETE FROM AP_Note_Sub WHERE cProcStyle=? AND cCancelNo=?", new object[] { style, cancelNo });
        }

        public static void DeleteBill(object conn, NoteUndoBill bill, string flag)
        {
            GlSql.Exec(conn, "DELETE FROM Ap_Vouchs WHERE cLink=?", new object[] { bill.Link });
            GlSql.Exec(conn, "DELETE FROM Ap_Vouch WHERE Auto_ID=? AND cVouchType=? AND cVouchID=? AND cFlag=?",
                new object[] { bill.Id, bill.VType, bill.Code, flag });
        }

        public static void RestoreHead(object conn, string link, decimal back, decimal backLocal)
        {
            GlSql.Exec(conn, "UPDATE AP_Note SET iRAmount=isnull(iRAmount,0)+" + Money + ", iRAmount_Local=isnull(iRAmount_Local,0)+" + Money
                + ", lAcctID=NULL WHERE cLink=?", new object[] { Text(back), Text(backLocal), link });
        }

        // 可用区间整组重写（U8 同样先删后插）。
        public static void WriteRanges(object conn, string link, List<NoteAvailRange> ranges)
        {
            GlSql.Exec(conn, "DELETE FROM Ap_Note_AvailRange WHERE cNoteLink=?", new object[] { link });
            foreach (NoteAvailRange r in ranges)
            {
                GlSql.Exec(conn, "INSERT INTO Ap_Note_AvailRange(cNoteLink,cavailstart,cavailend,iavailamount,iavailamount_local) "
                    + "VALUES (?, convert(bigint, ?), convert(bigint, ?), " + Money + ", " + Money + ")",
                    new object[] { link, Int(r.Start), Int(r.End), Text(r.Amount), Text(r.Local) });
            }
        }

        public static int CountSubs(object conn, string style, string cancelNo)
        {
            return CoRows.AsId(Rows.Scalar(conn, "select convert(varchar(20), count(*)) from AP_Note_Sub where cProcStyle=? and cCancelNo=?",
                new object[] { style, cancelNo }));
        }

        public static int CountBill(object conn, NoteUndoBill bill)
        {
            return CoRows.AsId(Rows.Scalar(conn, "select convert(varchar(20), (select count(*) from Ap_Vouch where Auto_ID=?) "
                + "+ (select count(*) from Ap_Vouchs where cLink=?))", new object[] { bill.Id, bill.Link }));
        }

        static long Long(string text)
        {
            long value;
            return long.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out value) ? value : 0;
        }

        static string Int(long value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }

        static string Text(decimal value)
        {
            return value.ToString("0.00", CultureInfo.InvariantCulture);
        }

        // 占位符个数（自检核对）。
        internal static string[] Texts()
        {
            return new string[] { SubSql, HeadSql, AmountSql, LaterSql, BillSql, BillUsedSql, RangesSql, PrepaySql };
        }

        internal static int[] ArgCounts()
        {
            return new int[] { 2, 1, 2, 2, 3, 6, 1, 2 };
        }
    }
}
