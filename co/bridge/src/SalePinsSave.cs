using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 约定字段保存后、提交前的处理（同一事务、请求连接）：定表体行主键 → 补写组件不落库的字段 → 逐项回读核对。
    // 不符抛 409 u8_rejected（调用方回滚）。组件已自行结束事务时不补写，504 outcome_unknown。
    // 表名、列名只来自 SalePins.Spec 并加方括号，值和主键走参数。
    internal sealed partial class SalePins
    {
        const string OrderLinesSql = "SELECT iSOsID, LTRIM(RTRIM(ISNULL(cInvCode,''))) AS inv FROM SO_SODetails"
            + " WHERE ID=? ORDER BY iRowNo, iSOsID";
        const string OrderIdSql = "SELECT ID FROM SO_SOMain WHERE cSOCode=?";
        const string DispatchIdSql = "SELECT TOP 1 DLID FROM DispatchList WHERE cDLCode=? ORDER BY DLID DESC";

        internal static void Settle(object conn, WorkItem item, SalePins pins, int id, string code)
        {
            if (!Needed(pins))
            {
                return;
            }
            if (!Alive(conn))
            {
                CoRows.Note(item, "约定字段：U8 已自行提交，未补写");
                throw new BridgeException(504, "outcome_unknown", "U8 已自行提交，约定字段未能核对，需要人工核对");
            }
            pins.HeadId = HeadOf(conn, pins, id, code);
            pins.AssignKeys(conn, item);
            WritePins(conn, item, pins);
            // 计量组可能变的行：请求给的数量、件数、换算率原样落库，且与单位成一套（SalePinsGroup）。
            pins.CheckGroups(conn, item);
        }

        static bool Needed(SalePins pins)
        {
            return pins != null && (pins.Items.Count > 0 || pins.Groups.Count > 0);
        }

        static int HeadOf(object conn, SalePins pins, int id, string code)
        {
            int head = pins.HeadId > 0 ? pins.HeadId : id;
            if (head <= 0)
            {
                head = IdByCode(conn, pins.Kind, code);
            }
            if (head <= 0)
            {
                throw new BridgeException(409, "u8_rejected", "U8 保存后未能确定单据标识");
            }
            return head;
        }

        static void WritePins(object conn, WorkItem item, SalePins pins)
        {
            for (int i = 0; i < pins.Items.Count; i++)
            {
                if (pins.Items[i].Explicit)
                {
                    GlSql.Exec(conn, WriteSql(pins.Items[i]), WriteArgs(pins.Items[i]));
                }
            }
            for (int i = 0; i < pins.Items.Count; i++)
            {
                Verify(conn, item, pins.Items[i]);
            }
        }

        static bool Alive(object conn)
        {
            try
            {
                return CoTrans.Count(conn) != "0";
            }
            catch (Exception)
            {
                return false;
            }
        }

        static int IdByCode(object conn, string kind, string code)
        {
            string no = code == null ? "" : code.Trim();
            if (no.Length == 0)
            {
                return 0;
            }
            string sql = kind == Dispatch ? DispatchIdSql : OrderIdSql;
            List<Dictionary<string, object>> rows = Rows.Query(conn, sql, new object[] { no }, 2);
            return rows.Count == 1 ? CoRows.AsId(CoRows.Col(rows[0], kind == Dispatch ? "DLID" : "ID")) : 0;
        }

        // 表头主键 = 单据主键；本次新增的表体行 = 保存后不在 OldLines 里的行，按 iRowNo, iSOsID 与请求顺序对应。
        void AssignKeys(object conn, WorkItem item)
        {
            bool adds = Items.Exists(delegate(SalePin p) { return p.Line >= 0; })
                || Groups.Exists(delegate(SaleQtyLine g) { return g.Line >= 0; });
            List<int> fresh = Adds > 0 && adds ? FreshLines(conn, item) : new List<int>();
            for (int i = 0; i < Items.Count; i++)
            {
                SalePin pin = Items[i];
                if (pin.Path.StartsWith("head.", StringComparison.Ordinal))
                {
                    pin.Key = HeadId;
                }
                else if (pin.Line >= 0)
                {
                    pin.Key = fresh[pin.Line];
                }
            }
            for (int i = 0; i < Groups.Count; i++)
            {
                if (Groups[i].Line >= 0)
                {
                    Groups[i].Key = fresh[Groups[i].Line];
                }
            }
        }

        List<int> FreshLines(object conn, WorkItem item)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, OrderLinesSql, new object[] { HeadId }, 5000);
            List<int> fresh = new List<int>();
            List<string> invs = new List<string>();
            for (int i = 0; i < rows.Count; i++)
            {
                int line = CoRows.AsId(CoRows.Col(rows[i], "iSOsID"));
                if (line > 0 && !OldLines.Contains(line))
                {
                    fresh.Add(line);
                    invs.Add(CoRows.Col(rows[i], "inv"));
                }
            }
            if (fresh.Count != Adds || !InvsMatch(invs))
            {
                CoRows.Note(item, "约定字段：新增行 " + fresh.Count.ToString(CultureInfo.InvariantCulture)
                    + "/" + Adds.ToString(CultureInfo.InvariantCulture));
                throw new BridgeException(409, "u8_rejected", "U8 保存的明细与请求不一致");
            }
            return fresh;
        }

        bool InvsMatch(List<string> invs)
        {
            for (int i = 0; i < Items.Count; i++)
            {
                SalePin pin = Items[i];
                if (pin.Line >= 0 && pin.Inv.Length > 0
                    && !string.Equals(pin.Inv, invs[pin.Line], StringComparison.OrdinalIgnoreCase))
                {
                    return false;
                }
            }
            for (int i = 0; i < Groups.Count; i++)
            {
                SaleQtyLine g = Groups[i];
                if (g.Line >= 0 && g.Inv.Length > 0 && !string.Equals(g.Inv, invs[g.Line], StringComparison.OrdinalIgnoreCase))
                {
                    return false;
                }
            }
            return true;
        }

        // 主表列 UPDATE；扩展表有行 UPDATE、没有行 INSERT 主键和该列。日期列显式转换（ValueExpr），不靠会话的日期格式。
        internal static string WriteSql(SalePin pin)
        {
            string t = Q(pin.Table);
            string c = Q(pin.Column);
            string k = Q(pin.KeyColumn);
            string v = ValueExpr(pin.Col);
            string update = "UPDATE " + t + " SET " + c + "=" + v + " WHERE " + k + "=?";
            if (!pin.Extra)
            {
                return update;
            }
            return "IF EXISTS (SELECT 1 FROM " + t + " WHERE " + k + "=?) " + update
                + " ELSE INSERT INTO " + t + " (" + k + ", " + c + ") VALUES (?, " + v + ")";
        }

        // 按列的实际类型转换（CHECKLIST「字符串转日期」）：date 列 CONVERT(date, ?, 23)；datetime2 列
        // CAST(CONVERT(date, ?, 23) AS datetime2)（不经 datetime，保留 0001 年起的范围）；datetime、smalldatetime
        // 再包一层 CONVERT(datetime, …)。其余照参数。
        internal static string ValueExpr(SaleCol col)
        {
            if (col == null || col.Kind != "date")
            {
                return "?";
            }
            if (col.Type == "date")
            {
                return "CONVERT(date, ?, 23)";
            }
            if (col.Type == "datetime2")
            {
                return "CAST(CONVERT(date, ?, 23) AS datetime2)";
            }
            return "CONVERT(datetime, CONVERT(date, ?, 23))";
        }

        internal static object[] WriteArgs(SalePin pin)
        {
            object value = pin.Value.Length == 0 ? null : pin.Value;
            if (!pin.Extra)
            {
                return new object[] { value, pin.Key };
            }
            return new object[] { pin.Key, value, pin.Key, pin.Key, value };
        }

        internal static string ReadSql(SalePin pin)
        {
            return "SELECT " + Q(pin.Column) + " AS v FROM " + Q(pin.Table) + " WHERE " + Q(pin.KeyColumn) + "=?";
        }

        static void Verify(object conn, WorkItem item, SalePin pin)
        {
            Dictionary<string, object> row = Rows.One(conn, ReadSql(pin), new object[] { pin.Key });
            string stored = row == null ? "" : CoRows.Col(row, "v");
            if (Same(pin.Col == null ? "text" : pin.Col.Kind, pin.Value, stored))
            {
                return;
            }
            CoRows.Note(item, "约定字段回读 " + pin.Path + " [" + pin.Value + "] → [" + stored + "]");
            throw new BridgeException(409, "u8_rejected",
                "U8 保存后 " + pin.Path + " 与请求不一致：请求 " + Shown(pin.Value) + "，保存 " + Shown(stored), pin.Path);
        }

        static string Shown(string value)
        {
            return value.Trim().Length == 0 ? "空" : value.Trim();
        }

        // 数值按数值、日期按日期（只比日期部分）、文本去首尾空白后不分大小写；两边都空为相同。
        internal static bool Same(string kind, string sent, string stored)
        {
            string a = (sent ?? "").Trim();
            string b = (stored ?? "").Trim();
            if (a.Length == 0 || b.Length == 0)
            {
                return a.Length == 0 && b.Length == 0;
            }
            if (kind == "number")
            {
                return SameNumber(a, b);
            }
            if (kind == "date")
            {
                return SameDate(a, b);
            }
            return string.Equals(a, b, StringComparison.OrdinalIgnoreCase);
        }

        static bool SameNumber(string sent, string stored)
        {
            decimal x;
            decimal y;
            return SoUnits.Dec(sent, out x) && SoUnits.Dec(stored, out y) && x == y;
        }

        static bool SameDate(string sent, string stored)
        {
            DateTime x;
            DateTime y;
            return ParseDate(sent, out x) && ParseDate(stored, out y) && x.Date == y.Date;
        }

        static string Q(string name)
        {
            return "[" + name.Replace("]", "]]") + "]";
        }
    }
}
