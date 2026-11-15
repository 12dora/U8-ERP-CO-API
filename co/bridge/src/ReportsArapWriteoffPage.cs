using System;
using System.Collections.Generic;

namespace U8Co
{
    // 核销记录查询一页的数据（减少往返）：取页之后一次读出整页各批的行、收付款单 id 和各行余额、
    // 被核销单据表头、各单据上「之后的处理」（WriteoffPageSql），各批的展示和 cancellable 都从这里取。
    // 预读里没有的（例如对方单据是另一张收付款单）照旧逐条查。条件与逐条查相同，结果相同；仍是不加锁的快照。
    internal sealed class WriteoffPageData
    {
        readonly WriteoffListScope _scope;
        readonly Dictionary<string, List<Dictionary<string, object>>> _rows = Map<List<Dictionary<string, object>>>();
        // "类型|单号" → 收付款单 id（0 表示没找到）。
        readonly Dictionary<string, int> _receipts = Map<int>();
        readonly Dictionary<int, Dictionary<int, decimal>> _lines = new Dictionary<int, Dictionary<int, decimal>>();
        // "类型|单号" → 表头 { id, locked }（null 表示没找到）。
        readonly Dictionary<string, Dictionary<string, object>> _heads = Map<Dictionary<string, object>>();
        // "类型|单号" → 该单据上「之后的处理」按核销号的最大 Auto_ID：{ 核销号, Auto_ID }。
        readonly Dictionary<string, List<string[]>> _later = Map<List<string[]>>();
        int _floor;

        WriteoffPageData(WriteoffListScope scope)
        {
            _scope = scope;
        }

        static Dictionary<string, T> Map<T>()
        {
            return new Dictionary<string, T>(StringComparer.OrdinalIgnoreCase);
        }

        internal static string Key(string type, string code)
        {
            return (type ?? string.Empty).TrimEnd() + "|" + (code ?? string.Empty).TrimEnd();
        }

        public static WriteoffPageData Load(WriteoffListScope scope, List<string> nos)
        {
            WriteoffPageData data = new WriteoffPageData(scope);
            if (nos.Count == 0)
            {
                return data;
            }
            int max = ArapUnwriteoffGate.MaxRows + 1;
            foreach (Dictionary<string, object> r in WriteoffPageSql.Batches(scope.Conn, scope.Flag, nos, max))
            {
                string no = CoRows.Col(r, "cno");
                if (!data._rows.ContainsKey(no))
                {
                    data._rows[no] = new List<Dictionary<string, object>>();
                }
                data._rows[no].Add(r);
            }
            data.LoadReceipts();
            data.LoadLines();
            data.LoadHeads();
            data.LoadLater();
            return data;
        }

        // 一个核销号的全部行（最多 MaxRows + 1 行，按 Auto_ID）；取页之后被别人取消了为空。
        public List<Dictionary<string, object>> RowsOf(string no)
        {
            List<Dictionary<string, object>> rows;
            return _rows.TryGetValue(no, out rows) ? rows : new List<Dictionary<string, object>>();
        }

        public int ReceiptId(string type, string code)
        {
            int id;
            string key = Key(type, code);
            if (!_receipts.TryGetValue(key, out id))
            {
                id = UnwriteoffSql.ReceiptId(_scope.Conn, type, code, _scope.Flag);
                _receipts[key] = id;
            }
            return id;
        }

        public Dictionary<int, decimal> Lines(int receiptId)
        {
            if (!_lines.ContainsKey(receiptId))
            {
                _lines[receiptId] = new Dictionary<int, decimal>();
                AddLines(WriteoffPageSql.Lines(_scope.Conn, new List<int> { receiptId }));
            }
            return _lines[receiptId];
        }

