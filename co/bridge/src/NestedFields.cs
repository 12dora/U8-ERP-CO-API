using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // 单据请求里值可以是数组的字段，按单据类型登记，分表头和明细行（Json.CheckMap 放行，细查在各自的解析里）：
    // 检验单（来料、产品、其他检验单）表头的 items（检验项目，QmReq）；物料清单明细行的 substitutes、locators（BomSubReq）。
    // 其余字段的值只能是字符串、数字或布尔。API（co_models_nested.py）和客户端（u8co_rows.py）各有一份同样的表。
    internal static class NestedFields
    {
        static readonly Dictionary<string, string[]> HeadKeys = BuildHead();
        static readonly Dictionary<string, string[]> LineKeys = BuildLines();

        static Dictionary<string, string[]> BuildHead()
        {
            Dictionary<string, string[]> map = new Dictionary<string, string[]>(StringComparer.Ordinal);
            foreach (QmSpec spec in QmSpec.List())
            {
                if (!spec.Inspect)
                {
                    map[spec.Kind] = new string[] { "items" };
                }
            }
            map[QmOthSpec.CheckKind] = new string[] { "items" };
            return map;
        }

        static Dictionary<string, string[]> BuildLines()
        {
            Dictionary<string, string[]> map = new Dictionary<string, string[]>(StringComparer.Ordinal);
            map[BomRoutes.KindName] = new string[] { BomSubs.SubsKey, BomSubs.LocsKey };
            return map;
        }

        // head：表头（否则是明细行）。值须是数组，字段名不分大小写。
        internal static bool Allowed(VoucherKind kind, bool head, string key, object value)
        {
            string[] keys;
            if (kind == null || key == null || !(value is IList) || !(head ? HeadKeys : LineKeys).TryGetValue(kind.Name, out keys))
            {
                return false;
            }
            for (int i = 0; i < keys.Length; i++)
            {
                if (string.Equals(keys[i], key, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }
    }
}
