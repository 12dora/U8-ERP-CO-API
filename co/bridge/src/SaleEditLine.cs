using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    internal static partial class SaleEdit
    {
        static int ApplyOne(EditBag bag, LineOp op, int rowNo)
        {
            if (op.Op == "delete")
            {
                object row = FindLine(bag.Doms[1], op.LineId);
                if (row == null)
                {
                    throw new BridgeException(400, "bad_request", "明细行不存在");
                }
                DomRows.Set(bag.Doms[1], row, "editprop", "D");
                return rowNo;
            }
            if (op.Op == "update")
            {
                UpdateLine(bag, op);
                return rowNo;
            }
            rowNo = rowNo + 1;
            AddLine(bag, op, rowNo);
            return rowNo;
        }

        static void UpdateLine(EditBag bag, LineOp op)
        {
            object row = FindLine(bag.Doms[1], op.LineId);
            if (row == null)
            {
                throw new BridgeException(400, "bad_request", "明细行不存在");
            }
            Dictionary<string, string> clean = SoFields.Map(op.Fields, bag.Schema, false, "表体行必须是对象");
            RenewUnits(bag, row, op, clean);
            Write(bag.Doms[1], row, clean);
            DomRows.Set(bag.Doms[1], row, "editprop", "M");
            if (!op.Num && !op.Qty && !op.TaxPrice && !op.UnitPrice && !op.TaxRate && !op.Inv)
            {
                return;
            }
            TouchCalc(bag.Co, bag.Doms, row, op);
            row = FindLine(bag.Doms[1], op.LineId);
            if (row == null)
            {
                throw new BridgeException(500, "internal", "模板行数不符");
            }
            DomRows.Set(bag.Doms[1], row, "editprop", "M");
        }

        static void AddLine(EditBag bag, LineOp op, int rowNo)
        {
            Dictionary<string, string> clean = SoFields.Map(op.Fields, bag.Schema, false, "表体行必须是对象");
            object row = AppendClone(bag.Doms[1]);
            int at = DomRows.RowsOf(bag.Doms[1]).Count - 1;
            BlankClone(bag.Doms[1], row, clean);
            List<Dictionary<string, string>> one = new List<Dictionary<string, string>>();
            one.Add(clean);
            SoUnits.Fill(bag.Ctx.Conn, bag.Schema, one, bag.HeadVals);
            Write(bag.Doms[1], row, clean);
            DomRows.Set(bag.Doms[1], row, "irowno", rowNo.ToString(CultureInfo.InvariantCulture));
            DomRows.Set(bag.Doms[1], row, "editprop", "A");
            TouchCalc(bag.Co, bag.Doms, row, op);
            row = At(bag.Doms[1], at);
            ClearNew(bag.Doms[1], row);
            DomRows.Set(bag.Doms[1], row, "irowno", rowNo.ToString(CultureInfo.InvariantCulture));
            DomRows.Set(bag.Doms[1], row, "editprop", "A");
        }

        static void Write(object dom, object row, Dictionary<string, string> clean)
        {
            foreach (KeyValuePair<string, string> pair in clean)
            {
                if (BlankCode(pair.Key, pair.Value))
                {
                    continue;
                }
                DomRows.Set(dom, row, pair.Key, pair.Value);
            }
        }

        // 空的存货或辅计量当作没传，不能把原行清掉。
        static bool BlankCode(string key, string value)
        {
            if (!CodeName(key))
            {
                return false;
            }
            return value == null || value.Trim().Length == 0;
        }

        static bool CodeName(string key)
        {
            return string.Equals(key, "cinvcode", StringComparison.OrdinalIgnoreCase)
                || string.Equals(key, "cassunit", StringComparison.OrdinalIgnoreCase);
        }

        static void ClearNew(object dom, object row)
        {
            if (DomRows.Get(row, "autoid").Length > 0)
            {
                DomRows.Set(dom, row, "autoid", null);
            }
            if (DomRows.Get(row, "isosid").Length > 0)
            {
                DomRows.Set(dom, row, "isosid", null);
            }
        }

        static void ApplyHead(object dom, Dictionary<string, object> head)
        {
            if (head == null || head.Count == 0)
            {
                return;
            }
            Dictionary<string, string> clean = SoFields.Map(head, Names(dom), true, "表头必须是对象");
            List<object> rows = DomRows.RowsOf(dom);
            if (rows.Count < 1)
            {
                throw new BridgeException(500, "internal", "模板没有字段");
            }
            Write(dom, rows[0], clean);
        }

        static void StampUser(WorkContext ctx, object dom)
        {
            List<object> rows = DomRows.RowsOf(dom);
            if (rows.Count < 1)
            {
                throw new BridgeException(500, "internal", "模板没有字段");
            }
            object row = rows[0];
            DomRows.Set(dom, row, "editprop", "M");
            DomRows.Set(dom, row, "cmodifier", User(ctx));
            DomRows.Set(dom, row, "dmoddate", LoginDate(ctx));
            string now = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture);
            DomRows.Set(dom, row, "dmodifysystime", now);
        }

        static void BlankEdit(object body)
        {
            List<object> rows = DomRows.RowsOf(body);
            for (int i = 0; i < rows.Count; i++)
            {
                DomRows.Set(body, rows[i], "editprop", "");
            }
        }

        static List<LineOp> Parse(object body, object[] lines)
        {
            int existing = DomRows.RowsOf(body).Count;
            Dictionary<int, bool> have = IdsOf(body);
            List<LineOp> ops = new List<LineOp>();
            Dictionary<int, bool> used = new Dictionary<int, bool>();
            int deletes = 0;
            int adds = 0;
            for (int i = 0; i < lines.Length; i++)
            {
                LineOp op = ReadOp(lines[i]);
                if (op.Op == "add")
                {
                    adds = adds + 1;
                }
                else
                {
                    TakeId(have, used, op);
                    if (op.Op == "delete")
                    {
                        deletes = deletes + 1;
                    }
                }
                ops.Add(op);
            }
            if (deletes > 0 && existing - deletes + adds < 1)
            {
                throw new BridgeException(400, "bad_request", "不能删除全部明细");
            }
            return ops;
        }

        static void TakeId(Dictionary<int, bool> have, Dictionary<int, bool> used, LineOp op)
        {
            if (op.LineId <= 0 || !have.ContainsKey(op.LineId))
            {
                throw new BridgeException(400, "bad_request", "明细行不存在");
            }
            if (used.ContainsKey(op.LineId))
            {
                throw new BridgeException(400, "bad_request", "明细行重复");
            }
            used[op.LineId] = true;
        }

        static LineOp ReadOp(object raw)
        {
            Dictionary<string, object> map = raw as Dictionary<string, object>;
            if (map == null)
            {
                throw new BridgeException(400, "bad_request", "表体行必须是对象");
            }
            LineOp op = new LineOp();
            op.Op = TextKey(map, "op").ToLowerInvariant();
            op.Fields = Strip(map);
            RequireOp(op.Op);
            if (op.Op == "add")
            {
                return ReadAdd(map, op);
            }
            return ReadExisting(map, op);
        }

        static void RequireOp(string op)
        {
            if (op != "add" && op != "update" && op != "delete")
            {
                throw new BridgeException(400, "bad_request", "op 必须是 add、update 或 delete");
            }
        }

        static LineOp ReadAdd(Dictionary<string, object> map, LineOp op)
        {
            if (HasKey(map, "line_id"))
            {
                throw new BridgeException(400, "bad_request", "新增行不能带 line_id");
            }
            if (TextKey(op.Fields, "cinvcode").Length == 0)
            {
                throw new BridgeException(400, "bad_request", "新增行必须填写存货编码");
            }
            Mark(op);
            return op;
        }

        static LineOp ReadExisting(Dictionary<string, object> map, LineOp op)
        {
            op.LineId = CoRows.AsId(RawKey(map, "line_id"));
            if (op.Op == "delete" && op.Fields.Count > 0)
            {
                throw new BridgeException(400, "bad_request", "删除行只能带 line_id");
            }
            if (op.Op == "update" && op.Fields.Count == 0)
            {
                throw new BridgeException(400, "bad_request", "没有要修改的内容");
            }
            Mark(op);
            return op;
        }

        static void Mark(LineOp op)
        {
            op.Qty = HasKey(op.Fields, "iquantity");
            op.Num = HasKey(op.Fields, "inum");
            op.Inv = TextKey(op.Fields, "cinvcode").Length > 0;
            op.TaxPrice = HasKey(op.Fields, "itaxunitprice");
            op.UnitPrice = HasKey(op.Fields, "iunitprice");
            op.TaxRate = HasKey(op.Fields, "itaxrate");
        }

        static Dictionary<string, object> Strip(Dictionary<string, object> map)
        {
            Dictionary<string, object> fields = new Dictionary<string, object>();
            foreach (KeyValuePair<string, object> pair in map)
            {
                if (Same(pair.Key, "op") || Same(pair.Key, "line_id"))
                {
                    continue;
                }
                fields[pair.Key] = pair.Value;
            }
            return fields;
        }

        static Dictionary<int, bool> IdsOf(object body)
        {
            Dictionary<int, bool> have = new Dictionary<int, bool>();
            List<object> rows = DomRows.RowsOf(body);
            for (int i = 0; i < rows.Count; i++)
            {
                int lineId = CoRows.AsId(DomRows.Get(rows[i], "isosid"));
                if (lineId > 0)
                {
                    have[lineId] = true;
                }
            }
            return have;
        }

        // 存货编码没变就保留原行的单位和换算率。
        static void RenewUnits(EditBag bag, object row, LineOp op, Dictionary<string, string> clean)
        {
            if (!op.Inv || SameInv(clean, row))
            {
                return;
            }
            DropAttrs(bag.Doms[1], row);
            SeedQty(clean, row);
            List<Dictionary<string, string>> one = new List<Dictionary<string, string>>();
            one.Add(clean);
            bool keepPre = HasKey(op.Fields, "dpremodate");
            bool keepPreDate = HasKey(op.Fields, "dpredate");
            SoUnits.Fill(bag.Ctx.Conn, bag.Schema, one, bag.HeadVals);
            if (!keepPre)
            {
                clean.Remove("dPreMoDate");
            }
            if (!keepPreDate)
            {
                clean.Remove("dPreDate");
            }
        }

        static void DropAttrs(object dom, object row)
        {
            for (int i = 0; i < UnitAttrs.Length; i++)
            {
                DomRows.Set(dom, row, UnitAttrs[i], null);
            }
        }

        static bool SameInv(Dictionary<string, string> clean, object row)
        {
            string sent = SentCode(clean, "cinvcode");
            if (sent.Length == 0)
            {
                return true;
            }
            return string.Equals(sent, DomRows.Get(row, "cinvcode").Trim(), StringComparison.OrdinalIgnoreCase);
        }

        static string SentCode(Dictionary<string, string> clean, string name)
        {
            string value;
            if (clean == null || !clean.TryGetValue(name, out value) || value == null)
            {
                return "";
            }
            return value.Trim();
        }

        static void SeedQty(Dictionary<string, string> clean, object row)
        {
            string have;
            if (clean.TryGetValue("iquantity", out have) && have != null && have.Trim().Length > 0)
            {
                return;
            }
            string qty = DomRows.Get(row, "iquantity").Trim();
            if (qty.Length > 0)
            {
                clean["iquantity"] = qty;
            }
        }

        static void TouchCalc(object co, object[] doms, object row, LineOp op)
        {
            SalePrices flags = SaleCalc.Flags(op.TaxPrice, op.UnitPrice, op.TaxRate);
            if (op.Num)
            {
                SaleCalc.RecalcNum(co, doms[0], doms[1], row, flags);
                return;
            }
            SaleCalc.Recalc(co, doms[0], doms[1], row, flags);
        }

        // 新增行必须克隆已装入的行。空 z:row 缺少 U8 保存要的属性。
        static object AppendClone(object body)
        {
            List<object> rows = DomRows.RowsOf(body);
            object seed = PickSeed(rows);
            object parent = ComUtil.Get(seed, "parentNode");
            object copy = null;
            try
            {
                if (parent == null || parent is DBNull)
                {
                    throw new BridgeException(500, "internal", "模板没有行");
                }
                copy = ComUtil.Call(seed, "cloneNode", new object[] { true });
                if (copy == null || copy is DBNull)
                {
                    throw new BridgeException(500, "internal", "模板没有行");
                }
                ComUtil.Call(parent, "appendChild", new object[] { copy });
            }
            finally
            {
                ComUtil.Final(copy);
                ComUtil.ReleaseOne(parent);
            }
            return LastRow(body);
        }

        static object PickSeed(List<object> rows)
        {
            object deleted = null;
            for (int i = 0; i < rows.Count; i++)
            {
                if (DomRows.Get(rows[i], "isosid").Length == 0)
                {
                    continue;
                }
                if (DomRows.Get(rows[i], "editprop") == "D")
                {
                    if (deleted == null)
                    {
                        deleted = rows[i];
                    }
                    continue;
                }
                return rows[i];
            }
            if (deleted != null)
            {
                return deleted;
            }
            if (rows.Count > 0)
            {
                return rows[0];
            }
            throw new BridgeException(500, "internal", "模板没有行");
        }

        static object LastRow(object body)
        {
            List<object> rows = DomRows.RowsOf(body);
            if (rows.Count == 0)
            {
                throw new BridgeException(500, "internal", "模板没有行");
            }
            return rows[rows.Count - 1];
        }

        // 种子行的单位、备注和存货文本不能带到新行。调用方没传的清掉，单位留给 SoUnits 按新存货重填。
        static void BlankClone(object dom, object row, Dictionary<string, string> sent)
        {
            DomRows.Set(dom, row, "autoid", null);
            DomRows.Set(dom, row, "isosid", null);
            DropAttrs(dom, row);
            WipeListed(dom, row, ClearedCols, null);
            WipeOne(dom, row, sent, "cmemo");
            WipeSpan(dom, row, sent, "cdefine", 22, 37);
            WipeSpan(dom, row, sent, "cfree", 1, 10);
            WipeListed(dom, row, ItemText, sent);
            WipeListed(dom, row, LinkCols, sent);
        }

        static void WipeListed(object dom, object row, string[] names, Dictionary<string, string> sent)
        {
            for (int i = 0; i < names.Length; i++)
            {
                WipeOne(dom, row, sent, names[i]);
            }
        }

        static void WipeSpan(object dom, object row, Dictionary<string, string> sent, string prefix, int from, int to)
        {
            for (int n = from; n <= to; n++)
            {
                WipeOne(dom, row, sent, prefix + n.ToString(CultureInfo.InvariantCulture));
            }
        }

        static void WipeOne(object dom, object row, Dictionary<string, string> sent, string name)
        {
            if (sent != null && sent.ContainsKey(name))
            {
                return;
            }
            if (DomRows.Get(row, name).Length > 0)
            {
                DomRows.Set(dom, row, name, "");
            }
        }

        sealed class LineOp
        {
            public string Op;
            public int LineId;
            public Dictionary<string, object> Fields;
            public bool Qty;
            public bool Num;
            public bool Inv;
            public bool TaxPrice;
            public bool UnitPrice;
            public bool TaxRate;
        }

    }
}
