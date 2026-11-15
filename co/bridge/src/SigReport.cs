using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using System.Web.Script.Serialization;

namespace U8Co
{
    // 把逐行核对结果汇总成按功能分组的 JSON。一个功能取最坏的状态：missing > mismatch > unknown > ok。
    // summary 只有 ok 或 mismatch:<n>，n 是状态为 mismatch 或 missing 的功能个数；unknown 不计入。
    internal static class SigReport
    {
        const int AuditProblems = 30;

        public static string Summary(List<SigEntry> entries)
        {
            Dictionary<string, string> features = FeatureStatus(entries);
            int bad = 0;
            foreach (string status in features.Values)
            {
                if (status == "mismatch" || status == "missing")
                {
                    bad++;
                }
            }
            return bad == 0 ? "ok" : "mismatch:" + bad.ToString(CultureInfo.InvariantCulture);
        }

        public static Dictionary<string, object> Build(List<SigEntry> entries, DateTime checkedUtc)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["summary"] = Summary(entries);
            body["checked_utc"] = checkedUtc.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture);
            body["counts"] = Counts(entries);
            body["features"] = Features(entries);
            return body;
        }

        static Dictionary<string, string> FeatureStatus(List<SigEntry> entries)
        {
            Dictionary<string, string> map = new Dictionary<string, string>(StringComparer.Ordinal);
            for (int i = 0; i < entries.Count; i++)
            {
                string key = entries[i].Need.Feature;
                string cur;
                if (!map.TryGetValue(key, out cur) || Rank(entries[i].Status) > Rank(cur))
                {
                    map[key] = entries[i].Status;
                }
            }
            return map;
        }

        static int Rank(string status)
        {
            switch (status)
            {
                case "missing":
                    return 3;
                case "mismatch":
                    return 2;
                case "unknown":
                    return 1;
                default:
                    return 0;
            }
        }

        static Dictionary<string, object> Counts(List<SigEntry> entries)
        {
            Dictionary<string, object> counts = new Dictionary<string, object>();
            counts["ok"] = 0;
            counts["mismatch"] = 0;
            counts["missing"] = 0;
            counts["unknown"] = 0;
            for (int i = 0; i < entries.Count; i++)
            {
                string key = entries[i].Status;
                counts[key] = (counts.ContainsKey(key) ? (int)counts[key] : 0) + 1;
            }
            return counts;
        }

        // 每个功能：status、受影响的路由（只列非 ok 的行涉及的）、非 ok 或带提示的行。
        static Dictionary<string, object> Features(List<SigEntry> entries)
        {
            Dictionary<string, string> status = FeatureStatus(entries);
            Dictionary<string, object> result = new Dictionary<string, object>();
            foreach (KeyValuePair<string, string> pair in status)
            {
                Dictionary<string, object> one = new Dictionary<string, object>();
                one["status"] = pair.Value;
                one["routes"] = AffectedRoutes(entries, pair.Key);
                one["entries"] = Interesting(entries, pair.Key);
                result[pair.Key] = one;
            }
            return result;
        }

        static List<object> AffectedRoutes(List<SigEntry> entries, string feature)
        {
            List<object> routes = new List<object>();
            for (int i = 0; i < entries.Count; i++)
            {
                SigEntry e = entries[i];
                if (e.Need.Feature != feature || e.Status == "ok")
                {
                    continue;
                }
                string[] parts = e.Need.Routes.Split(',');
                for (int j = 0; j < parts.Length; j++)
                {
                    if (!routes.Contains(parts[j]))
                    {
                        routes.Add(parts[j]);
                    }
                }
            }
            return routes;
        }

        static List<object> Interesting(List<SigEntry> entries, string feature)
        {
            List<object> rows = new List<object>();
            for (int i = 0; i < entries.Count; i++)
            {
                SigEntry e = entries[i];
                if (e.Need.Feature != feature || (e.Status == "ok" && e.Note.Length == 0))
                {
                    continue;
                }
                Dictionary<string, object> row = new Dictionary<string, object>();
                row["progid"] = e.Need.ProgId;
                row["member"] = e.Need.Member;
                row["invoke"] = e.Need.Invoke;
                row["args"] = e.Need.ArgCount;
                row["status"] = e.Status;
                row["detail"] = e.Detail;
                row["note"] = e.Note;
                rows.Add(row);
            }
            return rows;
        }

        // 审计日志里只放摘要和至多 30 条非 ok 行的一句话。
        public static List<object> Problems(List<SigEntry> entries)
        {
            List<object> list = new List<object>();
            for (int i = 0; i < entries.Count && list.Count < AuditProblems; i++)
            {
                SigEntry e = entries[i];
                if (e.Status == "ok")
                {
                    continue;
                }
                string member = e.Need.Member.Length == 0 ? "" : "." + e.Need.Member;
                list.Add(e.Need.Feature + " " + e.Need.ProgId + member + " " + e.Status + " " + e.Detail);
            }
            return list;
        }

        // 命令行输出只用 ASCII：非 ASCII 字符写成 \uXXXX，控制台代码页不影响安装脚本解析。
        public static string AsciiJson(object value)
        {
            JavaScriptSerializer ser = new JavaScriptSerializer();
            ser.MaxJsonLength = Json.ResponseLimit;
            string text = ser.Serialize(value);
            StringBuilder sb = new StringBuilder(text.Length + 64);
            for (int i = 0; i < text.Length; i++)
            {
                char c = text[i];
                if (c < 128)
                {
                    sb.Append(c);
                }
                else
                {
                    sb.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                }
            }
            return sb.ToString();
        }
    }
}
