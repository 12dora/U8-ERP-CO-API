using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 在 Load 出来的销售出库单 DOM 上按请求标记批号 / 货位 / 拆行（StockGenSaleBatchApply 的一部分，未经实测）。
    // 要改的发货行：第 i 行对第 i 次（数量、cBatch、cPosition，editprop=M）；次数多于行数时克隆该发货行的第 1 行追加
    // （editprop=A，保留来源关联和单价，清掉 AutoID、条码，行号接着排）；行数多于次数时多出的行 editprop=D。其余行 editprop 置空。
    // 改了数量的行按换算率重算 iNum、有单位成本时 iPrice = round(数量 × iUnitCost, 2)，应发数量 iNQuantity、应发件数 iNNum 跟着改（在 schema 里时）。
    internal static partial class StockGen
    {
        sealed class OutDom
        {
            public WorkContext Ctx;
            public OutAsk Ask;
            public HashSet<int> Lines;
            object dom;
            List<string> names;
            int next;

            public void Mark(StockLoaded loaded)
            {
                List<object> heads = DomRows.RowsOf(loaded.Head);
                List<object> rows = DomRows.RowsOf(loaded.Body);
                try
                {
                    dom = loaded.Body;
                    names = DomRows.Schema(dom);
                    next = MaxRowNo(rows) + 1;
                    Dictionary<int, List<object>> byLine = GroupRows(rows);
                    foreach (KeyValuePair<int, List<object>> kv in byLine)
                    {
                        if (Lines.Contains(kv.Key))
                        {
                            MarkLine(kv.Value, Ask.ByLine[kv.Key]);
                        }
                        else
                        {
                            Untouched(kv.Value);
                        }
                    }
                    if (heads.Count == 0)
                    {
                        throw new BridgeException(409, "u8_rejected", "U8 生成的销售出库单没有表头");
                    }
                    StockDom.SetCell(loaded.Head, heads[0], "editprop", "M");
                }
                finally
                {
                    ReleaseAll(heads);
                    ReleaseAll(rows);
                }
            }

            Dictionary<int, List<object>> GroupRows(List<object> rows)
            {
                Dictionary<int, List<object>> byLine = new Dictionary<int, List<object>>();
                for (int i = 0; i < rows.Count; i++)
                {
                    int line = CoRows.AsId(DomRows.Get(rows[i], "iDLsID"));
                    List<object> list;
                    if (!byLine.TryGetValue(line, out list))
                    {
                        list = new List<object>();
                        byLine[line] = list;
                    }
                    list.Add(rows[i]);
                }
                return byLine;
            }

            static int MaxRowNo(List<object> rows)
            {
                int max = 0;
                for (int i = 0; i < rows.Count; i++)
                {
                    int no;
                    if (int.TryParse(DomRows.Get(rows[i], "irowno").Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out no)
                        && no > max)
                    {
                        max = no;
                    }
                }
                return max;
            }

            void Untouched(List<object> rows)
            {
                for (int i = 0; i < rows.Count; i++)
                {
                    StockDom.SetCell(dom, rows[i], "editprop", "", names);
                }
            }

            void MarkLine(List<object> rows, List<OutSpec> specs)
            {
                int n = Math.Min(rows.Count, specs.Count);
                for (int i = 0; i < n; i++)
                {
                    Put(rows[i], specs[i], "M");
                }
                for (int i = n; i < specs.Count; i++)
                {
                    object copy = CloneAfter(rows[0]);
                    try
                    {
                        BlankClone(copy);
                        Put(copy, specs[i], "A");
                    }
                    finally
                    {
                        ComUtil.ReleaseOne(copy);
                    }
                }
                for (int i = n; i < rows.Count; i++)
                {
                    StockDom.SetCell(dom, rows[i], "editprop", "D", names);
                }
            }

            void Put(object row, OutSpec spec, string prop)
            {
                decimal have;
                bool known = StockUnits.Dec(DomRows.Get(row, "iQuantity"), out have);
                bool qty = prop == "A" || !known || Math.Abs(have - spec.Qty) > 0.000001m;
                if (qty)
                {
                    StockDom.SetCell(dom, row, "iQuantity", StockUnits.Price(spec.Qty), names);
                    SetKnown(row, "iNQuantity", StockUnits.Price(spec.Qty));
                }
                if (spec.Batch.Length > 0 && !string.Equals(DomRows.Get(row, "cBatch").Trim(), spec.Batch, StringComparison.OrdinalIgnoreCase))
                {
                    ResetLot(row, spec.Batch);
                }
                if (spec.Pos.Length > 0)
                {
                    StockDom.SetCell(dom, row, "cPosition", spec.Pos, names);
                }
                StockDom.SetCell(dom, row, "editprop", prop, names);
                if (qty)
                {
                    Recount(row);
                }
            }

            // 换批号时清掉跟批号走的列，再按 AA_BatchProperty 填新批号的属性（StockGenSaleBatchProp）。
            void ResetLot(object row, string batch)
            {
                Dictionary<string, string> props = LotProps(Ctx.Conn, row, batch);
                for (int i = 0; i < LotCols.Length; i++)
                {
                    SetKnown(row, LotCols[i], null);
                }
                foreach (KeyValuePair<string, string> kv in props)
                {
                    SetKnown(row, kv.Key, kv.Value);
                }
                StockDom.SetCell(dom, row, "cBatch", batch, names);
            }

            // 改了数量：辅数量 iNum 按换算率重算，应发件数 iNNum 跟着应发数量 iNQuantity，有单位成本时重算金额。
            void Recount(object row)
            {
                StockUnits.ApplyDom(Ctx.Conn, dom, row, UnitJobOf("iQuantity", "iNum"));
                if (Known("iNQuantity") && Known("iNNum"))
                {
                    StockUnits.ApplyDom(Ctx.Conn, dom, row, UnitJobOf("iNQuantity", "iNNum"));
                }
                StockUnits.FixPriceSent(dom, row, false, false, true, names);
            }

            StockUnits.UnitJob UnitJobOf(string qty, string num)
            {
                StockUnits.UnitJob unit = new StockUnits.UnitJob();
                unit.QtyName = qty;
                unit.NumName = num;
                unit.Force = true;
                unit.Schema = names;
                return unit;
            }

            bool Known(string name)
            {
                for (int i = 0; i < names.Count; i++)
                {
                    if (string.Equals(names[i], name, StringComparison.OrdinalIgnoreCase))
                    {
                        return true;
                    }
                }
                return false;
            }

            // 克隆行：保留来源关联（iDLsID 等）、存货、单价；主键、条码清掉，行号接着排。批号不同时 Put 里 ResetLot 清批次列。
            void BlankClone(object row)
            {
                StockDom.SetCell(dom, row, "AutoID", "", names);
                SetKnown(row, "cbsysbarcode", null);
                StockDom.SetCell(dom, row, "irowno", next.ToString(CultureInfo.InvariantCulture), names);
                next++;
            }

            // schema 里有这一列才写（没有就不加属性）。
            void SetKnown(object row, string name, string value)
            {
                for (int i = 0; i < names.Count; i++)
                {
                    if (string.Equals(names[i], name, StringComparison.OrdinalIgnoreCase))
                    {
                        StockDom.SetCell(dom, row, names[i], value, names);
                        return;
                    }
                }
            }

            // cloneNode(true) 后追加到同一父节点末尾，返回追加的节点（调用方 ReleaseOne）。
            static object CloneAfter(object seed)
            {
                object parent = null;
                object copy = null;
                try
                {
                    parent = ComUtil.Get(seed, "parentNode");
                    copy = ComUtil.Call(seed, "cloneNode", new object[] { true });
                    if (parent == null || parent is DBNull || copy == null || copy is DBNull)
                    {
                        throw new BridgeException(500, "internal", "出库单行无法复制");
                    }
                    ComUtil.Call(parent, "appendChild", new object[] { copy });
                    object keep = copy;
                    copy = null;
                    return keep;
                }
                finally
                {
                    ComUtil.ReleaseOne(copy);
                    ComUtil.ReleaseOne(parent);
                }
            }
        }
    }
}
