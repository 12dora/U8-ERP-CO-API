using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 质量统计报表（qm_indicators、qm_pass_rate）的请求参数。
    internal sealed class QmReportArgs
    {
        // incoming 来料检验单（QM03），product 产品检验单（QM04）。
        public string Kind = "";
        public string VoucherType = "";
        public string DateFrom = "";
        // 空串表示取登录日期。
        public string DateTo = "";
        // 只有 qm_pass_rate 用：month、vendor、inventory。
        public string GroupBy = "month";
        public string[] Inv = new string[0];
        public string[] Vendor = new string[0];
        public string[] Guide = new string[0];
        public string InvClass = "";
        public bool Unverified;
        public int Limit;
    }

    // 质量统计报表族：检验指标统计 qm_indicators、检验合格率 qm_pass_rate。字段名由 Requests.ReportSpecs（Specs）把关，
    // 这里只校验取值，登录前抛 400；处理函数里再解析一次。
    internal static class ReportsQmReq
    {
        internal const string IndicatorsName = "qm_indicators";
        internal const string PassRateName = "qm_pass_rate";
        internal const int MaxCodes = 20;
        internal const int CodeMax = 60;

        internal static readonly string[] Names = new string[] { IndicatorsName, PassRateName };

        // { 路径, 允许的请求字段… }，并入 Requests.ReportSpecs。
        internal static readonly string[][] Specs = new string[][]
        {
            new string[]
            {
                Requests.ReportRoot + IndicatorsName, "kind", "date_from", "date_to", "inv", "inv_class", "vendor",
                "guide", "include_unverified", "limit"
            },
            new string[]
            {
                Requests.ReportRoot + PassRateName, "kind", "date_from", "date_to", "group_by", "inv", "inv_class",
                "vendor", "include_unverified", "limit"
            }
        };

        static readonly string[] GroupBys = new string[] { "month", "vendor", "inventory" };

        internal static bool Owns(string name)
        {
            return Array.IndexOf(Names, name) >= 0;
        }

        // 登录前校验，只抛 400。
        internal static void Check(string name, Dictionary<string, object> body)
        {
            Parse(name, body);
        }

        // 登录子系统：同 vouchers/list 对来料、产品检验单用的（Kinds 里的 SubId）。不是这一族的报表返回 null。
        internal static string SubOf(string name, Dictionary<string, object> body)
        {
            if (!Owns(name))
            {
                return null;
            }
            bool product = (GlReq.Field(body, "kind") as string) == "product";
            VoucherKind kind = Kinds.Find(product ? "qm_product_check" : "qm_incoming_check");
            return kind == null || string.IsNullOrEmpty(kind.SubId) ? "SA" : kind.SubId;
        }

        internal static void AddRunners(Dictionary<string, Func<WorkContext, ReportArgs, ApiResult>> map)
        {
            map.Add(IndicatorsName, ReportsQmIndicators.Run);
            map.Add(PassRateName, ReportsQmPassRate.Run);
        }

        internal static QmReportArgs Parse(string name, Dictionary<string, object> body)
        {
            QmReportArgs q = new QmReportArgs();
            ParseKind(q, body);
            ParseRange(q, body);
            q.Inv = CodeList(body, "inv");
            q.InvClass = OptPrefix(body, "inv_class");
            q.Vendor = CodeList(body, "vendor");
            if (q.Vendor.Length > 0 && q.Kind != "incoming")
            {
                throw GlReq.Bad("vendor 只能用于 kind=incoming", "vendor");
            }
            q.Unverified = OptBool(body, "include_unverified", false);
            q.Limit = OptInt(body, "limit", 200, 1, 1000);
            if (name == IndicatorsName)
            {
                q.Guide = CodeList(body, "guide");
                return q;
            }
            ParseGroupBy(q, body);
            return q;
        }

        // 区间：date_from 必填，date_to 缺省取登录日期（处理函数里再核对先后）。
        internal static void CheckRange(string from, string to)
        {
            if (string.CompareOrdinal(from, to) > 0)
            {
                throw GlReq.Bad("date_from 不能晚于 date_to（缺省为登录日期）", "date_from");
            }
        }

        static void ParseKind(QmReportArgs q, Dictionary<string, object> body)
        {
            string kind = GlReq.Field(body, "kind") as string;
            if (kind == "incoming")
            {
                q.VoucherType = "QM03";
            }
            else if (kind == "product")
            {
                q.VoucherType = "QM04";
            }
            else
            {
                throw GlReq.Bad("kind 只能是 incoming 或 product", "kind");
            }
            q.Kind = kind;
        }

        static void ParseRange(QmReportArgs q, Dictionary<string, object> body)
        {
            q.DateFrom = GlReq.OptDate(GlReq.Field(body, "date_from"), "date_from");
            if (q.DateFrom.Length == 0)
            {
                throw GlReq.Bad("缺少字段 date_from", "date_from");
            }
            q.DateTo = GlReq.OptDate(GlReq.Field(body, "date_to"), "date_to");
            if (q.DateTo.Length > 0)
            {
                CheckRange(q.DateFrom, q.DateTo);
            }
        }

        static void ParseGroupBy(QmReportArgs q, Dictionary<string, object> body)
        {
            object raw = GlReq.Field(body, "group_by");
            if (raw == null)
            {
                return;
            }
            string text = raw as string;
            if (text == null || Array.IndexOf(GroupBys, text) < 0)
            {
                throw GlReq.Bad("group_by 只能是 month、vendor 或 inventory", "group_by");
            }
            if (text == "vendor" && q.Kind != "incoming")
            {
                throw GlReq.Bad("group_by=vendor 只能用于 kind=incoming", "group_by");
            }
            q.GroupBy = text;
        }

        // 编码清单：缺省为空（不限）；给了就 1 到 20 个，每个去掉首尾空白后 1 到 60 个字符、不含控制字符，重复的合并。
        internal static string[] CodeList(Dictionary<string, object> body, string key)
        {
            object raw = GlReq.Field(body, key);
            if (raw == null)
            {
                return new string[0];
            }
            string message = key + " 必须是 1 到 " + MaxCodes.ToString(CultureInfo.InvariantCulture)
                + " 个编码（每个 1 到 " + CodeMax.ToString(CultureInfo.InvariantCulture) + " 个字符，不含控制字符）";
            IList list = raw as IList;
            if (list == null || list.Count < 1 || list.Count > MaxCodes)
            {
                throw GlReq.Bad(message, key);
            }
            List<string> codes = new List<string>();
            for (int i = 0; i < list.Count; i++)
            {
                string text = list[i] as string;
                text = text == null ? "" : text.Trim();
                if (!CleanText(text, CodeMax))
                {
                    throw GlReq.Bad(message, FieldPath.Item(key, i));
                }
                if (!codes.Contains(text))
                {
                    codes.Add(text);
                }
            }
            return codes.ToArray();
        }

        // 存货分类编码前缀：只收字母、数字、点和减号，可以直接当 LIKE 前缀用。
        static string OptPrefix(Dictionary<string, object> body, string key)
        {
            object raw = GlReq.Field(body, key);
            if (raw == null)
            {
                return "";
            }
            if (!ReportsReq.CodeChars(raw as string, 40))
            {
                throw GlReq.Bad(key + " 格式无效", key);
            }
            return (string)raw;
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

        static bool CleanText(string text, int max)
        {
            if (text.Length == 0 || text.Length > max)
            {
                return false;
            }
            for (int i = 0; i < text.Length; i++)
            {
                if (char.IsControl(text[i]))
                {
                    return false;
                }
            }
            return true;
        }
    }
}
