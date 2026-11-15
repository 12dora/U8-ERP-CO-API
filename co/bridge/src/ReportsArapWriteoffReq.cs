using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;

namespace U8Co
{
    // 核销记录查询 arap_writeoffs 的参数（只读）。字段名由 Requests.ReportSpecs 把关；after 由 ReportsReq.Parse 统一校验，
    // 这里只校验其余取值，登录前抛 400；处理函数里再解析一次。
    internal sealed class WriteoffListArgs
    {
        // AR 应收、AP 应付，也是登录子系统。
        public string Flag = "";
        public string Partner = "";
        public int ReceiptId;
        public string ReceiptCode = "";
        public string TargetKind = "";
        public int TargetId;
        public string TargetCode = "";
        public string DateFrom = "";
        public string DateTo = "";
        public string CancelNo = "";
        public int Limit;
    }

    internal static class ReportsArapWriteoffReq
    {
        internal const string Name = "arap_writeoffs";
        const int CodeMax = 60;
        static readonly string[] RefKeys = new string[] { "type", "id" };
        static readonly Regex CancelNo = new Regex("^HX(AR|AP)[0-9]{1,20}\\z", RegexOptions.CultureInvariant);

        public static bool Owns(string name)
        {
            return name == Name;
        }

        // 登录子系统：flag（AR / AP）。不是本报表返回 null。
        public static string SubOf(string name, Dictionary<string, object> body)
        {
            if (!Owns(name))
            {
                return null;
            }
            return (Requests.Field(body, "flag") as string) == "AP" ? "AP" : "AR";
        }

        // 权限规则键：report:arap_writeoffs:ar|ap（PermRegistryWriteoff.WriteoffListRules）。
        public static string RuleKey(Dictionary<string, object> body)
        {
            return "report:" + Name + ":" + ((Requests.Field(body, "flag") as string) == "AP" ? "ap" : "ar");
        }

        public static WriteoffListArgs Parse(ReportArgs a, Dictionary<string, object> body)
        {
            WriteoffListArgs w = Parse(body);
            a.Limit = w.Limit;
            return w;
        }

        public static WriteoffListArgs Parse(Dictionary<string, object> body)
        {
            WriteoffListArgs w = new WriteoffListArgs();
            w.Flag = Requests.Field(body, "flag") as string;
            if (w.Flag != "AR" && w.Flag != "AP")
            {
                throw GlReq.Bad("flag 只能是 AR 或 AP", "flag");
            }
            w.Partner = OptText(body, "partner");
            w.CancelNo = OptCancelNo(body, w.Flag);
            ParseReceipt(w, body);
            ParseTarget(w, body);
            w.DateFrom = GlReq.OptDate(Requests.Field(body, "date_from"), "date_from");
            w.DateTo = GlReq.OptDate(Requests.Field(body, "date_to"), "date_to");
            if (w.DateFrom.Length > 0 && w.DateTo.Length > 0 && string.CompareOrdinal(w.DateFrom, w.DateTo) > 0)
            {
                throw GlReq.Bad("date_from 不能晚于 date_to", "date_from");
            }
            object limit = Requests.Field(body, "limit");
            w.Limit = limit == null ? 50 : GlReq.IntIn(limit, "limit", 1, 200);
            return w;
        }

        // receipt {type, id} 与 receipt_code 二选一；type 必须与 flag 同侧（AR 收款单、AP 付款单）。
        static void ParseReceipt(WriteoffListArgs w, Dictionary<string, object> body)
        {
            w.ReceiptCode = OptText(body, "receipt_code");
            Dictionary<string, object> receipt = OptRef(body, "receipt", w.ReceiptCode.Length > 0, "receipt_code");
            if (receipt == null)
            {
                return;
            }
            string kind = Requests.Field(receipt, "type") as string;
            if (ArapWriteoffReq.FlagOfReceipt(kind) != w.Flag)
            {
                throw GlReq.Bad(w.Flag == "AR" ? "flag=AR 时 receipt.type 只能是 ar_receipt" : "flag=AP 时 receipt.type 只能是 ap_payment", "receipt.type");
            }
            w.ReceiptId = GlReq.IntIn(Requests.Field(receipt, "id"), "receipt.id", 1, int.MaxValue);
        }

        // target {type, id} 与 target_code 二选一；type 必须与 flag 同侧（AR 销售发票、应收单，AP 采购发票、应付单）。
        static void ParseTarget(WriteoffListArgs w, Dictionary<string, object> body)
        {
            w.TargetCode = OptText(body, "target_code");
            Dictionary<string, object> target = OptRef(body, "target", w.TargetCode.Length > 0, "target_code");
            if (target == null)
            {
                return;
            }
            string kind = Requests.Field(target, "type") as string;
            if (ArapWriteoffReq.FlagOfItem(kind) != w.Flag)
            {
                throw GlReq.Bad(w.Flag == "AR" ? "flag=AR 时 target.type 只能是 sale_invoice 或 ar_bill"
                    : "flag=AP 时 target.type 只能是 purchase_invoice 或 ap_bill", "target.type");
            }
            w.TargetKind = kind;
            w.TargetId = GlReq.IntIn(Requests.Field(target, "id"), "target.id", 1, int.MaxValue);
        }

        // 可选的 {type, id} 对象；与对应的 *_code 同时给时 400。
        static Dictionary<string, object> OptRef(Dictionary<string, object> body, string key, bool codeGiven, string codeKey)
        {
            object raw = Requests.Field(body, key);
            if (raw == null)
            {
                return null;
            }
            if (codeGiven)
            {
                throw GlReq.Bad(key + " 和 " + codeKey + " 只能给一个", key);
            }
            Dictionary<string, object> map = raw as Dictionary<string, object>;
            if (map == null)
            {
                throw GlReq.Bad(key + " 必须是对象 {type, id}", key);
            }
            foreach (string name in map.Keys)
            {
                if (Array.IndexOf(RefKeys, name) < 0)
                {
                    throw GlReq.Bad(key + " 含未知字段 " + name, FieldPath.Join(key, name));
                }
            }
            return map;
        }

        static string OptCancelNo(Dictionary<string, object> body, string flag)
        {
            object raw = Requests.Field(body, "cancel_no");
            if (raw == null)
            {
                return "";
            }
            string no = raw as string;
            if (no == null || !CancelNo.IsMatch(no))
            {
                throw GlReq.Bad("cancel_no 必须是 HXAR 或 HXAP 后接数字的核销号", "cancel_no");
            }
            if (no.Substring(2, 2) != flag)
            {
                throw GlReq.Bad("cancel_no 与 flag 不一致（HXAR 是应收、HXAP 是应付）", "cancel_no");
            }
            return no;
        }

        // 编码类文本：去掉首尾空白后 1 到 60 个字符，不含控制字符。只进参数。
        static string OptText(Dictionary<string, object> body, string key)
        {
            object raw = Requests.Field(body, key);
            if (raw == null)
            {
                return "";
            }
            string text = raw as string;
            text = text == null ? "" : text.Trim();
            if (text.Length == 0 || text.Length > CodeMax || HasControl(text))
            {
                throw GlReq.Bad(key + " 必须是 1 到 " + CodeMax.ToString(CultureInfo.InvariantCulture) + " 个字符，不含控制字符", key);
            }
            return text;
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
