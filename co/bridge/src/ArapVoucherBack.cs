using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 制单后的回写（与 U8 制单后的回写一致，实测核对）和取消制单的清除，都由调用方包在 CoTrans 里。
    // 表名、列名都是常量；调用方的值只进参数；日期参数一律 cast(convert(date, ?, 23) as datetime)。
    internal static class ArapVoucherBack
    {
        const string Day = "cast(convert(date, ?, 23) as datetime)";
        const string Own = " where cFlag=? and cVouchType=? and cVouchID=? and cProcStyle=cVouchType";

        // 按 U8 制单的样子补齐凭证的来源列（导入器写不写这些列、写成什么，以这里为准）：coutsysname、coutsign、coutno_id、
        // coutbillsign、coutid、coutaccset、ioutyear、ioutperiod、doutbilldate；bvouchAddordele=1；受控科目行 bvalueedit=0、其余 1。
        public static void PatchGl(object conn, VoucherPlan plan, string acc)
        {
            GlKey key = plan.Key;
            DateTime day = DateTime.ParseExact(plan.Date, "yyyy-MM-dd", CultureInfo.InvariantCulture);
            string sql = "update GL_accvouch set coutsysname=?, coutsign=?, coutno_id=?, coutbillsign=?, coutid=?, coutaccset=?, "
                + "ioutyear=?, ioutperiod=?, doutbilldate=" + Day + ", bvouchAddordele=1, bvalueedit=1" + GlState.KeyWhere
                + " and isnull(ibook,0)=0";
            object[] head = new object[]
            {
                plan.Doc.Flag, plan.OutSign, plan.PzId, plan.Doc.VType, plan.Doc.Code, acc ?? "", day.Year, day.Month, plan.Date
            };
            GlSql.Exec(conn, sql, GlSql.With(head, GlSql.KeyArgs(key)));
            Bills(conn, plan);
            foreach (int entry in plan.Controlled)
            {
                GlSql.Exec(conn, "update GL_accvouch set bvalueedit=0" + GlState.KeyWhere + " and inid=?",
                    GlSql.With(GlSql.KeyArgs(key), new object[] { entry }));
            }
            // 导入器不填 GL_CashTable.csign（同 GlSave）。
            GlSql.Exec(conn, "update GL_CashTable set csign=? where iyear=? and iPeriod=? and iSignSeq=? and iNo_id=? and csign is null",
                new object[] { key.Sign, key.Year, key.Period, plan.Seq, key.No });
        }

        // 合并制单：上面一条语句按第一张单据写了全部行，其余单据的分录行改成各自的 coutbillsign / coutid（同 U8 合并制单，
        // 多张收款单合并成一张凭证时，每行 coutid 是该行来源收款单的单号）。
        static void Bills(object conn, VoucherPlan plan)
        {
            for (int bill = 1; bill < plan.Docs.Count; bill++)
            {
                List<object> entries = new List<object>();
                for (int i = 0; i < plan.Bills.Count; i++)
                {
                    if (plan.Bills[i] == bill)
                    {
                        entries.Add(i + 1);
                    }
                }
                if (entries.Count == 0)
                {
                    continue;
                }
                VoucherDoc doc = plan.Docs[bill];
                string sql = "update GL_accvouch set coutbillsign=?, coutid=?" + GlState.KeyWhere + " and isnull(ibook,0)=0 and inid in (?"
                    + new StringBuilder().Insert(0, ",?", entries.Count - 1).ToString() + ")";
                object[] args = GlSql.With(GlSql.With(new object[] { doc.VType, doc.Code }, GlSql.KeyArgs(plan.Key)), entries.ToArray());
                GlSql.Exec(conn, sql, args);
            }
        }

        // 回写全部单据（合并制单逐张，同一个外部业务号和凭证号）。
        public static void Mark(object conn, VoucherPlan plan)
        {
            for (int bill = 0; bill < plan.Docs.Count; bill++)
            {
                MarkDoc(conn, plan, bill);
            }
        }

        static void MarkDoc(object conn, VoucherPlan plan, int bill)
        {
            VoucherDoc doc = plan.Docs[bill];
            object[] own = new object[] { doc.Flag, doc.VType, doc.Code };
            string num = plan.PzNum();
            GlSql.Exec(conn, "update " + doc.Detail + " set cPZid=?, dPZDate=" + Day + ", cGLSign=?, iGLno_id=?" + Own
                + " and isnull(cPZid,N'')=N''", GlSql.With(new object[] { plan.PzId, plan.Date, plan.Key.Sign, plan.Key.No }, own));
            Entries(conn, plan, bill);
            object[] marks = new object[] { plan.PzId, num, plan.Date };
            switch (doc.Ask.Kind)
            {
                case "sale_invoice":
                    Invoice(conn, marks, new string[] { "SaleBillVouchs", "SBVID", "cIncomeSub", "AutoID" }, doc);
                    break;
                case "purchase_invoice":
                    Invoice(conn, marks, new string[] { "PurBillVouchs", "PBVID", "cDebitHead", "ID" }, doc);
                    break;
                case "ar_receipt":
                case "ap_payment":
                case "ar_refund":
                case "ap_refund":
                    GlSql.Exec(conn, "update Ap_CloseBill set cPzID=?, cPZNum=?, doutbilldate=" + Day + " where iID=? and isnull(cPzID,N'')=N''",
                        GlSql.With(marks, new object[] { doc.Id }));
                    break;
                default:
                    GlSql.Exec(conn, "update Ap_Vouch set cPZid=?, cPZNum=?, doutbilldate=" + Day + " where Auto_ID=? and isnull(cPZid,N'')=N''",
                        GlSql.With(marks, new object[] { doc.Id }));
                    break;
            }
        }

        // 明细 ino_id 按行回写（U8 逐行写 ino_id = 该行所在分录号）：同一分录号的行一条语句，按 Auto_ID 每批 200 个。
        static void Entries(object conn, VoucherPlan plan, int bill)
        {
            VoucherDoc doc = plan.Docs[bill];
            Dictionary<int, List<object>> groups = new Dictionary<int, List<object>>();
            foreach (Dictionary<string, object> row in doc.Rows)
            {
                int entry = plan.EntryOf(bill, row);
                if (entry <= 0)
                {
                    continue;
                }
                List<object> ids;
                if (!groups.TryGetValue(entry, out ids))
                {
                    ids = new List<object>();
                    groups[entry] = ids;
                }
                ids.Add(CoRows.Col(row, "aid"));
            }
            foreach (KeyValuePair<int, List<object>> pair in groups)
            {
                for (int start = 0; start < pair.Value.Count; start += 200)
                {
                    List<object> chunk = pair.Value.GetRange(start, Math.Min(200, pair.Value.Count - start));
                    string sql = "update " + doc.Detail + " set ino_id=? where cPZid=? and Auto_ID in (?"
                        + new StringBuilder().Insert(0, ",?", chunk.Count - 1).ToString() + ")";
                    GlSql.Exec(conn, sql, GlSql.With(new object[] { pair.Key, plan.PzId }, chunk.ToArray()));
                }
            }
        }

        // 发票表体：线索号 cClue = 外部业务号、cPZNum、dSignDate；销售发票 cIncomeSub、采购发票 cDebitHead 同步成往来明细的往来科目
        // （按 iBVid 对到表体行，同 U8）。
        // names：表体表、表头外键、科目列、表体主键（常量）。
        static void Invoice(object conn, object[] marks, string[] names, VoucherDoc doc)
        {
            string body = names[0];
            string fk = names[1];
            string subject = names[2];
            string line = names[3];
            GlSql.Exec(conn, "update " + body + " set cClue=?, cPZNum=?, dSignDate=" + Day + " where " + fk + "=?",
                GlSql.With(marks, new object[] { doc.Id }));
            GlSql.Exec(conn, "update a set a." + subject + "=d.cCode from " + body + " a inner join " + doc.Detail + " d on d.iBVid=a." + line
                + " and d.cFlag=? and d.cVouchType=? and d.cVouchID=? and d.cProcStyle=d.cVouchType and d.cPZid=? where a." + fk + "=?",
                new object[] { doc.Flag, doc.VType, doc.Code, marks[0], doc.Id });
        }

        // 提交前核对：原始往来明细全部带上本凭证号，发票表体线索号、收付款单 / 应收应付单表头凭证号都已写上。不符返回原因。
        // 合并制单逐张核对，原因前加单号。
        public static string Mismatch(object conn, VoucherPlan plan)
        {
            foreach (VoucherDoc doc in plan.Docs)
            {
                string problem = DocMismatch(conn, plan, doc);
                if (problem != null)
                {
                    return plan.Docs.Count > 1 ? "单据 " + doc.Code + "：" + problem : problem;
                }
            }
            return plan.Docs.Count > 1 ? LineSources(conn, plan) : null;
        }

        // 合并制单：每张单据的分录行数与凭证上 coutbillsign / coutid 是它的行数一致。
        static string LineSources(object conn, VoucherPlan plan)
        {
            for (int bill = 0; bill < plan.Docs.Count; bill++)
            {
                int want = 0;
                foreach (int one in plan.Bills)
                {
                    want += one == bill ? 1 : 0;
                }
                VoucherDoc doc = plan.Docs[bill];
                int got = Count(conn, "select convert(varchar(12), count(*)) from GL_accvouch" + GlState.KeyWhere
                    + " and coutbillsign=? and coutid=?", GlSql.With(GlSql.KeyArgs(plan.Key), new object[] { doc.VType, doc.Code }));
                if (got != want)
                {
                    return "凭证上来源单据是 " + doc.Code + " 的分录 " + got.ToString(CultureInfo.InvariantCulture) + " 行，应为 "
                        + want.ToString(CultureInfo.InvariantCulture) + " 行";
                }
            }
            return null;
        }

        static string DocMismatch(object conn, VoucherPlan plan, VoucherDoc doc)
        {
            int marked = Count(conn, "select convert(varchar(12), count(*)) from " + doc.Detail + Own + " and cPZid=?",
                new object[] { doc.Flag, doc.VType, doc.Code, plan.PzId });
            if (marked != doc.Rows.Count)
            {
                return "往来明细回写了 " + marked.ToString(CultureInfo.InvariantCulture) + " 行，应为 "
                    + doc.Rows.Count.ToString(CultureInfo.InvariantCulture) + " 行（单据可能已在 U8 客户端制单）";
            }
            string sql;
            switch (doc.Ask.Kind)
            {
                case "sale_invoice":
                    sql = "select convert(varchar(12), count(*)) from SaleBillVouchs where SBVID=? and isnull(cClue,N'')<>?";
                    break;
                case "purchase_invoice":
                    sql = "select convert(varchar(12), count(*)) from PurBillVouchs where PBVID=? and isnull(cClue,N'')<>?";
                    break;
                case "ar_receipt":
                case "ap_payment":
                case "ar_refund":
                case "ap_refund":
                    sql = "select convert(varchar(12), count(*)) from Ap_CloseBill where iID=? and isnull(cPzID,N'')<>?";
                    break;
                default:
                    sql = "select convert(varchar(12), count(*)) from Ap_Vouch where Auto_ID=? and isnull(cPZid,N'')<>?";
                    break;
            }
            return Count(conn, sql, new object[] { doc.Id, plan.PzId }) == 0 ? null : "单据的凭证号没有写上";
        }

        // 提交前核对外部业务号没有撞号（U8 客户端取号是先读后 update … where iCancelNo<n，读的时候不等桥的锁）：
        // 总账里只有本凭证的行用这个号，两张往来明细表里用这个号的正好是本次全部单据的原始行。不符返回原因（调用方回滚、删本凭证、409）。
        public static string Clash(object conn, VoucherPlan plan)
        {
            object[] args = GlSql.With(new object[] { plan.PzId }, GlSql.KeyArgs(plan.Key));
            int foreign = Count(conn, "select convert(varchar(12), count(*)) from GL_accvouch where coutno_id=? "
                + "and not (iyear=? and iperiod=? and csign=? and ino_id=?)", args);
            if (foreign != 0)
            {
                return "外部业务号 " + plan.PzId + " 已被别的凭证占用（U8 客户端可能同时在制单）";
            }
            int rows = Count(conn, "select convert(varchar(12), (select count(*) from Ar_Detail where cPZid=?) "
                + "+ (select count(*) from Ap_Detail where cPZid=?))", new object[] { plan.PzId, plan.PzId });
            if (rows != plan.RowCount())
            {
                return "外部业务号 " + plan.PzId + " 已被别的单据的往来明细引用";
            }
            return null;
        }

        // 取消制单：与 U8 删除凭证后对单据的清除一致（实测核对）。9P 核销制单的删行不做（取消制单拒绝核销凭证）。
        public static void Clear(object conn, string flag, string pzId)
        {
            object[] args = new object[] { pzId };
            GlSql.Exec(conn, "update SaleBillVouchs set cClue=null, cPZNum=null, dSignDate=null where cClue=?", args);
            GlSql.Exec(conn, "update PurBillVouchs set cClue=null, cPZNum=null, dSignDate=null where cClue=?", args);
            GlSql.Exec(conn, "update Ar_BadPara set cPZid=null where cPZid=?", args);
            GlSql.Exec(conn, "update AR_RZDetail set cPZid=null, cGLSign=null, iGLno_id=null where cPZid=?", args);
            GlSql.Exec(conn, "update Ap_Vouch set cPZid=null, cPZNum=null, doutbilldate=null where cPZid=?", args);
            GlSql.Exec(conn, "update Ap_CloseBill set cPzID=null, cPZNum=null, doutbilldate=null, cPreCode=null where cPzID=?", args);
            GlSql.Exec(conn, "update Ap_Note_Sub set cPzID=null where cPzID=?", args);
            GlSql.Exec(conn, "update CM_Balance set cPZID=null, cPZNum=null, doutbilldate=null where cPZID=?", args);
            GlSql.Exec(conn, "update " + WriteoffSql.Detail(flag) + " set cPZid=null, cGLSign=null, iGLno_id=null where cPZid=?", args);
        }

        // 删凭证：同总账删除（GlOps）的三张表，只删未记账的。
        public static void DeleteGl(object conn, GlKey key, int seq)
        {
            GlSql.Exec(conn, "delete from GL_CashTable where iyear=? and iPeriod=? and iSignSeq=? and iNo_id=?",
                new object[] { key.Year, key.Period, seq, key.No });
            GlSql.Exec(conn, "delete from GL_CodeRemark where iyear=? and iPeriod=? and csign=? and iNo_id=?", GlSql.KeyArgs(key));
            GlSql.Exec(conn, "delete from GL_accvouch" + GlState.KeyWhere + " and isnull(ibook,0)=0", GlSql.KeyArgs(key));
        }

        // 清除后核对：凭证行、应收和应付两张往来明细、发票线索号、表头凭证号都不再引用该外部业务号。
        public static int Left(object conn, string pzId)
        {
            string sql = "select convert(varchar(12), (select count(*) from GL_accvouch where coutno_id=?) "
                + "+ (select count(*) from Ar_Detail where cPZid=?) + (select count(*) from Ap_Detail where cPZid=?) "
                + "+ (select count(*) from SaleBillVouchs where cClue=?) + (select count(*) from PurBillVouchs where cClue=?) "
                + "+ (select count(*) from Ap_Vouch where cPZid=?) + (select count(*) from Ap_CloseBill where cPzID=?))";
            return Count(conn, sql, new object[] { pzId, pzId, pzId, pzId, pzId, pzId, pzId });
        }

        static int Count(object conn, string sql, object[] args)
        {
            return CoRows.AsId(Rows.Scalar(conn, sql, args));
        }
    }
}
