using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 坏账处理制单（arap/process/voucher 的 HZAR 批次）：计提 9F、发生 9G、收回 9H，coutsign JT。第二级写入。
    // 三种共用 HZAR 编号，Prepare 先按库识别（Resolve）：往来明细有 9G / 9H 行，或坏账准备参数 Ar_BadPara.cCancelNo 是它（9F）。
    // 分录（科目取坏账准备参数：9F 取该批次所在的参数行，9G / 9H 取处理年度的参数行）：
    // - 9F：借 对方科目 cDyCode、贷 坏账准备科目 cHzCode，金额 = 参数行的 iJtAmount（U8 同年多次计提累加在这一列，冲回为负数照记）；
    //   没有往来明细，批次行由参数行拼出（Aid 为空），两行都不回写明细，回写 Ar_BadPara.cPZid；凭证行来源 coutbillsign 9F、coutid 处理号。
    // - 9G：每行明细 借 cHzCode、贷 明细的应收科目 cCode（往来单位辅助核算取明细行），金额 = 贷 - 借；cCode 那行回写明细分录号。
    // - 9H：借 收款单表头的结算科目 Ap_CloseBill.cCode（明细 cCode 是应收科目，不用）、贷 cHzCode，金额 = 借 - 贷；结算科目那行回写
    //   分录号；银行科目是现金流量科目时同票据处理挂现金流量项目（ArapProcVoucherNotes.Flows）。另把收款单自己的审核行
    //   （cProcStyle = cVouchType = 48，同 arap/voucher 回写收款单的那些行）和表头 Ap_CloseBill.cPzID / cPZNum / doutbilldate 一并
    //   写成本凭证（MarkReceipts），U8 的制单列表不会再列出这张收款单；取消制单、补偿时一并清掉（Clear）。
    // 坏账准备参数里的科目必须是登录年度存在的末级科目，否则 409。只收本币（外币明细 ArapProcVoucherLoad.CheckRows 拒绝）。
    // 分录顺序：借方行在前、贷方行在后，各自保持生成顺序。
    internal static class ArapProcVoucherBad
    {
        public const string OutSign = "JT";
        const string DetailStyle = "select top 1 d.cProcStyle from Ar_Detail d where d.cCancelNo=? and d.cFlag=N'AR' "
            + "and d.cProcStyle in (N'9G',N'9H')";
        const string ParaStyle = "select top 1 N'9F' from Ar_BadPara b where b.cCancelNo=? and b.cProcStyle=N'9F' and b.dJtDate is not null";
        const string ParaCols = "b.cCancelNo as cno, isnull(b.cPZid,N'') as pz, {jt} as jt, convert(varchar(10), b.dJtDate, 23) as rd, "
            + "convert(varchar(4), year(b.dJtDate)) as ry, convert(varchar(4), month(b.dJtDate)) as per, convert(varchar(6), b.iYear) as y";
        const string ByNoSql = "select top 1 isnull(b.cHzCode,N'') as hz, isnull(b.cDyCode,N'') as dy from Ar_BadPara b "
            + "where b.cCancelNo=? and b.cProcStyle=N'9F' order by b.autoid desc";
        const string ByYearSql = "select top 1 isnull(b.cHzCode,N'') as hz, isnull(b.cDyCode,N'') as dy from Ar_BadPara b "
            + "where b.iYear=? order by b.autoid desc";
        const string BankSql = "select isnull(h.cCode,N'') as code from Ap_CloseBill h where h.cVouchType=N'48' and h.cVouchID=? and h.cFlag=N'AR'";
        const string LeafSql = "select convert(varchar(4), isnull(bend,0)) from code where iyear=? and ccode=?";
        const string Day = "cast(convert(date, ?, 23) as datetime)";
        // 本凭证上 9H 行所在的收款单号（坏账收回制单回写收款单用）。
        const string Receipts = "(select h.cVouchID from Ar_Detail h where h.cPZid=? and h.cProcStyle=N'9H' and h.cFlag=N'AR' "
            + "and h.cVouchType=N'48')";
        // 收款单自己的审核行（同 ArapVoucherBack 回写收款单的条件 cProcStyle = cVouchType）。
        const string OwnRows = "a.cFlag=N'AR' and a.cVouchType=N'48' and a.cProcStyle=N'48' and a.cVouchID in " + Receipts;
        // 取消制单（ArapProcVoucherDrop.Owns）放行的收款单审核行：同一凭证上有该收款单的 9H 行（x 是外层 Ar_Detail）。
        internal const string ReceiptRowCond = "x.cFlag=N'AR' and x.cVouchType=N'48' and x.cProcStyle=N'48' and exists (select 1 "
            + "from Ar_Detail h where h.cPZid=x.cPZid and h.cProcStyle=N'9H' and h.cFlag=N'AR' and h.cVouchType=N'48' "
            + "and h.cVouchID=x.cVouchID)";
        // NoOrphan 的补充条件：计提 9F 的凭证由坏账准备参数行引用（ArapProcVoucherLoad.NoOrphan，g 是 GL_accvouch）。
        internal const string OrphanCond = " and not exists (select 1 from Ar_BadPara b where b.cPZid=g.coutno_id)";

        // HZAR 批次 → 9F / 9G / 9H（ask.Style 由占位的 HZ 改成实际处理方式）；批次不存在 404，混了几种 409（编号前缀分不出，登录前查不了）。
        public static void Resolve(object conn, ProcVoucherAsk ask)
        {
            string style = null;
            foreach (string no in ask.CancelNos)
            {
                string one = Rows.Scalar(conn, DetailStyle, new object[] { no }) ?? Rows.Scalar(conn, ParaStyle, new object[] { no });
                if (one == null)
                {
                    throw new BridgeException(404, "not_found", "处理批次不存在：" + no);
                }
                style = Pick(style, one.Trim(), no);
            }
            ask.Style = style;
        }

        // 前面批次的处理方式 style（第一个为 null）与本批 one 必须相同，返回 one；不同 409。纯函数，--selftest 用。
        internal static string Pick(string style, string one, string no)
        {
            if (style != null && one != style)
            {
                throw ArapVoucherDoc.Refuse("一次制单只能是同一种坏账处理（" + no + " 是" + ArapProcVoucherReq.Title(one)
                    + "，前面的批次是" + ArapProcVoucherReq.Title(style) + "）");
            }
            return one;
        }

        // 计提 9F 的批次行（带锁）：每个批次一行，由坏账准备参数行拼出（没有往来明细）；之后同其他处理逐批核对（存在、未制单）。
        public static List<ProcVoucherRow> LoadPara(object conn, ProcVoucherAsk ask)
        {
            object[] nos = ask.CancelNos.ToArray();
            string inList = "(?" + new StringBuilder().Insert(0, ",?", nos.Length - 1).ToString() + ")";
            string sql = "select " + ParaCols.Replace("{jt}", WriteoffSql.Dec("isnull(b.iJtAmount,0)", 2))
                + " from Ar_BadPara b with (UPDLOCK, HOLDLOCK) where b.cProcStyle=N'9F' and b.dJtDate is not null and b.cCancelNo in " + inList;
            List<ProcVoucherRow> rows = new List<ProcVoucherRow>();
            foreach (Dictionary<string, object> r in Rows.Query(conn, sql, nos, ArapProcVoucherReq.Max + 1))
            {
                rows.Add(ParaRow(r));
            }
            foreach (string no in ask.CancelNos)
            {
                ArapProcVoucherLoad.CheckBatch(ask, no, rows);
            }
            CheckPara(rows);
            return rows;
        }

        internal static ProcVoucherRow ParaRow(Dictionary<string, object> r)
        {
            ProcVoucherRow row = new ProcVoucherRow();
            row.Ledger = "AR";
            row.Style = "9F";
            row.Flag = "AR";
            row.CancelNo = CoRows.Col(r, "cno");
            row.VType = "9F";
            row.VId = row.CancelNo;
            row.Pz = CoRows.Col(r, "pz");
            row.RegDate = CoRows.Col(r, "rd");
            row.RegYear = CoRows.AsId(CoRows.Col(r, "ry"));
            row.Period = CoRows.AsId(CoRows.Col(r, "per"));
            row.Dm = WriteoffSql.Num(CoRows.Col(r, "jt"));
            row.ParaYear = CoRows.AsId(CoRows.Col(r, "y"));
            return row;
        }

        // 每个批次恰好一行参数、参数年度与计提日期同年、计提金额不为 0。
        internal static void CheckPara(List<ProcVoucherRow> rows)
        {
            HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (ProcVoucherRow row in rows)
            {
                Need(seen.Add(row.CancelNo), "批次 " + row.CancelNo + " 对应多行坏账准备参数，请在 U8 客户端制单");
                Need(row.ParaYear == row.RegYear, "批次 " + row.CancelNo + " 的坏账准备参数年度与计提日期不一致，请在 U8 客户端制单");
                Need(row.Dm != 0m, "批次 " + row.CancelNo + " 的计提金额为 0，不需要制单");
            }
        }

        // 拼分录：科目取坏账准备参数（同一批次 / 年度只读一次），9H 的借方取收款单的结算科目。
        public static List<ProcPart> Parts(object conn, ProcPlan plan, int year)
        {
            ProcVoucherAsk ask = plan.Ask;
            Dictionary<string, string[]> cache = new Dictionary<string, string[]>(StringComparer.Ordinal);
            List<ProcPart> parts = new List<ProcPart>();
            foreach (ProcVoucherRow row in plan.Rows)
            {
                string[] para = Accounts(conn, ask.Style, row, year, cache);
                string[] acct = new string[] { para[0], para[1], ask.Style == "9H" ? Bank(conn, row) : "" };
                RowParts(parts, row, ask.Style, acct, ArapProcVoucherParts.DigestOf(row, ask.Style, ask.Digest));
            }
            return parts;
        }

        // 一行处理的分录（纯函数，--selftest 用）。acct：[坏账准备科目, 对方科目（只 9F 用）, 结算科目（只 9H 用）]。
        internal static void RowParts(List<ProcPart> parts, ProcVoucherRow row, string style, string[] acct, string digest)
        {
            string hz = acct[0];
            switch (style)
            {
                case "9F":
                    Pair(parts, Part(row, acct[1], true, row.Dm, digest, true), Part(row, hz, false, row.Dm, digest, true));
                    return;
                case "9G":
                    Need(row.Code.Length > 0, "批次 " + row.CancelNo + " 的往来明细（" + row.VType + " " + row.VId + "）缺科目，请在 U8 客户端制单");
                    Pair(parts, Part(row, hz, true, row.Cm - row.Dm, digest, true), Part(row, row.Code, false, row.Cm - row.Dm, digest, false));
                    return;
                case "9H":
                    Pair(parts, Part(row, acct[2], true, row.Dm - row.Cm, digest, false), Part(row, hz, false, row.Dm - row.Cm, digest, true));
                    return;
                default:
                    throw new BridgeException(500, "internal", "不是坏账处理：" + style);
            }
        }

        static void Pair(List<ProcPart> parts, ProcPart debit, ProcPart credit)
        {
            if (debit.Amount == 0m)
            {
                return;
            }
            parts.Add(debit);
            parts.Add(credit);
        }

        static ProcPart Part(ProcVoucherRow row, string account, bool debit, decimal amount, string digest, bool pl)
        {
            ProcPart part = new ProcPart();
            part.Row = row;
            part.Account = account;
            part.Debit = debit;
            part.Amount = amount;
            part.Digest = digest;
            part.Pl = pl;
            return part;
        }

        // [坏账准备科目, 对方科目]：9F 按批次的参数行，9G / 9H 按处理年度的参数行（同年度多行取最新）；必须是登录年度的末级科目。
        static string[] Accounts(object conn, string style, ProcVoucherRow row, int year, Dictionary<string, string[]> cache)
        {
            string key = style == "9F" ? "N" + row.CancelNo : "Y" + row.RegYear.ToString(CultureInfo.InvariantCulture);
            string[] acct;
            if (cache.TryGetValue(key, out acct))
            {
                return acct;
            }
            Dictionary<string, object> para = style == "9F" ? Rows.One(conn, ByNoSql, new object[] { row.CancelNo })
                : Rows.One(conn, ByYearSql, new object[] { row.RegYear });
            Need(para != null, "未设置 " + row.RegYear.ToString(CultureInfo.InvariantCulture) + " 年坏账准备参数（应收款管理 › 设置 › 坏账准备）");
            acct = new string[] { CoRows.Col(para, "hz"), CoRows.Col(para, "dy") };
            Need(acct[0].Length > 0, "坏账准备参数里没有设置坏账准备科目");
            Need(style != "9F" || acct[1].Length > 0, "坏账准备参数里没有设置对方科目");
            Leaf(conn, year, acct[0]);
            if (style == "9F")
            {
                Leaf(conn, year, acct[1]);
            }
            cache[key] = acct;
            return acct;
        }

        static void Leaf(object conn, int year, string code)
        {
            string bend = Rows.Scalar(conn, LeafSql, new object[] { year, code });
            Need(bend != null && bend.Trim() == "1", "坏账准备参数里的科目 " + code + " 不存在或不是末级");
        }

        // 坏账收回的借方：收款单表头的结算科目（明细行的科目是收款单行的应收科目，不能代替）。
        static string Bank(object conn, ProcVoucherRow row)
        {
            List<Dictionary<string, object>> heads = Rows.Query(conn, BankSql, new object[] { row.VId }, 2);
            Need(heads.Count == 1, "收款单 " + row.VId + (heads.Count == 0 ? " 不存在" : " 的单号不唯一") + "，请在 U8 客户端制单");
            string code = CoRows.Col(heads[0], "code").Trim();
            Need(code.Length > 0, "收款单 " + row.VId + " 没有结算科目，请在 U8 客户端制单");
            return code;
        }

        // 借方行在前、贷方行在后（各自保持顺序），再查行数和借贷平衡。
        public static List<ProcLine> Order(List<ProcLine> lines)
        {
            List<ProcLine> ordered = new List<ProcLine>();
            foreach (ProcLine line in lines)
            {
                if (line.Line.Debit != 0)
                {
                    ordered.Add(line);
                }
            }
            foreach (ProcLine line in lines)
            {
                if (line.Line.Debit == 0)
                {
                    ordered.Add(line);
                }
            }
            ArapProcVoucherParts.Balanced(ordered);
            return ordered;
        }

        // 回写计提 9F 的坏账准备参数行、坏账收回 9H 的收款单（往来明细的 9G / 9H 行由 ArapProcVoucherBack.Mark 照常写）。
        public static void Mark(object conn, ProcPlan plan)
        {
            if (plan.Ask.Style == "9H")
            {
                MarkReceipts(conn, plan);
            }
            if (plan.Ask.Style != "9F")
            {
                return;
            }
            foreach (string no in plan.Ask.CancelNos)
            {
                GlSql.Exec(conn, "update Ar_BadPara set cPZid=? where cCancelNo=? and cProcStyle=N'9F' and dJtDate is not null "
                    + "and isnull(cPZid,N'')=N''", new object[] { plan.Gl.PzId, no });
            }
        }

        // 9H：收款单审核行写凭证号三列和 dPZDate，ino_id 取同一收款单 9H 行的分录号（借方银行行）；表头写 cPzID、cPZNum、doutbilldate
        // （同 arap/voucher 回写收款单）。回写的审核行加进 plan.Gl.Doc 的行（ArapVoucherBack.Clash 按它核对引用外部业务号的明细行数）。
        static void MarkReceipts(object conn, ProcPlan plan)
        {
            VoucherPlan gl = plan.Gl;
            string pick = "select convert(varchar(20), a.Auto_ID) as aid from Ar_Detail a where " + OwnRows + " and isnull(a.cPZid,N'')=N''";
            List<Dictionary<string, object>> rows = Rows.Query(conn, pick, new object[] { gl.PzId }, 1001);
            Need(rows.Count <= 1000, "收款单审核行超过 1000 行，请在 U8 客户端制单");
            GlSql.Exec(conn, "update a set cPZid=?, dPZDate=" + Day + ", cGLSign=?, iGLno_id=? from Ar_Detail a where " + OwnRows
                + " and isnull(a.cPZid,N'')=N''", new object[] { gl.PzId, gl.Date, gl.Key.Sign, gl.Key.No, gl.PzId });
            GlSql.Exec(conn, "update a set ino_id=h.ino_id from Ar_Detail a inner join Ar_Detail h on h.cPZid=a.cPZid and h.cProcStyle=N'9H' "
                + "and h.cFlag=N'AR' and h.cVouchType=N'48' and h.cVouchID=a.cVouchID where a.cPZid=? and a.cFlag=N'AR' "
                + "and a.cVouchType=N'48' and a.cProcStyle=N'48'", new object[] { gl.PzId });
            GlSql.Exec(conn, "update Ap_CloseBill set cPzID=?, cPZNum=?, doutbilldate=" + Day + " where cFlag=N'AR' and cVouchType=N'48' "
                + "and isnull(cPzID,N'')=N'' and cVouchID in " + Receipts, new object[] { gl.PzId, gl.PzNum(), gl.Date, gl.PzId });
            foreach (Dictionary<string, object> row in rows)
            {
                Dictionary<string, object> one = new Dictionary<string, object>();
                one["aid"] = CoRows.Col(row, "aid");
                gl.Doc.Rows.Add(one);
            }
        }

        // 9H 制单另回写的收款单审核行数（= 计划单据行数 − 批次里有往来明细的行数；其余处理为 0）。提交后回读核对用。
        public static int ReceiptRows(ProcPlan plan)
        {
            if (plan.Ask.Style != "9H" || plan.Gl.Doc == null)
            {
                return 0;
            }
            int own = 0;
            foreach (ProcVoucherRow row in plan.Rows)
            {
                own += row.Aid.Length > 0 ? 1 : 0;
            }
            return plan.Gl.RowCount() - own;
        }

        // 9H 回写核对：本凭证 9H 行所在收款单的审核行、表头都已是本凭证号；有别的凭证号（U8 客户端已对收款单制单）返回原因。
        public static string ReceiptMismatch(object conn, ProcPlan plan)
        {
            if (plan.Ask.Style != "9H")
            {
                return null;
            }
            string pz = plan.Gl.PzId;
            string sql = "select convert(varchar(12), (select count(*) from Ar_Detail a where " + OwnRows + " and isnull(a.cPZid,N'')<>?)"
                + " + (select count(*) from Ap_CloseBill c where c.cFlag=N'AR' and c.cVouchType=N'48' and isnull(c.cPzID,N'')<>? "
                + "and c.cVouchID in " + Receipts + "))";
            int bad = ArapProcVoucherBack.Count(conn, sql, new object[] { pz, pz, pz, pz });
            return bad == 0 ? null : "坏账收回的收款单已有别的凭证（可能已在 U8 客户端对收款单制单），请先删除那张凭证";
        }

        // 引用本凭证的坏账准备参数行数（只 9F 计入回写核对；其余处理返回 0）。
        public static int ParaMarked(object conn, ProcPlan plan, string pzId)
        {
            return plan.Ask.Style == "9F" ? Left(conn, pzId) : 0;
        }

        public static int Left(object conn, string pzId)
        {
            return ArapProcVoucherBack.Count(conn, "select convert(varchar(12), count(*)) from Ar_BadPara where cPZid=?", new object[] { pzId });
        }

        // 清坏账准备参数行上的凭证号（补偿、取消制单；U8 删凭证同样 Update Ar_BadPara Set cPZID=null，实测），
        // 以及坏账收回制单回写的收款单审核行（凭证号三列）和表头（cPzID、cPZNum、doutbilldate，同 arap/voucher 取消制单）。
        public static void Clear(object conn, string pzId)
        {
            object[] args = new object[] { pzId };
            GlSql.Exec(conn, "update Ar_BadPara set cPZid=null where cPZid=?", args);
            GlSql.Exec(conn, "update Ar_Detail set cPZid=null, cGLSign=null, iGLno_id=null where cPZid=? and cFlag=N'AR' "
                + "and cVouchType=N'48' and cProcStyle=N'48'", args);
            GlSql.Exec(conn, "update Ap_CloseBill set cPzID=null, cPZNum=null, doutbilldate=null where cPzID=? and cFlag=N'AR' "
                + "and cVouchType=N'48'", args);
        }

        // 取消制单：计提 9F 的凭证（往来明细里没有引用、坏账准备参数行引用它、每行 coutsign 都是 JT）。
        public static bool OwnsPara(object conn, string pzId)
        {
            if (Left(conn, pzId) == 0)
            {
                return false;
            }
            string other = "select top 1 'x' from GL_accvouch where coutno_id=? and isnull(coutsign,N'')<>N'" + OutSign + "' "
                + "union all select top 1 'x' from Ar_Detail where cPZid=? union all select top 1 'x' from Ap_Detail where cPZid=?";
            return Rows.Scalar(conn, other, new object[] { pzId, pzId, pzId }) == null;
        }

        // 取消制单的闸门（处理凭证部分）：坏账处理第二级（测试账套）；计提 9F 的参数行所在月份应收未结账。
        public static void DropGate(WorkContext ctx, object conn, string pzId)
        {
            string sql = "select convert(varchar(6), iYear) as y, convert(varchar(4), isnull(month(dJtDate),0)) as p "
                + "from Ar_BadPara where cPZid=?";
            foreach (Dictionary<string, object> row in Rows.Query(conn, sql, new object[] { pzId }, 101))
            {
                TestAccountGate.Require(ctx.Item, ArapProcVoucherReq.BadTestOnly);
                int period = CoRows.AsId(CoRows.Col(row, "p"));
                if (period > 0 && WriteoffSql.Closed(conn, "AR", CoRows.AsId(CoRows.Col(row, "y")), period))
                {
                    throw ArapVoucherDoc.Refuse("应收已结账（坏账计提所在月份）");
                }
            }
        }

        static void Need(bool ok, string message)
        {
            if (!ok)
            {
                throw ArapVoucherDoc.Refuse(message);
            }
        }
    }
}
