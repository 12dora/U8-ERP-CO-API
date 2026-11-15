using System;
using System.Collections.Generic;

namespace U8Co
{
    // 历史发货单表头修正的下游部分：收集销售出库单（rdrecord32）、销售发票（SaleBillVouch）的事实（同一事务里 UPDLOCK 锁表头），
    // 交给 HeadFixPlan.DecideAll 判断，再按结论逐张 UPDATE，发票的业务员同步改 Ar_Detail 里属于该发票的行，最后回读。
    // 出库单、发票通过明细行的 iDLsID 关联发货单；Ar_Detail 的发票行用 cVouchType = SaleBillVouch.cVouchType、
    // cVouchID = SaleBillVouch.cSBVCode 定位（含 26 / 27 发票行及其 9M / 9N / BZ / 9J 调整行），收款、核销行是别的单据，不碰。
    // 表名列名都是本类常量，调用方的值只进 ADO 参数。
    internal static class VoucherHeadFixDown
    {
        const string OutTable = HeadFixUndo.OutTable;
        const string InvTable = HeadFixUndo.InvTable;
        const int Max = 500;
        const string OutIdsSql = "SELECT DISTINCT r.ID AS id FROM rdrecords32 r JOIN DispatchLists l ON l.iDLsID=r.iDLsID"
            + " WHERE l.DLID=? ORDER BY r.ID";
        internal const string OutSql = "SELECT LTRIM(RTRIM(ISNULL(cCode,''))) AS code, LTRIM(RTRIM(ISNULL(caddcode,''))) AS caddcode,"
            + " LTRIM(RTRIM(ISNULL(cPersonCode,''))) AS cPersonCode FROM rdrecord32 WITH (UPDLOCK, ROWLOCK) WHERE ID=?";
        const string OutForeignSql = "SELECT COUNT(*) AS n FROM rdrecords32 r2 LEFT JOIN DispatchLists l2 ON l2.iDLsID=r2.iDLsID"
            + " WHERE r2.ID=? AND (l2.DLID IS NULL OR l2.DLID<>?)";
        const string InvIdsSql = "SELECT DISTINCT b.SBVID AS id FROM SaleBillVouchs b JOIN DispatchLists l ON l.iDLsID=b.iDLsID"
            + " WHERE l.DLID=? ORDER BY b.SBVID";
        internal const string InvSql = "SELECT LTRIM(RTRIM(ISNULL(cSBVCode,''))) AS code, LTRIM(RTRIM(ISNULL(cVouchType,''))) AS vt,"
            + " LTRIM(RTRIM(ISNULL(caddcode,''))) AS caddcode, LTRIM(RTRIM(ISNULL(cPersonCode,''))) AS cPersonCode"
            + " FROM SaleBillVouch WITH (UPDLOCK, ROWLOCK) WHERE SBVID=?";
        // 发票明细里没有来源发货单（iDLsID 为空或对不上）的行数。
        const string InvForeignSql = "SELECT COUNT(*) AS n FROM SaleBillVouchs b LEFT JOIN DispatchLists l ON l.iDLsID=b.iDLsID"
            + " WHERE b.SBVID=? AND l.DLID IS NULL";
        // 发票的其他来源发货单（不含本张）的当前值。
        const string InvOthersSql = "SELECT DISTINCT h.DLID AS id, LTRIM(RTRIM(ISNULL(h.caddcode,''))) AS caddcode,"
            + " LTRIM(RTRIM(ISNULL(h.cPersonCode,''))) AS cPersonCode FROM SaleBillVouchs b"
            + " JOIN DispatchLists l ON l.iDLsID=b.iDLsID JOIN DispatchList h ON h.DLID=l.DLID"
            + " WHERE b.SBVID=? AND h.DLID<>? ORDER BY h.DLID";
        // 发票在 Ar_Detail 里业务员等于改前值的行（含 26 / 27 发票行及其 9M / 9N / BZ / 9J 调整行），加行锁取 Auto_ID。
        const string ArIdsSql = "SELECT Auto_ID AS id FROM Ar_Detail WITH (UPDLOCK, ROWLOCK)"
            + " WHERE cVouchType=? AND cVouchID=? AND LTRIM(RTRIM(ISNULL(cPerson,'')))=? ORDER BY Auto_ID";
        const string ArUpdateSql = "UPDATE Ar_Detail SET cPerson=? WHERE Auto_ID=? AND LTRIM(RTRIM(ISNULL(cPerson,'')))=?";
        const string ArPersonSql = "SELECT LTRIM(RTRIM(ISNULL(cPerson,''))) AS cPerson FROM Ar_Detail WHERE Auto_ID=?";

