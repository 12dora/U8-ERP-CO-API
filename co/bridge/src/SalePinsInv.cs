using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 销售发票参照发货单的一行（给 SalePins.ForInvoice）：来源发货行 iDLsID、请求数量、请求字段。
    internal sealed class InvPinLine
    {
        internal int SrcLine;
        internal decimal Qty;
        internal Dictionary<string, object> Fields;
    }

    // 销售发票参照发货单（蓝字，单张或多张来源）的保存后核对与回签字段：
    // 保存前（事务之外）收集表体 cbdefine1–3 并按列目录校验类型、长度（SalePinsCheck.Check）；
    // Save 之后、提交之前在同一事务里（SalePinsSave.Settle）：按 iDLsID 找本次保存的发票行——行数等于请求行数、
    // 每个请求行恰有一条发票行且数量相同（6 位小数，同 QtyText），否则 409 u8_rejected 回滚；
    // 再把 cbdefine1–3 写进 SaleBillVouchs_extradefine（有行 UPDATE、没有行 INSERT）并逐项回读。
    // 扩展自定义项从不进 DOM（DomOnly 在 FillInvLine 之前去掉）。
    internal sealed partial class SalePins
    {
        const string InvoiceIdSql = "SELECT TOP 1 SBVID FROM SaleBillVouch WHERE cSBVCode=? ORDER BY SBVID DESC";
        const string InvoiceLinesSql = "SELECT AutoID, iDLsID, CONVERT(varchar(40), iQuantity) AS q FROM SaleBillVouchs WHERE SBVID=?";

        internal List<InvPinLine> InvLines = new List<InvPinLine>();

        internal static SalePins ForInvoice(object conn, string date, List<InvPinLine> lines)
        {
            SalePins pins = Collect(Invoice, lines);
            pins.Date = date;
            pins.Check(conn);
            return pins;
        }

        // 纯收集（--selftest 用）：路径按请求里全部行的顺序编号（多来源时各张 lines 依次连起来）。
        internal static SalePins Collect(string kind, List<InvPinLine> lines)
        {
            SalePins pins = new SalePins();
            pins.Kind = kind;
            pins.InvLines = lines ?? new List<InvPinLine>();
            for (int i = 0; i < pins.InvLines.Count; i++)
            {
                int before = pins.Items.Count;
                Take(pins, pins.InvLines[i].Fields, false, FieldPath.Item("lines", i), false);
                for (int k = before; k < pins.Items.Count; k++)
                {
                    pins.Items[k].SrcLine = pins.InvLines[i].SrcLine;
                }
            }
            return pins;
        }

        // 写进 DOM 的字段：去掉该类型表体的约定字段（扩展自定义项由桥在 Save 之后写）。原字典不动。
        internal static Dictionary<string, object> DomOnly(string kind, Dictionary<string, object> fields)
        {
            if (fields == null)
            {
                return null;
            }
            Dictionary<string, object> kept = new Dictionary<string, object>();
            foreach (KeyValuePair<string, object> pair in fields)
            {
                string lower = pair.Key == null ? "" : pair.Key.ToLowerInvariant();
                if (Find(kind, false, lower) == null)
                {
                    kept[pair.Key] = pair.Value;
                }
            }
            return kept;
        }

        static int InvoiceIdByCode(object conn, string no)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, InvoiceIdSql, new object[] { no }, 2);
            return rows.Count == 1 ? CoRows.AsId(CoRows.Col(rows[0], "SBVID")) : 0;
        }

        // 保存后的发票行按来源发货行 iDLsID 编索引；来源缺失、重复或数量不是数时返回说明。
        static string IndexInvoice(List<Dictionary<string, object>> rows, Dictionary<int, int> autoBySrc, Dictionary<int, decimal> qtyBySrc)
        {
            for (int i = 0; i < rows.Count; i++)
            {
                int src = CoRows.AsId(CoRows.Col(rows[i], "iDLsID"));
                decimal qty;
                if (src <= 0 || autoBySrc.ContainsKey(src) || !SoUnits.Dec(CoRows.Col(rows[i], "q"), out qty))
                {
                    return "发票行 " + CoRows.Col(rows[i], "AutoID") + " 的来源发货行缺失或重复";
                }
                autoBySrc[src] = CoRows.AsId(CoRows.Col(rows[i], "AutoID"));
                qtyBySrc[src] = qty;
            }
            return "";
        }

        void AssignInvoice(object conn, WorkItem item)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, InvoiceLinesSql, new object[] { HeadId }, 5000);
            Dictionary<int, int> autoBySrc = new Dictionary<int, int>();
            string problem = MatchInvoice(rows, InvLines, autoBySrc);
            if (problem.Length > 0)
            {
                CoRows.Note(item, "发票行核对：" + problem);
                throw new BridgeException(409, "u8_rejected", "U8 保存的明细与请求不一致");
            }
            for (int i = 0; i < Items.Count; i++)
            {
                Items[i].Key = autoBySrc[Items[i].SrcLine];
            }
        }

        // 纯核对（--selftest 用）：rows 是保存后的发票行（AutoID、iDLsID、q），按来源发货行一一对上；返回空串为一致，否则说明。
        internal static string MatchInvoice(List<Dictionary<string, object>> rows, List<InvPinLine> want, Dictionary<int, int> autoBySrc)
        {
            Dictionary<int, decimal> qtyBySrc = new Dictionary<int, decimal>();
            string bad = IndexInvoice(rows, autoBySrc, qtyBySrc);
            if (bad.Length > 0)
            {
                return bad;
            }
            if (rows.Count != want.Count)
            {
                return "行数 " + rows.Count.ToString(CultureInfo.InvariantCulture) + "/" + want.Count.ToString(CultureInfo.InvariantCulture);
            }
            for (int i = 0; i < want.Count; i++)
            {
                decimal got;
                decimal expect = Math.Round(want[i].Qty, 6, MidpointRounding.AwayFromZero);
                if (!qtyBySrc.TryGetValue(want[i].SrcLine, out got) || got != expect)
                {
                    return "来源发货行 " + want[i].SrcLine.ToString(CultureInfo.InvariantCulture) + " 的数量 "
                        + (qtyBySrc.ContainsKey(want[i].SrcLine) ? got.ToString(CultureInfo.InvariantCulture) : "缺失");
                }
            }
            return "";
        }
    }
}
