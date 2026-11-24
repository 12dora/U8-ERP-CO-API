using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 生单的一行（给 SalePins.ForInvoice / ForDispatch）：来源行（发票为发货行 iDLsID，发货单为订单行 iSOsID）、请求数量、请求字段。
    internal sealed class InvPinLine
    {
        internal int SrcLine;
        internal decimal Qty;
        internal Dictionary<string, object> Fields;
    }

    // 销售发票参照发货单（蓝字，单张或多张来源）的保存后核对与表体扩展自定义项 cbdefineN：
    // 保存前（事务之外）收集请求给了的 cbdefineN，按列目录校验列存在、类型、长度（SalePinsCheck.Check）；
    // Save 之后、提交之前在同一事务里（SalePinsSave.Settle）：按 iDLsID 找本次保存的发票行——行数等于请求行数、
    // 每个请求行恰有一条发票行且数量相同（6 位小数，同 QtyText），否则 409 u8_rejected 回滚；
    // 再把这些列写进 SaleBillVouchs_extradefine（有行 UPDATE、没有行 INSERT）并逐项回读。
    // 扩展自定义项从不进 DOM（DomOnly 在 FillInvLine 之前去掉）。
    // 发货单参照销售订单生单的表体扩展自定义项 cbdefineN（DispatchLists_extradefine）同一套：按 iSOsID 找本次保存的发货单行
    // iDLsID（行数、数量同样核对），只在请求给了表体扩展自定义项时才核对和补写（ForDispatch）；DomOnly 在 FillLine 之前去掉。
    internal sealed partial class SalePins
    {
        const string InvoiceIdSql = "SELECT TOP 1 SBVID FROM SaleBillVouch WHERE cSBVCode=? ORDER BY SBVID DESC";
        // 数量先转 decimal(38,6) 再转文本：float / real 列直接转 varchar 只保留 6 位有效数字（12.345678 成了 12.3457），
        // 会把正确保存的行误判为不一致。请求一侧同样按 6 位小数（AwayFromZero）规整后比较（Match）。
        const string QtySel = "CONVERT(varchar(40), CONVERT(decimal(38,6), iQuantity)) AS q";
        const string InvoiceLinesSql = "SELECT AutoID, iDLsID, " + QtySel + " FROM SaleBillVouchs WHERE SBVID=?";
        const string DispatchLinesSql = "SELECT iDLsID, iSOsID, " + QtySel + " FROM DispatchLists WHERE DLID=?";
        // {保存后的行 SQL, 行主键, 来源行列, 行说明, 来源行说明}
        static readonly string[] InvoiceMatch = new string[] { InvoiceLinesSql, "AutoID", "iDLsID", "发票行", "来源发货行" };
        static readonly string[] DispatchMatch = new string[] { DispatchLinesSql, "iDLsID", "iSOsID", "发货单行", "来源订单行" };

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
            TakeSrcLines(pins, lines);
            return pins;
        }

        // 各行的表体核对字段记下来源行；InvLines 是全部请求行（保存后按来源行一一核对）。
        static void TakeSrcLines(SalePins pins, List<InvPinLine> lines)
        {
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
        }

        // 发货单参照订单（--selftest 也用）：只有请求给了表体扩展自定义项才保留 InvLines（才在保存后核对行），
        // 否则与没有表体核对字段时一样，不加新的 409。
        internal static void TakeDispatchLines(SalePins pins, List<InvPinLine> lines)
        {
            TakeSrcLines(pins, lines);
            if (!pins.Items.Exists(delegate(SalePin p) { return p.SrcLine > 0; }))
            {
                pins.InvLines = new List<InvPinLine>();
            }
        }

        // 写进 DOM 的字段：去掉该类型表体的核对字段（扩展自定义项由桥在 Save 之后写）。原字典不动。
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

        // 保存后的行按来源行编索引；来源缺失、重复或数量不是数时返回说明。m 是 InvoiceMatch / DispatchMatch。
        static string IndexLines(List<Dictionary<string, object>> rows, string[] m, Dictionary<int, int> autoBySrc,
            Dictionary<int, decimal> qtyBySrc)
        {
            for (int i = 0; i < rows.Count; i++)
            {
                int src = CoRows.AsId(CoRows.Col(rows[i], m[2]));
                decimal qty;
                if (src <= 0 || autoBySrc.ContainsKey(src) || !SoUnits.Dec(CoRows.Col(rows[i], "q"), out qty))
                {
                    return m[3] + " " + CoRows.Col(rows[i], m[1]) + " 的" + m[4] + "缺失或重复";
                }
                autoBySrc[src] = CoRows.AsId(CoRows.Col(rows[i], m[1]));
                qtyBySrc[src] = Math.Round(qty, 6, MidpointRounding.AwayFromZero);
            }
            return "";
        }

        // 销售发票（全部行）、发货单（给了表体扩展自定义项时）：按来源行找本次保存的行，表头核对字段不动（SrcLine 为 0）。
        void AssignBySource(object conn, WorkItem item)
        {
            string[] m = Kind == Invoice ? InvoiceMatch : DispatchMatch;
            List<Dictionary<string, object>> rows = Rows.Query(conn, m[0], new object[] { HeadId }, 5000);
            Dictionary<int, int> autoBySrc = new Dictionary<int, int>();
            string problem = Match(rows, m, InvLines, autoBySrc);
            if (problem.Length > 0)
            {
                CoRows.Note(item, m[3] + "核对：" + problem);
                throw new BridgeException(409, "u8_rejected", "U8 保存的明细与请求不一致");
            }
            for (int i = 0; i < Items.Count; i++)
            {
                if (Items[i].SrcLine > 0)
                {
                    Items[i].Key = autoBySrc[Items[i].SrcLine];
                }
            }
        }

        // 纯核对（--selftest 用）：rows 是保存后的发票行（AutoID、iDLsID、q），按来源发货行一一对上；返回空串为一致，否则说明。
        internal static string MatchInvoice(List<Dictionary<string, object>> rows, List<InvPinLine> want, Dictionary<int, int> autoBySrc)
        {
            return Match(rows, InvoiceMatch, want, autoBySrc);
        }

        // 纯核对（--selftest 用）：rows 是保存后的发货单行（iDLsID、iSOsID、q），按来源订单行一一对上。
        internal static string MatchDispatch(List<Dictionary<string, object>> rows, List<InvPinLine> want, Dictionary<int, int> dlsBySrc)
        {
            return Match(rows, DispatchMatch, want, dlsBySrc);
        }

        static string Match(List<Dictionary<string, object>> rows, string[] m, List<InvPinLine> want, Dictionary<int, int> autoBySrc)
        {
            Dictionary<int, decimal> qtyBySrc = new Dictionary<int, decimal>();
            string bad = IndexLines(rows, m, autoBySrc, qtyBySrc);
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
                    return m[4] + " " + want[i].SrcLine.ToString(CultureInfo.InvariantCulture) + " 的数量 "
                        + (qtyBySrc.ContainsKey(want[i].SrcLine) ? got.ToString(CultureInfo.InvariantCulture) : "缺失");
                }
            }
            return "";
        }
    }
}
