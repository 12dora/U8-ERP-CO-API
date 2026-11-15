using System;
using System.Collections.Generic;

namespace U8Co
{
    // U8 单据模板必输项（voucheritems.IsNull=1，按 VT 351–354）：DOM 填好之后、调用 U8 之前核对。桥推不出、调用方又没送的
    // 必输字段一次列全，400「缺少必输字段 A、B（U8 单据模板设置为必输）」。每次请求现读（一条小查询），不缓存。
    // skip 是桥在调用前才填的字段（检验单号 CCHECKCODE 在核对通过后才取号，免得 400 也占掉流水）。
    internal static class QmTplReq
    {
        public static void Require(object conn, QmSpec spec, object[] doms, string skip)
        {
            List<Dictionary<string, object>> fields = QmSql.Required(conn, spec.Vt);
            if (fields == null || fields.Count == 0)
            {
                return;
            }
            List<Dictionary<string, object>> head = Rows.FromDom(doms[0], 1);
            List<Dictionary<string, object>> body = Rows.FromDom(doms[1], 500);
            List<string> missing = Missing(fields, head, body, skip);
            if (missing.Count > 0)
            {
                throw new BridgeException(400, "bad_request",
                    "缺少必输字段 " + string.Join("、", missing.ToArray()) + "（U8 单据模板设置为必输）");
            }
        }

        internal static List<string> Missing(List<Dictionary<string, object>> fields,
            List<Dictionary<string, object>> head, List<Dictionary<string, object>> body, string skip)
        {
            List<string> missing = new List<string>();
            for (int i = 0; i < fields.Count; i++)
            {
                string name = CoRows.Col(fields[i], "FieldName");
                bool isBody = string.Equals(CoRows.Col(fields[i], "CardSection"), "B", StringComparison.OrdinalIgnoreCase);
                if (name.Length == 0 || QmSpec.Same(name, skip) || Contains(missing, name))
                {
                    continue;
                }
                if (!Filled(isBody ? body : head, name))
                {
                    missing.Add(name);
                }
            }
            return missing;
        }

        // 每一行都有非空值才算填了；没有行（表体为空）算没填。
        static bool Filled(List<Dictionary<string, object>> rows, string name)
        {
            if (rows == null || rows.Count == 0)
            {
                return false;
            }
            for (int i = 0; i < rows.Count; i++)
            {
                if (CoRows.Col(rows[i], name).Length == 0)
                {
                    return false;
                }
            }
            return true;
        }

        static bool Contains(List<string> list, string name)
        {
            for (int i = 0; i < list.Count; i++)
            {
                if (QmSpec.Same(list[i], name))
                {
                    return true;
                }
            }
            return false;
        }
    }
}
