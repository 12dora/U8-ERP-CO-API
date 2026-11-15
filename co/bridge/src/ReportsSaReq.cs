using System;
using System.Collections.Generic;

namespace U8Co
{
    // 销售类只读报表（open_orders 在手订单、sales_price_history 销售价格历史、customer_churn 客户变动）的名单、
    // 字段表、登录前校验和运行表。取值校验在 SaArgs；三张报表都按销售管理（SA）登录。
    internal static class ReportsSaReq
    {
        internal const string OpenOrders = "open_orders";
        internal const string PriceHistory = "sales_price_history";
        internal const string Churn = "customer_churn";

        internal static readonly string[] Names = new string[] { OpenOrders, PriceHistory, Churn };
        internal static readonly string[][] Specs = new string[][]
        {
            new string[]
            {
                Requests.ReportRoot + OpenOrders, "basis", "group_by", "customer", "inv", "person", "date_from", "date_to",
                "due_before", "limit"
            },
            new string[]
            {
                Requests.ReportRoot + PriceHistory, "customer", "inv", "date_from", "date_to", "include_unverified", "limit"
            },
            new string[]
            {
                Requests.ReportRoot + Churn, "date_from", "date_to", "lookback_months", "decline_pct", "limit"
            }
        };

        internal static bool Owns(string name)
        {
            return Array.IndexOf(Names, name) >= 0;
        }

        // 登录前校验，只抛 400。
        internal static void Check(string name, Dictionary<string, object> body)
        {
            if (name == OpenOrders)
            {
                SaArgs.Open(body);
            }
            else if (name == PriceHistory)
            {
                SaArgs.Price(body);
            }
            else if (name == Churn)
            {
                SaArgs.Churn(body);
            }
        }

        // 三张都按销售管理登录。不是这一组的报表返回 null。
        internal static string SubOf(string name, Dictionary<string, object> body)
        {
            return Owns(name) ? "SA" : null;
        }

        internal static void AddRunners(Dictionary<string, Func<WorkContext, ReportArgs, ApiResult>> map)
        {
            map.Add(OpenOrders, ReportsSaOpen.Run);
            map.Add(PriceHistory, ReportsSaPrice.Run);
            map.Add(Churn, ReportsSaChurn.Run);
        }
    }
}
