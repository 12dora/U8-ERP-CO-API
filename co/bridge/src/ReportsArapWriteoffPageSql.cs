using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 核销记录查询按整页取数的 SQL（不加锁）：一页核销号的全部行、收付款单 id 和各行余额、被核销单据表头、
    // 这些单据上的「之后的处理」，各一条（单据多时按 UnwriteoffSql.DocChunk 分几条）。条件和逐批逐单据查时相同，结果相同。
    // 表名、列名只来自固定表；调用方和库里读出的值只进参数。
    internal static class WriteoffPageSql
    {
        // 与取消核销读整批的列相同（UnwriteoffSql.RowOf 认这些别名），另带核销号、操作员和凭证列；每个核销号最多取 ? 行（按 Auto_ID）。
        const string RowsSql = "select x.cno, x.auto, x.style, x.flag, x.vtype, x.vcode, x.cotype, x.cocode, x.bvid, x.coclose, "
            + "x.period, x.regdate, x.dw, x.pz, x.contract, x.cur, x.rate, x.df, x.cf, x.op, x.pzsign, x.pzno, x.pzdate from ("
            + "select d.cCancelNo as cno, convert(varchar(20), d.Auto_ID) as auto, d.cProcStyle as style, d.cFlag as flag, "
            + "d.cVouchType as vtype, d.cVouchID as vcode, d.cCoVouchType as cotype, d.cCoVouchID as cocode, "
            + "convert(varchar(20), isnull(d.iBVid,0)) as bvid, convert(varchar(20), isnull(d.iCoClosesID,0)) as coclose, "
            + "convert(varchar(10), d.iPeriod) as period, convert(varchar(10), d.dRegDate, 23) as regdate, d.cDwCode as dw, "
            + "d.cPZid as pz, d.cContractID as contract, d.cexch_name as cur, {rate} as rate, {df} as df, {cf} as cf, "
            + "d.cOperator as op, d.cGLSign as pzsign, convert(varchar(20), d.iGLno_id) as pzno, "
            + "convert(varchar(10), d.dPZDate, 23) as pzdate, row_number() over (partition by d.cCancelNo order by d.Auto_ID) as rn "
            + "from {D} d where d.cProcStyle=N'9P' and d.cFlag=? and d.cCancelNo in ({IN})) x where x.rn<=? order by x.cno, x.rn";

        // 同 UnwriteoffSql.ReceiptId（按单号、类型、AR / AP 找收付款单 id），一次查多张。
        const string ReceiptSql = "select h.cVouchType as t, h.cVouchID as c, convert(varchar(20), min(h.iID)) as id "
            + "from Ap_CloseBill h where h.cFlag=? and ({DOCS}) group by h.cVouchType, h.cVouchID";

        // 收付款单各行的未核销余额（原币），每张最多 501 行（按行 id），同 WriteoffListSql 逐张查时。
        const string LinesSql = "select x.rid, x.line, x.rem_f from (select convert(varchar(20), b.iID) as rid, "
            + "convert(varchar(20), b.ID) as line, {rem} as rem_f, row_number() over (partition by b.iID order by b.ID) as rn "
            + "from Ap_CloseBills b where b.iID in ({IN})) x where x.rn<=501";

        // 同 UnwriteoffSql.LaterSql 的条件，按（单据、核销号）给出最大 Auto_ID：对方单据是它的行和本单据是它的行分开归。
        // 某批「之后有处理」⇔ 它的某张单据上有核销号不同、Auto_ID 大于该批最大 Auto_ID 的一组。Auto_ID 下限取整页各批的最小值。
        const string LaterSql = "select x.t, x.c, x.cno, convert(varchar(20), max(x.a)) as a from ("
            + "select d.cCoVouchType as t, d.cCoVouchID as c, isnull(d.cCancelNo,N'') as cno, d.Auto_ID as a from {D} d "
            + "where d.cFlag=? and d.Auto_ID>? and {BLOCK} and ({CO}) union all "
            + "select d.cVouchType, d.cVouchID, isnull(d.cCancelNo,N''), d.Auto_ID from {D} d "
            + "where d.cFlag=? and d.Auto_ID>? and {BLOCK} and ({OWN})) x group by x.t, x.c, x.cno";

        const int Unlimited = 1000000;

        public static List<Dictionary<string, object>> Batches(object conn, string flag, List<string> nos, int max)
        {
            List<object> args = new List<object>();
            args.Add(flag);
            string inList = InList(nos.Count);
            foreach (string no in nos)
            {
                args.Add(no);
            }
            args.Add(max);
            string sql = RowsSql.Replace("{D}", WriteoffSql.Detail(flag)).Replace("{IN}", inList)
                .Replace("{rate}", WriteoffSql.Dec("d.iExchRate", 10))
                .Replace("{df}", WriteoffSql.Dec("d.iDAmount_f", 2)).Replace("{cf}", WriteoffSql.Dec("d.iCAmount_f", 2));
            return Rows.Query(conn, sql, args.ToArray(), nos.Count * max);
        }

        // docs[start ..] 一段（最多 DocChunk 张）的收付款单 id：{ t, c, id }。
        public static List<Dictionary<string, object>> Receipts(object conn, string flag, List<string[]> docs, int start)
        {
            List<object> args = new List<object>();
            args.Add(flag);
            string cond = Pairs(docs, start, args, "(h.cVouchType=? and h.cVouchID=?)", false);
            return Rows.Query(conn, ReceiptSql.Replace("{DOCS}", cond), args.ToArray(), Unlimited);
        }

        // 收付款单各行余额：{ rid, line, rem_f }。ids 最多 DocChunk 个。
        public static List<Dictionary<string, object>> Lines(object conn, List<int> ids)
        {
            List<object> args = new List<object>();
            foreach (int id in ids)
            {
                args.Add(id);
            }
            string sql = LinesSql.Replace("{IN}", InList(ids.Count)).Replace("{rem}", WriteoffSql.Dec("b.iRAmt_f", 2));
            return Rows.Query(conn, sql, args.ToArray(), Unlimited);
        }

        // 一种单据（doc 是 WriteoffListSql.Doc 的 { 表, 主键, 单号列, 类型列, 网络锁表达式 }）一段单号的表头：
        // { code, vtype, id, locked }，每个（单号、类型）取主键最小的一张（同 WriteoffListSql.Head 的 top 1 order by 主键）。
        public static List<Dictionary<string, object>> Heads(object conn, string[] doc, List<string[]> docs, int start)
        {
            List<object> args = new List<object>();
            string pair = "(k." + doc[2] + "=? and k." + doc[3] + "=?)";
            string cond = Pairs(docs, start, args, pair, true);
            string sql = "select h." + doc[2] + " as code, h." + doc[3] + " as vtype, convert(varchar(20), h." + doc[1] + ") as id, "
                + doc[4] + " as locked from " + doc[0] + " h where h." + doc[1] + " in (select min(k." + doc[1] + ") from "
                + doc[0] + " k where " + cond + " group by k." + doc[2] + ", k." + doc[3] + ")";
            return Rows.Query(conn, sql, args.ToArray(), Unlimited);
        }

        // docs[start ..] 一段单据上 Auto_ID 大于 floor 的「之后的处理」：{ t, c, cno, a }。
        public static List<Dictionary<string, object>> Later(object conn, string flag, int floor, List<string[]> docs, int start)
        {
            List<object> args = new List<object>();
            args.Add(flag);
            args.Add(floor);
            string co = Pairs(docs, start, args, "(d.cCoVouchType=? and d.cCoVouchID=?)", false);
            args.Add(flag);
            args.Add(floor);
            string own = Pairs(docs, start, args, "(d.cVouchType=? and d.cVouchID=?)", false);
            string sql = LaterSql.Replace("{D}", WriteoffSql.Detail(flag)).Replace("{BLOCK}", UnwriteoffSql.Blocker)
                .Replace("{CO}", co).Replace("{OWN}", own);
            return Rows.Query(conn, sql, args.ToArray(), Unlimited);
        }

        // docs[start ..] 最多 DocChunk 张，每张一组 pair 条件（or 连起来），参数（类型、单号，codeFirst 时单号在前）追加到 args。
        static string Pairs(List<string[]> docs, int start, List<object> args, string pair, bool codeFirst)
        {
            StringBuilder cond = new StringBuilder();
            for (int i = start; i < docs.Count && i < start + UnwriteoffSql.DocChunk; i++)
            {
                cond.Append(cond.Length > 0 ? " or " : string.Empty).Append(pair);
                args.Add(codeFirst ? docs[i][1] : docs[i][0]);
                args.Add(codeFirst ? docs[i][0] : docs[i][1]);
            }
            return cond.ToString();
        }

        static string InList(int count)
        {
            StringBuilder list = new StringBuilder();
            for (int i = 0; i < count; i++)
            {
                list.Append(i > 0 ? "," : string.Empty).Append('?');
            }
            return list.ToString();
        }
    }
}