        // 表头：收付款单按单号、类型和 AR / AP 查 id；发票、应收应付单按单号和类型查 { id, locked }。
        public Dictionary<string, object> Head(string kind, string vtype, string code)
        {
            string key = Key(vtype, code);
            Dictionary<string, object> head;
            if (_heads.TryGetValue(key, out head))
            {
                return head;
            }
            if (kind == "ar_receipt" || kind == "ap_payment")
            {
                int id = ReceiptId(vtype, code);
                head = id > 0 ? new Dictionary<string, object>() : null;
                if (head != null)
                {
                    head["id"] = ReportsArapWriteoff.Idx(id);
                }
            }
            else
            {
                head = WriteoffListSql.Head(_scope.Conn, kind, code, vtype);
            }
            _heads[key] = head;
            return head;
        }

        // 同 ArapUnwriteoffGate.NothingLater：本批的单据上有核销号不同、Auto_ID 大于本批最大 Auto_ID 的处理。
        // 单据不在预读里（或本批最大 Auto_ID 低于预读下限）时按本批单独查。
        public bool AnyLater(UnwriteoffPlan plan)
        {
            List<string[]> docs = UnwriteoffSql.Docs(plan);
            if (plan.MaxAuto < _floor || !AllLoaded(docs))
            {
                return UnwriteoffSql.LaterStyle(_scope.Conn, plan) != null;
            }
            string self = plan.CancelNo.TrimEnd();
            foreach (string[] doc in docs)
            {
                foreach (string[] mark in _later[Key(doc[0], doc[1])])
                {
                    if (!string.Equals(mark[0].TrimEnd(), self, StringComparison.OrdinalIgnoreCase)
                        && CoRows.AsId(mark[1]) > plan.MaxAuto)
                    {
                        return true;
                    }
                }
            }
            return false;
        }

        bool AllLoaded(List<string[]> docs)
        {
            foreach (string[] doc in docs)
            {
                if (!_later.ContainsKey(Key(doc[0], doc[1])))
                {
                    return false;
                }
            }
            return true;
        }

        // 各批第一行的收付款单（类型、单号）的 id。
        void LoadReceipts()
        {
            List<string[]> docs = new List<string[]>();
            foreach (List<Dictionary<string, object>> rows in _rows.Values)
            {
                string[] doc = new string[] { CoRows.Col(rows[0], "vtype"), CoRows.Col(rows[0], "vcode") };
                if (!_receipts.ContainsKey(Key(doc[0], doc[1])))
                {
                    _receipts[Key(doc[0], doc[1])] = 0;
                    docs.Add(doc);
                }
            }
            for (int start = 0; start < docs.Count; start += UnwriteoffSql.DocChunk)
            {
                foreach (Dictionary<string, object> r in WriteoffPageSql.Receipts(_scope.Conn, _scope.Flag, docs, start))
                {
                    _receipts[Key(CoRows.Col(r, "t"), CoRows.Col(r, "c"))] = CoRows.AsId(CoRows.Col(r, "id"));
                }
            }
        }

        void LoadLines()
        {
            List<int> ids = new List<int>();
            foreach (int id in _receipts.Values)
            {
                if (id > 0 && !ids.Contains(id))
                {
                    ids.Add(id);
                    _lines[id] = new Dictionary<int, decimal>();
                }
            }
            for (int start = 0; start < ids.Count; start += UnwriteoffSql.DocChunk)
            {
                int count = Math.Min(UnwriteoffSql.DocChunk, ids.Count - start);
                AddLines(WriteoffPageSql.Lines(_scope.Conn, ids.GetRange(start, count)));
            }
        }

        void AddLines(List<Dictionary<string, object>> rows)
        {
            foreach (Dictionary<string, object> r in rows)
            {
                int id = CoRows.AsId(CoRows.Col(r, "rid"));
                if (!_lines.ContainsKey(id))
                {
                    _lines[id] = new Dictionary<int, decimal>();
                }
                _lines[id][CoRows.AsId(CoRows.Col(r, "line"))] = WriteoffSql.Num(CoRows.Col(r, "rem_f"));
            }
        }

