using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // ca_inputs 的参数。Period 为 0 表示缺省（会计年度是登录年度时取登录月份，否则取 12）；Tables 为空表示全部表。
    internal sealed class CaInputsArgs
    {
        public int Period;
        public string[] Tables = new string[0];
        public int Limit;
    }

    // 成本核算期间数据 ca_inputs 请求取值的校验：只抛 400，不连库。会计年度 fiscal_year 已在 ReportsReq.Parse 里校验。
    internal static class ReportsCaParse
    {
        internal const int DefaultLimit = 5000;
        internal const int MaxLimit = 20000;

        internal static CaInputsArgs Inputs(Dictionary<string, object> body)
        {
            CaInputsArgs q = new CaInputsArgs();
            q.Period = Int(body, "period", 0, 1, 12);
            q.Limit = Int(body, "limit", DefaultLimit, 1, MaxLimit);
            q.Tables = Tables(body);
            return q;
        }

        // 表名数组：缺省空（全部表）；给了就是 1 个到全部表个数的名称，每个必须是 ca_inputs 的表，不重复。
        static string[] Tables(Dictionary<string, object> body)
        {
            object raw = GlReq.Field(body, "tables");
            if (raw == null)
            {
                return new string[0];
            }
            IList list = raw as IList;
            if (list == null || list.Count < 1 || list.Count > ReportsCaInputsDef.All.Length)
            {
                throw GlReq.Bad("tables 必须是 1 到 " + ReportsCaInputsDef.All.Length + " 个表名", "tables");
            }
            string[] names = new string[list.Count];
            for (int i = 0; i < list.Count; i++)
            {
                string text = list[i] as string;
                text = text == null ? "" : text.Trim();
                if (ReportsCaInputsDef.Find(text) == null || Array.IndexOf(names, text) >= 0)
                {
                    throw GlReq.Bad("tables 里的表名无效或重复", FieldPath.Item("tables", i));
                }
                names[i] = text;
            }
            return names;
        }

        static int Int(Dictionary<string, object> body, string key, int fallback, int min, int max)
        {
            object raw = GlReq.Field(body, key);
            return raw == null ? fallback : GlReq.IntIn(raw, key, min, max);
        }
    }
}
