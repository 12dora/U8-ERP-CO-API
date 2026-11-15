using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 明细 op 解析，以及克隆新行时要清掉的旧列。
    internal static partial class StockEdit
    {

        static List<EditOp> Parse(List<object> rows, object[] lines, string lineCol)
        {
            List<EditOp> ops = new List<EditOp>();
            int deletes = 0;
            int adds = 0;
            if (lines == null)
            {
                return ops;
            }
            for (int i = 0; i < lines.Length; i++)
            {
                EditOp op = One(rows, lines[i], lineCol);
                ops.Add(op);
                if (op.Op == "delete")
                {
                    deletes = deletes + 1;
                }
                if (op.Op == "add")
                {
                    adds = adds + 1;
                }
            }
            if (rows.Count > 0 && deletes >= rows.Count && adds == 0)
            {
                throw new BridgeException(400, "bad_request", "不能删除全部明细");
            }
            return ops;
        }

        static EditOp One(List<object> rows, object raw, string lineCol)
        {
            Dictionary<string, object> line = raw as Dictionary<string, object>;
            if (line == null)
            {
                throw new BridgeException(400, "bad_request", "表体行不是对象");
            }
            string op = Text(line, "op");
            EditOp item = new EditOp();
            item.Op = op;
            item.Fields = FieldsOf(line);
            if (op == "add")
            {
                return item;
            }
            if (op != "update" && op != "delete")
            {
                throw new BridgeException(400, "bad_request", "op 只能是 add、update 或 delete");
            }
            int id = AsId(Raw(line, "line_id"));
            item.Target = Find(rows, lineCol, id);
            if (item.Target == null)
            {
                throw new BridgeException(400, "bad_request", "明细行不存在");
            }
            return item;
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

        static object Find(List<object> rows, string lineCol, int id)
        {
            if (id <= 0)
            {
                return null;
            }
            for (int i = 0; i < rows.Count; i++)
            {
                if (SameId(DomRows.Get(rows[i], lineCol), id))
                {
                    return rows[i];
                }
            }
            return null;
        }

        static int NextRow(List<object> rows)
        {
            int next = rows.Count + 1;
            for (int i = 0; i < rows.Count; i++)
            {
                int n;
                if (int.TryParse(DomRows.Get(rows[i], "irowno").Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out n) && n >= next)
                {
                    next = n + 1;
                }
            }
            return next;
        }

        static void Release(List<object> nodes)
        {
            if (nodes == null)
            {
                return;
            }
            for (int i = 0; i < nodes.Count; i++)
            {
                ComUtil.ReleaseOne(nodes[i]);
            }
        }

        static void RefuseSource(Dictionary<string, object> head)
        {
            if (StockCo.FromTransfer(head))
            {
                throw new BridgeException(409, "state_mismatch", "调拨生成的单据不能修改");
            }
            string source = StockMsg.Col(head, "cSource");
            if (source.Length > 0 && source != "库存")
            {
                throw new BridgeException(409, "state_mismatch", "只能修改来源为库存的单据");
            }
        }

        static void Require(VoucherKind kind)
        {
            if (kind == null)
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持修改");
            }
            // 32：来源库存的销售出库单，只经 StockEditSrc 闸门后进来。
            string st = kind.StType;
            if (st != "08" && st != "09" && st != "12" && st != "01" && st != "32")
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持修改");
            }
        }

        // 按其他入出库的行口径（iQuantity / iNum、单位成本与金额）改的类型：08、09，以及来源库存的销售出库单 32。
        static bool EightLike(VoucherKind kind)
        {
            return kind.StType == "08" || kind.StType == "09" || kind.StType == "32";
        }

        static string LineCol(VoucherKind kind)
        {
            if (kind.LineIdColumn != null && kind.LineIdColumn.Length > 0)
            {
                return kind.LineIdColumn;
            }
            return "AutoID";
        }

        static bool SameId(string text, int id)
        {
            int got;
            if (int.TryParse((text ?? "").Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out got))
            {
                return got == id;
            }
            decimal num;
            if (!StockUnits.Dec(text, out num))
            {
                return false;
            }
            return num == id;
        }

        static int AsId(object value)
        {
            int id = CoRows.AsId(value);
            if (id > 0)
            {
                return id;
            }
            decimal num;
            if (StockUnits.Dec(Values.Text(value), out num) && num == decimal.Truncate(num) && num > 0m && num <= int.MaxValue)
            {
                return (int)num;
            }
            return 0;
        }

        static object Raw(Dictionary<string, object> map, string name)
        {
            if (map == null)
            {
                return null;
            }
            foreach (KeyValuePair<string, object> kv in map)
            {
                if (Same(kv.Key, name))
                {
                    return kv.Value;
                }
            }
            return null;
        }

        static string Text(Dictionary<string, object> map, string name)
        {
            return Values.Text(Raw(map, name)).Trim();
        }

        static bool Sent(Dictionary<string, object> map, string name)
        {
            if (map == null)
            {
                return false;
            }
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

        // 克隆行只留表头外键、行号、editprop。调拨再留 bcosting。其余等调用方字段和算出的单价金额写入。
        static void KeepOnly(object row, bool eight)
        {
            object attrs = null;
            try
            {
                attrs = ComUtil.Get(row, "attributes");
                List<string> drop = DropNames(attrs, eight);
                for (int i = 0; i < drop.Count; i++)
                {
                    ComUtil.Call(row, "removeAttribute", new object[] { drop[i] });
                }
            }
            finally
            {
                ComUtil.ReleaseOne(attrs);
            }
        }

        static List<string> DropNames(object attrs, bool eight)
        {
            List<string> drop = new List<string>();
            if (attrs == null)
            {
                return drop;
            }
            int n = Convert.ToInt32(ComUtil.Get(attrs, "length"));
            for (int i = 0; i < n; i++)
            {
                object attr = ComUtil.Call(attrs, "item", new object[] { i });
                try
                {
                    string name = Values.Text(ComUtil.Get(attr, "nodeName"));
                    if (!KeepCol(name, eight))
                    {
                        drop.Add(name);
                    }
                }
                finally
                {
                    ComUtil.ReleaseOne(attr);
                }
            }
            return drop;
        }

        static bool KeepCol(string name, bool eight)
        {
            if (Same(name, "id") || Same(name, "irowno") || Same(name, "editprop"))
            {
                return true;
            }
            return !eight && Same(name, "bcosting");
        }

        sealed class EditOp
        {
            public string Op;
            public Dictionary<string, object> Fields;
            public object Target;
        }

    }
}
