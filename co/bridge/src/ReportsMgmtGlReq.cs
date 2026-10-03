using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // 经营管理报表（总账口径）mgmt/pnl、mgmt/meta、mgmt/cash_stock 的参数（只读，聚合结果、不翻页）。
    // 字段名由 Requests.ReportSpecs 把关，这里只校验取值，登录前抛 400；处理函数里再解析一次。登录子系统 GL。
    // 科目缺省按 2007 企业会计准则一级科目：损益类 6、本年利润 4103、货币资金 1001 / 1002 / 1012、应收票据 1121；
    // 科目体系不同的账套由调用方传 pl_accounts / profit_account / cash_accounts / notes_accounts。
    internal sealed class MgmtGlArgs
    {
        public int From = 1;
        public int To = 12;
        public bool Unposted;
        // prefix4（按一级科目，取编码前 4 位）或 leaf（末级科目）。
        public string Detail = "prefix4";
        public bool ByDept;
        public bool ByItem;
        public string[] PlAccounts = new string[] { "6" };
        public string ProfitAccount = "4103";
        // cash_stock：期间、货币资金与应收票据科目前缀、排行行数、采购来源、出入库是否含未审核。
        public int Period;
        public string[] CashAccounts = new string[] { "1001", "1002", "1012" };
        public string[] NotesAccounts = new string[] { "1121" };
        public int Top = 200;
        public string PurchaseSource = "auto";
        public bool Unverified;
    }

    internal static class ReportsMgmtGlReq
    {
        internal const string PnlName = "mgmt/pnl";
        internal const string MetaName = "mgmt/meta";
        internal const string CashName = "mgmt/cash_stock";
        internal const string PnlPath = Requests.ReportRoot + PnlName;
        internal const string MetaPath = Requests.ReportRoot + MetaName;
        internal const string CashPath = Requests.ReportRoot + CashName;
        internal const string PnlRule = "report:" + PnlName;
        internal const string MetaRule = "report:" + MetaName;
        internal const string CashRule = "report:" + CashName;
        // 一次返回的行数上限（聚合结果，不翻页）；超过时 400，请调用方缩小期间或减少维度。
        internal const int MaxRows = 20000;
        const int CodeMax = 40;
        static readonly string[] Details = new string[] { "prefix4", "leaf" };
        static readonly string[] Sources = new string[] { "auto", "invoice", "receipt" };

        public static bool Owns(string name)
        {
            return name == PnlName || name == MetaName || name == CashName;
        }

        // 三张都按总账登录；不是这三张时返回 null。
        public static string SubOf(string name)
        {
            return Owns(name) ? "GL" : null;
        }

        public static MgmtGlArgs Parse(string name, Dictionary<string, object> body)
        {
            MgmtGlArgs m = new MgmtGlArgs();
            if (name == PnlName)
            {
                ParsePnl(m, body);
            }
            else if (name == CashName)
            {
                ParseCash(m, body);
            }
            return m;
        }

        static void ParsePnl(MgmtGlArgs m, Dictionary<string, object> body)
        {
            m.From = GlReq.IntIn(GlReq.Field(body, "period_from"), "period_from", 1, 12);
            m.To = GlReq.IntIn(GlReq.Field(body, "period_to"), "period_to", 1, 12);
            if (m.From > m.To)
            {
                throw GlReq.Bad("period_from 不能大于 period_to", "period_from");
            }
            m.Unposted = OptBool(body, "include_unposted");
            object detail = GlReq.Field(body, "detail");
            m.Detail = detail == null ? "prefix4" : detail as string;
            if (m.Detail == null || Array.IndexOf(Details, m.Detail) < 0)
            {
                throw GlReq.Bad("detail 只能是 prefix4 或 leaf", "detail");
            }
            ParseDims(m, GlReq.Field(body, "dims"));
            m.PlAccounts = Codes(body, "pl_accounts", m.PlAccounts);
            object profit = GlReq.Field(body, "profit_account");
            if (profit != null)
            {
                m.ProfitAccount = profit as string;
                if (!ReportsReq.CodeChars(m.ProfitAccount, CodeMax))
                {
                    throw GlReq.Bad("profit_account 格式无效", "profit_account");
                }
            }
        }

        // dims：空数组、["dept"]、["item"] 或两者都要；不能重复。
        static void ParseDims(MgmtGlArgs m, object raw)
        {
            if (raw == null)
            {
                return;
            }
            const string message = "dims 只能是 dept、item 组成的数组（不重复）";
            IList list = AsList(raw);
            if (list == null || list.Count > 2)
            {
                throw GlReq.Bad(message, "dims");
            }
            List<string> seen = new List<string>();
            for (int i = 0; i < list.Count; i++)
            {
                string dim = list[i] as string;
                if ((dim != "dept" && dim != "item") || seen.Contains(dim))
                {
                    throw GlReq.Bad(message, FieldPath.Item("dims", i));
                }
                seen.Add(dim);
            }
            m.ByDept = seen.Contains("dept");
            m.ByItem = seen.Contains("item");
        }

        static void ParseCash(MgmtGlArgs m, Dictionary<string, object> body)
        {
            m.Period = GlReq.IntIn(GlReq.Field(body, "period"), "period", 1, 12);
            m.CashAccounts = Codes(body, "cash_accounts", m.CashAccounts);
            m.NotesAccounts = Codes(body, "notes_accounts", m.NotesAccounts);
            object top = GlReq.Field(body, "top");
            m.Top = top == null ? 200 : GlReq.IntIn(top, "top", 1, 500);
            object source = GlReq.Field(body, "purchase_source");
            m.PurchaseSource = source == null ? "auto" : source as string;
            if (m.PurchaseSource == null || Array.IndexOf(Sources, m.PurchaseSource) < 0)
            {
                throw GlReq.Bad("purchase_source 只能是 auto、invoice 或 receipt", "purchase_source");
            }
            m.Unverified = OptBool(body, "include_unverified");
        }

        static bool OptBool(Dictionary<string, object> body, string key)
        {
            object raw = GlReq.Field(body, key);
            if (raw == null)
            {
                return false;
            }
            if (!(raw is bool))
            {
                throw GlReq.Bad(key + " 必须是 true 或 false", key);
            }
            return (bool)raw;
        }

        // 科目编码前缀：1 到 10 个，每个只含字母、数字、点和减号（直接当 LIKE 前缀，没有通配符）。
        static string[] Codes(Dictionary<string, object> body, string key, string[] fallback)
        {
            object raw = GlReq.Field(body, key);
            if (raw == null)
            {
                return fallback;
            }
            string message = key + " 必须是 1 到 10 个科目编码前缀";
            IList list = AsList(raw);
            if (list == null || list.Count < 1 || list.Count > 10)
            {
                throw GlReq.Bad(message, key);
            }
            string[] codes = new string[list.Count];
            for (int i = 0; i < list.Count; i++)
            {
                string text = list[i] as string;
                if (!ReportsReq.CodeChars(text, CodeMax))
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
            return list ?? raw as object[];
        }
    }
}
