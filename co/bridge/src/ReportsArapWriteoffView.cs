using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 一个核销号的展示：收付款单（第一行所在的单据）、按（对方单据类型、单号、行）合计的被核销单据、合计金额。
    // 收付款单自身的冲减行（对方单据 = 收付款单自己）只用来找涉及的收付款单行（iCoClosesID）。金额是原币：
    // 应收取 贷-借、应付取 借-贷（同取消核销的 UnwriteoffTarget.Amount），即被核销单据一侧的 iCAmount_f / iDAmount_f。
    internal sealed class WriteoffBatchView
    {
        public readonly string CancelNo;
        public readonly List<UnwriteoffRow> Rows;
        public readonly int ReceiptId;
        readonly WriteoffListScope _scope;
        readonly WriteoffPageData _data;

        public WriteoffBatchView(WriteoffListScope scope, WriteoffPageData data, string cancelNo, List<UnwriteoffRow> rows)
        {
            _scope = scope;
            _data = data;
            CancelNo = cancelNo;
            Rows = rows;
            ReceiptId = data.ReceiptId(rows[0].VType, rows[0].VCode);
        }

        // 往来明细上的收付款单类型 → 类型名：应收 48 收款单、49 客户退款（付款单），应付 49 付款单、48 供应商退款（收款单）。
        static string ReceiptKind(string flag, string vtype)
        {
            if (vtype == "48")
            {
                return flag == "AR" ? "ar_receipt" : (flag == "AP" ? "ap_refund" : null);
            }
            if (vtype == "49")
            {
                return flag == "AP" ? "ap_payment" : (flag == "AR" ? "ar_refund" : null);
            }
            return null;
        }

        static bool IsReceipt(string kind)
        {
            return kind == "ar_receipt" || kind == "ap_payment" || kind == "ar_refund" || kind == "ap_refund";
        }

        string KindOf(string vtype)
        {
            WriteoffKind kind = WriteoffKind.OfType(_scope.Flag, vtype);
            return kind != null ? kind.Name : ReceiptKind(_scope.Flag, vtype);
        }

        public Dictionary<string, object> Describe(Dictionary<string, object> firstRaw)
        {
            UnwriteoffRow first = Rows[0];
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["cancel_no"] = CancelNo;
            item["flag"] = _scope.Flag;
            item["date"] = first.RegDate.Length > 0 ? first.RegDate : null;
            item["year"] = YearOf(first.RegDate);
            item["period"] = first.Period > 0 ? (object)first.Period : null;
            item["partner"] = first.Dw.Length > 0 ? first.Dw : null;
            item["currency"] = first.Cur.Length > 0 ? first.Cur : null;
            item["operator"] = Reports.Text(firstRaw, "op");
            List<int> lines = new List<int>();
            decimal total;
            item["targets"] = Targets(first, lines, out total);
            item["amount"] = total;
            item["receipt"] = Receipt(first, lines);
            return item;
        }

        // 年度按登记日期在 UA_Period 上所在的会计年度（同取消核销的期间判断），找不到按日期的年份。
        object YearOf(string date)
        {
            if (date.Length == 0)
            {
                return null;
            }
            int[] found = _scope.PeriodOf(date);
            if (found != null)
            {
                return found[0];
            }
            DateTime day;
            if (DateTime.TryParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out day))
            {
                return day.Year;
            }
            return null;
        }

        Dictionary<string, object> Receipt(UnwriteoffRow first, List<int> lines)
        {
            Dictionary<string, object> receipt = new Dictionary<string, object>();
            receipt["type"] = ReceiptKind(_scope.Flag, first.VType);
            receipt["vouch_type"] = first.VType;
            receipt["id"] = ReceiptId > 0 ? (object)ReceiptId : null;
            receipt["line_id"] = lines.Count == 1 ? (object)lines[0] : null;
            receipt["line_ids"] = lines;
            receipt["code"] = first.VCode;
            return receipt;
        }

        List<object> Targets(UnwriteoffRow first, List<int> lines, out decimal total)
        {
            total = 0m;
            List<object> list = new List<object>();
            Dictionary<string, Dictionary<string, object>> byKey =
                new Dictionary<string, Dictionary<string, object>>(StringComparer.Ordinal);
            foreach (UnwriteoffRow row in Rows)
            {
                if (row.CoType == first.VType && row.CoCode == first.VCode)
                {
                    if (row.CoClose > 0 && !lines.Contains(row.CoClose))
                    {
                        lines.Add(row.CoClose);
                    }
                    continue;
                }
                decimal amount = _scope.Flag == "AP" ? row.DF - row.CF : row.CF - row.DF;
                total += amount;
                Dictionary<string, object> target;
                string key = WriteoffSql.AmountKey(row.CoType, row.CoCode, row.BVid);
                if (!byKey.TryGetValue(key, out target))
                {
                    target = Target(row);
                    byKey[key] = target;
                    list.Add(target);
                }
                target["amount"] = (decimal)target["amount"] + amount;
            }
            return list;
        }

        Dictionary<string, object> Target(UnwriteoffRow row)
        {
            string kind = KindOf(row.CoType);
            int id = 0;
            if (kind != null && IsReceipt(kind))
            {
                // 收付款单（含退款）都在 Ap_CloseBill：按类型、单号和 AR / AP 查主键。
                id = _data.ReceiptId(row.CoType, row.CoCode);
            }
            else if (kind != null)
            {
                id = CoRows.AsId(CoRows.Col(_data.Head(kind, row.CoType, row.CoCode), "id"));
            }
            Dictionary<string, object> target = new Dictionary<string, object>();
            target["type"] = kind;
            target["vouch_type"] = row.CoType;
            target["id"] = id > 0 ? (object)id : null;
            target["line_id"] = row.BVid > 0 ? (object)row.BVid : null;
            target["code"] = row.CoCode;
            target["amount"] = 0m;
            return target;
        }
    }
}