        // 收集事实并判断；返回的行按出库单、发票（每张发票的业务员改了时紧跟一行 Ar_Detail）排列。
        public static List<HeadFixRow> Plan(object conn, int dlid, List<HeadFixField> changing)
        {
            List<HeadFixDoc> docs = LoadOuts(conn, dlid);
            docs.AddRange(LoadInvoices(conn, dlid, changing));
            return HeadFixPlan.DecideAll(docs, changing);
        }

        static List<HeadFixDoc> LoadOuts(object conn, int dlid)
        {
            List<HeadFixDoc> docs = new List<HeadFixDoc>();
            foreach (int id in Ids(conn, OutIdsSql, dlid))
            {
                Dictionary<string, object> row = Need(conn, OutSql, id);
                HeadFixDoc doc = Doc(OutTable, id, row);
                doc.Foreign = Count(conn, OutForeignSql, new object[] { id, dlid }) > 0;
                docs.Add(doc);
            }
            return docs;
        }

        static List<HeadFixDoc> LoadInvoices(object conn, int dlid, List<HeadFixField> changing)
        {
            List<HeadFixDoc> docs = new List<HeadFixDoc>();
            HeadFixField person = Find(changing, VoucherHeadFix.PersonKey);
            foreach (int id in Ids(conn, InvIdsSql, dlid))
            {
                Dictionary<string, object> row = Need(conn, InvSql, id);
                HeadFixDoc doc = Doc(InvTable, id, row);
                doc.VouchType = GlSql.Col(row, "vt");
                doc.Foreign = Count(conn, InvForeignSql, new object[] { id }) > 0;
                foreach (Dictionary<string, object> other in Capped(conn, InvOthersSql, new object[] { id, dlid }, dlid))
                {
                    doc.Others.Add(Values(other));
                }
                if (person != null && doc.VouchType.Length > 0 && doc.Code.Length > 0)
                {
                    foreach (Dictionary<string, object> ar in Capped(conn, ArIdsSql,
                        new object[] { doc.VouchType, doc.Code, (person.Old ?? "").Trim() }, dlid))
                    {
                        doc.ArIds.Add(GlSql.Int(ar, "id"));
                    }
                }
                docs.Add(doc);
            }
            return docs;
        }

        internal static HeadFixDoc Doc(string table, int id, Dictionary<string, object> row)
        {
            HeadFixDoc doc = new HeadFixDoc();
            doc.Table = table;
            doc.Id = id;
            doc.Code = GlSql.Col(row, "code");
            doc.Values = Values(row);
            return doc;
        }

        static Dictionary<string, string> Values(Dictionary<string, object> row)
        {
            Dictionary<string, string> values = new Dictionary<string, string>();
            values[VoucherHeadFix.AddrKey] = GlSql.Col(row, "caddcode");
            values[VoucherHeadFix.PersonKey] = GlSql.Col(row, "cPersonCode");
            return values;
        }

        // 查询结果达到上限就报错，绝不静默截断（截断会让「其余来源都已改」的判断出错）。
        static List<Dictionary<string, object>> Capped(object conn, string sql, object[] args, int dlid)
        {
            List<Dictionary<string, object>> raw = Rows.Query(conn, sql, args, Max);
            if (raw.Count >= Max)
            {
                throw new BridgeException(409, "too_many", "发货单 " + dlid.ToString() + " 的下游单据过多，无法处理");
            }
            return raw;
        }

