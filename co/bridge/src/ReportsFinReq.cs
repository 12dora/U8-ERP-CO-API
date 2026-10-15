using System;
using System.Collections.Generic;

namespace U8Co
{
    // 收付款与现金流报表族 Fin：receipts_payments 收付款汇总、notes_due 到期票据、cash_flow 现金流量统计。
    // 字段名由 Specs（Requests.ReportSpecs）把关，取值由 Check 在登录前校验（ReportsFinParse），处理函数里再解析一次。
    // 登录子系统：收付款汇总、到期票据按 side 用 AR / AP（同 arap_aging），现金流量统计 GL。
    internal static class ReportsFinReq
    {
        internal const string RecPayName = "receipts_payments";
        internal const string NotesName = "notes_due";
        internal const string CashName = "cash_flow";

        internal static readonly string[] Names = new string[] { RecPayName, NotesName, CashName };

        // 路由 → 允许的请求字段。都是聚合结果，没有 after 游标。
        internal static readonly string[][] Specs = new string[][]
        {
            new string[]
            {
                Requests.ReportRoot + RecPayName, "side", "date_from", "date_to", "group_by", "partner", "person", "dept",
                "include_unverified", "limit"
            },
            new string[]
            {
                Requests.ReportRoot + NotesName, "side", "expire_from", "expire_to", "open_only", "partner", "limit"
            },
            new string[]
            {
                Requests.ReportRoot + CashName, "fiscal_year", "period_from", "period_to", "include_unposted"
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
                case RecPayName:
                    ReportsFinParse.RecPay(body);
                    return;
                case NotesName:
                    ReportsFinParse.Notes(body);
                    return;
                case CashName:
                    ReportsFinParse.Cash(body);
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
            if (name == CashName)
            {
                return "GL";
            }
            return (GlReq.Field(body, "side") as string) == "ap" ? "AP" : "AR";
        }

        internal static void AddRunners(Dictionary<string, Func<WorkContext, ReportArgs, ApiResult>> map)
        {
            map.Add(RecPayName, ReportsFinRecPay.Run);
            map.Add(NotesName, ReportsFinNotes.Run);
            map.Add(CashName, ReportsFinCash.Run);
        }
    }
}
