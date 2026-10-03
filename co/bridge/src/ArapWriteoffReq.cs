using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 核销请求里的一张被核销单据（发票或应收应付单）。Line 为 0 表示没给 line_id（由桥按唯一有余额的行定）。
    internal sealed class WriteoffAskItem
    {
        public string Kind;
        public int Id;
        public int Line;
        public decimal Amount;
    }

    // arap/writeoff 的请求：一张收付款单的一行，对若干张发票 / 应收应付单核销。
    internal sealed class WriteoffAsk
    {
        public string Flag;
        public string ReceiptKind;
        public int ReceiptId;
        public int ReceiptLine;
        public List<WriteoffAskItem> Items;
    }

    // arap/writeoff 在登录前的校验（400）。核销日期就是登录日期（请求的 date，API 缺省今天）。
    // 应收（AR）：收款单 ar_receipt 对销售发票 sale_invoice、应收单 ar_bill；应付（AP）：付款单 ap_payment 对采购发票
    // purchase_invoice、应付单 ap_bill。登录子系统跟收付款单走（AR / AP）。取消核销是 arap/writeoff/cancel（ArapUnwriteoffReq）。
    internal static class ArapWriteoffReq
    {
        public const string Action = "writeoff";
        public const int MaxItems = 50;
        const decimal AmountMax = 1000000000000m;
        static readonly string[] ReceiptKeys = new string[] { "type", "id", "line_id" };
        static readonly string[] ItemKeys = new string[] { "type", "id", "line_id", "amount" };

        // meta 的 ops.writeoff：参与核销的六种类型。
        public static bool Handles(VoucherKind kind)
        {
            string name = kind == null ? "" : kind.Name;
            return FlagOfReceipt(name) != null || FlagOfItem(name) != null;
        }

        public static string FlagOfReceipt(string kind)
        {
            if (kind == "ar_receipt")
            {
                return "AR";
            }
            return kind == "ap_payment" ? "AP" : null;
        }

        public static string FlagOfItem(string kind)
        {
            if (kind == "sale_invoice" || kind == "ar_bill")
            {
                return "AR";
            }
            return kind == "purchase_invoice" || kind == "ap_bill" ? "AP" : null;
        }

        public static bool IsBill(string kind)
        {
            return kind == "ar_bill" || kind == "ap_bill";
        }

        // 登录前校验，返回登录子系统（AR / AP）。
        public static string Check(Dictionary<string, object> body)
        {
            return Parse(body).Flag;
        }

        public static WriteoffAsk Parse(Dictionary<string, object> body)
        {
            Dictionary<string, object> receipt = Obj(Requests.Field(body, "receipt"), "receipt");
            Only(receipt, ReceiptKeys, "receipt");
            WriteoffAsk ask = new WriteoffAsk();
            ask.ReceiptKind = Text(receipt, "type");
            ask.Flag = FlagOfReceipt(ask.ReceiptKind);
            if (ask.Flag == null)
            {
                throw Bad("receipt.type 只能是 ar_receipt 或 ap_payment", "receipt.type");
            }
            ask.ReceiptId = Id(Requests.Field(receipt, "id"), "receipt.id", true);
            ask.ReceiptLine = Id(Requests.Field(receipt, "line_id"), "receipt.line_id", false);
            ask.Items = Items(Requests.Field(body, "items"), ask.Flag);
            return ask;
        }

        static List<WriteoffAskItem> Items(object raw, string flag)
        {
            IList list = raw as IList;
            if (list == null || list.Count == 0 || list.Count > MaxItems)
            {
                throw Bad("items 必须是 1 到 " + MaxItems.ToString(CultureInfo.InvariantCulture) + " 项", "items");
            }
            List<WriteoffAskItem> items = new List<WriteoffAskItem>();
            HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
            for (int i = 0; i < list.Count; i++)
            {
                WriteoffAskItem item = ItemAt(list[i], flag, i);
                if (!seen.Add(item.Kind + ":" + item.Id.ToString(CultureInfo.InvariantCulture) + ":"
                    + item.Line.ToString(CultureInfo.InvariantCulture)))
                {
                    throw Bad("items 里有重复的单据行", FieldPath.Item("items", i));
                }
                items.Add(item);
            }
            return items;
        }

        static WriteoffAskItem ItemAt(object raw, string flag, int i)
        {
            try
            {
                return Item(raw, flag);
            }
            catch (BridgeException ex)
            {
                throw FieldPath.Under(ex, FieldPath.Item("items", i));
            }
        }

        static WriteoffAskItem Item(object raw, string flag)
        {
            Dictionary<string, object> map = Obj(raw, "items[]");
            Only(map, ItemKeys, "");
            WriteoffAskItem item = new WriteoffAskItem();
            item.Kind = Text(map, "type");
            string own = FlagOfItem(item.Kind);
            if (own == null)
            {
                throw Bad("items[].type 只能是 sale_invoice、ar_bill、purchase_invoice、ap_bill", "type");
            }
            if (own != flag)
            {
                throw Bad(flag == "AR" ? "收款单只能核销销售发票和应收单" : "付款单只能核销采购发票和应付单", "type");
            }
            item.Id = Id(Requests.Field(map, "id"), "items[].id", true);
            item.Line = Id(Requests.Field(map, "line_id"), "items[].line_id", false);
            if (item.Line != 0 && IsBill(item.Kind))
            {
                throw Bad("应收单、应付单按整单核销，不能带 line_id", "line_id");
            }
            item.Amount = Amount(Requests.Field(map, "amount"));
            return item;
        }

        internal static Dictionary<string, object> Obj(object raw, string name)
        {
            Dictionary<string, object> map = raw as Dictionary<string, object>;
            if (map == null)
            {
                throw Bad("缺少 " + name + " 或不是对象", FieldPath.Label(name));
            }
            return map;
        }

        // at：field 前缀；数组元素里传空串，由外层循环补下标。
        internal static void Only(Dictionary<string, object> map, string[] known, string at)
        {
            foreach (string key in map.Keys)
            {
                if (Array.IndexOf(known, key) < 0)
                {
                    throw Bad("含未知字段 " + key, FieldPath.Join(at, key));
                }
            }
        }

        internal static string Text(Dictionary<string, object> map, string key)
        {
            string text = Requests.Field(map, key) as string;
            return text == null ? "" : text;
        }

        // 以下几个解析函数自动核销（ArapAutoWriteoffReq）也用。required=false 时缺省或 JSON null 返回 0。只收严格整数 1 到 2147483647。
        internal static int Id(object raw, string name, bool required)
        {
            if (raw == null && !required)
            {
                return 0;
            }
            long value;
            if (raw is int)
            {
                value = (int)raw;
            }
            else if (raw is long)
            {
                value = (long)raw;
            }
            else
            {
                throw Bad(name + " 必须是整数", FieldPath.Label(name));
            }
            if (value < 1 || value > int.MaxValue)
            {
                throw Bad(name + " 无效", FieldPath.Label(name));
            }
            return (int)value;
        }

        // 金额大于 0、不超过 1000000000000、最多两位小数（原币 = 本币，本轮只做本币核销）。
        static decimal Amount(object raw)
        {
            decimal value;
            if (!TryNum(raw, out value) || value <= 0 || value > AmountMax || decimal.Round(value, 2) != value)
            {
                throw Bad("items[].amount 必须大于 0、不超过 1000000000000、最多两位小数", "amount");
            }
            return value;
        }

        internal static bool TryNum(object raw, out decimal value)
        {
            value = 0;
            try
            {
                if (raw is int || raw is long || raw is decimal)
                {
                    value = Convert.ToDecimal(raw, CultureInfo.InvariantCulture);
                    return true;
                }
                if (raw is double)
                {
                    value = (decimal)(double)raw;
                    return true;
                }
            }
            catch (OverflowException)
            {
                return false;
            }
            return false;
        }

        // 锁键：收付款单、每张被核销单据，另加 "arap:writeoff:<AR|AP>"（往来单位要查库才知道，按应收 / 应付串行）。
        // 请求体不合法时（登录前已 400，不会入队）返回空。
        public static string[] LockKeys(Dictionary<string, object> body)
        {
            WriteoffAsk ask;
            try
            {
                ask = Parse(body);
            }
            catch (BridgeException)
            {
                return new string[0];
            }
            List<string> keys = new List<string>();
            keys.Add(ask.ReceiptKind + ":" + ask.ReceiptId.ToString(CultureInfo.InvariantCulture));
            foreach (WriteoffAskItem item in ask.Items)
            {
                string key = item.Kind + ":" + item.Id.ToString(CultureInfo.InvariantCulture);
                if (!keys.Contains(key))
                {
                    keys.Add(key);
                }
            }
            keys.Add("arap:writeoff:" + ask.Flag);
            return keys.ToArray();
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
