using System;
using System.Collections.Generic;

namespace U8Co
{
    // 生产类报表（production_daily、mo_progress、mo_consumption）的请求参数，三张报表共用一个对象，各取所需。
    internal sealed class MfgArgs
    {
        public string DateFrom = "";
        // 空串表示取登录日期（date）。
        public string DateTo = "";
        // production_daily：day 按日、month 按月；by：product 按产品、class 按存货分类（往上取 ClassGrade 级祖先）。
        public string GroupBy = "day";
        public string By = "product";
        public int ClassGrade = 2;
        // inv / parent 是存货编码条件（空数组不限）；InvClass 是存货分类编码前缀，null 不过滤。
        public string[] Inv = new string[0];
        public string[] Parent = new string[0];
        public string InvClass;
        public bool Unverified;
        public bool OpenOnly;
        public int Limit;
    }

    // 生产类报表族：产成品入库日报 production_daily、生产订单完工状况 mo_progress、生产订单用料分析 mo_consumption。
    // 只读，全是 SQL。字段名由 Requests.ReportSpecs（Specs）把关，Check 只校验取值，登录前抛 400；处理函数里再 Parse 一次。
    // 登录子系统：production_daily 用 ST（同只要产量的 mgmt/cash_stock），生产订单两张用 SA（同 vouchers/load production_order）。
    internal static class ReportsMfgReq
    {
        internal const string DailyName = "production_daily";
        internal const string ProgressName = "mo_progress";
        internal const string ConsumptionName = "mo_consumption";

        internal static readonly string[] Names = new string[] { DailyName, ProgressName, ConsumptionName };

        // { 路径, 允许的请求字段… }，并入 Requests.ReportSpecs。三张都是聚合结果，不收 after。
        internal static readonly string[][] Specs = new string[][]
        {
            new string[]
            {
                Requests.ReportRoot + DailyName, "date_from", "date_to", "group_by", "by", "class_grade", "inv", "inv_class",
                "include_unverified", "limit"
            },
            new string[]
            {
                Requests.ReportRoot + ProgressName, "date_from", "date_to", "inv", "inv_class", "open_only", "limit"
            },
            new string[]
            {
                Requests.ReportRoot + ConsumptionName, "date_from", "date_to", "parent", "inv_class", "limit"
            }
        };

        static readonly string[] GroupBys = new string[] { "day", "month" };
        static readonly string[] Bys = new string[] { "product", "class" };

        internal static bool Owns(string name)
        {
            return Array.IndexOf(Names, name) >= 0;
        }

        internal static void Check(string name, Dictionary<string, object> body)
        {
            Parse(name, body);
        }

        internal static string SubOf(string name, Dictionary<string, object> body)
        {
            if (!Owns(name))
            {
                return null;
            }
            return name == DailyName ? "ST" : "SA";
        }

        internal static void AddRunners(Dictionary<string, Func<WorkContext, ReportArgs, ApiResult>> map)
        {
            map.Add(DailyName, ReportsMfgDaily.Run);
            map.Add(ProgressName, ReportsMfgMo.Progress);
            map.Add(ConsumptionName, ReportsMfgMo.Consumption);
        }

        internal static MfgArgs Parse(string name, Dictionary<string, object> body)
        {
            MfgArgs m = new MfgArgs();
            ReportsMfgParse.Range(body, m);
            m.InvClass = ReportsMfgParse.Prefix(body, "inv_class");
            switch (name)
            {
                case DailyName:
                    ParseDaily(m, body);
                    break;
                case ProgressName:
                    m.Inv = ReportsMfgParse.Codes(body, "inv");
                    m.OpenOnly = ReportsMfgParse.Bool(body, "open_only", false);
                    m.Limit = ReportsMfgParse.Int(body, "limit", 500, 1, 2000);
                    break;
                case ConsumptionName:
                    m.Parent = ReportsMfgParse.Codes(body, "parent");
                    m.Limit = ReportsMfgParse.Int(body, "limit", 500, 1, 2000);
                    break;
                default:
                    throw GlReq.Bad("未知的报表 " + name);
            }
            return m;
        }

        static void ParseDaily(MfgArgs m, Dictionary<string, object> body)
        {
            m.GroupBy = ReportsMfgParse.Choice(body, "group_by", "day", GroupBys);
            m.By = ReportsMfgParse.Choice(body, "by", "product", Bys);
            m.ClassGrade = ReportsMfgParse.Int(body, "class_grade", 2, 1, 9);
            m.Inv = ReportsMfgParse.Codes(body, "inv");
            m.Unverified = ReportsMfgParse.Bool(body, "include_unverified", false);
            m.Limit = ReportsMfgParse.Int(body, "limit", 1000, 1, 5000);
        }
    }
}
