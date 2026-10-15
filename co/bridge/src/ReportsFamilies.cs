using System;
using System.Collections.Generic;

namespace U8Co
{
    // 按角色分族的只读报表（质量 Qm、生产 Mfg、库龄 StockAge、采购 Pu、销售 Sa、收付款与现金流 Fin、账务核对 Acct）的统一入口。
    // 每一族有自己的 Reports<族>Req（Names、Owns、Check、SubOf、AddRunners、Specs），这里只做汇总和转发：
    // Reports 的名单、登录子系统、运行表，ReportsReq 的登录前校验，Requests.ReportSpecs 的字段表都从这里取，
    // 不在各处逐族加分支。新增一族只改下面的 Fams。
    internal static class ReportsFamilies
    {
        sealed class Fam
        {
            public readonly string[] Names;
            public readonly string[][] Specs;
            public readonly Action<string, Dictionary<string, object>> Check;
            public readonly Func<string, Dictionary<string, object>, string> SubOf;
            public readonly Action<Dictionary<string, Func<WorkContext, ReportArgs, ApiResult>>> AddRunners;

            public Fam(string[] names, string[][] specs, Action<string, Dictionary<string, object>> check,
                Func<string, Dictionary<string, object>, string> subOf,
                Action<Dictionary<string, Func<WorkContext, ReportArgs, ApiResult>>> addRunners)
            {
                Names = names;
                Specs = specs;
                Check = check;
                SubOf = subOf;
                AddRunners = addRunners;
            }
        }

        static readonly Fam[] Fams = new Fam[]
        {
            new Fam(ReportsQmReq.Names, ReportsQmReq.Specs, ReportsQmReq.Check, ReportsQmReq.SubOf, ReportsQmReq.AddRunners),
            new Fam(ReportsMfgReq.Names, ReportsMfgReq.Specs, ReportsMfgReq.Check, ReportsMfgReq.SubOf, ReportsMfgReq.AddRunners),
            new Fam(ReportsStockAgeReq.Names, ReportsStockAgeReq.Specs, ReportsStockAgeReq.Check, ReportsStockAgeReq.SubOf,
                ReportsStockAgeReq.AddRunners),
            new Fam(ReportsPuReq.Names, ReportsPuReq.Specs, ReportsPuReq.Check, ReportsPuReq.SubOf, ReportsPuReq.AddRunners),
            new Fam(ReportsSaReq.Names, ReportsSaReq.Specs, ReportsSaReq.Check, ReportsSaReq.SubOf, ReportsSaReq.AddRunners),
            new Fam(ReportsFinReq.Names, ReportsFinReq.Specs, ReportsFinReq.Check, ReportsFinReq.SubOf, ReportsFinReq.AddRunners),
            new Fam(ReportsAcctReq.Names, ReportsAcctReq.Specs, ReportsAcctReq.Check, ReportsAcctReq.SubOf, ReportsAcctReq.AddRunners)
        };

        // 全部报表名，并入 Reports.Names。
        internal static readonly string[] Names = AllNames();

        // { 路径, 允许的请求字段… } 行，并入 Requests.ReportSpecs。
        internal static readonly string[][] Specs = AllSpecs();

        internal static bool Owns(string name)
        {
            return Find(name) != null;
        }

        // 登录前校验，只抛 400。
        internal static void Check(string name, Dictionary<string, object> body)
        {
            Fam fam = Find(name);
            if (fam == null)
            {
                throw GlReq.Bad("未知的报表 " + name);
            }
            fam.Check(name, body);
        }

        // 登录子系统；不是这些族的报表返回 null。
        internal static string SubOf(string name, Dictionary<string, object> body)
        {
            Fam fam = Find(name);
            return fam == null ? null : fam.SubOf(name, body);
        }

        internal static void AddRunners(Dictionary<string, Func<WorkContext, ReportArgs, ApiResult>> map)
        {
            for (int i = 0; i < Fams.Length; i++)
            {
                Fams[i].AddRunners(map);
            }
        }

        static Fam Find(string name)
        {
            for (int i = 0; i < Fams.Length; i++)
            {
                if (Array.IndexOf(Fams[i].Names, name) >= 0)
                {
                    return Fams[i];
                }
            }
            return null;
        }

        static string[] AllNames()
        {
            List<string> list = new List<string>();
            for (int i = 0; i < Fams.Length; i++)
            {
                list.AddRange(Fams[i].Names);
            }
            return list.ToArray();
        }

        static string[][] AllSpecs()
        {
            List<string[]> list = new List<string[]>();
            for (int i = 0; i < Fams.Length; i++)
            {
                list.AddRange(Fams[i].Specs);
            }
            return list.ToArray();
        }
    }
}
