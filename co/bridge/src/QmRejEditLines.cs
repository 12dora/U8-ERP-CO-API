using System;
using System.Collections.Generic;

namespace U8Co
{
    // 不良品处理单修改的表体：请求的增删改与单据已有的行合并成整单处理行（Merge），再写进 VO 的 domBody（Apply）。
    // 修改行只改送了的字段；改成非降级类处理方式（流程不是 2）时处理后存货、数量随之清除；降级行没送 fdimquantity 时，
    // 新增行、改了数量或刚改成降级的行处理后数量 = 处理数量，其余保留原值。
    internal static class QmRejEditLines
    {
        public static void Merge(object conn, QmRejEditJob e, List<Dictionary<string, object>> rows)
        {
            List<QmRejLine> lines = e.Ask.Head.Lines;
            lines.Clear();
            for (int i = 0; i < rows.Count; i++)
            {
                lines.Add(Existing(rows[i]));
            }
            for (int i = 0; i < e.Ask.Ops.Count; i++)
            {
                try
                {
                    Apply(conn, e, e.Ask.Ops[i]);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
            if (lines.Count == 0)
            {
                throw BridgeException.BadField("lines", "不能删除全部处理行，单据至少保留一行");
            }
            RequireDistinct(lines);
        }

        static QmRejLine Existing(Dictionary<string, object> row)
        {
            QmRejLine line = new QmRejLine();
            line.AutoId = CoRows.AsId(CoRows.Col(row, "AUTOID"));
            line.SourceLineId = line.AutoId;
            line.Qty = QmSql.Dec(CoRows.Col(row, "FQUANTITY"));
            line.Dispose = CoRows.Col(row, "CSCRAPDISCODE");
            line.Reason = CoRows.Col(row, "CREASONCODE");
            line.DimInv = CoRows.Col(row, "CDIMINVCODE");
            line.DimQty = QmSql.Dec(CoRows.Col(row, "FDIMQUANTITY"));
            line.Wh = CoRows.Col(row, "CBWHCODE");
            line.Flow = (int)QmSql.Dec(CoRows.Col(row, "IDISPOSEFLOW"));
            return line;
        }

        static void Apply(object conn, QmRejEditJob e, QmRejEditOp op)
        {
            List<QmRejLine> lines = e.Ask.Head.Lines;
            if (op.Op == QmRejEditOp.Add)
            {
                QmRejLine added = new QmRejLine();
                Resolve(conn, added, op, -1);
                lines.Add(added);
                e.Touched.Add(added);
                return;
            }
            int at = IndexOf(lines, op.LineId);
            if (at < 0)
            {
                throw BridgeException.BadField("line_id", "单据没有表体行 " + op.LineId);
            }
            if (op.Op == QmRejEditOp.Delete)
            {
                lines.RemoveAt(at);
                e.Deleted.Add(op.LineId);
                return;
            }
            Resolve(conn, lines[at], op, lines[at].Flow);
            e.Touched.Add(lines[at]);
        }

        // 把送了的字段并进行，再按档案核对（QmRejSrc.Archives 补名称与处理流程）。oldFlow：修改前的处理流程（新增行为 -1）。
        static void Resolve(object conn, QmRejLine line, QmRejEditOp op, int oldFlow)
        {
            line.Qty = op.Qty.HasValue ? op.Qty.Value : line.Qty;
            line.Dispose = op.Dispose ?? line.Dispose;
            line.Reason = op.Reason ?? line.Reason;
            line.Wh = op.Wh ?? line.Wh;
            if (op.DimInv != null)
            {
                line.DimInv = op.DimInv;
            }
            else if (op.Dispose != null && QmRejSrc.FlowOf(conn, op.Dispose) != QmRejSrc.DimFlow)
            {
                line.DimInv = "";
            }
            QmRejSrc.Archives(conn, line);
            QmRejUnits.Dim(conn, line);
            DimQty(line, op, oldFlow);
        }

        static void DimQty(QmRejLine line, QmRejEditOp op, int oldFlow)
        {
            if (line.Flow != QmRejSrc.DimFlow)
            {
                if (op.DimQty.HasValue)
                {
                    throw BridgeException.BadField("fdimquantity", "只有降级类处理方式可以指定处理后数量 fdimquantity");
                }
                line.DimQty = 0m;
                return;
            }
            if (op.DimQty.HasValue)
            {
                line.DimQty = op.DimQty.Value;
            }
            else if (oldFlow != QmRejSrc.DimFlow || op.Qty.HasValue || line.DimQty <= 0m)
            {
                line.DimQty = 0m;
            }
        }

        // U8：「不能出现相同的'不良品原因'和'不良品处理方式'的处理」。
        static void RequireDistinct(List<QmRejLine> lines)
        {
            for (int i = 0; i < lines.Count; i++)
            {
                for (int j = i + 1; j < lines.Count; j++)
                {
                    if (Key(lines[i]) == Key(lines[j]))
                    {
                        throw BridgeException.BadField("lines", "不能有两行相同的不良原因 " + lines[i].Reason + " 与处理方式 "
                            + lines[i].Dispose);
                    }
                }
            }
        }

        internal static string Key(QmRejLine line)
        {
            return Key(line.Reason, line.Dispose);
        }

        internal static string Key(string reason, string dispose)
        {
            return (reason ?? "").Trim().ToUpperInvariant() + "\u0001" + (dispose ?? "").Trim().ToUpperInvariant();
        }

        static int IndexOf(List<QmRejLine> lines, int autoId)
        {
            for (int i = 0; i < lines.Count; i++)
            {
                if (lines[i].AutoId == autoId)
                {
                    return i;
                }
            }
            return -1;
        }
    }
}
