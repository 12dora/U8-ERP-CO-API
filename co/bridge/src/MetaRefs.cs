using System.Collections.Generic;

namespace U8Co
{
    // meta 的 field_refs / gl_field_refs：写请求里的字段（小写）指向哪种档案，调用方先用 archives/resolve 把名称换成编码。
    // field_refs 是单据 head / lines 与档案 fields 的字段名（含生产订单、物料清单的 inv_code / dept_code / wh_code）；
    // gl_field_refs 是总账凭证分录的键。指向哪种档案要看单据的字段（cdwcode）不进 field_refs，写在 field_refs_notes。
    internal static class MetaRefs
    {
        static readonly string[][] Fields = new string[][]
        {
            new string[] { "customer", "ccuscode" },
            new string[] { "vendor", "cvencode" },
            new string[] { "inventory", "cinvcode", "inv_code" },
            new string[] { "warehouse", "cwhcode", "cowhcode", "ciwhcode", "wh_code" },
            new string[] { "department", "cdepcode", "codepcode", "cidepcode", "cinspectdepcode", "dept_code" },
            new string[] { "person", "cpersoncode", "ccheckpersoncode" },
            new string[] { "position", "cposition", "cposcode" },
            new string[] { "sale_type", "cstcode" },
            new string[] { "purchase_type", "cptcode" },
            new string[] { "rd_style", "crdcode", "cordcode", "cirdcode" },
            new string[] { "currency", "cexch_name" },
            new string[] { "settle_style", "csscode" },
            new string[] { "unit", "cunitid", "ccomunitcode", "cassunit" },
            new string[] { "unit_group", "cgroupcode" },
            new string[] { "project", "citemcode" },
            new string[] { "account", "ccode", "ckm" },
            new string[] { "voucher_sign", "csign" },
            new string[] { "bank", "cbank" },
            // 不良品处理单表体的不良原因（原因码档案，Reasontype 1）。
            new string[] { "reason", "creasoncode" }
        };

        static readonly string[][] GlFields = new string[][]
        {
            new string[] { "account", "account" },
            new string[] { "department", "dept" },
            new string[] { "person", "person" },
            new string[] { "customer", "customer" },
            new string[] { "vendor", "supplier" },
            new string[] { "project", "item" },
            new string[] { "settle_style", "settle" },
            new string[] { "currency", "currency" },
            new string[] { "voucher_sign", "sign" }
        };

        internal static Dictionary<string, object> FieldRefs()
        {
            return Map(Fields);
        }

        internal static Dictionary<string, object> Notes()
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            map["cdwcode"] = "收付款单、应收应付单的往来单位：应收（AR）是 customer，应付（AP）是 vendor";
            return map;
        }

        internal static Dictionary<string, object> GlFieldRefs()
        {
            return Map(GlFields);
        }

        // 每行第一个是档案类型，其余是指向它的字段名。
        static Dictionary<string, object> Map(string[][] table)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            for (int i = 0; i < table.Length; i++)
            {
                for (int j = 1; j < table[i].Length; j++)
                {
                    map[table[i][j]] = table[i][0];
                }
            }
            return map;
        }
    }
}
