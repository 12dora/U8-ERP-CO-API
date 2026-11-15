using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // 经营管理销售分析 reports/mgmt/sales 的参数（只读）。字段名由 Requests.ReportSpecs 把关，这里只校验取值，
    // 登录前抛 400；处理函数里再解析一次。登录子系统 SA。fiscal_year 缺省为登录年度（Reports.Handle 填）。
    internal sealed class MgmtSalesArgs
    {
        public int From;
        public int To;
        // 分组维度，按 ReportsMgmtSalesReq.Dims 的顺序排好、不重复；空数组表示只出合计。
        public string[] GroupBy;
        public int Top;
        public bool Unverified;
        // 存货分类：ClassGrade 为 0 时分类维度取存货自己的分类，1 到 9 时往上取该级次的祖先（分类比它浅的存货保持自己的分类）；
        // InvClass 是存货分类编码前缀过滤（按存货自己的分类编码），null 表示不过滤。
        public int ClassGrade;
        public string InvClass;
        // 关联方客户编码（最多 20 个）：有时除分组结果外另给这些客户的合计 ic_totals（不受 top、group_by 影响）。
        public string[] IcCustomers = new string[0];
        // 只在内部使用：把这一遍限定在这些客户上（ic_totals 那一遍），null 表示不限。
        public string[] OnlyCustomers;

        public bool Has(string dim)
        {
            return Array.IndexOf(GroupBy, dim) >= 0;
        }

        // 分类维度或分类过滤需要连存货档案。
        public bool NeedClass
        {
            get { return Has("inventory_class") || InvClass != null; }
        }

        // 分类维度要往上取祖先分类。
        public bool RollUp
        {
            get { return ClassGrade > 0 && Has("inventory_class"); }
        }

        // 同样的期间、复核口径和分类过滤，只出合计、只含这些客户（关联方合计那一遍）。
        public MgmtSalesArgs ForCustomers(string[] customers)
        {
            MgmtSalesArgs only = (MgmtSalesArgs)MemberwiseClone();
            only.GroupBy = new string[0];
            only.IcCustomers = new string[0];
            only.OnlyCustomers = customers;
            return only;
        }
    }

    internal static class ReportsMgmtSalesReq
    {
        internal const string Name = "mgmt/sales";
        internal const string Path = Requests.ReportRoot + Name;
        internal const string RuleKey = "report:" + Name;
        internal const int DefaultTop = 200;
        internal const int MaxTop = 500;
        internal const int MaxIcCustomers = 20;
        const int MaxText = 60;

        // 可选的分组维度（输出列的前缀）；输出顺序固定按这里。
        internal static readonly string[] Dims = new string[] { "period", "customer", "inventory", "inventory_class", "person", "department" };
        static readonly string[] DefaultGroupBy = new string[] { "customer" };

        public static bool Owns(string name)
        {
            return name == Name;
        }

        public static string SubOf(string name)
        {
            return Owns(name) ? "SA" : null;
        }

        public static MgmtSalesArgs Parse(Dictionary<string, object> body)
        {
            MgmtSalesArgs m = new MgmtSalesArgs();
            m.From = GlReq.IntIn(GlReq.Field(body, "period_from"), "period_from", 1, 12);
            m.To = GlReq.IntIn(GlReq.Field(body, "period_to"), "period_to", 1, 12);
            if (m.From > m.To)
            {
                throw GlReq.Bad("period_from 不能大于 period_to", "period_from");
            }
            m.GroupBy = GroupBy(GlReq.Field(body, "group_by"));
            m.ClassGrade = ClassGrade(body);
            m.InvClass = InvClass(body);
            m.IcCustomers = IcCustomers(GlReq.Field(body, "ic_customers"));
            object top = GlReq.Field(body, "top");
            m.Top = top == null ? DefaultTop : GlReq.IntIn(top, "top", 1, MaxTop);
            object unverified = GlReq.Field(body, "include_unverified");
            if (unverified != null && !(unverified is bool))
            {
                throw GlReq.Bad("include_unverified 必须是 true 或 false", "include_unverified");
            }
            m.Unverified = unverified != null && (bool)unverified;
            return m;
        }

        // class_grade：1 到 9 的整数；缺省 0（存货自己的分类）。mgmt/cash_stock 的产量分类也用。
        internal static int ClassGrade(Dictionary<string, object> body)
        {
            object raw = GlReq.Field(body, "class_grade");
            return raw == null ? 0 : GlReq.IntIn(raw, "class_grade", 1, 9);
        }

        // inv_class：存货分类编码前缀，1 到 60 个字符，不含控制字符；缺省 null（不过滤）。
        internal static string InvClass(Dictionary<string, object> body)
        {
            object raw = GlReq.Field(body, "inv_class");
            return raw == null ? null : Text(raw, "inv_class");
        }

        // ic_customers：0 到 20 个客户编码（去重）；缺省空。
        static string[] IcCustomers(object raw)
        {
            if (raw == null)
            {
                return new string[0];
            }
            const string message = "ic_customers 必须是不超过 20 个客户编码组成的数组";
            IList list = raw as IList;
            if (list == null || list.Count > MaxIcCustomers)
            {
                throw GlReq.Bad(message, "ic_customers");
            }
            List<string> codes = new List<string>();
            for (int i = 0; i < list.Count; i++)
            {
                string code = Text(list[i], FieldPath.Item("ic_customers", i));
                if (!codes.Contains(code))
                {
                    codes.Add(code);
                }
            }
            return codes.ToArray();
        }

        static string Text(object raw, string field)
        {
            string text = raw as string;
            if (text == null || text.Trim().Length == 0 || text.Length > MaxText || HasControl(text))
            {
                throw GlReq.Bad(field + " 必须是 1 到 60 个字符，不含控制字符", field);
            }
            return text.Trim();
        }

        static bool HasControl(string text)
        {
            for (int i = 0; i < text.Length; i++)
            {
                if (text[i] < ' ' || (text[i] >= '\u007f' && text[i] <= '\u009f'))
                {
                    return true;
                }
            }
            return false;
        }

        // group_by：0 到 6 个不重复的维度名；缺省按客户。
        static string[] GroupBy(object raw)
        {
            if (raw == null)
            {
                return (string[])DefaultGroupBy.Clone();
            }
            const string message = "group_by 只能是 period、customer、inventory、inventory_class、person、department 中不重复的若干项";
            IList list = raw as IList;
            if (list == null || list.Count > Dims.Length)
            {
                throw GlReq.Bad(message, "group_by");
            }
            bool[] seen = new bool[Dims.Length];
            for (int i = 0; i < list.Count; i++)
            {
                int at = Array.IndexOf(Dims, list[i] as string);
                if (at < 0 || seen[at])
                {
                    throw GlReq.Bad(message, FieldPath.Item("group_by", i));
                }
                seen[at] = true;
            }
            List<string> dims = new List<string>();
            for (int i = 0; i < Dims.Length; i++)
            {
                if (seen[i])
                {
                    dims.Add(Dims[i]);
                }
            }
            return dims.ToArray();
        }
    }
}
