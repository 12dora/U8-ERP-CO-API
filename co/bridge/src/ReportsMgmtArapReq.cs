using System.Collections.Generic;

namespace U8Co
{
    // 经营管理往来账期 reports/mgmt/arap_terms 的参数（只读）。字段名由 Requests.ReportSpecs 把关，这里只校验取值，
    // 登录前抛 400；处理函数里再解析一次。登录子系统按 side 用 AR / AP（同 arap_aging）。
    // side、as_of、accounts、buckets、default_credit_days 的取值规则与 arap_aging（basis=due）完全相同：拼成一份
    // arap_aging 请求交给 ReportsReq.Parse 校验，得到的 ReportArgs 直接用于 ReportsArapAging.Inner。
    // buckets 缺省 [30, 60, 90, 180]（arap_aging 缺省多一段 365）；top 1–500，缺省 200。
    internal sealed class MgmtArapArgs
    {
        public ReportArgs Aging;
        public int Top;
    }

    internal static class ReportsMgmtArapReq
    {
        internal const string Name = "mgmt/arap_terms";
        internal const string Path = Requests.ReportRoot + Name;
        internal const int DefaultTop = 200;
        internal const int MaxTop = 500;

        static readonly object[] DefaultBuckets = new object[] { 30, 60, 90, 180 };
        static readonly string[] Passed = new string[] { "side", "as_of", "accounts", "buckets", "default_credit_days" };

        public static bool Owns(string name)
        {
            return name == Name;
        }

        public static string SubOf(string name, Dictionary<string, object> body)
        {
            if (!Owns(name))
            {
                return null;
            }
            return (Requests.Field(body, "side") as string) == "ap" ? "AP" : "AR";
        }

        // 应收、应付两条规则（PermRegistryMgmtSalesArap）。
        public static string RuleKey(Dictionary<string, object> body)
        {
            object side = body == null ? null : Requests.Field(body, "side");
            return "report:" + Name + ":" + ((side as string) == "ap" ? "ap" : "ar");
        }

        // 经营管理销售分析、往来账期两张报表的登录前校验（ReportsReq.ParseOwned 的一个分支）；不是这两张时返回 false。
        public static bool Check(ReportArgs a, Dictionary<string, object> body)
        {
            if (ReportsMgmtSalesReq.Owns(a.Name))
            {
                ReportsMgmtSalesReq.Parse(body);
                return true;
            }
            if (!Owns(a.Name))
            {
                return false;
            }
            a.AsOf = Parse(body).Aging.AsOf;
            return true;
        }

        public static MgmtArapArgs Parse(Dictionary<string, object> body)
        {
            Dictionary<string, object> aging = new Dictionary<string, object>();
            for (int i = 0; i < Passed.Length; i++)
            {
                object value = GlReq.Field(body, Passed[i]);
                if (value != null)
                {
                    aging[Passed[i]] = value;
                }
            }
            if (!aging.ContainsKey("buckets"))
            {
                aging["buckets"] = DefaultBuckets;
            }
            aging["basis"] = "due";
            MgmtArapArgs m = new MgmtArapArgs();
            m.Aging = ReportsReq.Parse("arap_aging", aging);
            object top = GlReq.Field(body, "top");
            m.Top = top == null ? DefaultTop : GlReq.IntIn(top, "top", 1, MaxTop);
            return m;
        }
    }
}
