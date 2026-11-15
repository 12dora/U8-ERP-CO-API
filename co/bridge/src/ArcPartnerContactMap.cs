using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 客户联系人的 EAI 标签 → 表列名（Crm_Contact）。对照取自 EAI 联系人模板，并在测试账套上逐列写入回读核对。
    // 性别、婚姻状况在 EAI 里是文字（男 / 女 / 不详，已婚 / 未婚 / 离异 / 不详），表里是 Crm_BaseCode_Base.ID
    // （如男 → 6，婚姻不详 → 11），修改时由 Text 把当前值换回文字再发；换不出的不猜，409。
    internal static class ArcPartnerContactMap
    {
        internal const string Sex = "sex";
        internal const string Marriage = "marriage";
        internal const string Major = "be_main_linker";
        internal const string Birthday = "birthday";
        internal const string Unknown = "不详";
        static readonly string[] SexValues = new string[] { "男", "女", Unknown };
        static readonly string[] MarriageValues = new string[] { "已婚", "未婚", "离异", Unknown };
        // 文字标签的最大长度（字符）取 Crm_Contact 的列宽（nvarchar 字节数 / 2），超长 400，免得 U8 截断后回读对不上。
        static readonly Dictionary<string, int> Limits = BuildLimits();
        // 先按 ID 找（实测存的是 ID），找不到再按 cCode 的数值找；ID 和编码在同一类里应不重叠。
        const string BaseSql = "SELECT TOP 1 l.cName AS n FROM Crm_BaseCode_Base b JOIN Crm_BaseCode_Lang l"
            + " ON l.BaseCodeOID=b.OID AND l.LocaleID='zh-CN' WHERE b.cType=? AND (b.ID=? OR b.cCode=RIGHT('0000'+?,4))"
            + " ORDER BY CASE WHEN b.ID=? THEN 0 ELSE 1 END";

        internal static string[] Pairs()
        {
            List<string> pairs = new List<string>(new string[]
            {
                "name", "cContactName", "title", "cAppellation", Sex, "bcSexID", Birthday, "dBirthday",
                "native", "cNative", "position", "bcDutyID", "direct_leader", "cSuperiorID",
                "mobile", "cMobilePhone", "office_phone", "cOfficePhone", "family_phone", "cHomePhone", "bp", "cCall",
                "email", "cEmail", "web", "cHomePage", "work_address", "cWorkAddress", "postcode", "cZipcode",
                Marriage, "bcMarriageID", "family_member", "cHomeMember", "family_address", "cHomeAddress",
                "favorite", "bcTasteID", Major, "bMajor", "charge_person", "cPrincipal", "memo", "cMemo"
            });
            for (int i = 1; i <= 10; i++)
            {
                string n = i.ToString(CultureInfo.InvariantCulture);
                pairs.Add("self_define" + n);
                pairs.Add("cConDefine" + n);
            }
            return pairs.ToArray();
        }

        // 供应商联系人（Ven_Contact，列与 Crm_Contact 相同）：去掉职务 position、个人爱好 favorite——两列在表里是编号
        // （bcDutyID、bcTasteID），U8 基础编码里没有对应的类别，受控 SQL 修改时换不出，新增、修改都不开放。
        internal static string[] VendorPairs()
        {
            string[] all = Pairs();
            List<string> pairs = new List<string>();
            for (int i = 0; i + 1 < all.Length; i += 2)
            {
                if (all[i] != "position" && all[i] != "favorite")
                {
                    pairs.Add(all[i]);
                    pairs.Add(all[i + 1]);
                }
            }
            return pairs.ToArray();
        }

        // 调用方的值：性别、婚姻状况只收上面的文字；主要联系人收布尔或 0 / 1；生日 yyyy-mm-dd；其余不超过 200 个字符。
        internal static void Check(ArcReq req)
        {
            IList<string> tags = req.Fields.Tags;
            for (int i = 0; i < tags.Count; i++)
            {
                string tag = tags[i];
                string value = req.Fields.Get(tag) ?? "";
                if (tag == Sex)
                {
                    OneOf(tag, value, SexValues);
                }
                else if (tag == Marriage)
                {
                    OneOf(tag, value, MarriageValues);
                }
                else if (tag == Major)
                {
                    Bool(tag, value);
                }
                else if (tag == Birthday)
                {
                    Date(tag, value);
                }
                else
                {
                    Text(tag, value);
                }
            }
        }

        static Dictionary<string, int> BuildLimits()
        {
            string[] pairs = new string[]
            {
                "name", "50", "title", "20", "native", "30", "position", "255", "direct_leader", "30", "mobile", "100",
                "office_phone", "100", "family_phone", "100", "bp", "20", "email", "255", "web", "50", "work_address", "150",
                "postcode", "20", "family_member", "100", "family_address", "150", "favorite", "255", "charge_person", "20",
                "memo", "240", "self_define1", "20", "self_define2", "20", "self_define3", "20", "self_define4", "60",
                "self_define5", "60", "self_define6", "60", "self_define7", "120", "self_define8", "120", "self_define9", "120",
                "self_define10", "120"
            };
            Dictionary<string, int> map = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i + 1 < pairs.Length; i += 2)
            {
                map[pairs[i]] = int.Parse(pairs[i + 1], CultureInfo.InvariantCulture);
            }
            return map;
        }

        // 名称不能全是空白；前后空白不收（U8 可能去掉，回读按名称查会对不上）。
        static void Text(string tag, string value)
        {
            int max;
            if (!Limits.TryGetValue(tag, out max))
            {
                max = 20;
            }
            if (value.Length > max || (tag == "name" && (value.Trim().Length == 0 || value.Trim().Length != value.Length)))
            {
                throw ArcReq.Bad("字段 " + tag + " 必须是不超过 " + max.ToString(CultureInfo.InvariantCulture) + " 个字符的文字"
                    + (tag == "name" ? "，不能全是空白，前后不能有空格" : ""), FieldPath.Join("fields", tag));
            }
        }

        static void OneOf(string tag, string value, string[] allowed)
        {
            if (Array.IndexOf(allowed, value) < 0)
            {
                throw ArcReq.Bad("字段 " + tag + " 只能是 " + string.Join("、", allowed), FieldPath.Join("fields", tag));
            }
        }

        static void Bool(string tag, string value)
        {
            if (value != "0" && value != "1")
            {
                throw ArcReq.Bad("字段 " + tag + " 必须是布尔或 0 / 1", FieldPath.Join("fields", tag));
            }
        }

        static void Date(string tag, string value)
        {
            DateTime d;
            if (value.Length != 10 || !DateTime.TryParseExact(value, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out d))
            {
                throw ArcReq.Bad("字段 " + tag + " 必须是 yyyy-mm-dd", FieldPath.Join("fields", tag));
            }
        }

        // 当前行（列名 → 值）换成 EAI 标签打底：性别、婚姻状况换成文字，生日只取日期，其余原样。
        // 调用方这次给了的标签不用当前值；性别、婚姻状况换不出文字又没给时 409（不按「不详」覆盖原值）。
        internal static void Current(object conn, ArcReq req, Dictionary<string, string> row, ArcBag bag)
        {
            IList<string> tags = req.Map.Tags;
            for (int i = 0; i < tags.Count; i++)
            {
                string tag = tags[i];
                string value;
                if (req.Fields.Has(tag) || !row.TryGetValue(req.Map.Column(tag), out value) || value.Trim().Length == 0)
                {
                    continue;
                }
                if (tag == Sex || tag == Marriage)
                {
                    value = Coded(conn, tag, value.Trim());
                }
                else if (tag == Birthday && value.Length > 10)
                {
                    value = value.Substring(0, 10);
                }
                bag.Put(tag, value);
            }
        }

        static string Coded(object conn, string tag, string id)
        {
            string name = Text(conn, BaseType(tag), id);
            if (name == null)
            {
                throw ArcGuard.State("联系人当前的 " + tag + " 值 " + id + " 在 U8 基础编码里找不到，请在 fields 里给出 " + tag);
            }
            return name;
        }

        // 性别、婚姻状况在 Crm_BaseCode_Base 里的类别（cType）。
        internal static string BaseType(string tag)
        {
            return tag == Sex ? "Sex" : "Marriage";
        }

        static string Text(object conn, string type, string id)
        {
            int n;
            if (!int.TryParse(id, NumberStyles.Integer, CultureInfo.InvariantCulture, out n))
            {
                return null;
            }
            string name = Rows.Scalar(conn, BaseSql, new object[] { type, n, id, n });
            return string.IsNullOrEmpty(name) ? null : name.Trim();
        }
    }
}
