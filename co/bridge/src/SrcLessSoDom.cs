using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 无来源发货单、先开票销售发票（SrcLessSa）：同 Apply 把调用方字段写进 GetDefaultVoucherDom 的模板，
    // 名单换成 SrcLessReq，另写桥固定的表头值（模板里没有的列跳过，不新建属性）。
    internal static partial class SoDom
    {
        internal static void ApplyFree(object conn, object[] doms, VoucherKind kind, Dictionary<string, object> head,
            object[] lines, Dictionary<string, string> fixedHead)
        {
            Dictionary<string, string> headFields = FieldMap(doms[0]);
            Dictionary<string, string> bodyFields = FieldMap(doms[1]);
            if (headFields.Count == 0 || bodyFields.Count == 0)
            {
                throw new BridgeException(500, "internal", "模板没有字段");
            }
            Dictionary<string, string> headRow = FreeMap(kind, head, headFields, true);
            foreach (KeyValuePair<string, string> pair in fixedHead)
            {
                string canon;
                if (headFields.TryGetValue(pair.Key, out canon))
                {
                    headRow[canon] = pair.Value;
                }
            }
            List<Dictionary<string, string>> headRows = new List<Dictionary<string, string>>();
            headRows.Add(headRow);
            Fill(doms[0], headRows);
            List<Dictionary<string, string>> body = new List<Dictionary<string, string>>();
            for (int i = 0; i < lines.Length; i++)
            {
                body.Add(FreeMap(kind, lines[i] as Dictionary<string, object>, bodyFields, false));
            }
            SoUnits.Fill(conn, bodyFields, body, headRow);
            Fill(doms[1], body);
        }

        // 名单已在 SrcLessReq.Check 核过；这里只按模板 schema 换成模板的大小写，schema 里没有的字段 400「未知字段」。
        static Dictionary<string, string> FreeMap(VoucherKind kind, Dictionary<string, object> map,
            Dictionary<string, string> fields, bool head)
        {
            Dictionary<string, string> clean = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            if (map == null)
            {
                return clean;
            }
            foreach (KeyValuePair<string, object> pair in map)
            {
                string low = pair.Key == null ? "" : pair.Key.ToLowerInvariant();
                if (head && low == "cvouchtype")
                {
                    continue;
                }
                bool ok = head ? SrcLessReq.HeadAllowed(kind, low) : SrcLessReq.LineAllowed(kind, low);
                if (!ok)
                {
                    throw BridgeException.BadField(FieldPath.Join(FieldPath.Side(head), pair.Key), "不能设置字段 " + pair.Key)
                        .WithHint(FieldPath.WritableHint);
                }
                string canon;
                if (!fields.TryGetValue(pair.Key, out canon))
                {
                    throw BridgeException.BadField(FieldPath.Join(FieldPath.Side(head), pair.Key), "未知字段 " + pair.Key);
                }
                string text = FreeCell(pair.Value);
                if (text != null)
                {
                    clean[canon] = text;
                }
            }
            return clean;
        }

        static string FreeCell(object value)
        {
            if (value == null || value is DBNull)
            {
                return null;
            }
            string text = value as string;
            if (text != null)
            {
                return text;
            }
            if (value is bool)
            {
                return (bool)value ? "1" : "0";
            }
            IFormattable fmt = value as IFormattable;
            if (fmt == null)
            {
                throw new BridgeException(400, "bad_request", "字段值类型不正确");
            }
            return fmt.ToString(null, CultureInfo.InvariantCulture);
        }
    }
}
