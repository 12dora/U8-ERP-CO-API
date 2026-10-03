using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 外部业务号（GL_accvouch.coutno_id = Ar_Detail / Ap_Detail.cPZid）照 U8 应收应付制单取号：
    // Ap_CancelNo 的 cType='PZ'、cFlag=AR|AP 行加一（没有这一行就插入），不是 GL_accvouch 的最大号。
    // 在桥的事务里带锁读、写；新号若已被占用（总账凭证或往来明细上已有）就往后跳，不重号。
    internal static class ArapVoucherNo
    {
        const int MaxSkip = 1000;

        public static string Allocate(object conn, string flag)
        {
            string raw = Rows.Scalar(conn, "select convert(varchar(20), iCancelNo) from Ap_CancelNo with (UPDLOCK, HOLDLOCK) "
                + "where cType=N'PZ' and cFlag=?", new object[] { flag });
            int current = 0;
            bool have = raw != null && int.TryParse(raw.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out current);
            int next = (have ? current : 0) + 1;
            for (int i = 0; i < MaxSkip && Used(conn, flag, ArapVoucherRule.PzId(flag, next)); i++)
            {
                next++;
            }
            string id = ArapVoucherRule.PzId(flag, next);
            if (Used(conn, flag, id))
            {
                throw ArapVoucherDoc.Refuse("凭证外部业务号 " + id + " 已被占用，请检查 Ap_CancelNo");
            }
            if (have)
            {
                GlSql.Exec(conn, "update Ap_CancelNo set iCancelNo=? where cType=N'PZ' and cFlag=? and iCancelNo<?",
                    new object[] { next, flag, next });
            }
            else if (raw == null)
            {
                GlSql.Exec(conn, "insert into Ap_CancelNo (iCancelNo, cType, cFlag) values (?, N'PZ', ?)", new object[] { next, flag });
            }
            else
            {
                throw ArapVoucherDoc.Refuse("Ap_CancelNo 的 PZ 号无法识别：" + raw.Trim());
            }
            return id;
        }

        static bool Used(object conn, string flag, string id)
        {
            string sql = "select top 1 'x' from GL_accvouch where coutno_id=? union all select top 1 'x' from "
                + WriteoffSql.Detail(flag) + " where cPZid=?";
            return Rows.Scalar(conn, sql, new object[] { id, id }) != null;
        }
    }

    // 摘要：调用方给了就用；否则取往来明细上的摘要（U8 审核时写的，U8 制单也用它），
    // 往来行优先；都没有就是「销售 / 购 / 收 / 付」加往来单位名称（退款单再加「退款」）。每行相同，最多 120 字。
    internal static class ArapVoucherDigest
    {
        public static string Of(object conn, VoucherDoc doc)
        {
            string text = doc.Ask.Digest;
            if (text.Length == 0)
            {
                text = FromRows(doc.Rows, true);
            }
            if (text.Length == 0)
            {
                text = FromRows(doc.Rows, false);
            }
            if (text.Length == 0)
            {
                text = ArapVoucherRule.Prefix(doc.Ask.Kind) + PartnerName(conn, doc.Flag, doc.Dw) + ArapVoucherRule.Suffix(doc.Ask.Kind);
            }
            return text.Length > ArapVoucherReq.DigestMax ? text.Substring(0, ArapVoucherReq.DigestMax) : text;
        }

        static string FromRows(List<Dictionary<string, object>> rows, bool dwOnly)
        {
            foreach (Dictionary<string, object> row in rows)
            {
                if (dwOnly && CoRows.Col(row, "iflag") != "0")
                {
                    continue;
                }
                string digest = CoRows.Col(row, "cDigest");
                if (digest.Length > 0)
                {
                    return digest;
                }
            }
            return "";
        }

        static string PartnerName(object conn, string flag, string dw)
        {
            string sql = flag == "AP" ? "select isnull(cVenName,N'') from Vendor where cVenCode=?"
                : "select isnull(cCusName,N'') from Customer where cCusCode=?";
            string name = (Rows.Scalar(conn, sql, new object[] { dw }) ?? "").Trim();
            return name.Length > 0 ? name : dw;
        }
    }
}
