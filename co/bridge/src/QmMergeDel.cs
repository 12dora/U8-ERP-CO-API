using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 删除合并检验的检验单（BMERGECHECKFLAG=1，U8 合并检验或本接口 QmMergeCheck 生成）：删除前记下每条合并明细的来源报检单行和数量、来源行当时的累计检验数量，
    // 删除确认之后在新连接上核对每条来源行都回退了本单的数量（U8 删除时按合并明细回写 FSUMCHECKQTY / BFLAG）；
    // 没回退 504（单据已删，需在 U8 中核对）。删除走 VO 接口 GetTheVoucher → GetMergeDetailVoucher（取合并明细 VO）→
    // DelVoucher(login, VO, IVTID 省略, detailVO)，U8 按合并明细回退每条来源（测试账套实测）；普通检验单的
    // VoucherOperate("delete") 对合并检验单报「未设置对象变量或 With block 变量」。
    internal static class QmMergeDel
    {
        const string FlagSql = "select convert(varchar(5), isnull(BMERGECHECKFLAG,0)) from QMCHECKVOUCHER where ID=? and CVOUCHTYPE=?";
        const string SourcesSql = "select convert(varchar(20), m.INSPECTAUTOID) as src, convert(varchar(40), isnull(m.FQUANTITY,0)) as qty,"
            + " convert(varchar(40), isnull(b.FSUMCHECKQTY,0)) as used from QMMergeCheckDetail m"
            + " left join QMINSPECTVOUCHERS b on b.AUTOID=m.INSPECTAUTOID where m.ID=? order by m.AUTOID";
        const int Cap = 500;

        // 删除前的快照；不是合并检验返回 null。
        internal static List<Dictionary<string, object>> Before(object conn, QmSpec spec, int id)
        {
            if (spec.Inspect || QmSql.Scalar(conn, FlagSql, id, spec.VouchType) != "1")
            {
                return null;
            }
            return QmSql.Many(conn, SourcesSql, Cap, id);
        }

        internal static bool IsMerged(List<Dictionary<string, object>> before)
        {
            return before != null;
        }

        // 单据已删除之后：每条来源行的累计检验数量 = 删除前 − 本单数量。
        internal static void After(WorkContext ctx, List<Dictionary<string, object>> before)
        {
            if (before == null)
            {
                return;
            }
            string wrong;
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                wrong = Unreversed(conn, before);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "QmMergeDel " + ex.Message);
                wrong = "回读来源报检单行失败";
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (wrong.Length > 0)
            {
                throw new BridgeException(504, "outcome_unknown", "U8 已删除合并检验的检验单，但" + wrong + "，需在 U8 中核对");
            }
        }

        static string Unreversed(object conn, List<Dictionary<string, object>> before)
        {
            for (int i = 0; i < before.Count; i++)
            {
                int src = CoRows.AsId(CoRows.Col(before[i], "src"));
                decimal want = QmSql.Dec(CoRows.Col(before[i], "used")) - QmSql.Dec(CoRows.Col(before[i], "qty"));
                decimal now = QmSql.Dec(QmSql.Scalar(conn, QmSql.InsUsedSql, src));
                if (now != (want > 0m ? want : 0m))
                {
                    return "来源报检单行 " + src.ToString(CultureInfo.InvariantCulture) + " 的累计检验数量没有回退";
                }
            }
            return "";
        }

        // VO 接口删除，自己提交。载入失败（U8 拒绝）409、找不到 404（QmRejCo.Load）。
        internal static QmOutcome Delete(WorkContext ctx, QmSpec spec, int id)
        {
            using (QmRejCo co = QmRejCo.OpenCheck(ctx, spec.ProgId, spec.Vt))
            {
                co.Load(id);
                object detail = co.MergeDetail(id);
                try
                {
                    return co.DeleteWith(detail);
                }
                finally
                {
                    ComUtil.Final(detail);
                }
            }
        }
    }
}
