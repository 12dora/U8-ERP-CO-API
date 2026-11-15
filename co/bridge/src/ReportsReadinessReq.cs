using System;
using System.Collections.Generic;

namespace U8Co
{
    // 账套体检 reports/account_readiness（只读）：参数与检查项名单。字段名由 Requests.ReportSpecs 把关（只收 as_of），
    // 取值在这里校验，登录前抛 400。登录子系统 SA（同 close_status）；权限规则 report:account_readiness
    // （PermRegistryMore.OtherRules），不按记录过滤。
    internal static class ReportsReadinessReq
    {
        internal const string Name = "account_readiness";
        internal const string RuleKey = "report:" + Name;
        internal const string Path = Requests.ReportRoot + Name;
        // docs_anchor 指向 docs/getting-started.md 里同名的小节。
        internal const string DocsPage = "getting-started.md#";

        // 检查项 id 与标题，顺序即响应里 checks 的顺序。
        internal static readonly string[] Ids = new string[]
        {
            "patches", "years", "calendar", "yearly_config", "pu_opening", "vendor_extradefine", "modules",
            "prior_gl_close", "workflow", "defaults"
        };
        static readonly string[] Titles = new string[]
        {
            "账套补丁", "年度账", "工作日历", "年度配置", "采购期初记账", "供应商扩展自定义项表", "系统启用",
            "以前年度总账结账", "质检审批流与操作员人员", "缺省档案与编号规则"
        };

        public static bool Owns(string name)
        {
            return name == Name;
        }

        // 登录子系统：SA。不是本报表返回 null。
        public static string SubOf(string name)
        {
            return Owns(name) ? "SA" : null;
        }

        // 可选 as_of（yyyy-MM-dd）；缺省为空串，Reports.Handle 换成登录日期。
        public static void Parse(ReportArgs a, Dictionary<string, object> body)
        {
            a.AsOf = GlReq.OptDate(GlReq.Field(body, "as_of"), "as_of");
        }

        public static string Title(string id)
        {
            int i = Array.IndexOf(Ids, id);
            return i < 0 ? id : Titles[i];
        }
    }
}
