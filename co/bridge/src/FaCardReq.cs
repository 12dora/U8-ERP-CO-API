using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 一张待导入的卡片：EAI 表头标签 → 值（已规整），以及闸门要查的几个值。
    internal sealed class FaCardPlan
    {
        public string AssetNum;
        public string Name;
        public string Type;
        public string Origin;
        public string Status;
        public string Method;
        public string Dept;
        public string Currency;
        public decimal Value;
        public DateTime Start;
        public Dictionary<string, string> Tags = new Dictionary<string, string>(StringComparer.Ordinal);
    }

    // 固定资产卡片（fa_card）新增的请求规则，登录前在 ArcReq.Parse 的 KindChecks 里调用。
    // 新增的 code 是资产编号（fa_Cards.sAssetNum，EAI 的 assetno），卡片编号由 U8 自动编号，响应的 code 是新卡片编号。
    // 字段名用 fa_card 读取时的名字；EAI 标签照 U8 的 CapitalAsserts 模板。只收单个使用部门（比例 1）、本位币。
    internal static class FaCardReq
    {
        const decimal MoneyMax = 1000000000000m;
        // { 字段名, EAI 标签, 表列名（meta 用）, 类型 t 文本 / n 数 / i 整数 / d 日期, 文本最长或整数上限, 必填 }。
        static readonly string[][] Spec = new string[][]
        {
            new string[] { "name", "assetname", "sAssetName", "t", "50", "1" },
            new string[] { "type_code", "typeno", "sTypeNum", "t", "20", "1" },
            new string[] { "original_value", "originalvalue", "dblValue", "n", "", "1" },
            new string[] { "start_date", "startusedate", "dStartdate", "d", "", "1" },
            new string[] { "origin_code", "accountaddmannerno", "sOrgAddID", "t", "10", "1" },
            new string[] { "status_code", "status", "sStatusID", "t", "10", "1" },
            new string[] { "depreciation_method_code", "depreciationmanner", "sDeprMethodID", "t", "10", "1" },
            new string[] { "dept_code", "deptno", "sDeptNum", "t", "12", "1" },
            new string[] { "useful_life_months", "life", "lLife", "i", "11988", "" },
            new string[] { "used_months", "usedmonths", "lUsedMonths", "i", "11988", "" },
            new string[] { "accumulated_depreciation", "accdepr", "dblDeprT", "n", "", "" },
            new string[] { "net_salvage", "netleftvalue", "dblBV", "n", "", "" },
            new string[] { "net_salvage_rate", "netleftvaluerate", "dblBVRate", "n", "", "" },
            new string[] { "spec", "style", "sStyle", "t", "50", "" },
            new string[] { "location", "reservesite", "sSite", "t", "50", "" },
            new string[] { "keeper", "skeeper", "sKeeper", "t", "20", "" },
            new string[] { "impairment", "decvalue", "dblDecValueT", "n", "", "" },
            new string[] { "currency", "currency", "sCurrency", "t", "8", "" }
        };

        internal const string UpdateText = "固定资产卡片不能直接修改：原值、使用状况等的变化请在 U8 客户端录入变动单（U8 的 EAI 没有变动单导入样式表）";

        // 档案登记（ArcKindRo）：可新增、可撤销新增（删除），不能修改；读取照旧走 ArcFa。
        internal static ArcKind Kind(ArcKind k)
        {
            k.ReadOnly = false;
            k.NoUpdate = true;
            k.NoUpdateText = UpdateText;
            string[] pairs = new string[Spec.Length * 2];
            for (int i = 0; i < Spec.Length; i++)
            {
                pairs[i * 2] = Spec[i][0];
                pairs[i * 2 + 1] = Spec[i][2];
            }
            k.SqlMap = ArcMap.Fixed(pairs);
            return k;
        }

        // 登录前：新增的字段逐个校验；删除只有编码（卡片编号）。
        internal static void Check(ArcReq req)
        {
            if (req.Op != "create")
            {
                return;
            }
            if (req.Template != null)
            {
                throw ArcReq.Bad("档案 fa_card 不支持 template", "template");
            }
            Plan(req);
        }

        // 请求 → 卡片（同时完成全部格式校验，400 带字段路径）。
        internal static FaCardPlan Plan(ArcReq req)
        {
            FaCardPlan plan = new FaCardPlan();
            plan.AssetNum = req.Code;
            plan.Tags["assetno"] = req.Code;
            for (int i = 0; i < Spec.Length; i++)
            {
                string value = Value(req, Spec[i]);
                if (value != null)
                {
                    plan.Tags[Spec[i][1]] = value;
                }
            }
            plan.Name = plan.Tags["assetname"];
            plan.Type = plan.Tags["typeno"];
            plan.Origin = plan.Tags["accountaddmannerno"];
            plan.Status = plan.Tags["status"];
            plan.Method = plan.Tags["depreciationmanner"];
            plan.Dept = plan.Tags["deptno"];
            plan.Tags.Remove("deptno");
            string currency;
            plan.Currency = plan.Tags.TryGetValue("currency", out currency) ? currency : null;
            plan.Value = Dec(plan.Tags["originalvalue"]);
            plan.Start = DateTime.ParseExact(plan.Tags["startusedate"], "yyyy-MM-dd", CultureInfo.InvariantCulture);
            Cross(plan);
            return plan;
        }

        static string Value(ArcReq req, string[] spec)
        {
            string text = req.Fields.Get(spec[0]);
            string at = FieldPath.Join("fields", spec[0]);
            if (text == null || text.Trim().Length == 0)
            {
                if (spec[5] == "1")
                {
                    throw ArcReq.Bad("缺少字段 " + spec[0], at);
                }
                return null;
            }
            text = text.Trim();
            switch (spec[3])
            {
                case "n":
                    return Money(text, spec[0], at);
                case "i":
                    return Int(text, spec[0], int.Parse(spec[4], CultureInfo.InvariantCulture), at);
                case "d":
                    return Day(text, spec[0], at);
                default:
                    return Text(text, spec[0], int.Parse(spec[4], CultureInfo.InvariantCulture), at);
            }
        }

        // 净残值、累计折旧不超过原值，净残值率小于 1。
        static void Cross(FaCardPlan plan)
        {
            string[] caps = new string[] { "accdepr", "accumulated_depreciation", "netleftvalue", "net_salvage" };
            for (int i = 0; i < caps.Length; i += 2)
            {
                string text;
                if (plan.Tags.TryGetValue(caps[i], out text) && Dec(text) > plan.Value)
                {
                    throw ArcReq.Bad("字段 " + caps[i + 1] + " 不能大于原值 original_value", FieldPath.Join("fields", caps[i + 1]));
                }
            }
            string rate;
            if (plan.Tags.TryGetValue("netleftvaluerate", out rate) && Dec(rate) >= 1m)
            {
                throw ArcReq.Bad("字段 net_salvage_rate 必须小于 1", "fields.net_salvage_rate");
            }
            if (Dec(plan.Tags["originalvalue"]) <= 0m)
            {
                throw ArcReq.Bad("原值 original_value 必须大于 0", "fields.original_value");
            }
        }

        // 金额：不小于 0、不超过 1000000000000 的有限数，按不变区域格式。
        internal static string Money(string text, string name, string at)
        {
            decimal value;
            if (!decimal.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out value) || value < 0m || value > MoneyMax)
            {
                throw ArcReq.Bad("字段 " + name + " 必须是 0 到 1000000000000 之间的数", at);
            }
            return Format(value);
        }

        // 去掉末尾的 0（1200.00 发 1200），最多 10 位小数。
        internal static string Format(decimal value)
        {
            return value.ToString("0.##########", CultureInfo.InvariantCulture);
        }

        static string Int(string text, string name, int max, string at)
        {
            int value;
            if (!int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out value) || value < 0 || value > max)
            {
                throw ArcReq.Bad("字段 " + name + " 必须是 0 到 " + max.ToString(CultureInfo.InvariantCulture) + " 的整数", at);
            }
            return value.ToString(CultureInfo.InvariantCulture);
        }

        internal static string Day(string text, string name, string at)
        {
            DateTime day;
            if (!DateTime.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out day))
            {
                throw ArcReq.Bad("字段 " + name + " 必须写成 yyyy-mm-dd", at);
            }
            return day.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        }

        static string Text(string text, string name, int max, string at)
        {
            if (text.Length > max)
            {
                throw ArcReq.Bad("字段 " + name + " 最长 " + max.ToString(CultureInfo.InvariantCulture) + " 个字符", at);
            }
            return text;
        }

        internal static decimal Dec(string text)
        {
            return decimal.Parse(text, NumberStyles.Float, CultureInfo.InvariantCulture);
        }
    }
}