        static List<int> Ids(object conn, string sql, int dlid)
        {
            List<Dictionary<string, object>> raw = Capped(conn, sql, new object[] { dlid }, dlid);
            List<int> ids = new List<int>();
            foreach (Dictionary<string, object> r in raw)
            {
                ids.Add(GlSql.Int(r, "id"));
            }
            return ids;
        }

        internal static Dictionary<string, object> Need(object conn, string sql, int id)
        {
            Dictionary<string, object> row = Rows.One(conn, sql, new object[] { id });
            if (row == null)
            {
                throw new BridgeException(409, "state_mismatch", "下游单据 " + id.ToString() + " 已被他人删除或修改");
            }
            return row;
        }

        internal static int Count(object conn, string sql, object[] args)
        {
            return GlSql.Int(Rows.One(conn, sql, args), "n");
        }

        static HeadFixField Find(List<HeadFixField> fields, string key)
        {
            for (int i = 0; i < fields.Count; i++)
            {
                if (fields[i].Key == key)
                {
                    return fields[i];
                }
            }
            return null;
        }

        // 按结论写下游：出库单、发票只改 Set 里的字段；Ar_Detail 只改业务员，且仅限旧业务员的行。
        public static void Write(object conn, List<HeadFixRow> plan)
        {
            for (int i = 0; i < plan.Count; i++)
            {
                HeadFixRow row = plan[i];
                if (row.Action != HeadFixRow.Updated)
                {
                    continue;
                }
                if (row.IsAr)
                {
                    WriteAr(conn, row);
                    continue;
                }
                List<object> args = new List<object>();
                string set = HeadFixPlan.SetClause(row.Set, args);
                args.Add(row.Id);
                GlSql.Exec(conn, "UPDATE " + row.Table + " SET " + set + " WHERE " + IdColumn(row.Table) + "=?", args.ToArray());
            }
        }

        // Ar_Detail 逐个 Auto_ID 改，且仍要求业务员是改前值。
        static void WriteAr(object conn, HeadFixRow row)
        {
            HeadFixField person = row.Set[0];
            for (int i = 0; i < row.AutoIds.Count; i++)
            {
                GlSql.Exec(conn, ArUpdateSql, new object[] { person.New, row.AutoIds[i], (person.Old ?? "").Trim() });
            }
        }

        static string IdColumn(string table)
        {
            if (table == OutTable)
            {
                return "ID";
            }
            if (table == InvTable)
            {
                return "SBVID";
            }
            throw new BridgeException(500, "internal", "未知的下游表 " + table);
        }

        // 回读：改过的下游单据各字段等于新值；写过的 Ar_Detail 行业务员都是新值。不一致返回 false（504 由调用方抛）。
        public static bool Confirm(object conn, List<HeadFixRow> plan)
        {
            for (int i = 0; i < plan.Count; i++)
            {
                HeadFixRow row = plan[i];
                if (row.Action != HeadFixRow.Updated)
                {
                    continue;
                }
                bool ok = row.IsAr ? ArCleared(conn, row) : DocMatches(conn, row);
                if (!ok)
                {
                    return false;
                }
            }
            return true;
        }

        static bool ArCleared(object conn, HeadFixRow row)
        {
            for (int i = 0; i < row.AutoIds.Count; i++)
            {
                Dictionary<string, object> now = Rows.One(conn, ArPersonSql, new object[] { row.AutoIds[i] });
                if (now == null || !HeadFixPlan.Same(GlSql.Col(now, "cPerson"), row.Set[0].New))
                {
                    return false;
                }
            }
            return true;
        }

        static bool DocMatches(object conn, HeadFixRow row)
        {
            string sql = row.Table == OutTable ? OutSql : InvSql;
            Dictionary<string, object> now = Rows.One(conn, sql, new object[] { row.Id });
            if (now == null)
            {
                return false;
            }
            for (int i = 0; i < row.Set.Count; i++)
            {
                if (!HeadFixPlan.Same(GlSql.Col(now, row.Set[i].Column), row.Set[i].New))
                {
                    return false;
                }
            }
            return true;
        }
    }
}
