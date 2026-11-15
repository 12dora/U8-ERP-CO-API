using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    internal static partial class PurchaseEdit
    {
        static Dictionary<string, string> FromRow(object row)
        {
            Dictionary<string, string> map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            Take(map, row, "cinvcode");
            Take(map, row, "iquantity");
            Take(map, row, "iunitprice");
            Take(map, row, "itaxprice");
            Take(map, row, "ipertaxrate");
            Take(map, row, "darrivedate");
            Take(map, row, "btaxcost");
            Take(map, row, "cunitid");
            Take(map, row, "inum");
            Take(map, row, "iinvexchrate");
            return map;
        }

        static void Take(Dictionary<string, string> map, object row, string name)
        {
            string text = DomRows.Get(row, name);
            if (text != null && text.Trim().Length > 0)
            {
                map[name] = text.Trim();
            }
        }

        static void WriteRow(object dom, object row, Dictionary<string, string> fields, List<string> schema)
        {
            foreach (KeyValuePair<string, string> pair in fields)
            {
                DomRows.Set(dom, row, pair.Key, pair.Value, schema);
            }
        }

        // 换了存货又没指定单位时，丢掉原行的单位、换算率和件数，让新存货自己的档案生效。
        static void DropStaleUnit(object source, Dictionary<string, string> calc, Dictionary<string, string> fields)
        {
            if (source == null || calc == null || fields == null)
            {
                return;
            }
            if (!PuFields.Contains(fields, "cinvcode") || PuFields.Contains(fields, "cunitid"))
            {
                return;
            }
            string next = fields["cinvcode"] == null ? "" : fields["cinvcode"].Trim();
            string prev = DomRows.Get(source, "cinvcode").Trim();
            if (next.Length == 0 || string.Equals(next, prev, StringComparison.OrdinalIgnoreCase))
            {
                return;
            }
            calc.Remove("cunitid");
            calc.Remove("iinvexchrate");
            if (!PuFields.Contains(fields, "inum"))
            {
                calc.Remove("inum");
            }
        }

        static void ClearUnit(object dom, object row, List<string> schema)
        {
            Drop(dom, row, "cunitid", schema);
            Drop(dom, row, "inum", schema);
            Drop(dom, row, "iinvexchrate", schema);
        }

        static void Drop(object dom, object row, string name, List<string> schema)
        {
            string text = DomRows.Get(row, name);
            if (text != null && text.Length > 0)
            {
                DomRows.Set(dom, row, name, null, schema);
            }
        }

        static void ClearNew(object dom, object row, List<string> schema)
        {
            Drop(dom, row, "id", schema);
            Drop(dom, row, "autoid", schema);
        }

        static int NextNo(object body)
        {
            int max = 0;
            List<object> rows = DomRows.RowsOf(body);
            if (rows == null)
            {
                return 1;
            }
            for (int i = 0; i < rows.Count; i++)
            {
                int no = CoRows.AsId(DomRows.Get(rows[i], "ivouchrowno"));
                if (no > max)
                {
                    max = no;
                }
            }
            return max + 1;
        }

        static PuOp FindOp(List<PuOp> ops, int id)
        {
            if (id <= 0)
            {
                return null;
            }
            for (int i = 0; i < ops.Count; i++)
            {
                if (ops[i].Op != "add" && ops[i].Id == id)
                {
                    return ops[i];
                }
            }
            return null;
        }

        static object FindRow(List<object> rows, int id)
        {
            if (rows == null || id <= 0)
            {
                return null;
            }
            for (int i = 0; i < rows.Count; i++)
            {
                if (LineId(rows[i]) == id)
                {
                    return rows[i];
                }
            }
            return null;
        }

        static int LineId(object row)
        {
            return CoRows.AsId(DomRows.Get(row, "id"));
        }

        static int IndexOf(int[] ids, int id)
        {
            for (int i = 0; i < ids.Length; i++)
            {
                if (ids[i] == id)
                {
                    return i;
                }
            }
            return -1;
        }

        static object OnlyRow(object dom)
        {
            List<object> rows = DomRows.RowsOf(dom);
            if (rows == null || rows.Count != 1)
            {
                throw new BridgeException(500, "internal", "模板没有字段");
            }
            return rows[0];
        }

        static object EnsureRow(object dom)
        {
            List<object> rows = DomRows.RowsOf(dom);
            int count = rows == null ? 0 : rows.Count;
            if (count == 1)
            {
                return rows[0];
            }
            if (count > 1)
            {
                throw new BridgeException(500, "internal", "空白模板行数不正确");
            }
            return DomRows.AddRow(dom);
        }

        static void RequireSchema(object[] doms)
        {
            List<string> head = DomRows.Schema(doms[0]);
            List<string> body = DomRows.Schema(doms[1]);
            if (head == null || body == null || head.Count == 0 || body.Count == 0)
            {
                throw new BridgeException(500, "internal", "模板没有字段");
            }
        }

        static int Vt(string name)
        {
            if (name == "purchase_order")
            {
                return 1;
            }
            if (name == "arrival")
            {
                return 2;
            }
            throw new BridgeException(400, "bad_request", "该单据类型不支持");
        }

        static string Clean(string text)
        {
            if (text == null)
            {
                return "";
            }
            return text.Trim();
        }

        static string Pick(Dictionary<string, string> map, string key, string fallback)
        {
            if (PuFields.Has(map, key))
            {
                return map[key];
            }
            return fallback;
        }

        static string Maker(WorkContext ctx)
        {
            string name = ctx.Session.OperatorName == null ? "" : ctx.Session.OperatorName.Trim();
            if (name.Length == 0 && ctx.Item != null && ctx.Item.Operator != null)
            {
                name = ctx.Item.Operator.Trim();
            }
            return name;
        }

        static string LoginDate(WorkContext ctx)
        {
            string date = ctx.Item == null || ctx.Item.Date == null ? "" : ctx.Item.Date.Trim();
            if (date.Length == 0)
            {
                return DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
            }
            return date;
        }

        static string Unknown(string code)
        {
            return Unknown(0, code);
        }

        static string Unknown(int id, string code)
        {
            string msg = "已保存但未能确定单据标识";
            string no = code == null ? "" : code.Trim();
            if (no.Length > 0)
            {
                msg = msg + "，单号 " + no;
            }
            if (id > 0)
            {
                msg = msg + "，标识 " + id.ToString(CultureInfo.InvariantCulture);
            }
            return msg;
        }

        static int CountOf(object value)
        {
            if (CountBlank(value))
            {
                return 0;
            }
            int whole;
            if (TryWhole(value, out whole))
            {
                return whole;
            }
            return ParsedCount(value);
        }

        static bool CountBlank(object value)
        {
            return value == null || value is DBNull || value == Type.Missing;
        }

        static bool TryWhole(object value, out int whole)
        {
            if (value is short)
            {
                whole = (short)value;
                return true;
            }
            if (value is int)
            {
                whole = (int)value;
                return true;
            }
            if (value is byte)
            {
                whole = (byte)value;
                return true;
            }
            if (value is long)
            {
                whole = FitCount((long)value);
                return true;
            }
            whole = 0;
            return false;
        }

        static int FitCount(long wide)
        {
            if (wide > int.MaxValue || wide < int.MinValue)
            {
                return 0;
            }
            return (int)wide;
        }

        static int ParsedCount(object value)
        {
            int parsed;
            string text = Convert.ToString(value, CultureInfo.InvariantCulture).Trim();
            if (int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out parsed))
            {
                return parsed;
            }
            return 0;
        }

        static int AsInt(object value)
        {
            if (value is int)
            {
                return (int)value;
            }
            if (value is short)
            {
                return (short)value;
            }
            if (value is byte)
            {
                return (byte)value;
            }
            if (value is long)
            {
                return FitId((long)value);
            }
            if (value is decimal)
            {
                return FitDecimal((decimal)value);
            }
            if (value is double)
            {
                return FitDouble((double)value);
            }
            return CoRows.AsId(value);
        }

        static int FitId(long wide)
        {
            if (wide > int.MaxValue || wide < 1)
            {
                return 0;
            }
            return (int)wide;
        }

        static int FitDecimal(decimal number)
        {
            if (number != decimal.Truncate(number) || number < 1m || number > int.MaxValue)
            {
                return 0;
            }
            return (int)number;
        }

        static int FitDouble(double number)
        {
            if (number < 1d || number > int.MaxValue || number != Math.Truncate(number))
            {
                return 0;
            }
            return (int)number;
        }

        static object Cell(Dictionary<string, object> row, string name)
        {
            if (row == null || name == null)
            {
                return null;
            }
            object value;
            if (row.TryGetValue(name, out value))
            {
                return value;
            }
            foreach (KeyValuePair<string, object> pair in row)
            {
                if (string.Equals(pair.Key, name, StringComparison.OrdinalIgnoreCase))
                {
                    return pair.Value;
                }
            }
            return null;
        }

        static string Show(object value)
        {
            if (value == null || value is DBNull)
            {
                return "";
            }
            if (value is DateTime)
            {
                DateTime at = (DateTime)value;
                string pattern = at.TimeOfDay.Ticks == 0 ? "yyyy-MM-dd" : "yyyy-MM-dd HH:mm:ss";
                return at.ToString(pattern, CultureInfo.InvariantCulture);
            }
            return Values.Text(value).Trim();
        }

        static void Release(object info, object co, object[] doms)
        {
            if (doms != null)
            {
                ComUtil.Final(doms[1]);
                ComUtil.Final(doms[0]);
            }
            ComUtil.Final(co);
            ComUtil.Final(info);
        }
    }

    sealed class PuDraft
    {
        public object Conn;
        public decimal Exch;
        public string Tax;
        public string Arrive;
        public bool FlatChanged;
        // 新增行缺税率时取存货档案税率（请求表头没给 itaxrate；修改时订单表头已存的税率也为空）。
        public bool InvTax;
        public string PoId;
        public string Code;
    }
}
