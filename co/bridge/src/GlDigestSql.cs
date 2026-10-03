using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // gl/vouchers/digest 的 SQL 与指纹。只用参数 ?；期间个数决定占位符个数，调用方的文本不进 SQL。
    internal static class GlDigestSql
    {
        // 未结账期间（1 到 12，不含期初 0）与最近 N 个已结账期间。
        const string OpenSql = "SELECT CONVERT(varchar(2), iperiod) p FROM GL_mend"
            + " WHERE iyear=? AND iperiod BETWEEN 1 AND 12 AND ISNULL(bflag,0)=0";
        const string ClosedSql = "SELECT TOP (?) CONVERT(varchar(2), iperiod) p FROM GL_mend"
            + " WHERE iyear=? AND iperiod BETWEEN 1 AND 12 AND ISNULL(bflag,0)<>0 ORDER BY iperiod DESC";

        // watermark：所扫期间的 MAX(i_id)；ident：GL_accvouch 的 IDENT_CURRENT（删凭证不回退，还原快照才回退）。
        internal const string MarkHead = "SELECT CONVERT(varchar(12), ISNULL(MAX(i_id),0)) w,"
            + " CONVERT(varchar(20), IDENT_CURRENT('GL_accvouch')) ident FROM GL_accvouch WHERE iyear=?";

        // 指纹的输入一律在 SQL 里转成字符串（金额 4 位小数），与区域设置无关。
        // 按聚集索引 VouchCluster（iperiod, isignseq, ino_id）分组排序，SQL 可以顺序流式聚合、取够一页即停，不必每页把所扫期间
        // 剩下的分录全部聚合再排序（实测每页逻辑读下降一个数量级）。类别字 csign 与类别序号 isignseq 一一对应（dsign），
        // 取 MAX 不改变分组。
        const string Head = "SELECT TOP (?) iperiod, isignseq, MAX(ISNULL(csign,'')) csign, ino_id,"
            + " CONVERT(varchar(10), MIN(dbill_date), 23) d,"
            + " CONVERT(varchar(40), SUM(ISNULL(md,0)), 2) dsum, CONVERT(varchar(40), SUM(ISNULL(mc,0)), 2) csum,"
            + " CONVERT(varchar(12), COUNT(*)) n, CONVERT(varchar(12), MAX(i_id)) maxid,"
            + " MAX(ISNULL(cbill,'')) maker, MAX(ISNULL(ccheck,'')) checker, MAX(ISNULL(ccashier,'')) cashier,"
            + " MAX(ISNULL(cbook,'')) book, CONVERT(varchar(4), MAX(ISNULL(ibook,0))) posted,"
            + " CONVERT(varchar(4), MAX(ISNULL(iflag,0))) flag FROM GL_accvouch WHERE iyear=?";
        internal const string Tail = " GROUP BY iperiod, isignseq, ino_id ORDER BY iperiod, isignseq, ino_id";

        // 指纹按这个顺序拼接（| 分隔）后取 SHA-256。改顺序或列会让事件服务把全部凭证当成修改。
        internal static readonly string[] FingerprintCols = new string[]
        {
            "dsum", "csum", "n", "maxid", "maker", "checker", "cashier", "book", "posted", "flag"
        };

        public static int[] DefaultPeriods(object conn, int year, int closed)
        {
            List<int> periods = new List<int>();
            Collect(Rows.Query(conn, OpenSql, new object[] { year }, 12), periods);
            if (closed > 0)
            {
                Collect(Rows.Query(conn, ClosedSql, new object[] { closed, year }, closed), periods);
            }
            periods.Sort();
            return periods.ToArray();
        }

        static void Collect(List<Dictionary<string, object>> rows, List<int> periods)
        {
            for (int i = 0; i < rows.Count; i++)
            {
                int period = GlSql.Int(rows[i], "p");
                if (period >= 1 && period <= 12 && !periods.Contains(period))
                {
                    periods.Add(period);
                }
            }
        }

        // 年度和期间条件；没有期间时恒假（水位为 0）。
        internal static void Scope(StringBuilder sql, List<object> args, int year, int[] periods)
        {
            args.Add(year);
            if (periods.Length == 0)
            {
                sql.Append(" AND 1=0");
                return;
            }
            sql.Append(" AND iperiod IN (");
            for (int i = 0; i < periods.Length; i++)
            {
                sql.Append(i == 0 ? "?" : ",?");
                args.Add(periods[i]);
            }
            sql.Append(")");
        }

        // 一页的 SELECT 和 WHERE（不含数据权限和 GROUP BY）。after 与 gl/vouchers/list 同一键集。
        internal static StringBuilder PageSql(GlDigestReq req, int year, int[] periods, List<object> args)
        {
            args.Add(req.Limit + 1);
            StringBuilder sql = new StringBuilder(Head);
            Scope(sql, args, year, periods);
            if (req.After != null)
            {
                sql.Append(GlList.AfterSql);
                args.Add(req.After[0]);
                args.Add(req.After[0]);
                args.Add(req.After[1]);
                args.Add(req.After[1]);
                args.Add(req.After[2]);
            }
            return sql;
        }

        internal static string Next(Dictionary<string, object> last)
        {
            return GlSql.Col(last, "iperiod") + "." + GlSql.Col(last, "isignseq") + "." + GlSql.Col(last, "ino_id");
        }

        // SUM(md)、SUM(mc)、COUNT(*)、MAX(i_id)、制单人、审核人、出纳、记账人、记账标志、作废标志的 SHA-256（小写十六进制）。
        internal static string Fingerprint(Dictionary<string, object> row)
        {
            StringBuilder text = new StringBuilder();
            for (int i = 0; i < FingerprintCols.Length; i++)
            {
                if (i > 0)
                {
                    text.Append('|');
                }
                text.Append(GlSql.Col(row, FingerprintCols[i]));
            }
            return Crypto.Hex(Crypto.Sha256(Encoding.UTF8.GetBytes(text.ToString())));
        }
    }
}
