using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 下游连带（cascade=true）：在订单的同一事务里找出这张订单的下游单据，收集每张单据的全部来源订单，
    // 按 VoucherSoDefinesCascade.DecideDown 判断，预演到此为止，否则逐张按 ufts 乐观锁只改请求的各列，回读核对。
    // 来源解析与表体关联见 VoucherSoDefinesLinks。加锁顺序固定（都持有到提交）：本订单表头（调用方已锁）→ 发现和收来源时的
    // 表体关联行及其订单行（UPDLOCK, HOLDLOCK，按类型、单据主键；每张来源订单超过 200 张先 409）→ 其它来源订单表头（UPDLOCK，按 ID 升序）
    // → 下游表头（UPDLOCK，按类型、主键），锁表头后重读表体：已不指向本订单的单据剔除，来源集合变了 409。
    // 只改表头的请求列：不写修改人、审核人等任何其它列，不碰表体、金额、数量、日期、状态、各 History 表和总账。
    // 表名列名都是常量或已校验的列键，调用方的值只进 ADO 参数。
    internal static class VoucherSoDefinesDown
    {
        // 一张订单最多连带的下游单据张数（三类合计）；超过时 409 too_many_downstream，什么都不写。
        internal const int Cap = 200;
        // 一张下游单据最多读的表体行数；超过同样 409（绝不截断）。
        internal const int LineCap = 5000;
        // 一张下游单据最多的不同来源订单数（正的订单 ID，不含无来源 0）；超过同样 409，在锁其它订单和任何写入之前。
        internal const int SourceCap = 200;

        internal static readonly SoDownKind[] Kinds = VoucherSoDefinesLinks.Kinds;

        // 三张表头表的自定义项列（类型、列宽）和 cLocker、ufts 列是否存在（按表名、列名取，不区分大小写）。
        static readonly string ColSql = "SELECT OBJECT_NAME(c.object_id) AS tbl, c.name AS col, " + VoucherSoDefinesSql.TextExpr + ", "
            + VoucherSoDefinesSql.LenExpr + " FROM sys.columns c"
            + " WHERE c.object_id IN (OBJECT_ID(N'DispatchList'), OBJECT_ID(N'rdrecord32'), OBJECT_ID(N'SaleBillVouch'))"
            + " AND c.name IN (" + VoucherSoDefinesSql.DefineList + ",'cLocker','ufts')";
        const string TsCond = " AND ufts=CONVERT(binary(8), CONVERT(bigint, ?))";

        // 找出下游单据（超过 Cap 先拒），核对下游列（缺列或非文本 400），收来源，锁其它来源订单，再逐张锁表头复查、判断，
        // 最后核对下游列宽（超出 400）。在订单写入之前调用：本单的来源按写后值算（DecideDown）。
        public static List<SoDownDoc> Plan(object conn, SoDefinesAsk ask, string user)
        {
            List<SoDownDoc> found = Find(conn, ask.Id);
            Dictionary<string, SoDefinesColumn> cols = Columns(conn);
            VoucherSoDefinesCascade.CheckColumns(ask, found, cols);
            foreach (SoDownDoc doc in found)
            {
                bool mentions;
                doc.SourceIds = ReadSources(conn, doc, ask.Id, out mentions);
            }
            Dictionary<int, SoDownSource> orders = LockOrders(conn, found, ask);
            List<SoDownDoc> docs = new List<SoDownDoc>();
            foreach (SoDownDoc doc in found)
            {
                if (Lock(conn, doc, cols, ask))
                {
                    Attach(doc, orders, ask.Id);
                    VoucherSoDefinesCascade.DecideDown(ask, doc, user);
                    docs.Add(doc);
                }
            }
            VoucherSoDefinesCascade.CheckWidths(ask, docs);
            return docs;
        }

        static List<SoDownDoc> Find(object conn, int soId)
        {
            List<SoDownDoc> found = new List<SoDownDoc>();
            foreach (SoDownKind kind in Kinds)
            {
                string sql = VoucherSoDefinesLinks.FindSql(kind);
                foreach (Dictionary<string, object> row in Rows.Query(conn, sql, VoucherSoDefinesLinks.FindArgs(kind, soId), Cap + 1))
                {
                    SoDownDoc doc = new SoDownDoc();
                    doc.Kind = kind;
                    doc.Id = GlSql.Int(row, "id");
                    found.Add(doc);
                }
                CheckCap(found.Count, "销售订单 " + soId.ToString(CultureInfo.InvariantCulture) + " 的下游单据");
            }
            return found;
        }

        // 读单据的全部表体行（加锁）并解析来源：升序、去重的来源订单 ID（0 为无来源）；mentions 是有没有行指向 soId。
        static List<int> ReadSources(object conn, SoDownDoc doc, int soId, out bool mentions)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, VoucherSoDefinesLinks.LinesSql(doc.Kind),
                new object[] { doc.Id }, LineCap + 1);
            if (rows.Count > LineCap)
            {
                throw new BridgeException(409, "too_many_downstream", "下游单据 " + doc.Id.ToString(CultureInfo.InvariantCulture)
                    + " 的表体超过 " + LineCap.ToString(CultureInfo.InvariantCulture) + " 行，不能连带更新");
            }
            List<SoDownLine> lines = new List<SoDownLine>();
            foreach (Dictionary<string, object> row in rows)
            {
                lines.Add(VoucherSoDefinesLinks.Line(row));
            }
            List<int> ids = VoucherSoDefinesLinks.SourcesOf(lines, soId, out mentions);
            CheckSourceCap(ids, doc.Id);
            return ids;
        }

        // 不同来源订单（正 ID）超过 SourceCap：409 too_many_downstream（无来源 0 不计）。
        internal static void CheckSourceCap(List<int> ids, int docId)
        {
            int count = 0;
            foreach (int sid in ids)
            {
                if (sid > 0)
                {
                    count++;
                }
            }
            if (count > SourceCap)
            {
                throw new BridgeException(409, "too_many_downstream", "下游单据 " + docId.ToString(CultureInfo.InvariantCulture)
                    + " 的来源订单超过 " + SourceCap.ToString(CultureInfo.InvariantCulture) + " 张，不能连带更新");
            }
        }

        // 其它来源订单的表头读句：请求各列（SO_SOMain 上的列已由 VoucherSoDefinesSql.CheckColumns 核对过）。
        internal static string OrderSql(List<string> keys)
        {
            string select = VoucherSoDefinesSql.Select(keys, null);
            return "SELECT " + select.Substring(0, select.Length - 1) + " FROM SO_SOMain WITH (UPDLOCK, ROWLOCK) WHERE ID=?";
        }

        // 其它来源订单的表头按 ID 升序加 UPDLOCK（持有到提交），取请求各列当前值；订单行不在了的不进字典（按无来源算）。
        static Dictionary<int, SoDownSource> LockOrders(object conn, List<SoDownDoc> docs, SoDefinesAsk ask)
        {
            SortedSet<int> ids = new SortedSet<int>();
            foreach (SoDownDoc doc in docs)
            {
                foreach (int sid in doc.SourceIds)
                {
                    if (sid > 0 && sid != ask.Id)
                    {
                        ids.Add(sid);
                    }
                }
            }
            Dictionary<int, SoDownSource> map = new Dictionary<int, SoDownSource>();
            string sql = OrderSql(ask.Keys);
            foreach (int id in ids)
            {
                Dictionary<string, object> row = Rows.One(conn, sql, new object[] { id });
                if (row != null)
                {
                    SoDownSource s = new SoDownSource();
                    s.Id = id;
                    foreach (string key in ask.Keys)
                    {
                        s.Values[key] = VoucherSoDefinesSql.Raw(row, key);
                    }
                    map[id] = s;
                }
            }
            return map;
        }

        // 来源 ID → 来源（带请求各列当前值）；本订单按目标值算（DecideDown 里），找不到表头的按无来源（0）。
        internal static void Attach(SoDownDoc doc, Dictionary<int, SoDownSource> orders, int soId)
        {
            doc.Sources.Clear();
            foreach (int sid in doc.SourceIds)
            {
                SoDownSource s;
                if (sid == soId)
                {
                    s = new SoDownSource();
                    s.Id = sid;
                }
                else if (sid <= 0 || !orders.TryGetValue(sid, out s))
                {
                    s = new SoDownSource();
                }
                doc.Sources.Add(s);
            }
        }

        // 结果达到上限就拒，绝不静默截断（截断会让「来源一致」的判断出错）。
        internal static void CheckCap(int count, string what)
        {
            if (count > Cap)
            {
                throw new BridgeException(409, "too_many_downstream", what + "超过 " + Cap.ToString(CultureInfo.InvariantCulture)
                    + " 张，不能连带更新");
            }
        }

        // 键 "表名.列名"（不区分大小写）→ 列；cLocker、ufts 只看在不在。
        static Dictionary<string, SoDefinesColumn> Columns(object conn)
        {
            Dictionary<string, SoDefinesColumn> map = new Dictionary<string, SoDefinesColumn>(StringComparer.OrdinalIgnoreCase);
            foreach (Dictionary<string, object> row in Rows.Query(conn, ColSql, new object[0], 3 * (VoucherSoDefines.MaxColumn + 2) + 6))
            {
                SoDefinesColumn c = new SoDefinesColumn();
                c.Name = GlSql.Col(row, "col");
                c.Text = GlSql.Int(row, "txt") == 1;
                c.Limit = GlSql.Int(row, "col_len");
                map[GlSql.Col(row, "tbl") + "." + c.Name] = c;
            }
            return map;
        }

        internal static bool Has(Dictionary<string, SoDefinesColumn> cols, SoDownKind kind, string col)
        {
            return cols.ContainsKey(kind.Table + "." + col);
        }

        // 表头读句：请求各列按列键取别名；没有 cLocker / ufts 列的表按空串取（不引用不存在的列）。
        internal static string HeadSql(SoDownKind kind, List<string> keys, bool locker, bool ts)
        {
            return "SELECT LTRIM(RTRIM(ISNULL(" + kind.CodeCol + ",''))) AS code, " + VoucherSoDefinesSql.Select(keys, null) + " "
                + (locker ? "LTRIM(RTRIM(ISNULL(cLocker,'')))" : "''") + " AS locker, "
                + (ts ? "CONVERT(varchar(20), CONVERT(bigint, ufts))" : "''") + " AS ts FROM " + kind.Table
                + " WITH (UPDLOCK, ROWLOCK) WHERE " + kind.Key + "=?";
        }

        // 锁表头并复查：读不到 409；表体已不指向本订单返回 false（剔除）；来源集合与先前不同 409。
        static bool Lock(object conn, SoDownDoc doc, Dictionary<string, SoDefinesColumn> cols, SoDefinesAsk ask)
        {
            string sql = HeadSql(doc.Kind, ask.Keys, Has(cols, doc.Kind, "cLocker"), Has(cols, doc.Kind, "ufts"));
            Dictionary<string, object> head = Rows.One(conn, sql, new object[] { doc.Id });
            if (head == null)
            {
                throw new BridgeException(409, "state_mismatch", "下游单据 " + doc.Id.ToString(CultureInfo.InvariantCulture)
                    + " 已被他人删除或修改");
            }
            Fill(doc, head, ask.Keys);
            foreach (string key in ask.Keys)
            {
                SoDefinesColumn c;
                doc.Limits[key] = cols.TryGetValue(doc.Kind.Table + "." + VoucherSoDefines.Column(key), out c) ? c.Limit : 0;
            }
            bool mentions;
            List<int> now = ReadSources(conn, doc, ask.Id, out mentions);
            if (!mentions)
            {
                return false;
            }
            if (!VoucherSoDefinesLinks.SameIds(now, doc.SourceIds))
            {
                throw new BridgeException(409, "state_mismatch", "下游单据 " + doc.Code + " 的来源订单在处理中变了");
            }
            return true;
        }

        internal static SoDownDoc Fill(SoDownDoc doc, Dictionary<string, object> head, List<string> keys)
        {
            doc.Code = GlSql.Col(head, "code");
            foreach (string key in keys)
            {
                doc.Values[key] = VoucherSoDefinesSql.Raw(head, key);
            }
            doc.Locker = GlSql.Col(head, "locker");
            doc.Ts = GlSql.Col(head, "ts");
            return doc;
        }

        // UPDATE 句：只写请求的各列；有行版本时带 ufts 条件。
        internal static string UpdateSql(SoDownKind kind, List<string> keys, bool ts)
        {
            List<string> parts = new List<string>();
            foreach (string key in keys)
            {
                parts.Add(VoucherSoDefines.Column(key) + "=?");
            }
            return "UPDATE " + kind.Table + " SET " + string.Join(", ", parts.ToArray()) + " WHERE " + kind.Key + "=?"
                + (ts ? TsCond : "");
        }

        // 逐张写结论为 updated 的单据并回读：请求各列等于目标才算；不符时行版本没变是别人在中间改了（409），变了是结果未知（504）。
        public static void Write(object conn, SoDefinesAsk ask, List<SoDownDoc> docs)
        {
            foreach (SoDownDoc doc in docs)
            {
                if (doc.Result != VoucherSoDefinesCascade.Updated)
                {
                    continue;
                }
                bool ts = doc.Ts.Length > 0;
                List<object> args = new List<object>();
                foreach (string key in ask.Keys)
                {
                    args.Add(ask.Values[key]);
                }
                args.Add(doc.Id);
                if (ts)
                {
                    args.Add(doc.Ts);
                }
                GlSql.Exec(conn, UpdateSql(doc.Kind, ask.Keys, ts), args.ToArray());
                Confirm(conn, ask, doc, ts);
            }
        }

        static void Confirm(object conn, SoDefinesAsk ask, SoDownDoc doc, bool ts)
        {
            Dictionary<string, object> head = Rows.One(conn, HeadSql(doc.Kind, ask.Keys, false, ts), new object[] { doc.Id });
            SoDownDoc after = null;
            if (head != null)
            {
                after = new SoDownDoc();
                after.Kind = doc.Kind;
                after.Id = doc.Id;
                Fill(after, head, ask.Keys);
            }
            if (after != null && VoucherSoDefines.AllExact(ask, after.Values))
            {
                return;
            }
            if (after != null && ts && after.Ts == doc.Ts)
            {
                throw new BridgeException(409, "state_mismatch", "下游单据 " + doc.Code + " 已被他人修改");
            }
            throw new BridgeException(504, "outcome_unknown", "下游单据 " + doc.Code + " 已写入表头自定义项，但回读与写入不一致，结果未知");
        }
    }
}
