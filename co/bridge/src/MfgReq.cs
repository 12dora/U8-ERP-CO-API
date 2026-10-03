using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 材料出库（参照生产订单）、产成品入库（参照产品检验单或产品不良品处理单）生单的登录前校验。字段名不分大小写。
    internal static class MfgReq
    {
        const decimal QtyMax = 1000000000000m;
        // 参照产品检验单的产成品入库最多行数：合并检验一个来源一行（测试账套每张 2 个来源）；非合并检验登录后再限 1 行。
        internal const int CheckLinesMax = 20;

        public static void CheckGenerate(VoucherKind target, Dictionary<string, object> head, object[] lines)
        {
            CheckGenerate(target, head, lines, "");
        }

        // source 是来源类型名（WorkItem.Source.Name）；产成品入库参照产品检验单时放宽到 CheckLinesMax 行，其余来源 1 行。
        public static void CheckGenerate(VoucherKind target, Dictionary<string, object> head, object[] lines, string source)
        {
            string name = target == null ? "" : target.Name;
            if (name != "material_out" && name != "product_in")
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持生单");
            }
            CheckHead(head);
            NeedRd(head, name == "product_in");
            CheckLines(lines, MaxLines(name, source));
        }

        internal static int MaxLines(string name, string source)
        {
            if (name != "product_in")
            {
                return 200;
            }
            return source == "qm_product_check" ? CheckLinesMax : 1;
        }

        // 材料出库、产成品入库没有 U8 缺省收发类别（各账套编码不同），调用方必须给。存在、末级、方向在 MfgGen.CheckArchives 查。
        internal static string NeedRd(Dictionary<string, object> head, bool inbound)
        {
            string rd = head == null ? "" : Text(head, "crdcode");
            if (rd.Length == 0)
            {
                throw BridgeException.BadField("head.crdcode", "必须指定收发类别")
                    .WithHint(inbound ? "入库用入库类收发类别（末级）" : "出库用出库类收发类别（末级）");
            }
            return rd;
        }

        static void CheckLines(object[] lines, int max)
        {
            if (lines == null || lines.Length < 1 || lines.Length > max)
            {
                throw BridgeException.BadField("lines", LinesMessage(max));
            }
            HashSet<int> seen = new HashSet<int>();
            for (int i = 0; i < lines.Length; i++)
            {
                try
                {
                    Dictionary<string, object> line = LineOf(lines[i], true);
                    if (!seen.Add(LineId(line)))
                    {
                        throw BridgeException.BadField("lines.source_line_id", "来源明细重复");
                    }
                    LineQty(line);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
        }

        static string LinesMessage(int max)
        {
            if (max == 1)
            {
                return "产成品入库只能有 1 行";
            }
            if (max == CheckLinesMax)
            {
                return "参照产品检验单的产成品入库表体须为 1 到 " + CheckLinesMax.ToString(CultureInfo.InvariantCulture) + " 行";
            }
            return "表体须为 1 到 200 行";
        }

        static void CheckHead(Dictionary<string, object> head)
        {
            if (head == null)
            {
                throw BridgeException.BadField("head.cwhcode", "必须指定仓库");
            }
            foreach (KeyValuePair<string, object> kv in head)
            {
                string low = kv.Key == null ? "" : kv.Key.ToLowerInvariant();
                if (!HeadKey(low))
                {
                    throw BridgeException.BadField(FieldPath.Join("head", kv.Key), "不能设置字段 " + kv.Key)
                        .WithHint(FieldPath.WritableHint);
                }
                if (!Plain(kv.Value))
                {
                    throw BridgeException.BadField(FieldPath.Join("head", kv.Key), "字段 " + kv.Key + " 的值只能是字符串、数字或布尔");
                }
            }
            if (Text(head, "cwhcode").Length == 0)
            {
                throw BridgeException.BadField("head.cwhcode", "必须指定仓库");
            }
            string date = Text(head, "ddate");
            if (date.Length > 0 && !IsDate(date))
            {
                throw BridgeException.BadField("head.ddate", "单据日期必须是 yyyy-MM-dd");
            }
        }

        // 表体行：对象，只收 source_line_id、quantity、cbatch、cbmemo。
        internal static Dictionary<string, object> LineOf(object raw)
        {
            return LineOf(raw, false);
        }

        // pos 为 true 时另收货位 cposition（材料出库、产成品入库、参照来料检验单的采购入库生单）。
        internal static Dictionary<string, object> LineOf(object raw, bool pos)
        {
            Dictionary<string, object> line = raw as Dictionary<string, object>;
            if (line == null)
            {
                throw BridgeException.BadField("lines", "表体行不是对象");
            }
            foreach (KeyValuePair<string, object> kv in line)
            {
                string low = kv.Key == null ? "" : kv.Key.ToLowerInvariant();
                if (!LineKey(low, pos))
                {
                    throw BridgeException.BadField(FieldPath.Join("lines", kv.Key), "不能设置字段 " + kv.Key);
                }
                if (!Plain(kv.Value))
                {
                    throw BridgeException.BadField(FieldPath.Join("lines", kv.Key), "字段 " + kv.Key + " 的值只能是字符串、数字或布尔");
                }
            }
            return line;
        }

        static bool LineKey(string low, bool pos)
        {
            if (low == "source_line_id" || low == "quantity" || low == "cbatch" || low == "cbmemo")
            {
                return true;
            }
            return pos && low == "cposition";
        }

        internal static int LineId(Dictionary<string, object> line)
        {
            object value = Raw(line, "source_line_id");
            long number = 0;
            if (value is int)
            {
                number = (int)value;
            }
            else if (value is long)
            {
                number = (long)value;
            }
            else
            {
                throw BridgeException.BadField("lines.source_line_id", "source_line_id 必须是整数");
            }
            if (number < 1 || number > int.MaxValue)
            {
                throw BridgeException.BadField("lines.source_line_id", "source_line_id 无效");
            }
            return (int)number;
        }

        internal static decimal LineQty(Dictionary<string, object> line)
        {
            decimal qty;
            if (!TryQty(Raw(line, "quantity"), out qty) || qty <= 0m || qty > QtyMax)
            {
                throw BridgeException.BadField("lines.quantity", "quantity 必须是大于 0 且不超过 1000000000000 的数");
            }
            // 写入 DOM 按 6 位小数格式化；多出的位数先拒绝，免得校验的和写进去的不是同一个数。
            if (decimal.Round(qty, 6) != qty)
            {
                throw BridgeException.BadField("lines.quantity", "quantity 最多 6 位小数");
            }
            return qty;
        }

        static bool TryQty(object value, out decimal qty)
        {
            qty = 0m;
            try
            {
                if (value is int || value is long || value is decimal)
                {
                    qty = Convert.ToDecimal(value, CultureInfo.InvariantCulture);
                    return true;
                }
                if (value is double)
                {
                    double d = (double)value;
                    if (double.IsNaN(d) || double.IsInfinity(d))
                    {
                        return false;
                    }
                    qty = (decimal)d;
                    return true;
                }
            }
            catch (OverflowException)
            {
                return false;
            }
            return false;
        }

        internal static bool HeadKey(string low)
        {
            if (low == "cwhcode" || low == "crdcode" || low == "ddate" || low == "cmemo"
                || low == "cdepcode" || low == "cpersoncode")
            {
                return true;
            }
            return IsDefine(low);
        }

        // cdefine1 到 cdefine16，拒绝前导零（cdefine01）。
        static bool IsDefine(string low)
        {
            if (!low.StartsWith("cdefine", StringComparison.Ordinal))
            {
                return false;
            }
            string tail = low.Substring(7);
            int n;
            if (tail.Length == 0 || !int.TryParse(tail, NumberStyles.None, CultureInfo.InvariantCulture, out n))
            {
                return false;
            }
            return n >= 1 && n <= 16 && tail == n.ToString(CultureInfo.InvariantCulture);
        }

        static bool IsDate(string text)
        {
            DateTime date;
            return DateTime.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out date);
        }

        static bool Plain(object value)
        {
            if (value is string || value is bool || value is int || value is long)
            {
                return true;
            }
            return value is decimal || value is double;
        }

        internal static object Raw(Dictionary<string, object> map, string name)
        {
            if (map == null)
            {
                return null;
            }
            foreach (KeyValuePair<string, object> kv in map)
            {
                if (string.Equals(kv.Key, name, StringComparison.OrdinalIgnoreCase))
                {
                    return kv.Value;
                }
            }
            return null;
        }

        internal static string Text(Dictionary<string, object> map, string name)
        {
            object value = Raw(map, name);
            if (value is double || value is decimal)
            {
                return Convert.ToString(value, CultureInfo.InvariantCulture).Trim();
            }
            return Values.Text(value).Trim();
        }
    }
}
