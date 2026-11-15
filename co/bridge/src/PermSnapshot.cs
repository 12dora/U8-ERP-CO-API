using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;

namespace U8Co
{
    // perm/snapshot：把读路由过滤用的同一个 PermContext（PermCheck.Of，60 秒缓存）原样序列化，作为人员权限的唯一来源。
    // 只读、读线程池，只用 ctx.Conn。不含口令、档案名称；受控对象的编码集合为空表示一行都不给（与 U8 一致，空不等于全部）。
    // perm/evaluate（PermEvaluate）用同一个 ToJson 序列化被查询操作员的快照。
    internal static class PermSnapshot
    {
        internal const string Path = "/u8co/v1/perm/snapshot";
        internal const string Action = "perm_snapshot";
        internal const string RuleKey = "perm:snapshot";
        internal const int TtlSeconds = 60;
        internal static readonly string[] Spec = new string[] { Path };

        // 本路由或 perm/evaluate。
        public static bool Owns(string path)
        {
            return string.Equals(path, Path, StringComparison.Ordinal) || string.Equals(path, PermEvaluate.Path, StringComparison.Ordinal);
        }

        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Item == null)
            {
                throw new BridgeException(500, "internal", "缺少请求上下文");
            }
            PermContext p = PermCheck.Of(ctx);
            Dictionary<string, object> body = ToJson(p);
            CoRows.Note(ctx.Item, Summary(p, body));
            return ApiResult.Ok(body);
        }

        internal static Dictionary<string, object> ToJson(PermContext p)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["acc"] = p.Acc ?? "";
            body["year"] = p.Year;
            body["acct_year"] = p.AcctYear;
            body["operator"] = p.Operator ?? "";
            body["supervisor"] = p.Supervisor;
            body["gl_subj_ctl"] = p.GlSubjCtl;
            body["roles"] = Sorted(p.Roles);
            body["functions"] = Sorted(p.Funcs);
            body["objects_on"] = Sorted(p.On);
            body["data_admin"] = Sorted(p.DataAdmin);
            Dictionary<string, object> data = DataOf(p);
            body["data"] = data;
            Dictionary<string, object> columns = ColumnsOf(p);
            body["columns"] = columns;
            body["ttl_s"] = TtlSeconds;
            body["fingerprint"] = Fingerprint(p.Supervisor, (List<string>)body["functions"], data, columns);
            return body;
        }

        // 只要指纹（mgmt/meta 等）：输入与 ToJson 完全相同，同一操作员得到与 perm/snapshot 一致的值。
        internal static string FingerprintOf(PermContext p)
        {
            return Fingerprint(p.Supervisor, Sorted(p.Funcs), DataOf(p), ColumnsOf(p));
        }

        // 字段权限：{对象 id: [拒绝查看的字段]}，只列至少有一个字段的对象，对象与字段都按序号序排列。主管、没有字段权限为 {}。
        internal static Dictionary<string, object> ColumnsOf(PermContext p)
        {
            Dictionary<string, object> columns = new Dictionary<string, object>(StringComparer.Ordinal);
            if (p.Supervisor)
            {
                return columns;
            }
            foreach (KeyValuePair<string, HashSet<string>> pair in p.Columns)
            {
                if (pair.Value.Count > 0)
                {
                    columns[pair.Key] = Sorted(pair.Value);
                }
            }
            return columns;
        }

        // 每个读取对象（PermObj.ReadObjects，新增的读取对象自动出现）一项：不受控 {all:true}；受控 {codes:[…]}（fitem 为 {pairs:[{class,code}]}），超过 PermSql.MaxInline 个另标 live。
        internal static Dictionary<string, object> DataOf(PermContext p)
        {
            Dictionary<string, object> data = new Dictionary<string, object>(StringComparer.Ordinal);
            string[] objs = PermObj.ReadObjects();
            for (int i = 0; i < objs.Length; i++)
            {
                if (!PermObj.NotForRead(objs[i]))
                {
                    data[objs[i]] = Entry(p, objs[i]);
                }
            }
            return data;
        }

        static Dictionary<string, object> Entry(PermContext p, string obj)
        {
            Dictionary<string, object> e = new Dictionary<string, object>(StringComparer.Ordinal);
            if (!p.Controls(obj))
            {
                e["all"] = true;
                return e;
            }
            // 受控而没有授权行时 CodesOf 返回空集合：一行都不给，不退回全部。
            List<string> codes = Sorted(p.CodesOf(obj));
            if (string.Equals(obj, PermObj.Item, StringComparison.OrdinalIgnoreCase))
            {
                e["pairs"] = Pairs(codes);
            }
            else
            {
                e["codes"] = codes;
            }
            if (codes.Count > PermSql.MaxInline)
            {
                e["live"] = true;
            }
            return e;
        }

        static List<object> Pairs(List<string> keys)
        {
            List<object> list = new List<object>();
            for (int i = 0; i < keys.Count; i++)
            {
                string key = keys[i];
                int cut = key.IndexOf(PermContext.PairSep);
                Dictionary<string, object> pair = new Dictionary<string, object>(StringComparer.Ordinal);
                pair["class"] = cut < 0 ? "" : key.Substring(0, cut);
                pair["code"] = cut < 0 ? key : key.Substring(cut + 1);
                list.Add(pair);
            }
            return list;
        }

        static List<string> Sorted(IEnumerable<string> items)
        {
            List<string> list = new List<string>();
            if (items != null)
            {
                foreach (string item in items)
                {
                    list.Add(item ?? "");
                }
            }
            list.Sort(StringComparer.Ordinal);
            return list;
        }

        // SHA-256（小写十六进制），输入是 {columns, data, functions, supervisor} 的规范 JSON：键按序号序排列，数组已排序。
        // 不含账套、操作员、角色名、口令；同样的权限得到同样的指纹，与集合的读取顺序无关。字段权限变了指纹也变。
        internal static string Fingerprint(bool supervisor, List<string> functions, Dictionary<string, object> data,
            Dictionary<string, object> columns)
        {
            SortedDictionary<string, object> root = new SortedDictionary<string, object>(StringComparer.Ordinal);
            root["columns"] = columns ?? new Dictionary<string, object>();
            root["data"] = data;
            root["functions"] = Sorted(functions);
            root["supervisor"] = supervisor;
            StringBuilder sb = new StringBuilder();
            Canon(sb, root, new JavaScriptSerializer());
            byte[] hash;
            using (SHA256 sha = SHA256.Create())
            {
                hash = sha.ComputeHash(Encoding.UTF8.GetBytes(sb.ToString()));
            }
            StringBuilder hex = new StringBuilder(hash.Length * 2);
            for (int i = 0; i < hash.Length; i++)
            {
                hex.Append(hash[i].ToString("x2", CultureInfo.InvariantCulture));
            }
            return hex.ToString();
        }

        static void Canon(StringBuilder sb, object value, JavaScriptSerializer ser)
        {
            IDictionary map = value as IDictionary;
            if (map != null)
            {
                CanonMap(sb, map, ser);
                return;
            }
            if (value is bool)
            {
                sb.Append((bool)value ? "true" : "false");
                return;
            }
            IEnumerable list = value as IEnumerable;
            if (list != null && !(value is string))
            {
                sb.Append('[');
                bool first = true;
                foreach (object item in list)
                {
                    sb.Append(first ? "" : ",");
                    first = false;
                    Canon(sb, item, ser);
                }
                sb.Append(']');
                return;
            }
            sb.Append(ser.Serialize(value == null ? "" : Convert.ToString(value, CultureInfo.InvariantCulture)));
        }

        static void CanonMap(StringBuilder sb, IDictionary map, JavaScriptSerializer ser)
        {
            List<string> keys = new List<string>();
            foreach (object key in map.Keys)
            {
                keys.Add(Convert.ToString(key, CultureInfo.InvariantCulture));
            }
            keys.Sort(StringComparer.Ordinal);
            sb.Append('{');
            for (int i = 0; i < keys.Count; i++)
            {
                sb.Append(i == 0 ? "" : ",");
                sb.Append(ser.Serialize(keys[i]));
                sb.Append(':');
                Canon(sb, map[keys[i]], ser);
            }
            sb.Append('}');
        }

        // 审计只记个数和指纹，不记编码清单和字段清单（columns 是拒绝的对象-字段对数）。
        internal static string Summary(PermContext p, Dictionary<string, object> body)
        {
            int controlled = 0;
            int codes = 0;
            Dictionary<string, object> data = body["data"] as Dictionary<string, object>;
            if (data != null)
            {
                foreach (string obj in data.Keys)
                {
                    if (p.Controls(obj))
                    {
                        controlled++;
                        codes += p.CodesOf(obj).Count;
                    }
                }
            }
            return "supervisor=" + (p.Supervisor ? "1" : "0") + " functions=" + p.Funcs.Count.ToString(CultureInfo.InvariantCulture)
                + " controlled=" + controlled.ToString(CultureInfo.InvariantCulture)
                + " codes=" + codes.ToString(CultureInfo.InvariantCulture)
                + " columns=" + ColumnPairs(p).ToString(CultureInfo.InvariantCulture) + " fingerprint=" + body["fingerprint"];
        }

        static int ColumnPairs(PermContext p)
        {
            int n = 0;
            if (!p.Supervisor)
            {
                foreach (HashSet<string> set in p.Columns.Values)
                {
                    n += set.Count;
                }
            }
            return n;
        }
    }
}