        // 各批被核销单据（不是收付款单自身的行）按单据种类分组，每种一条（或几条）语句。
        void LoadHeads()
        {
            Dictionary<string, List<string[]>> byKind = new Dictionary<string, List<string[]>>(StringComparer.Ordinal);
            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (List<Dictionary<string, object>> rows in _rows.Values)
            {
                foreach (string[] doc in Targets(rows))
                {
                    WriteoffKind kind = WriteoffKind.OfType(_scope.Flag, doc[0]);
                    if (kind == null || WriteoffListSql.Doc(kind.Name) == null || !seen.Add(Key(doc[0], doc[1])))
                    {
                        continue;
                    }
                    if (!byKind.ContainsKey(kind.Name))
                    {
                        byKind[kind.Name] = new List<string[]>();
                    }
                    byKind[kind.Name].Add(doc);
                }
            }
            foreach (KeyValuePair<string, List<string[]>> one in byKind)
            {
                LoadHeads(WriteoffListSql.Doc(one.Key), one.Value);
            }
        }

        void LoadHeads(string[] table, List<string[]> docs)
        {
            foreach (string[] doc in docs)
            {
                _heads[Key(doc[0], doc[1])] = null;
            }
            for (int start = 0; start < docs.Count; start += UnwriteoffSql.DocChunk)
            {
                foreach (Dictionary<string, object> r in WriteoffPageSql.Heads(_scope.Conn, table, docs, start))
                {
                    _heads[Key(CoRows.Col(r, "vtype"), CoRows.Col(r, "code"))] = r;
                }
            }
        }

        // 一批里对方单据不是第一行的收付款单自己的（类型、单号）。
        static List<string[]> Targets(List<Dictionary<string, object>> rows)
        {
            List<string[]> docs = new List<string[]>();
            string receipt = Key(CoRows.Col(rows[0], "vtype"), CoRows.Col(rows[0], "vcode"));
            foreach (Dictionary<string, object> r in rows)
            {
                string[] doc = new string[] { CoRows.Col(r, "cotype"), CoRows.Col(r, "cocode") };
                if (!string.Equals(Key(doc[0], doc[1]), receipt, StringComparison.OrdinalIgnoreCase))
                {
                    docs.Add(doc);
                }
            }
            return docs;
        }

        // 整页各批的收付款单和被核销单据上、Auto_ID 大于各批最大 Auto_ID 之最小值的「之后的处理」。
        void LoadLater()
        {
            List<string[]> docs = new List<string[]>();
            _floor = int.MaxValue;
            foreach (List<Dictionary<string, object>> rows in _rows.Values)
            {
                int max = 0;
                LaterDoc(CoRows.Col(rows[0], "vtype"), CoRows.Col(rows[0], "vcode"), docs);
                foreach (Dictionary<string, object> r in rows)
                {
                    max = Math.Max(max, CoRows.AsId(CoRows.Col(r, "auto")));
                    LaterDoc(CoRows.Col(r, "cotype"), CoRows.Col(r, "cocode"), docs);
                }
                _floor = Math.Min(_floor, max);
            }
            for (int start = 0; start < docs.Count; start += UnwriteoffSql.DocChunk)
            {
                foreach (Dictionary<string, object> r in WriteoffPageSql.Later(_scope.Conn, _scope.Flag, _floor, docs, start))
                {
                    string[] mark = new string[] { CoRows.Col(r, "cno"), CoRows.Col(r, "a") };
                    LaterDoc(CoRows.Col(r, "t"), CoRows.Col(r, "c"), null).Add(mark);
                }
            }
        }

        // 单据的「之后的处理」表（没有就建一个空的；docs 不为 null 时新建的单据加进要查的列表）。
        List<string[]> LaterDoc(string type, string code, List<string[]> docs)
        {
            string key = Key(type, code);
            List<string[]> marks;
            if (!_later.TryGetValue(key, out marks))
            {
                marks = new List<string[]>();
                _later[key] = marks;
                if (docs != null)
                {
                    docs.Add(new string[] { type, code });
                }
            }
            return marks;
        }
    }
}
