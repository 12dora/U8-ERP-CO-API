using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 只读报表的请求参数。字段名由 Requests.ReportSpecs 把关，这里只校验取值，登录前抛 400。
    internal sealed class ReportArgs
    {
        public string Name;
        // 0 表示取登录日期的年份（与总账一致）。
        public int FiscalYear;
        public int From;
        public int To;
        public int GradeFrom;
        public int GradeTo;
        public string CodePrefix;
        public bool LeafOnly;
        public bool Unposted;
        public bool NonZero;
        public string Dim;
        public string DimCode;
        public string ProjectClass;
        public string Side;
        // 空串表示取登录日期。
        public string AsOf;
        public string[] Accounts;
        public string[] Excludes;
        public string Partner;
        public string Basis;
        public int[] Buckets;
        // 账龄分组：partner（缺省）、person、partner_person；Persons 是业务员编码条件（空数组不限）。
        public string GroupBy;
        public string[] Persons;
        public bool OverdueOnly;
        // 账龄 basis=due：没有收付款日期、信用期为 0 的单据按起算日 + 这么多天算到期日（缺省 0）；HasDefaultCreditDays 表示请求给了。
        public int DefaultCreditDays;
        public bool HasDefaultCreditDays;
        public string Parent;
        public int Levels;
        public string After;
        public int Limit;
    }

    internal static class ReportsReq
    {
        const int MaxYear = 2099;
        // next 游标是 base64url：辅助核算的（科目 + 大类 + 60 字的维度编码，UTF-8）最长约 300 字符，留足余量。
        internal const int MaxCursor = 512;
        const int MinYear = 2000;
        static readonly string[] Dims = new string[] { "customer", "vendor", "dept", "person", "project" };
        static readonly int[] DefaultBuckets = new int[] { 30, 60, 90, 180, 365 };
        static readonly string[] GroupBys = new string[] { "partner", "person", "partner_person" };

        public static ReportArgs Parse(string name, Dictionary<string, object> body)
        {
            ReportArgs a = new ReportArgs();
            a.Name = name ?? "";
            a.FiscalYear = OptInt(body, "fiscal_year", 0, MinYear, MaxYear);
            a.After = OptCode(body, "after", MaxCursor, false);
            if (a.Name == "close_status")
            {
                return a;
            }
            if (a.Name == "gl_balance" || a.Name == "gl_aux_balance")
            {
                ParseGl(a, body);
                return a;
            }
            if (a.Name == "arap_balance" || a.Name == "arap_aging")
            {
                ParseArap(a, body);
                return a;
            }
            if (a.Name == "bom")
            {
                a.Parent = NeedText(body, "parent", 60);
                a.AsOf = OptDate(body, "as_of");
                a.Levels = OptInt(body, "levels", 1, 1, 10);
                a.Limit = OptInt(body, "limit", 1000, 1, 5000);
                return a;
            }
            ParseOwned(a, body);
            return a;
        }

        // 参数另有专门类的报表（明细账、订单执行与单据追溯、库存与销售支持报表）；都不是时 400。
        static void ParseOwned(ReportArgs a, Dictionary<string, object> body)
        {
            // 明细账（arap_detail、gl_detail）：参数另有 DetailArgs，处理函数里再解析一次。
            if (ReportsDetailReq.Owns(a.Name))
            {
                ReportsDetailReq.Parse(a.Name, body);
                return;
            }
            // 订单执行、单据追溯（order_execution、doc_trace）：参数另有 ExecArgs / TraceArgs，处理函数里再解析一次。
            if (ReportsTraceReq.Owns(a.Name))
            {
                ReportsTraceReq.Check(a.Name, body);
                return;
            }
            // 库存与销售支持报表（stock_ledger 等六张）：参数另有 StockReportArgs，处理函数里再解析一次。
            if (ReportsStockReq.Owns(a.Name))
            {
                ReportsStockReq.Parse(a, body);
                return;
            }
            // 期初余额（opening_balance）：参数另有 OpeningArgs，处理函数里再解析一次。
            if (ReportsOpeningReq.Owns(a.Name))
            {
                ReportsOpeningReq.Parse(a, body);
                return;
            }
            // 核销记录（arap_writeoffs）：参数另有 WriteoffListArgs，处理函数里再解析一次。
            if (ReportsArapWriteoffReq.Owns(a.Name))
            {
                ReportsArapWriteoffReq.Parse(a, body);
                return;
            }
            // 账套体检（account_readiness）：只有可选 as_of。
            if (ReportsReadinessReq.Owns(a.Name))
            {
                ReportsReadinessReq.Parse(a, body);
                return;
            }
            // 固定资产变动单、折旧（fa_changes、fa_depreciation）：参数另有 FaReportArgs，处理函数里再解析一次。
            if (ReportsFaReq.Owns(a.Name))
            {
                ReportsFaReq.Parse(a, body);
                return;
            }
            // 经营管理（总账口径）mgmt/pnl、mgmt/meta、mgmt/cash_stock：参数另有 MgmtGlArgs，处理函数里再解析一次。
            if (ReportsMgmtGlReq.Owns(a.Name))
            {
                ReportsMgmtGlReq.Parse(a.Name, body);
                return;
            }
            // 经营管理销售分析、往来账期 mgmt/sales、mgmt/arap_terms：参数另有 MgmtSalesArgs / MgmtArapArgs，处理函数里再解析一次。
            if (ReportsMgmtArapReq.Check(a, body))
            {
                return;
            }
            throw GlReq.Bad("未知的报表 " + a.Name);
        }

        static void ParseGl(ReportArgs a, Dictionary<string, object> body)
        {
            a.From = GlReq.IntIn(GlReq.Field(body, "period_from"), "period_from", 1, 12);
            a.To = GlReq.IntIn(GlReq.Field(body, "period_to"), "period_to", 1, 12);
            if (a.From > a.To)
            {
                throw GlReq.Bad("period_from 不能大于 period_to", "period_from");
            }
            a.CodePrefix = OptCode(body, "code_prefix", 40, true);
            a.NonZero = OptBool(body, "nonzero", false);
            a.Limit = OptInt(body, "limit", 200, 1, 1000);
            if (a.Name == "gl_balance")
            {
                a.GradeFrom = OptInt(body, "grade_from", 1, 1, 9);
                a.GradeTo = OptInt(body, "grade_to", 9, 1, 9);
                if (a.GradeFrom > a.GradeTo)
                {
                    throw GlReq.Bad("grade_from 不能大于 grade_to", "grade_from");
                }
                a.LeafOnly = OptBool(body, "leaf_only", false);
                a.Unposted = OptBool(body, "include_unposted", false);
                return;
            }
            a.Dim = GlReq.Field(body, "dim") as string;
            if (a.Dim == null || Array.IndexOf(Dims, a.Dim) < 0)
            {
                throw GlReq.Bad("dim 只能是 customer、vendor、dept、person、project", "dim");
            }
            a.DimCode = OptText(body, "dim_code", 60);
            a.ProjectClass = OptAlnum(body, "project_class", 20);
            if (a.ProjectClass.Length > 0 && a.Dim != "project")
            {
                throw GlReq.Bad("只有 dim=project 才能带 project_class", "project_class");
            }
        }

        static void ParseArap(ReportArgs a, Dictionary<string, object> body)
        {
            a.Side = GlReq.Field(body, "side") as string;
            if (a.Side != "ar" && a.Side != "ap")
            {
                throw GlReq.Bad("side 只能是 ar 或 ap", "side");
            }
            a.AsOf = OptDate(body, "as_of");
            string[] fallback = new string[] { a.Side == "ar" ? "1122" : "2202" };
            a.Accounts = CodeList(body, "accounts", fallback, 1);
            a.Excludes = CodeList(body, "exclude_accounts", new string[0], 0);
            a.Partner = OptText(body, "partner", 60);
            a.NonZero = OptBool(body, "nonzero", true);
            a.Limit = OptInt(body, "limit", 200, 1, 1000);
            a.GroupBy = "partner";
            a.Persons = new string[0];
            if (a.Name != "arap_aging")
            {
                return;
            }
            object basis = GlReq.Field(body, "basis");
            a.Basis = basis == null ? "document" : basis as string;
            if (a.Basis != "document" && a.Basis != "due")
            {
                throw GlReq.Bad("basis 只能是 document 或 due", "basis");
            }
            a.Buckets = BucketList(GlReq.Field(body, "buckets"));
            ParsePersonKeys(a, body);
        }

        static void ParsePersonKeys(ReportArgs a, Dictionary<string, object> body)
        {
            object group = GlReq.Field(body, "group_by");
            a.GroupBy = group == null ? "partner" : group as string;
            if (a.GroupBy == null || Array.IndexOf(GroupBys, a.GroupBy) < 0)
            {
                throw GlReq.Bad("group_by 只能是 partner、person 或 partner_person", "group_by");
            }
            a.Persons = PersonList(GlReq.Field(body, "person"));
            a.OverdueOnly = OptBool(body, "overdue_only", false);
            if (a.OverdueOnly && a.Basis != "due")
            {
                throw GlReq.Bad("overdue_only 只能和 basis=due 一起用", "overdue_only");
            }
            ParseCreditDays(a, body);
        }

        // default_credit_days：0 到 3650 的整数，只能和 basis=due 一起用。
        static void ParseCreditDays(ReportArgs a, Dictionary<string, object> body)
        {
            a.HasDefaultCreditDays = GlReq.Field(body, "default_credit_days") != null;
            if (!a.HasDefaultCreditDays)
            {
                return;
            }
            if (a.Basis != "due")
            {
                throw GlReq.Bad("default_credit_days 只能和 basis=due 一起用", "default_credit_days")
                    .WithHint("按到期日算账龄时才用缺省信用天数，请同时给 basis=due");
            }
            a.DefaultCreditDays = OptInt(body, "default_credit_days", 0, 0, 3650);
        }

        // 业务员编码：1 到 20 个，每个去掉首尾空白后 1 到 20 个字符，不含控制字符。
        static string[] PersonList(object raw)
        {
            if (raw == null)
            {
                return new string[0];
            }
            const string message = "person 必须是 1 到 20 个业务员编码（每个 1 到 20 个字符，不含控制字符）";
            IList list = AsList(raw);
            if (list == null || list.Count < 1 || list.Count > 20)
            {
                throw GlReq.Bad(message, "person");
            }
            string[] codes = new string[list.Count];
            for (int i = 0; i < list.Count; i++)
            {
                string text = list[i] as string;
                text = text == null ? "" : text.Trim();
                if (text.Length == 0 || text.Length > 20 || HasControl(text))
                {
                    throw GlReq.Bad(message, FieldPath.Item("person", i));
                }
                codes[i] = text;
            }
            return codes;
        }

        static int[] BucketList(object raw)
        {
            if (raw == null)
            {
                return (int[])DefaultBuckets.Clone();
            }
            const string message = "buckets 必须是 1 到 10 个递增的天数（1 到 3650）";
            IList list = AsList(raw);
            if (list == null || list.Count < 1 || list.Count > 10)
            {
                throw GlReq.Bad(message, "buckets");
            }
            int[] days = new int[list.Count];
            for (int i = 0; i < list.Count; i++)
            {
                long day = DayOf(list[i]);
                if (day < 1 || day > 3650 || (i > 0 && day <= days[i - 1]))
                {
                    throw GlReq.Bad(message, FieldPath.Item("buckets", i));
                }
                days[i] = (int)day;
            }
            return days;
        }

        // 严格整数；布尔、字符串、小数返回 0（不合格）。
        static long DayOf(object item)
        {
            if (item is int || item is long)
            {
                return Convert.ToInt64(item, CultureInfo.InvariantCulture);
            }
            return 0;
        }

        static string[] CodeList(Dictionary<string, object> body, string key, string[] fallback, int min)
        {
            object raw = GlReq.Field(body, key);
            if (raw == null)
            {
                return fallback;
            }
            string message = key + " 必须是 " + min.ToString(CultureInfo.InvariantCulture) + " 到 20 个科目编码前缀";
            IList list = AsList(raw);
            if (list == null || list.Count < min || list.Count > 20)
            {
                throw GlReq.Bad(message, key);
            }
            string[] codes = new string[list.Count];
            for (int i = 0; i < list.Count; i++)
            {
                string text = list[i] as string;
                if (!CodeChars(text, 40))
                {
                    throw GlReq.Bad(message, FieldPath.Item(key, i));
                }
                codes[i] = text;
            }
            return codes;
        }

        static IList AsList(object raw)
        {
            IList list = raw as ArrayList;
            if (list != null)
            {
                return list;
            }
            return raw as object[];
        }

        static int OptInt(Dictionary<string, object> body, string key, int fallback, int min, int max)
        {
            object raw = GlReq.Field(body, key);
            return raw == null ? fallback : GlReq.IntIn(raw, key, min, max);
        }

        static bool OptBool(Dictionary<string, object> body, string key, bool fallback)
        {
            object raw = GlReq.Field(body, key);
            if (raw == null)
            {
                return fallback;
            }
            if (!(raw is bool))
            {
                throw GlReq.Bad(key + " 必须是 true 或 false", key);
            }
            return (bool)raw;
        }

        static string OptDate(Dictionary<string, object> body, string key)
        {
            return GlReq.OptDate(GlReq.Field(body, key), key);
        }

        // 编码类参数：只收字母、数字、点和减号，可以直接当 LIKE 前缀用（没有通配符）。after 游标另收 = 和 _。
        static string OptCode(Dictionary<string, object> body, string key, int max, bool strict)
        {
            object raw = GlReq.Field(body, key);
            if (raw == null)
            {
                return "";
            }
            string text = raw as string;
            bool ok = strict ? CodeChars(text, max) : CursorChars(text, max);
            if (!ok)
            {
                throw GlReq.Bad(key + " 格式无效", key);
            }
            return text;
        }

        // 只收 ASCII 字母和数字。
        static string OptAlnum(Dictionary<string, object> body, string key, int max)
        {
            string text = OptCode(body, key, max, true);
            for (int i = 0; i < text.Length; i++)
            {
                if (text[i] == '.' || text[i] == '-')
                {
                    throw GlReq.Bad(key + " 格式无效", key);
                }
            }
            return text;
        }

        static string OptText(Dictionary<string, object> body, string key, int max)
        {
            object raw = GlReq.Field(body, key);
            if (raw == null)
            {
                return "";
            }
            string text = raw as string;
            if (text == null || text.Trim().Length == 0 || text.Length > max || HasControl(text))
            {
                throw GlReq.Bad(key + " 必须是 1 到 " + max.ToString(CultureInfo.InvariantCulture) + " 个字符，不含控制字符", key);
            }
            return text.Trim();
        }

        static string NeedText(Dictionary<string, object> body, string key, int max)
        {
            string text = OptText(body, key, max);
            if (text.Length == 0)
            {
                throw GlReq.Bad("缺少字段 " + key, key);
            }
            return text;
        }

        internal static bool CodeChars(string text, int max)
        {
            if (text == null || text.Length < 1 || text.Length > max)
            {
                return false;
            }
            for (int i = 0; i < text.Length; i++)
            {
                if (!CodeChar(text[i]))
                {
                    return false;
                }
            }
            return true;
        }

        static bool CodeChar(char c)
        {
            if (c == '.' || c == '-')
            {
                return true;
            }
            return (c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z');
        }

        static bool CursorChars(string text, int max)
        {
            if (text == null || text.Length < 1 || text.Length > max)
            {
                return false;
            }
            for (int i = 0; i < text.Length; i++)
            {
                char c = text[i];
                if (c <= ' ' || c > '~')
                {
                    return false;
                }
            }
            return true;
        }

        static bool HasControl(string text)
        {
            for (int i = 0; i < text.Length; i++)
            {
                if (char.IsControl(text[i]))
                {
                    return true;
                }
            }
            return false;
        }
    }
}
