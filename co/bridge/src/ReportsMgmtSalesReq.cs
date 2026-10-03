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

        public bool Has(string dim)
        {
            return Array.IndexOf(GroupBy, dim) >= 0;
        }
    }

    internal static class ReportsMgmtSalesReq
    {
        internal const string Name = "mgmt/sales";
        internal const string Path = Requests.ReportRoot + Name;
        internal const string RuleKey = "report:" + Name;
        internal const int DefaultTop = 200;
        internal const int MaxTop = 500;

        // 可选的分组维度（输出列的前缀）；输出顺序固定按这里。
        internal static readonly string[] Dims = new string[] { "period", "customer", "inventory", "person", "department" };
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

        // group_by：0 到 5 个不重复的维度名；缺省按客户。
        static string[] GroupBy(object raw)
        {
            if (raw == null)
            {
                return (string[])DefaultGroupBy.Clone();
            }
            const string message = "group_by 只能是 period、customer、inventory、person、department 中不重复的若干项";
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
