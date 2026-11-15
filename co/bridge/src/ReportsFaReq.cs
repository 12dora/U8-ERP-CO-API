using System.Collections.Generic;

namespace U8Co
{
    // 固定资产变动单 reports/fa_changes、折旧 reports/fa_depreciation 的参数（只读）。字段名由 Requests.ReportSpecs 把关，
    // 这里只校验取值，登录前抛 400；处理函数里再解析一次。登录子系统 SA（固定资产 FA 不在许可采样的登录模块里，同 fa_card）。
    // 都按会计年度 fiscal_year（缺省登录年度，Reports.Handle 填），period 可选 1–12；card 是卡片编号 sCardNum。
    // fiscal_year 指固定资产业务年度（折旧 iyear、变动日期的年份），不是请求 year / 账套库年份。
    internal sealed class FaReportArgs
    {
        public string Card = "";
        public string Code = "";
        // 0 表示不按期间过滤。
        public int Period;
        // 0 表示不按变动单类型过滤。
        public int ChangeType;
        public bool NonZero;
    }

    internal static class ReportsFaReq
    {
        internal const string ChangesName = "fa_changes";
        internal const string DeprName = "fa_depreciation";
        internal const string ChangesPath = Requests.ReportRoot + ChangesName;
        internal const string DeprPath = Requests.ReportRoot + DeprName;
        internal const string ChangesRule = "report:" + ChangesName;
        internal const string DeprRule = "report:" + DeprName;
        // 卡片编号 fa_Cards.sCardNum 按 20 个字符（同 ArcFa.CodeMax）；变动单号 fa_Vouchers.sNum nvarchar(10)。
        const int CardMax = 20;
        const int CodeMax = 10;

        public static bool Owns(string name)
        {
            return name == ChangesName || name == DeprName;
        }

        public static FaReportArgs Parse(ReportArgs a, Dictionary<string, object> body)
        {
            FaReportArgs f = new FaReportArgs();
            object limit = GlReq.Field(body, "limit");
            a.Limit = limit == null ? 200 : GlReq.IntIn(limit, "limit", 1, 1000);
            f.Card = Code(body, "card", CardMax);
            f.Period = OptInt(body, "period", 1, 12);
            if (a.Name == ChangesName)
            {
                f.Code = Code(body, "code", CodeMax);
                f.ChangeType = OptInt(body, "change_type", 1, 99);
                return f;
            }
            object nonzero = GlReq.Field(body, "nonzero");
            if (nonzero != null && !(nonzero is bool))
            {
                throw GlReq.Bad("nonzero 必须是 true 或 false", "nonzero");
            }
            f.NonZero = nonzero != null && (bool)nonzero;
            return f;
        }

        static int OptInt(Dictionary<string, object> body, string key, int min, int max)
        {
            object raw = GlReq.Field(body, key);
            return raw == null ? 0 : GlReq.IntIn(raw, key, min, max);
        }

        // 编码：字母、数字、点和减号（ReportsReq.CodeChars），不收通配符；缺省为空串。
        static string Code(Dictionary<string, object> body, string key, int max)
        {
            object raw = GlReq.Field(body, key);
            if (raw == null)
            {
                return "";
            }
            string text = raw as string;
            if (!ReportsReq.CodeChars(text, max))
            {
                throw GlReq.Bad(key + " 格式无效", key);
            }
            return text;
        }
    }
}
