using System;
using System.Collections.Generic;

namespace U8Co
{
    // 账务核对报表族 Acct：close_check 月末结账检查、ia_anomalies 存货核算检查、cost_unit 完工产品单位成本。
    // 字段名由 Specs（Requests.ReportSpecs）把关，取值由 Check 在登录前校验（ReportsAcctParse），处理函数里再解析一次。
    // 登录子系统：月末结账检查 GL，存货核算检查 IA（同 ia/post），单位成本 GL。
    internal static class ReportsAcctReq
    {
        internal const string CloseName = "close_check";
        internal const string IaName = "ia_anomalies";
        internal const string CostName = "cost_unit";

        internal static readonly string[] Names = new string[] { CloseName, IaName, CostName };

        // 路由 → 允许的请求字段。都是聚合结果，没有 after 游标。
        internal static readonly string[][] Specs = new string[][]
        {
            new string[] { Requests.ReportRoot + CloseName, "fiscal_year", "period" },
            new string[] { Requests.ReportRoot + IaName, "fiscal_year", "period", "limit" },
            new string[]
            {
                Requests.ReportRoot + CostName, "fiscal_year", "period_from", "period_to", "inv", "inv_class"
            }
        };

        internal static bool Owns(string name)
        {
            return Array.IndexOf(Names, name) >= 0;
        }

        // 登录前校验取值，只抛 400。
        internal static void Check(string name, Dictionary<string, object> body)
        {
            switch (name)
            {
                case CloseName:
                    ReportsAcctParse.Close(body);
                    return;
                case IaName:
                    ReportsAcctParse.Ia(body);
                    return;
                case CostName:
                    ReportsAcctParse.Cost(body);
                    return;
                default:
                    throw GlReq.Bad("未知的报表 " + name);
            }
        }

        // 不是本族的报表返回 null。
        internal static string SubOf(string name, Dictionary<string, object> body)
        {
            if (!Owns(name))
            {
                return null;
            }
            return name == IaName ? "IA" : "GL";
        }

        internal static void AddRunners(Dictionary<string, Func<WorkContext, ReportArgs, ApiResult>> map)
        {
            map.Add(CloseName, ReportsAcctClose.Run);
            map.Add(IaName, ReportsAcctIa.Run);
            map.Add(CostName, ReportsAcctCost.Run);
        }
    }
}
