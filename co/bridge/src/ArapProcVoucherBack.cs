using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 处理制单后的回写和取消制单的清除，都由调用方包在 CoTrans 里。表名、列名是常量；调用方的值只进参数。
    // 回写照 U8：批次全部明细行（两张表、含不出分录的 iFlag=6 行）写 cPZid、dPZDate、cGLSign、iGLno_id，出分录的行另写 ino_id。
    internal static class ArapProcVoucherBack
    {
        const string Day = "cast(convert(date, ?, 23) as datetime)";
        internal const string ProcStyles = "(N'9I',N'9J',N'BZ',N'9M',N'9N',N'9A',N'9D',N'9E',N'9C',N'9G',N'9H')";
        static readonly string[] Tables = new string[] { "Ar_Detail", "Ap_Detail" };

        // 补齐凭证来源列（同 ArapVoucherBack.PatchGl）：coutsysname = flag、coutsign = ZZ / BZ / SY（9N 为 flag）、coutno_id、每行自己的
        // coutbillsign / coutid、coutaccset、ioutyear、ioutperiod、doutbilldate、bvouchAddordele=1、受控科目行 bvalueedit=0；
        // 9M 的外币往来科目行写币种、汇率和原币为 0（同 U8）。
        public static void PatchGl(object conn, ProcPlan plan, string acc)
        {
            VoucherPlan gl = plan.Gl;
            GlKey key = gl.Key;
            DateTime day = DateTime.ParseExact(gl.Date, "yyyy-MM-dd", CultureInfo.InvariantCulture);
            string sql = "update GL_accvouch set coutsysname=?, coutsign=?, coutno_id=?, coutbillsign=?, coutid=?, coutaccset=?, "
                + "ioutyear=?, ioutperiod=?, doutbilldate=" + Day + ", bvouchAddordele=1, bvalueedit=1" + GlState.KeyWhere
                + " and isnull(ibook,0)=0";
            object[] head = new object[]
            {
                gl.Doc.Flag, gl.OutSign, gl.PzId, gl.Doc.VType, gl.Doc.Code, acc ?? "", day.Year, day.Month, gl.Date
            };
            GlSql.Exec(conn, sql, GlSql.With(head, GlSql.KeyArgs(key)));
            string one = GlState.KeyWhere + " and isnull(ibook,0)=0 and inid=?";
            for (int i = 0; i < plan.Sources.Count; i++)
            {
                string[] src = plan.Sources[i];
                if (src[0] == gl.Doc.VType && src[1] == gl.Doc.Code)
                {
                    continue;
                }
                GlSql.Exec(conn, "update GL_accvouch set coutbillsign=?, coutid=?" + one,
                    GlSql.With(new object[] { src[0], src[1] }, GlSql.With(GlSql.KeyArgs(key), new object[] { i + 1 })));
            }
            foreach (int entry in gl.Controlled)
            {
                GlSql.Exec(conn, "update GL_accvouch set bvalueedit=0" + one, GlSql.With(GlSql.KeyArgs(key), new object[] { entry }));
            }
            foreach (KeyValuePair<int, string> fc in plan.Fc)
            {
                GlSql.Exec(conn, "update GL_accvouch set cexch_name=?, nfrat=0, md_f=0, mc_f=0" + one,
                    GlSql.With(new object[] { fc.Value }, GlSql.With(GlSql.KeyArgs(key), new object[] { fc.Key })));
            }
            GlSql.Exec(conn, "update GL_CashTable set csign=? where iyear=? and iPeriod=? and iSignSeq=? and iNo_id=? and csign is null",
                new object[] { key.Sign, key.Year, key.Period, gl.Seq, key.No });
        }

        // 回写：每个批次在两张表上（并账、汇兑损益只有一张有行，另一张的语句不改行）。
        public static void Mark(object conn, ProcPlan plan)
        {
            VoucherPlan gl = plan.Gl;
            foreach (string no in plan.Ask.CancelNos)
            {
                foreach (string table in Tables)
                {
                    GlSql.Exec(conn, "update " + table + " set cPZid=?, dPZDate=" + Day + ", cGLSign=?, iGLno_id=? where cCancelNo=? "
                        + "and cProcStyle=? and isnull(cPZid,N'')=N''",
                        new object[] { gl.PzId, gl.Date, gl.Key.Sign, gl.Key.No, no, plan.Ask.Style });
                }
            }
            Entries(conn, plan);
            // 票据处理：AP_Note_Sub.cPzID，退回生成的单据（ArapProcVoucherNotes）。
            ArapProcVoucherNotes.Mark(conn, plan);
            // 计提坏账 9F：Ar_BadPara.cPZid（ArapProcVoucherBad）。
            ArapProcVoucherBad.Mark(conn, plan);
        }

        // 明细 ino_id = 该行所在分录号：按（表、分录号）一条语句，Auto_ID 每批 200 个。
        static void Entries(object conn, ProcPlan plan)
        {
            Dictionary<string, List<object>> groups = new Dictionary<string, List<object>>(StringComparer.Ordinal);
            foreach (KeyValuePair<string, int> pair in plan.Gl.Entries)
            {
                int colon = pair.Key.IndexOf(':');
                string group = pair.Key.Substring(0, colon) + ":" + pair.Value.ToString(CultureInfo.InvariantCulture);
                List<object> ids;
                if (!groups.TryGetValue(group, out ids))
                {
                    ids = new List<object>();
                    groups[group] = ids;
                }
                ids.Add(pair.Key.Substring(colon + 1));
            }
            foreach (KeyValuePair<string, List<object>> pair in groups)
            {
                string[] parts = pair.Key.Split(':');
                string table = parts[0] == "AP" ? "Ap_Detail" : "Ar_Detail";
                int entry = int.Parse(parts[1], CultureInfo.InvariantCulture);
                for (int start = 0; start < pair.Value.Count; start += 200)
                {
                    List<object> chunk = pair.Value.GetRange(start, Math.Min(200, pair.Value.Count - start));
                    string sql = "update " + table + " set ino_id=? where cPZid=? and Auto_ID in (?"
                        + new StringBuilder().Insert(0, ",?", chunk.Count - 1).ToString() + ")";
                    GlSql.Exec(conn, sql, GlSql.With(new object[] { entry, plan.Gl.PzId }, chunk.ToArray()));
                }
            }
        }

        // 提交前核对：这些批次的明细全部带上本凭证号；凭证行数与计划一致、来源列已写上。不符返回原因。
        public static string Mismatch(object conn, ProcPlan plan)
        {
            VoucherPlan gl = plan.Gl;
            int marked = Count(conn, "select convert(varchar(12), (select count(*) from Ar_Detail where cPZid=? and cProcStyle=?) "
                + "+ (select count(*) from Ap_Detail where cPZid=? and cProcStyle=?))",
                new object[] { gl.PzId, plan.Ask.Style, gl.PzId, plan.Ask.Style }) + ArapProcVoucherBad.ParaMarked(conn, plan, gl.PzId);
            if (marked != plan.Rows.Count)
            {
                return "往来明细回写了 " + Int(marked) + " 行，应为 " + Int(plan.Rows.Count) + " 行（批次可能已在 U8 客户端制单）";
            }
            int lines = Count(conn, "select convert(varchar(12), count(*)) from GL_accvouch" + GlState.KeyWhere
                + " and coutno_id=? and coutsign=?", GlSql.With(GlSql.KeyArgs(gl.Key), new object[] { gl.PzId, gl.OutSign }));
            if (lines != gl.Draft.Lines.Count)
            {
                return "凭证上带外部业务号的分录 " + Int(lines) + " 行，应为 " + Int(gl.Draft.Lines.Count) + " 行";
            }
            // 坏账收回 9H：收款单审核行和表头也已是本凭证号（ArapProcVoucherBad.ReceiptMismatch）。
            return ArapProcVoucherNotes.Mismatch(conn, plan) ?? ArapProcVoucherBad.ReceiptMismatch(conn, plan);
        }

        // 清除处理明细上的凭证号（补偿、取消制单）：两张表上引用该外部业务号的 9I / 9J / BZ / 9M / 9N 和票据 9A / 9D / 9E / 9C 行，
        // 以及票据处理行、退回生成的单据（ArapProcVoucherNotes.Clear）；含坏账 9G / 9H 行和计提 9F 的坏账准备参数行。
        // 同 U8 删凭证只清 cPZid、cGLSign、iGLno_id；dPZDate、ino_id 留着（U8 也不清）。
        public static void Clear(object conn, string pzId)
        {
            ArapProcVoucherNotes.Clear(conn, pzId);
            ArapProcVoucherBad.Clear(conn, pzId);
            foreach (string table in Tables)
            {
                GlSql.Exec(conn, "update " + table + " set cPZid=null, cGLSign=null, iGLno_id=null where cPZid=? "
                    + "and cProcStyle in " + ProcStyles, new object[] { pzId });
            }
        }

        internal static int Count(object conn, string sql, object[] args)
        {
            return CoRows.AsId(Rows.Scalar(conn, sql, args));
        }

        static string Int(int value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }
    }
}
