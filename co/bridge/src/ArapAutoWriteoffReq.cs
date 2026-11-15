using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 自动核销限定的一张被核销单据（targets[]）：只按表头，不分行。
    internal sealed class AutoWriteoffTarget
    {
        public string Kind;
        public int Id;
    }

    // arap/writeoff/auto 的请求。Cutoff 是单据日期上限（date_to，缺省登录日期）；MaxAmount 为 0 表示不限。
    internal sealed class AutoWriteoffAsk
    {
        public string Flag;
        public string Partner;
        public string Cutoff;
        public string ReceiptKind;
        public int ReceiptId;
        public int ReceiptLine;
        public List<AutoWriteoffTarget> Targets;
        public decimal MaxAmount;
        public bool DryRun;
        // 预收 / 预付行（bPrePay=1）是否参加，缺省 false。
        public bool IncludePrepay;
    }

    // arap/writeoff/auto（自动核销）在登录前的校验（400）。字段 flag（AR / AP，也是登录子系统）、partner（往来单位编码，必填，
    // 不做整个账套的自动核销）、date_to、receipt {type,id[,line_id]}、targets [{type,id}]、max_amount、dry_run、include_prepay。
    // 匹配规则、上限在 ArapAutoWriteoffPlan；处理在 ArapAutoWriteoff。
    internal static class ArapAutoWriteoffReq
    {
        public const string Action = "writeoff_auto";
        public const int MaxTargetFilter = 200;
        const int PartnerMax = 20;
        const decimal AmountMax = 1000000000000m;
        static readonly string[] ReceiptKeys = new string[] { "type", "id", "line_id" };
        static readonly string[] TargetKeys = new string[] { "type", "id" };

        public static AutoWriteoffAsk Parse(Dictionary<string, object> body)
        {
            AutoWriteoffAsk ask = new AutoWriteoffAsk();
            ask.Flag = Requests.Field(body, "flag") as string;
            if (ask.Flag != "AR" && ask.Flag != "AP")
            {
                throw Bad("flag 只能是 AR 或 AP", "flag");
            }
            ask.Partner = Partner(Requests.Field(body, "partner"));
            ask.Cutoff = Cutoff(Requests.Field(body, "date_to"), Requests.Field(body, "date") as string);
            Receipt(Requests.Field(body, "receipt"), ask);
            ask.Targets = Targets(Requests.Field(body, "targets"), ask.Flag);
            ask.MaxAmount = MaxAmount(Requests.Field(body, "max_amount"));
            ask.DryRun = Flag(body, "dry_run");
            ask.IncludePrepay = Flag(body, "include_prepay");
            return ask;
        }

        // 可选布尔，缺省 false；给了就必须是 JSON 布尔值。
        static bool Flag(Dictionary<string, object> body, string name)
        {
            object raw = Requests.Field(body, name);
            if (raw != null && !(raw is bool))
            {
                throw Bad(name + " 必须是布尔值", name);
            }
            return raw != null && (bool)raw;
        }

        static string Partner(object raw)
        {
            string text = raw as string;
            text = text == null ? "" : text.Trim();
            if (text.Length == 0)
            {
                throw Bad("缺少 partner（客户或供应商编码）；自动核销不做整个账套", "partner");
            }
            if (text.Length > PartnerMax || HasControl(text))
            {
                throw Bad("partner 无效", "partner");
            }
            return text;
        }

        static bool HasControl(string text)
        {
            foreach (char c in text)
            {
                if (char.IsControl(c))
                {
                    return true;
                }
            }
            return false;
        }

        // date_to 缺省是登录日期（核销日期）；不能晚于它（U8 不让核销日期早于单据日期）。
        static string Cutoff(object raw, string login)
        {
            if (raw == null)
            {
                return login;
            }
            string text = raw as string;
            DateTime parsed;
            if (text == null || !DateTime.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None,
                out parsed))
            {
                throw Bad("date_to 必须是 yyyy-MM-dd", "date_to");
            }
            if (login != null && string.CompareOrdinal(text, login) > 0)
            {
                throw Bad("date_to 不能晚于核销日期 date", "date_to");
            }
            return text;
        }

        static void Receipt(object raw, AutoWriteoffAsk ask)
        {
            if (raw == null)
            {
                return;
            }
            Dictionary<string, object> map = ArapWriteoffReq.Obj(raw, "receipt");
            ArapWriteoffReq.Only(map, ReceiptKeys, "receipt");
            ask.ReceiptKind = ArapWriteoffReq.Text(map, "type");
            string flag = ArapWriteoffReq.FlagOfReceipt(ask.ReceiptKind);
            if (flag == null)
            {
                throw Bad("receipt.type 只能是 ar_receipt 或 ap_payment", "receipt.type");
            }
            if (flag != ask.Flag)
            {
                throw Bad(ask.Flag == "AR" ? "应收自动核销的 receipt.type 只能是 ar_receipt" : "应付自动核销的 receipt.type 只能是 ap_payment", "receipt.type");
            }
            ask.ReceiptId = ArapWriteoffReq.Id(Requests.Field(map, "id"), "receipt.id", true);
            ask.ReceiptLine = ArapWriteoffReq.Id(Requests.Field(map, "line_id"), "receipt.line_id", false);
        }

        static List<AutoWriteoffTarget> Targets(object raw, string flag)
        {
            List<AutoWriteoffTarget> list = new List<AutoWriteoffTarget>();
            if (raw == null)
            {
                return list;
            }
            IList items = raw as IList;
            if (items == null || items.Count == 0 || items.Count > MaxTargetFilter)
            {
                throw Bad("targets 必须是 1 到 " + MaxTargetFilter.ToString(CultureInfo.InvariantCulture) + " 项", "targets");
            }
            HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (object one in items)
            {
                AutoWriteoffTarget t = TargetAt(one, flag, list.Count);
                if (!seen.Add(t.Kind + ":" + t.Id.ToString(CultureInfo.InvariantCulture)))
                {
                    throw Bad("targets 里有重复的单据", FieldPath.Item("targets", list.Count));
                }
                list.Add(t);
            }
            return list;
        }

        // i 是 targets 里的下标；项内校验的 field 相对该项，这里补上 targets.<下标>。
        static AutoWriteoffTarget TargetAt(object raw, string flag, int i)
        {
            try
            {
                return Target(raw, flag);
            }
            catch (BridgeException ex)
            {
                throw FieldPath.Under(ex, FieldPath.Item("targets", i));
            }
        }

        static AutoWriteoffTarget Target(object raw, string flag)
        {
            Dictionary<string, object> map = ArapWriteoffReq.Obj(raw, "targets[]");
            ArapWriteoffReq.Only(map, TargetKeys, "");
            AutoWriteoffTarget t = new AutoWriteoffTarget();
            t.Kind = ArapWriteoffReq.Text(map, "type");
            string own = ArapWriteoffReq.FlagOfItem(t.Kind);
            if (own == null)
            {
                throw Bad("targets[].type 只能是 sale_invoice、ar_bill、purchase_invoice、ap_bill", "type");
            }
            if (own != flag)
            {
                throw Bad(flag == "AR" ? "应收自动核销只能核销销售发票和应收单" : "应付自动核销只能核销采购发票和应付单", "type");
            }
            t.Id = ArapWriteoffReq.Id(Requests.Field(map, "id"), "targets[].id", true);
            return t;
        }

        // 缺省或 null 为 0（不限）；给了就大于 0、不超过 1000000000000、最多两位小数。
        static decimal MaxAmount(object raw)
        {
            if (raw == null)
            {
                return 0m;
            }
            decimal value;
            if (!ArapWriteoffReq.TryNum(raw, out value) || value <= 0 || value > AmountMax || decimal.Round(value, 2) != value)
            {
                throw Bad("max_amount 必须大于 0、不超过 1000000000000、最多两位小数", "max_amount");
            }
            return value;
        }

        // 锁键只有 "arap:writeoff:<AR|AP>"：要核销哪些单据查库才知道，与同一侧的全部核销、取消核销串行
        // （手工核销、取消核销的锁键里都有这一个）。请求体不合法时（登录前已 400）返回空。
        public static string[] LockKeys(Dictionary<string, object> body)
        {
            try
            {
                return new string[] { "arap:writeoff:" + Parse(body).Flag };
            }
            catch (BridgeException)
            {
                return new string[0];
            }
        }

        static BridgeException Bad(string message)
        {
            return new BridgeException(400, "bad_request", message);
        }

        static BridgeException Bad(string message, string field)
        {
            return new BridgeException(400, "bad_request", message, field);
        }
    }
}
