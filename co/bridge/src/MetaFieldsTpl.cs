using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 单据模板的一行：voucheritems（类型、必输、长度、枚举）+ voucheritems_lang 的中文标题（zh-cn）。
    internal sealed class TplRow
    {
        public string Name;
        public bool Body;
        public int FieldType = -1;
        public bool IsNull;
        public bool IsEnum;
        public string EnumType = "";
        public int MaxLength;
        public string Label;
    }

    // meta/fields 的模板读取与字段描述。只有参数化 SQL，每次请求现读（两条小查询），不缓存。
    internal static class MetaFieldsTpl
    {
        const int MaxRows = 3000;
        internal const int EnumChunk = 50;
        const string TplSql = "SELECT vi.FieldName AS f, vi.CardSection AS s, vi.FieldType AS t, vi.IsNull AS n, "
            + "vi.IsEnum AS e, vi.EnumType AS et, vi.MaxLength AS m, vl.carditemname AS c FROM voucheritems vi "
            + "LEFT JOIN voucheritems_lang vl ON vl.vt_id=vi.VT_ID AND vl.fieldname=vi.FieldName "
            + "AND vl.cardsection=vi.CardSection AND vl.localeid='zh-cn' WHERE vi.VT_ID=?";

        internal static List<TplRow> Load(object conn, int vt)
        {
            List<TplRow> rows = new List<TplRow>();
            if (vt <= 0)
            {
                return rows;
            }
            List<Dictionary<string, object>> raw = Rows.Query(conn, TplSql, new object[] { vt }, MaxRows);
            for (int i = 0; i < raw.Count; i++)
            {
                TplRow row = RowOf(raw[i]);
                if (row != null)
                {
                    rows.Add(row);
                }
            }
            return rows;
        }

        internal static TplRow RowOf(Dictionary<string, object> raw)
        {
            string name = CoRows.Col(raw, "f").Trim();
            if (name.Length == 0)
            {
                return null;
            }
            TplRow row = new TplRow();
            row.Name = name;
            row.Body = string.Equals(CoRows.Col(raw, "s").Trim(), "B", StringComparison.OrdinalIgnoreCase);
            row.FieldType = Int(CoRows.Col(raw, "t"), -1);
            row.IsNull = CoRows.Col(raw, "n").Trim() == "1";
            row.IsEnum = CoRows.Col(raw, "e").Trim() == "1";
            row.EnumType = CoRows.Col(raw, "et").Trim();
            row.MaxLength = Int(CoRows.Col(raw, "m"), 0);
            string label = CoRows.Col(raw, "c").Trim();
            row.Label = label.Length == 0 ? null : label;
            return row;
        }

        // 表头只配 CardSection 不是 B 的行，表体只配 B 行；字段名不分大小写。
        internal static Dictionary<string, TplRow> Index(List<TplRow> rows, bool body)
        {
            Dictionary<string, TplRow> map = new Dictionary<string, TplRow>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < rows.Count; i++)
            {
                if (rows[i].Body == body && !map.ContainsKey(rows[i].Name))
                {
                    map[rows[i].Name] = rows[i];
                }
            }
            return map;
        }

        // 枚举值：AA_Enum（zh-CN），按 EnumIndex。只查 rows 里用得到的枚举类型，IN 列表按 EnumChunk 个一批。
        internal static Dictionary<string, List<object>> Enums(object conn, List<TplRow> used)
        {
            Dictionary<string, List<object>> map = new Dictionary<string, List<object>>(StringComparer.OrdinalIgnoreCase);
            List<string> types = EnumTypes(used);
            for (int at = 0; at < types.Count; at += EnumChunk)
            {
                int n = Math.Min(EnumChunk, types.Count - at);
                object[] args = types.GetRange(at, n).ToArray();
                AddEnums(map, Rows.Query(conn, EnumSql(n), args, MaxRows));
            }
            return map;
        }

        internal static List<string> EnumTypes(List<TplRow> used)
        {
            List<string> types = new List<string>();
            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < used.Count; i++)
            {
                if (used[i].IsEnum && used[i].EnumType.Length > 0 && seen.Add(used[i].EnumType))
                {
                    types.Add(used[i].EnumType);
                }
            }
            return types;
        }

        static void AddEnums(Dictionary<string, List<object>> map, List<Dictionary<string, object>> raw)
        {
            for (int i = 0; i < raw.Count; i++)
            {
                string type = CoRows.Col(raw[i], "t");
                List<object> list;
                if (!map.TryGetValue(type, out list))
                {
                    list = new List<object>();
                    map[type] = list;
                }
                Dictionary<string, object> item = new Dictionary<string, object>();
                item["code"] = CoRows.Col(raw[i], "c");
                item["name"] = CoRows.Col(raw[i], "n");
                list.Add(item);
            }
        }

        internal static string EnumSql(int count)
        {
            StringBuilder sql = new StringBuilder("SELECT EnumType AS t, EnumCode AS c, EnumName AS n FROM AA_Enum "
                + "WHERE LocaleId='zh-CN' AND EnumType IN (");
            for (int i = 0; i < count; i++)
            {
                sql.Append(i == 0 ? "?" : ",?");
            }
            return sql.Append(") ORDER BY EnumType, EnumIndex").ToString();
        }

        // FieldType：0 bool，1 string（IsEnum=1 且有枚举值时 enum），2 / 3 int，4 decimal，5 date；其他 string。
        // 非字符串型的枚举字段 type 保持基础类型，另带 enum（Entry）。
        internal static string TypeOf(int fieldType, bool isEnum, bool hasValues)
        {
            switch (fieldType)
            {
                case 0:
                    return "bool";
                case 1:
                    return isEnum && hasValues ? "enum" : "string";
                case 2:
                case 3:
                    return "int";
                case 4:
                    return "decimal";
                case 5:
                    return "date";
            }
            return "string";
        }

        // 一个字段的描述。row 为 null（模板里没有这个字段）时 label、type 都是 null。
        internal static Dictionary<string, object> Entry(string name, TplRow row, bool bridgeRequired, List<object> values)
        {
            Dictionary<string, object> d = new Dictionary<string, object>();
            d["name"] = name;
            if (row == null)
            {
                d["label"] = null;
                d["type"] = null;
                d["required"] = bridgeRequired;
                return d;
            }
            bool hasValues = values != null && values.Count > 0;
            string type = TypeOf(row.FieldType, row.IsEnum, hasValues);
            d["label"] = row.Label;
            d["type"] = type;
            d["required"] = bridgeRequired || row.IsNull;
            if ((type == "string" || type == "enum") && row.MaxLength > 0)
            {
                d["max_length"] = row.MaxLength;
            }
            // IsEnum=1 且有枚举值就带 enum，不论 FieldType；type 仍是基础类型（字符串型为 "enum"）。
            if (row.IsEnum && hasValues)
            {
                d["enum"] = values;
            }
            return d;
        }

        static int Int(string text, int fallback)
        {
            int value;
            if (text == null || !int.TryParse(text.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out value))
            {
                return fallback;
            }
            return value;
        }
    }
}
