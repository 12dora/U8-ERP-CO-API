using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 把请求写进 Load 回来的 DOM：表头 editprop=M 加备注和自定义项；表体按 op 标 M / D。
    // 返回本次改了数量或删掉的行（AutoID → 新数量，删除为 0），交给回写核对。
    internal static partial class StockEditSrc
    {
        const string LineCol = "AutoID";

        static Dictionary<string, decimal> Apply(object conn, EditReq req, StockLoaded loaded)
        {
            List<object> heads = DomRows.RowsOf(loaded.Head);
            List<object> rows = null;
            try
            {
                if (heads.Count == 0)
                {
                    throw new BridgeException(404, "not_found", "单据不存在");
                }
                List<string> headNames = DomRows.Schema(loaded.Head);
                StockDom.SetCell(loaded.Head, heads[0], "editprop", "M", headNames);
                WriteFields(loaded.Head, heads[0], true, req.Head, headNames);
                rows = DomRows.RowsOf(loaded.Body);
                LineRun run = new LineRun();
                run.Conn = conn;
                run.Req = req;
                run.Dom = loaded.Body;
                run.Schema = DomRows.Schema(loaded.Body);
                run.Plan = new Dictionary<string, decimal>(StringComparer.Ordinal);
                for (int i = 0; i < rows.Count; i++)
                {
                    StockDom.SetCell(run.Dom, rows[i], "editprop", "", run.Schema);
                }
                ApplyLines(run, rows);
                return run.Plan;
            }
            finally
            {
                Release(heads);
                Release(rows);
            }
        }

        static void ApplyLines(LineRun run, List<object> rows)
        {
            int deletes = 0;
            HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
            for (int i = 0; i < run.Req.Lines.Length; i++)
            {
                Dictionary<string, object> line = run.Req.Lines[i] as Dictionary<string, object>;
                if (line == null)
                {
                    throw BridgeException.BadField(FieldPath.Item("lines", i), "表体行不是对象");
                }
                try
                {
                    deletes += ApplyLine(run, rows, line, seen, i);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
            if (rows.Count > 0 && deletes >= rows.Count)
            {
                throw BridgeException.BadField("lines", "不能删除全部明细");
            }
        }

        // 一行：删除返回 1，修改返回 0。行内 400 的 field 由调用方补下标。
        static int ApplyLine(LineRun run, List<object> rows, Dictionary<string, object> line, HashSet<string> seen, int i)
        {
            string op = OpOf(line);
            object row = Find(rows, CoRows.AsId(Raw(line, "line_id")));
            if (row == null)
            {
                throw BridgeException.BadField(FieldPath.Item("lines", i) + ".line_id", "明细行不存在");
            }
            if (!seen.Add(RowKey(row)))
            {
                throw BridgeException.BadField(FieldPath.Item("lines", i) + ".line_id", "明细行重复");
            }
            Dictionary<string, object> fields = FieldsOf(line);
            StockPosGuard.RefuseBinnedEdit(run.Req.Bins, row, LineCol, op, fields);
            if (op == "delete")
            {
                RefuseLocked(run.Req);
                StockDom.SetCell(run.Dom, row, "editprop", "D", run.Schema);
                run.Plan[RowKey(row)] = 0m;
                return 1;
            }
            UpdateLine(run, row, fields);
            return 0;
        }

        // op 不分大小写，缺省为 update；add 不支持，其他 400。
        static string OpOf(Dictionary<string, object> line)
        {
            string op = Values.Text(Raw(line, "op")).Trim().ToLowerInvariant();
            if (op.Length == 0)
            {
                return "update";
            }
            if (op == "add")
            {
                throw BridgeException.BadField("lines.op", "该单据类型修改不能新增行");
            }
            if (op != "update" && op != "delete")
            {
                throw BridgeException.BadField("lines.op", "op 只能是 add、update 或 delete");
            }
            return op;
        }

        // 不良品处理单来源的产成品入库单：数量改不了，删行同样拒绝。
        static void RefuseLocked(EditReq req)
        {
            if (req.QtyLocked)
            {
                throw BridgeException.BadField("lines.iquantity", "参照不良品处理单的入库单不能改数量");
            }
        }

        static void UpdateLine(LineRun run, object row, Dictionary<string, object> fields)
        {
            CheckNames(fields, false);
            decimal next = 0m;
            bool qty = Sent(fields, "iquantity");
            if (qty)
            {
                next = NewQty(run.Req, row, fields);
            }
            StockDom.SetCell(run.Dom, row, "editprop", "M", run.Schema);
            WriteFields(run.Dom, row, false, fields, run.Schema);
            if (!qty)
            {
                return;
            }
            run.Plan[RowKey(row)] = next;
            Reprice(run, row, fields);
        }

        // 数量必须大于 0，且不超过行上现有数量（只减不增）。不良品处理单来源的入库单不能改数量。
        static decimal NewQty(EditReq req, object row, Dictionary<string, object> fields)
        {
            RefuseLocked(req);
            decimal next;
            if (!StockUnits.Dec(Values.Text(Raw(fields, "iquantity")).Trim(), out next) || next <= 0m)
            {
                throw BridgeException.BadField("lines.iquantity", "数量必须大于 0");
            }
            decimal prev;
            if (!StockUnits.Dec(DomRows.Get(row, "iQuantity"), out prev))
            {
                throw new BridgeException(409, "state_mismatch", "明细行数量无法读取");
            }
            if (next > prev)
            {
                throw BridgeException.BadField("lines.iquantity", "修改只能减少数量");
            }
            return next;
        }

        // 数量变了：辅数量按换算率重算；采购入库按行上单价口径重算税价（StockDom.PurTouch），
        // 其他库存单据有单位成本就 iPrice = round(数量 × iUnitCost, 2)（同 08/09 修改）。
        static void Reprice(LineRun run, object row, Dictionary<string, object> fields)
        {
            StockUnits.UnitJob job = new StockUnits.UnitJob();
            job.QtyName = "iQuantity";
            job.NumName = "iNum";
            job.Force = true;
            job.Schema = run.Schema;
            StockUnits.ApplyDom(run.Conn, run.Dom, row, job);
            if (StockDom.PurType(run.Req.Kind))
            {
                StockDom.PurTouch(run.Conn, run.Dom, row, fields, run.Schema);
                return;
            }
            StockUnits.FixPriceSent(run.Dom, row, false, false, true, run.Schema);
        }

        static void WriteFields(object dom, object row, bool head, Dictionary<string, object> fields, List<string> schema)
        {
            if (fields == null)
            {
                return;
            }
            CheckNames(fields, head);
            foreach (KeyValuePair<string, object> kv in fields)
            {
                string name = Canonical(schema, kv.Key);
                if (name == null)
                {
                    throw BridgeException.BadField(FieldPath.Join(FieldPath.Side(head), kv.Key), "未知字段 " + kv.Key);
                }
                StockDom.SetCell(dom, row, name, CellOf(kv.Key, kv.Value, FieldPath.Side(head)), schema);
            }
        }

        static void CheckNames(Dictionary<string, object> fields, bool head)
        {
            foreach (KeyValuePair<string, object> kv in fields)
            {
                if (!SrcField(head, kv.Key))
                {
                    throw BridgeException.BadField(FieldPath.Join(FieldPath.Side(head), kv.Key), "不能修改字段 " + kv.Key)
                        .WithHint(FieldPath.WritableHint);
                }
            }
        }

        // null 清空文本字段（写空串），数量 null 400；字符串原样，数字按不变区域格式；布尔等其他类型 400。
        static string CellOf(string key, object value, string at)
        {
            if (value == null || value is DBNull)
            {
                if (Same(key, "iquantity"))
                {
                    throw BridgeException.BadField(FieldPath.Join(at, key), "字段 " + key + " 不能为 null");
                }
                return "";
            }
            string text = value as string;
            if (text != null)
            {
                return text;
            }
            if (value is bool || !(value is IConvertible))
            {
                throw BridgeException.BadField(FieldPath.Join(at, key), "字段 " + key + " 只能是字符串或数字");
            }
            return Convert.ToString(value, CultureInfo.InvariantCulture);
        }

        static string Canonical(List<string> schema, string key)
        {
            for (int i = 0; schema != null && i < schema.Count; i++)
            {
                if (string.Equals(schema[i], key, StringComparison.OrdinalIgnoreCase))
                {
                    return schema[i];
                }
            }
            return null;
        }

        static object Find(List<object> rows, int id)
        {
            if (id <= 0)
            {
                return null;
            }
            string want = id.ToString(CultureInfo.InvariantCulture);
            for (int i = 0; i < rows.Count; i++)
            {
                if (RowKey(rows[i]) == want)
                {
                    return rows[i];
                }
            }
            return null;
        }

        // 行主键统一成整数文本（DOM 里可能是 "123" 或 "123.0"），与回写核对读出的 AutoID 对齐。
        static string RowKey(object row)
        {
            decimal num;
            if (!StockUnits.Dec(DomRows.Get(row, LineCol), out num))
            {
                return "";
            }
            return decimal.Truncate(num).ToString(CultureInfo.InvariantCulture);
        }

        static Dictionary<string, object> FieldsOf(Dictionary<string, object> line)
        {
            Dictionary<string, object> fields = new Dictionary<string, object>();
            foreach (KeyValuePair<string, object> kv in line)
            {
                if (Same(kv.Key, "op") || Same(kv.Key, "line_id"))
                {
                    continue;
                }
                fields[kv.Key] = kv.Value;
            }
            return fields;
        }

        static object Raw(Dictionary<string, object> map, string name)
        {
            foreach (KeyValuePair<string, object> kv in map)
            {
                if (Same(kv.Key, name))
                {
                    return kv.Value;
                }
            }
            return null;
        }

        static bool Sent(Dictionary<string, object> map, string name)
        {
            foreach (KeyValuePair<string, object> kv in map)
            {
                if (Same(kv.Key, name))
                {
                    return true;
                }
            }
            return false;
        }

        static bool Same(string a, string b)
        {
            return string.Equals(a, b, StringComparison.OrdinalIgnoreCase);
        }

        static void Release(List<object> nodes)
        {
            for (int i = 0; nodes != null && i < nodes.Count; i++)
            {
                ComUtil.ReleaseOne(nodes[i]);
            }
        }

        sealed class LineRun
        {
            public object Conn;
            public EditReq Req;
            public object Dom;
            public List<string> Schema;
            public Dictionary<string, decimal> Plan;
        }
    }
}
