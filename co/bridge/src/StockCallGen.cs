using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 调拨审核生成的其他出入库主键。
    internal static partial class StockCall
    {

        public static List<Dictionary<string, object>> Generated(WorkContext ctx, object dict)
        {
            List<Dictionary<string, object>> list = new List<Dictionary<string, object>>();
            if (dict == null)
            {
                return list;
            }
            int count = Convert.ToInt32(ComUtil.Get(dict, "Count"));
            List<string> keys = Variants(DictPart(dict, "Keys"));
            List<string> items = Variants(DictPart(dict, "Items"));
            NoteKeys(ctx, keys);
            List<int> outs = new List<int>();
            List<int> inns = new List<int>();
            for (int i = 0; i < keys.Count; i++)
            {
                string item = i < items.Count ? items[i] : "";
                TakeGenerated(ctx.Conn, keys[i], item, outs, inns);
            }
            if (count > 0 && outs.Count == 0 && inns.Count == 0)
            {
                throw new BridgeException(504, "outcome_unknown", "审核已完成但未能读出调拨生成的单据");
            }
            AddGenerated(list, "other_out", outs);
            AddGenerated(list, "other_in", inns);
            return list;
        }

        static void TakeGenerated(object conn, string key, string item, List<int> outs, List<int> inns)
        {
            int id;
            string hint;
            if (ParseId(key, out id))
            {
                hint = item;
            }
            else if (ParseId(item, out id))
            {
                hint = key;
            }
            else
            {
                return;
            }
            string kind = RdKind(conn, id);
            if (kind.Length == 0)
            {
                kind = HintKind(hint);
            }
            if (kind == "other_out" && !outs.Contains(id))
            {
                outs.Add(id);
            }
            if (kind == "other_in" && !inns.Contains(id))
            {
                inns.Add(id);
            }
        }

        static string RdKind(object conn, int id)
        {
            if (HitRd(conn, Kinds.Find("other_out"), id))
            {
                return "other_out";
            }
            if (HitRd(conn, Kinds.Find("other_in"), id))
            {
                return "other_in";
            }
            return "";
        }

        static bool HitRd(object conn, VoucherKind kind, int id)
        {
            if (kind == null)
            {
                return false;
            }
            string sql = "select top 1 convert(varchar(20), " + kind.IdColumn + ") from " + kind.HeadTable
                + " where " + kind.IdColumn + "=?";
            return Rows.Scalar(conn, sql, new object[] { id }) != null;
        }

        static string HintKind(string text)
        {
            string low = text == null ? "" : text.ToLowerInvariant();
            if (low.IndexOf("09", StringComparison.Ordinal) >= 0 || low.IndexOf("other_out", StringComparison.Ordinal) >= 0)
            {
                return "other_out";
            }
            if (low.IndexOf("08", StringComparison.Ordinal) >= 0 || low.IndexOf("other_in", StringComparison.Ordinal) >= 0)
            {
                return "other_in";
            }
            return "";
        }

        static void AddGenerated(List<Dictionary<string, object>> list, string type, List<int> ids)
        {
            for (int i = 0; i < ids.Count; i++)
            {
                Dictionary<string, object> one = new Dictionary<string, object>();
                one["type"] = type;
                one["id"] = ids[i];
                list.Add(one);
            }
        }

        static bool ParseId(string text, out int id)
        {
            id = 0;
            if (text == null)
            {
                return false;
            }
            text = text.Trim();
            if (int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out id) && id > 0)
            {
                return true;
            }
            decimal num;
            if (!decimal.TryParse(text, NumberStyles.Any, CultureInfo.InvariantCulture, out num))
            {
                return false;
            }
            if (num != decimal.Truncate(num) || num <= 0m || num > int.MaxValue)
            {
                return false;
            }
            id = (int)num;
            return true;
        }

        static object DictPart(object dict, string name)
        {
            try
            {
                return ComUtil.Get(dict, name);
            }
            catch (Exception)
            {
                return ComUtil.Call(dict, name, new object[0]);
            }
        }

        static List<string> Variants(object value)
        {
            List<string> list = new List<string>();
            if (value == null || value is DBNull)
            {
                return list;
            }
            Array arr = value as Array;
            if (arr == null)
            {
                list.Add(Values.Text(value).Trim());
                return list;
            }
            foreach (object one in arr)
            {
                list.Add(Values.Text(one).Trim());
            }
            return list;
        }

        static void NoteKeys(WorkContext ctx, List<string> keys)
        {
            if (ctx == null || keys == null || keys.Count == 0)
            {
                return;
            }
            string text = "keys=" + string.Join(",", keys.ToArray());
            if (text.Length > 180)
            {
                text = text.Substring(0, 180);
            }
            CoRows.Note(ctx.Item, text);
        }

    }
}
