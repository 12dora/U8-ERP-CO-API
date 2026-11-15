using System.Collections.Generic;

namespace U8Co
{
    // 取消制单（arap/voucher/delete，ArapVoucherDrop）对处理凭证的部分：应收冲应付、应付冲应收、并账、汇兑损益、红票对冲的凭证
    // （arap/process/voucher 或 U8 制单处理生成的，coutsign ZZ / BZ / SY，红票对冲为 AR / AP）。U8 删这类凭证只清处理明细上的凭证号
    // （余额在处理时已改过），桥照做：两张往来明细上清 cPZid、cGLSign、iGLno_id。票据处理凭证（coutsign PJ，9A / 9D / 9E / 9C）
    // 另清 AP_Note_Sub.cPzID 和退回生成的单据的凭证号（U8 删凭证同样只清这些列）。坏账处理凭证（coutsign JT）：发生 9G / 收回 9H
    // 清往来明细的凭证号三列，计提 9F 清 Ar_BadPara.cPZid（坏账准备余额在处理时已改过，取消制单不动）；坏账收回 9H 的凭证还引用
    // 收款单自己的审核行和表头（制单时一并回写，ArapProcVoucherBad.MarkReceipts），一并清掉；坏账处理是第二级写入。
    internal static class ArapProcVoucherDrop
    {
        // 是处理凭证且整批引用：凭证每行 coutsign 都是 ZZ / BZ / SY / PJ / AR / AP（AR / AP 是红票对冲，也是普通单据凭证的值，所以下一条
        // 才是判据）；两张往来明细里引用它的都是 9I / 9J / BZ / 9M / 9N 等处理行（至少一行），另认同一凭证上有 9H 行的收款单的
        // 审核行（cProcStyle = 48，ArapProcVoucherBad.ReceiptRowCond）；
        // 引用到的批次每一行都引用它（不拆批次，同 U8 按批次制单）。
        public static bool Owns(object conn, string pzId)
        {
            if (Rows.Scalar(conn, "select top 1 'x' from GL_accvouch where coutno_id=? and isnull(coutsign,N'') not in (N'ZZ',N'BZ',N'SY',N'AR',N'AP',N'PJ',N'JT')",
                new object[] { pzId }) != null)
            {
                return false;
            }
            string styles = ArapProcVoucherBack.ProcStyles;
            string other = "select top 1 'x' from {t} x where x.cPZid=? and isnull(x.cProcStyle,N'') not in " + styles;
            string sql = other.Replace("{t}", "Ar_Detail") + " and not (" + ArapProcVoucherBad.ReceiptRowCond + ")"
                + " union all " + other.Replace("{t}", "Ap_Detail");
            if (Rows.Scalar(conn, sql, new object[] { pzId, pzId }) != null)
            {
                return false;
            }
            int mine = ArapProcVoucherBack.Count(conn, "select convert(varchar(12), (select count(*) from Ar_Detail where cPZid=?) "
                + "+ (select count(*) from Ap_Detail where cPZid=?))", new object[] { pzId, pzId });
            // 计提坏账 9F：没有往来明细，由坏账准备参数行引用（ArapProcVoucherBad.OwnsPara）。
            return mine > 0 ? WholeBatches(conn, pzId) : ArapProcVoucherBad.OwnsPara(conn, pzId);
        }

        static bool WholeBatches(object conn, string pzId)
        {
            string batches = "(select cCancelNo from Ar_Detail where cPZid=? union select cCancelNo from Ap_Detail where cPZid=?)";
            string part = "select top 1 'x' from {t} a where a.cProcStyle in " + ArapProcVoucherBack.ProcStyles + " and a.cCancelNo in "
                + batches + " and isnull(a.cPZid,N'')<>?";
            string sql = part.Replace("{t}", "Ar_Detail") + " union all " + part.Replace("{t}", "Ap_Detail");
            return Rows.Scalar(conn, sql, new object[] { pzId, pzId, pzId, pzId, pzId, pzId }) == null;
        }

        // 处理凭证的另外两道闸门（ArapVoucherDrop.Gate 之后）：含汇兑损益、坏账或应付票据处理（Ap_Detail 上的票据行 50）的行时
        // 只对测试账套开放（第二级，同 arap/process/voucher）；
        // 另一张往来明细（应收冲应付的应付方等）的登记期间也不能已结账。不是处理凭证时什么也不做。
        public static void Gate(WorkContext ctx, object conn, string flag, string pzId)
        {
            string sql = "select distinct d.cProcStyle as ps, convert(varchar(4), year(d.dRegDate)) as y, convert(varchar(10), d.iPeriod) as p "
                + "from {t} d where d.cPZid=? and d.cProcStyle in " + ArapProcVoucherBack.ProcStyles;
            string[] ledgers = new string[] { "AR", "AP" };
            foreach (string ledger in ledgers)
            {
                string table = WriteoffSql.Detail(ledger);
                foreach (Dictionary<string, object> row in Rows.Query(conn, sql.Replace("{t}", table), new object[] { pzId }, 501))
                {
                    string ps = CoRows.Col(row, "ps");
                    if (ps == "9M")
                    {
                        TestAccountGate.Require(ctx.Item, ArapProcVoucherReq.TestOnly);
                    }
                    if (ps == "9G" || ps == "9H")
                    {
                        TestAccountGate.Require(ctx.Item, ArapProcVoucherReq.BadTestOnly);
                    }
                    if (ledger != flag && WriteoffSql.Closed(conn, ledger, CoRows.AsId(CoRows.Col(row, "y")), CoRows.AsId(CoRows.Col(row, "p"))))
                    {
                        throw ArapVoucherDoc.Refuse(ArapProcVoucherLoad.Side(ledger) + "已结账（处理登记期间）");
                    }
                }
            }
            if (Rows.Scalar(conn, "select top 1 'x' from Ap_Detail where cPZid=? and cVouchType=N'50' and cProcStyle in "
                + "(N'9A',N'9D',N'9E',N'9C')", new object[] { pzId }) != null)
            {
                TestAccountGate.Require(ctx.Item, ArapProcVoucherReq.ApNotesTestOnly);
            }
            ArapProcVoucherBad.DropGate(ctx, conn, pzId);
        }
    }
}
