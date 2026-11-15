using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 采购结算（vouchers/generate，type=purchase_settle，source_type=purchase_invoice，id 是 PBVID）的请求规则。
    // 登录前在 Requests.FillGenerate 里调用：按整张发票自动结算，不收 lines（v1 不支持按行结算，带了 400）；
    // 表头只收 settle_date（yyyy-MM-dd），给了就替换本次请求的 U8 登录日期 date（结算日期就是 U8 登录日期）。
    // 写规则键见 PermRegistryPuSettle；预演为 rollback（DryRunModes）。
    internal static class PuSettleReq
    {
        internal const string DateKey = "settle_date";
        internal const string GenerateRule = "write:purchase_settle:generate";
        internal const string DeleteRule = "write:purchase_settle:delete";
        // U8 采购选项「结算单最多载入行数」（PU.iSettleLoadRowCount）的出厂值。
        internal const int LinesMax = 400;

        internal static bool Handles(VoucherKind kind)
        {
            return kind != null && kind.Name == PuSettleRead.KindName;
        }

        // 不带 lines 即整单（Requests.WholeGenerate）；带了 lines（哪怕 source_line_id）一律 400。
        internal static void Apply(WorkItem item)
        {
            if (item == null || !Handles(item.Type))
            {
                return;
            }
            if (item.Lines != null && item.Lines.Length > 0)
            {
                throw BridgeException.BadField("lines", "采购结算按整张发票结算，不能指定 lines");
            }
            string date = SettleDate(item.Head);
            if (date.Length > 0)
            {
                CheckDay(date, DateTime.Today);
                item.Date = date;
            }
        }

        // 结算日期不能晚于今天。不与请求的 year 比：year 是账套库年度（未建新年度库时，后续年度的业务仍记在旧年度库里）。
        internal static void CheckDay(string date, DateTime today)
        {
            const string At = "head." + DateKey;
            if (string.CompareOrdinal(date, today.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture)) > 0)
            {
                throw BridgeException.BadField(At, "settle_date 不能晚于今天");
            }
        }

        // 表头只能有 settle_date（不分大小写），值是 yyyy-MM-dd 字符串。没有时返回空串。
        internal static string SettleDate(Dictionary<string, object> head)
        {
            string found = "";
            if (head == null)
            {
                return found;
            }
            foreach (KeyValuePair<string, object> pair in head)
            {
                string at = FieldPath.Join("head", pair.Key);
                if (!HeadKey((pair.Key ?? "").ToLowerInvariant()))
                {
                    throw BridgeException.BadField(at, "不能设置字段 " + pair.Key);
                }
                if (found.Length > 0)
                {
                    throw BridgeException.BadField(at, "settle_date 重复");
                }
                string text = pair.Value as string;
                if (!IsDay(text))
                {
                    throw BridgeException.BadField(at, "settle_date 必须是 yyyy-MM-dd");
                }
                found = text;
            }
            return found;
        }

        internal static bool IsDay(string text)
        {
            DateTime day;
            return text != null && text.Length == 10 && DateTime.TryParseExact(text, "yyyy-MM-dd",
                CultureInfo.InvariantCulture, DateTimeStyles.None, out day);
        }

        internal static bool HeadKey(string low)
        {
            return low == DateKey;
        }

        // MetaFields 的字段全集。
        internal static string[] MetaNames()
        {
            return new string[] { DateKey };
        }

        // meta 的 writable.generate.purchase_invoice：表头只收 settle_date（可省），没有表体、没有定位列。
        internal static Dictionary<string, object> Meta()
        {
            Dictionary<string, object> spec = MetaWritable.Spec(HeadKey, null, 0, 0, new string[0]);
            spec["required"] = MetaWritable.Required(new string[0], new string[0]);
            return spec;
        }
    }

    internal static partial class Requests
    {
        // 不带 lines 表示整单生成：销售出库按发货单、红字销售发票按退货单或蓝字发票（SaleGen.FromReturn / FromBlue 看 item.Source）、
        // 采购结算按整张发票（带了 lines 由 PuSettleReq.Apply 拒绝）。从 Requests.cs 移来（文件行数上限）。
        static bool WholeGenerate(Dictionary<string, object> body, WorkItem item)
        {
            if (item.Type == null || (body != null && body.ContainsKey("lines")))
            {
                return false;
            }
            // 采购入库参照到货单不带 lines 时按各行剩余整单生成（StockGenArr）。
            return item.Type.Name == "sale_out"
                || (item.Type.Name == "sale_invoice" && (SaleGen.FromReturn(item) || SaleGen.FromBlue(item)))
                || PuSettleReq.Handles(item.Type) || StockGen.FromArrival(item);
        }
    }
}
