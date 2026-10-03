using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 字段白名单、单元格文本和视图名。
    internal static partial class StockDom
    {

        static bool Allowed(VoucherKind kind, bool head, string name)
        {
            string low = name == null ? "" : name.ToLowerInvariant();
            // 调拨单，以及形态转换单、调拨申请单、盘点单（StockMiscDom.cs）。
            bool? special = SpecialField(kind, head, low);
            if (special.HasValue)
            {
                return special.Value;
            }
            if (PurType(kind))
            {
                return PurField(head, low);
            }
            // 期初结存单（34）：EAI storeqc 有标签的字段（StockOpeningEai），meta 也按它算。
            if (StockOpening.Handles(kind))
            {
                return StockOpeningEai.Allowed(head, low);
            }
            if (kind == null || !OtherType(kind))
            {
                return false;
            }
            if (head)
            {
                return kind.StType == "32" ? SaleOutHeadField(low) : HeadField(low);
            }
            return BodyField(low);
        }

        static readonly HashSet<string> TransferHeadNames = new HashSet<string>(StringComparer.Ordinal)
        {
            "cowhcode", "ciwhcode", "cordcode", "cirdcode", "codepcode", "cidepcode",
            "dtvdate", "cmemo", "cpersoncode"
        };

        static readonly HashSet<string> TransferBodyNames = new HashSet<string>(StringComparer.Ordinal)
        {
            "cinvcode", "itvquantity", "cassunit", "cbatch", "cbmemo"
        };

        static readonly HashSet<string> HeadNames = new HashSet<string>(StringComparer.Ordinal)
        {
            "cwhcode", "crdcode", "cdepcode", "cpersoncode", "ddate", "cmemo",
            "cvencode", "ccuscode", "citemcode"
        };

        // 无来源销售出库单（StockSaleOut）：表头必须有客户、部门；没有供应商、项目。表体同其他出库。
        static readonly HashSet<string> SaleOutHeadNames = new HashSet<string>(StringComparer.Ordinal)
        {
            "cwhcode", "crdcode", "cdepcode", "cpersoncode", "ddate", "cmemo", "ccuscode", "cstcode"
        };

        static readonly HashSet<string> BodyNames = new HashSet<string>(StringComparer.Ordinal)
        {
            "cinvcode", "iquantity", "inum", "iunitcost", "iprice", "cbatch", "cposition", "cbmemo",
            "dmadedate", "dvdate", "imassdate", "cmassunit", "cassunit", "iinvexchrate"
        };

        static bool TransferHeadField(string low)
        {
            if (TransferHeadNames.Contains(low))
            {
                return true;
            }
            return Span(low, "cdefine", 1, 16);
        }

        static bool TransferBodyField(string low)
        {
            if (TransferBodyNames.Contains(low))
            {
                return true;
            }
            if (Span(low, "cfree", 1, 10))
            {
                return true;
            }
            return Span(low, "cdefine", 22, 37);
        }

        static bool HeadField(string low)
        {
            if (HeadNames.Contains(low))
            {
                return true;
            }
            return Span(low, "cdefine", 1, 16);
        }

        static bool SaleOutHeadField(string low)
        {
            if (SaleOutHeadNames.Contains(low))
            {
                return true;
            }
            return Span(low, "cdefine", 1, 16);
        }

        static bool BodyField(string low)
        {
            if (BodyNames.Contains(low))
            {
                return true;
            }
            if (Span(low, "cfree", 1, 10))
            {
                return true;
            }
            return Span(low, "cdefine", 22, 37);
        }

        static bool Span(string low, string prefix, int from, int to)
        {
            if (!low.StartsWith(prefix, StringComparison.Ordinal))
            {
                return false;
            }
            string tail = low.Substring(prefix.Length);
            int n;
            if (tail.Length == 0 || !int.TryParse(tail, NumberStyles.None, CultureInfo.InvariantCulture, out n))
            {
                return false;
            }
            if (n < from || n > to)
            {
                return false;
            }
            return tail == n.ToString(CultureInfo.InvariantCulture);
        }

        static string Cell(string key, object value, string at)
        {
            if (EmptyCell(value) || BlankCode(key, value))
            {
                return null;
            }
            string text = CellText(value);
            if (text == null)
            {
                throw BridgeException.BadField(FieldPath.Join(at, key), "字段 " + key + " 只能是字符串、数字或布尔");
            }
            return text;
        }

        // 空的存货或辅计量当作没传。
        static bool BlankCode(string key, object value)
        {
            string low = key == null ? "" : key.ToLowerInvariant();
            if (low != "cinvcode" && low != "cassunit")
            {
                return false;
            }
            string text = value as string;
            if (text == null)
            {
                return false;
            }
            return text.Trim().Length == 0;
        }

        static bool EmptyCell(object value)
        {
            return value == null || value is DBNull;
        }

        static string CellText(object value)
        {
            if (value is string)
            {
                return (string)value;
            }
            if (value is bool)
            {
                return ((bool)value) ? "1" : "0";
            }
            if (!IsNumber(value))
            {
                return null;
            }
            return Convert.ToString(value, CultureInfo.InvariantCulture);
        }

        static bool IsWhole(object value)
        {
            return value is byte || value is short || value is int || value is long;
        }

        static bool IsReal(object value)
        {
            return value is decimal || value is double || value is float;
        }

        static bool IsNumber(object value)
        {
            return IsWhole(value) || IsReal(value);
        }

        static readonly Dictionary<string, string> HeadViews = new Dictionary<string, string>(StringComparer.Ordinal)
        {
            { "01", "zpurrkdhead" },
            { "08", "KCOtherInH" },
            { "09", "KCOtherOutH" },
            { "10", "RecordInQ" },
            { "11", "RecordOutQ" },
            { "32", "kcsaleouth" },
            { "12", "transm" },
            { "15", "AssemM" },
            { "62", "transrequestm" },
            { "18", "checkm" },
            // 货位调整单（视图内连仓库、存货和两个货位档案）。
            { "19", "AdjustPM" },
            { "34", "KCQCHead" }
        };

        static readonly Dictionary<string, string> BodyViews = new Dictionary<string, string>(StringComparer.Ordinal)
        {
            { "01", "zpurrkdtail" },
            { "08", "KCOtherInB" },
            { "09", "KCOtherOutB" },
            { "10", "RecordInSQ" },
            { "11", "RecordOutSQ" },
            { "32", "kcsaleoutb" },
            { "12", "TransD" },
            { "15", "AssemD" },
            { "62", "transrequestd" },
            { "18", "checkd" },
            { "19", "AdjustPD" },
            { "34", "KCQCBody" }
        };

        public static string ViewName(string st, bool head)
        {
            if (st == null)
            {
                return null;
            }
            string name;
            Dictionary<string, string> map = head ? HeadViews : BodyViews;
            if (map.TryGetValue(st, out name))
            {
                return name;
            }
            return null;
        }

    }
}
