using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 退货申请单（sale_return_apply）写入的登录前校验（Requests.CheckKind 调用）：
    // 新增每行参照一条蓝字发货单行（source_line_id = DispatchLists.iDLsID、quantity 正数，桥写负数），不收无来源行；
    // 修改只改已有行（op=update：iquantity 正数、cmemo、creasoncode、cdefine22–37）和表头 ddate、cmemo、cdefine1–16。
    internal static class ReturnsApplyReq
    {
        const string V = "/u8co/v1/vouchers/";
        const string CreateHead = "ddate,cmemo,cdepcode,cpersoncode";
        const string CreateLine = "cwhcode,cmemo,creasoncode";
        const string EditHead = "ddate,cmemo";
        const string EditLine = "iquantity,cmemo,creasoncode";
        const decimal QtyMax = 1000000000000m;

        // 返回 true 表示是退货申请单的请求（新增、修改已校验；其余路由不用另查）。
        internal static bool Check(WorkItem item, string path)
        {
            if (item == null || !ReturnsApplyRead.Is(item.Type))
            {
                return false;
            }
            if (path == V + "create")
            {
                CheckCreate(item.Head, item.Lines);
            }
            else if (path == V + "update")
            {
                CheckUpdate(item.Head, item.Lines);
            }
            return true;
        }

        internal static bool HeadAllowed(string key, bool create)
        {
            string low = Low(key);
            return Listed(low, create ? CreateHead : EditHead) || ArapReq.Span(low, "cdefine", 1, 16);
        }

        internal static bool LineAllowed(string key, bool create)
        {
            string low = Low(key);
            return Listed(low, create ? CreateLine : EditLine) || ArapReq.Span(low, "cdefine", 22, 37);
        }

        static void CheckCreate(Dictionary<string, object> head, object[] lines)
        {
            CheckHead(head ?? new Dictionary<string, object>(), true);
            if (lines == null || lines.Length < 1 || lines.Length > 200)
            {
                throw BridgeException.BadField("lines", "表体行数必须在 1 到 200 之间");
            }
            HashSet<int> seen = new HashSet<int>();
            for (int i = 0; i < lines.Length; i++)
            {
                CheckCreateLine(Row(lines[i], i), i, seen);
            }
        }

        static void CheckCreateLine(Dictionary<string, object> map, int i, HashSet<int> seen)
        {
            int lineId = IdOf(map, "source_line_id", i);
            if (lineId == 0)
            {
                throw BridgeException.BadField(Field(i, "source_line_id"),
                    "退货申请单新增的每一行都要参照蓝字发货单行（source_line_id）");
            }
            if (!seen.Add(lineId))
            {
                throw BridgeException.BadField(Field(i, "source_line_id"), "明细行重复");
            }
            Qty(map, "quantity", i);
            foreach (string key in map.Keys)
            {
                if (!Same(key, "source_line_id") && !Same(key, "quantity") && !LineAllowed(key, true))
                {
                    throw BridgeException.BadField(Field(i, key), "不能设置字段 " + key);
                }
            }
        }

        static void CheckUpdate(Dictionary<string, object> head, object[] lines)
        {
            Dictionary<string, object> h = head ?? new Dictionary<string, object>();
            object[] rows = lines ?? new object[0];
            if (h.Count == 0 && rows.Length == 0)
            {
                throw new BridgeException(400, "bad_request", "没有要修改的内容");
            }
            if (rows.Length > 200)
            {
                throw BridgeException.BadField("lines", "lines 不能超过 200 行");
            }
            CheckHead(h, false);
            HashSet<int> seen = new HashSet<int>();
            for (int i = 0; i < rows.Length; i++)
            {
                CheckEditRow(Row(rows[i], i), i, seen);
            }
        }

        static void CheckEditRow(Dictionary<string, object> map, int i, HashSet<int> seen)
        {
            if (Text(Raw(map, "op")) != "update")
            {
                throw BridgeException.BadField(Field(i, "op"), "退货申请单修改只能改已有行（op=update），不能新增或删除行");
            }
            int lineId = IdOf(map, "line_id", i);
            if (lineId == 0 || !seen.Add(lineId))
            {
                throw BridgeException.BadField(Field(i, "line_id"), lineId == 0 ? "line_id 无效" : "line_id 重复");
            }
            CheckEditLine(map, i);
        }

        static void CheckEditLine(Dictionary<string, object> map, int i)
        {
            int fields = 0;
            foreach (string key in map.Keys)
            {
                if (Same(key, "op") || Same(key, "line_id"))
                {
                    continue;
                }
                if (!LineAllowed(key, false))
                {
                    throw BridgeException.BadField(Field(i, key), "不能设置字段 " + key);
                }
                fields++;
            }
            if (fields == 0)
            {
                throw BridgeException.BadField(Field(i, "line_id"), "修改行至少再改一个字段");
            }
            if (Raw(map, "iquantity") != null)
            {
                Qty(map, "iquantity", i);
            }
        }

        static void CheckHead(Dictionary<string, object> head, bool create)
        {
            foreach (KeyValuePair<string, object> pair in head)
            {
                if (!HeadAllowed(pair.Key, create))
                {
                    throw BridgeException.BadField("head." + pair.Key, "不能设置字段 " + pair.Key);
                }
                if (pair.Value is Dictionary<string, object> || pair.Value is System.Collections.ArrayList)
                {
                    throw BridgeException.BadField("head." + pair.Key, "字段值不能嵌套");
                }
            }
            string date = Text(Raw(head, "ddate"));
            DateTime day;
            if (date.Length > 0 && !DateTime.TryParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out day))
            {
                throw BridgeException.BadField("head.ddate", "单据日期必须是 yyyy-MM-dd");
            }
        }

        // 数量：数字（不收字符串），大于 0、不超过 1000000000000、最多 6 位小数。
        internal static decimal Qty(Dictionary<string, object> map, string name, int index)
        {
            decimal qty;
            if (!TryQty(Raw(map, name), out qty) || qty <= 0m || qty > QtyMax || decimal.Round(qty, 6) != qty)
            {
                throw BridgeException.BadField(Field(index, name), name + " 必须是大于 0、不超过 1000000000000、最多 6 位小数的数");
            }
            return qty;
        }

        static bool TryQty(object value, out decimal qty)
        {
            qty = 0m;
            try
            {
                if (value is int || value is long || value is decimal)
                {
                    qty = Convert.ToDecimal(value, CultureInfo.InvariantCulture);
                    return true;
                }
                if (value is double && !double.IsNaN((double)value) && !double.IsInfinity((double)value))
                {
                    qty = (decimal)(double)value;
                    return true;
                }
            }
            catch (OverflowException)
            {
                return false;
            }
            return false;
        }

        // 严格整数（1 到 2147483647）；缺省返回 0。布尔、字符串、浮点 400。
        internal static int IdOf(Dictionary<string, object> map, string name, int index)
        {
            object value = Raw(map, name);
            if (value == null)
            {
                return 0;
            }
            long number;
            if (value is int)
            {
                number = (int)value;
            }
            else if (value is long)
            {
                number = (long)value;
            }
            else
            {
                throw BridgeException.BadField(Field(index, name), name + " 必须是整数");
            }
            if (number < 1 || number > int.MaxValue)
            {
                throw BridgeException.BadField(Field(index, name), name + " 无效");
            }
            return (int)number;
        }

        internal static object Raw(Dictionary<string, object> map, string name)
        {
            if (map == null)
            {
                return null;
            }
            foreach (KeyValuePair<string, object> pair in map)
            {
                if (Same(pair.Key, name) && pair.Value != null && !(pair.Value is DBNull))
                {
                    return pair.Value;
                }
            }
            return null;
        }

        static Dictionary<string, object> Row(object raw, int index)
        {
            Dictionary<string, object> map = raw as Dictionary<string, object>;
            if (map == null)
            {
                throw BridgeException.BadField("lines." + index.ToString(CultureInfo.InvariantCulture), "表体行必须是对象");
            }
            return map;
        }

        static string Field(int index, string name)
        {
            return "lines." + index.ToString(CultureInfo.InvariantCulture) + "." + name;
        }

        static string Text(object value)
        {
            return value == null ? "" : Convert.ToString(value, CultureInfo.InvariantCulture).Trim();
        }

        static string Low(string key)
        {
            return key == null ? "" : key.ToLowerInvariant();
        }

        static bool Listed(string low, string csv)
        {
            return low.Length > 0 && ("," + csv + ",").IndexOf("," + low + ",", StringComparison.Ordinal) >= 0;
        }

        static bool Same(string key, string name)
        {
            return string.Equals(key, name, StringComparison.OrdinalIgnoreCase);
        }
    }
}
