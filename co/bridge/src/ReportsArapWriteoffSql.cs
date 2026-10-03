using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 核销记录查询的 SQL。全部不加锁（读线程池、不开事务）；表名、列名只来自这里的固定表，调用方的值只进参数。
    // 往来明细表按 AR / AP 取（WriteoffSql.Detail）。一个核销号（cCancelNo）是一批 cProcStyle=9P 的行。
    // 取页之后整页的行、表头等见 WriteoffPageSql（ReportsArapWriteoffPageSql.cs）。
    internal static class WriteoffListSql
    {
        // 按核销号分组，日期取登记日期（同一批相同）。绑定顺序：TOP、flag、{FILTER}，外层再追加日期和游标条件。
        const string PageSql = "SELECT TOP (?) g.no, g.dt FROM (SELECT d.cCancelNo no, CONVERT(char(10), MIN(d.dRegDate), 23) dt"
            + " FROM {D} d WHERE d.cProcStyle=N'9P' AND d.cFlag=? AND ISNULL(d.cCancelNo, N'')<>N''{FILTER}"
            + " GROUP BY d.cCancelNo) g WHERE 1=1";

        // 被核销单据的类型名 → { 表, 主键, 单号列, 类型列, 网络锁表达式 }（同 WriteoffKind，不加锁）。
        static readonly Dictionary<string, string[]> Docs = BuildDocs();

        static Dictionary<string, string[]> BuildDocs()
        {
            Dictionary<string, string[]> map = new Dictionary<string, string[]>(StringComparer.Ordinal);
            map.Add("sale_invoice", new string[] { "SaleBillVouch", "SBVID", "cSBVCode", "cVouchType", "0" });
            map.Add("purchase_invoice", new string[]
            {
                "PurBillVouch", "PBVID", "cPBVCode", "cPBVBillType", "case when isnull(h.iNetLock,0)<>0 then 1 else 0 end"
            });
            map.Add("ar_bill", new string[] { "Ap_Vouch", "Auto_ID", "cVouchID", "cVouchType", "0" });
            map.Add("ap_bill", new string[] { "Ap_Vouch", "Auto_ID", "cVouchID", "cVouchType", "0" });
            return map;
        }

        // 被核销单据类型名的 { 表, 主键, 单号列, 类型列, 网络锁表达式 }；不认识返回 null。
        internal static string[] Doc(string kind)
        {
            string[] doc;
            return kind != null && Docs.TryGetValue(kind, out doc) ? doc : null;
        }

        // 一页核销号（多取一条判断是否有下一页）。after 是 { 日期, 核销号 }。
        public static List<Dictionary<string, object>> Page(WorkContext ctx, WriteoffListArgs w, string[] after)
        {
            List<object> args = new List<object>();
            args.Add(w.Limit + 1);
            args.Add(w.Flag);
            StringBuilder filter = new StringBuilder();
            Filter(filter, args, w);
            // 数据权限：客户或供应商（往来明细的 cDwCode）。
            PermHook.Where(filter, args, ctx, "d");
            StringBuilder sql = new StringBuilder(PageSql.Replace("{D}", WriteoffSql.Detail(w.Flag))
                .Replace("{FILTER}", filter.ToString()));
            Outer(sql, args, w, after);
            sql.Append(" ORDER BY g.dt DESC, g.no DESC");
            return Rows.Query(ctx.Conn, sql.ToString(), args.ToArray(), w.Limit + 1);
        }

        // 行上的条件：往来单位、核销号、收付款单（id 或单号）、被核销单据（id 或单号；单号不含收付款单自身的冲减行）。
        static void Filter(StringBuilder sql, List<object> args, WriteoffListArgs w)
        {
            Eq(sql, args, " AND d.cDwCode=?", w.Partner);
            Eq(sql, args, " AND d.cCancelNo=?", w.CancelNo);
            Eq(sql, args, " AND d.cVouchID=?", w.ReceiptCode);
            Eq(sql, args, " AND d.cCoVouchID=? AND NOT (d.cCoVouchType=d.cVouchType AND d.cCoVouchID=d.cVouchID)", w.TargetCode);
            if (w.ReceiptId > 0)
            {
                sql.Append(" AND EXISTS (SELECT 1 FROM Ap_CloseBill h WHERE h.iID=? AND h.cVouchID=d.cVouchID"
                    + " AND h.cVouchType=d.cVouchType AND h.cFlag=d.cFlag)");
                args.Add(w.ReceiptId);
            }
            if (w.TargetId > 0)
            {
                string[] doc = Docs[w.TargetKind];
                sql.Append(" AND EXISTS (SELECT 1 FROM ").Append(doc[0]).Append(" h WHERE h.").Append(doc[1])
                    .Append("=? AND h.").Append(doc[2]).Append("=d.cCoVouchID AND h.").Append(doc[3]).Append("=d.cCoVouchType)");
                args.Add(w.TargetId);
            }
        }

        static void Eq(StringBuilder sql, List<object> args, string cond, string value)
        {
            if (value.Length > 0)
            {
                sql.Append(cond);
                args.Add(value);
            }
        }

        // 分组之后的条件：登记日期区间（含两端）、翻页游标（日期降序、核销号降序）。
        static void Outer(StringBuilder sql, List<object> args, WriteoffListArgs w, string[] after)
        {
            Eq(sql, args, " AND g.dt>=?", w.DateFrom);
            Eq(sql, args, " AND g.dt<=?", w.DateTo);
            if (after != null)
            {
                sql.Append(" AND (g.dt<? OR (g.dt=? AND g.no<?))");
                args.Add(after[0]);
                args.Add(after[0]);
                args.Add(after[1]);
            }
        }

        // 按单号和单据类型找表头：{ id, locked }；类型不认识或找不到返回 null。
        public static Dictionary<string, object> Head(object conn, string kind, string code, string vtype)
        {
            string[] doc;
            if (kind == null || !Docs.TryGetValue(kind, out doc))
            {
                return null;
            }
            string sql = "select top 1 convert(varchar(20), h." + doc[1] + ") as id, " + doc[4] + " as locked from " + doc[0]
                + " h where h." + doc[2] + "=? and h." + doc[3] + "=? order by h." + doc[1];
            return Rows.One(conn, sql, new object[] { code, vtype });
        }
    }
}
