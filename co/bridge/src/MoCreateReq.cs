using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 生产订单新增（vouchers/create，type=production_order）的登录前校验。字段名不分大小写。
    // 表头只收 mo_code（省略则 U8 按 MO21 规则自动编号）、remark；「code」在全局禁写名单里，所以单号用 mo_code。
    // 明细 1 到 50 行：inv_code、qty、start_date、due_date、mo_type、dept_code 必填，wh_code、remark 可省。
    // 表头 remark 是各行备注的缺省（mom_order 没有备注列，备注在行上 DRemark）。行号按下标 +1。
    // 销售订单关联（DOrderType / DOrderCode / DOrderSeq）本期不收：订单类型取值没有实测过。
    internal static class MoCreateReq
    {
        public const int LinesMax = 50;
        const decimal QtyMax = 1000000000000m;
        static readonly string[] HeadKeys = new string[] { "mo_code", "remark" };
        static readonly string[] LineKeys = new string[]
        {
            "inv_code", "qty", "start_date", "due_date", "mo_type", "dept_code", "wh_code", "remark"
        };

        // meta 用：表头、表体可写字段和表体必填字段。
        internal static readonly string[] RequiredLine = new string[]
        {
            "inv_code", "qty", "start_date", "due_date", "mo_type", "dept_code"
        };

        internal static string[] HeadNames()
        {
            return (string[])HeadKeys.Clone();
        }

        internal static string[] LineNames()
        {
            return (string[])LineKeys.Clone();
        }

        public static MoCreateAsk Parse(Dictionary<string, object> head, object[] lines)
        {
            MoCreateAsk ask = new MoCreateAsk();
            string remark;
            try
            {
                Only(head, HeadKeys);
                ask.Code = Str(head, "mo_code", 30, false);
                remark = Str(head, "remark", 255, false);
            }
            catch (BridgeException ex)
            {
                throw FieldPath.Under(ex, "head");
            }
            if (lines == null || lines.Length < 1 || lines.Length > LinesMax)
            {
                throw BridgeException.BadField("lines", "lines 必须是 1 到 50 行");
            }
            for (int i = 0; i < lines.Length; i++)
            {
                try
                {
                    ask.Lines.Add(Line(lines[i], i + 1, remark));
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
            return ask;
        }

        static MoLine Line(object raw, int seq, string remark)
        {
            Dictionary<string, object> row = raw as Dictionary<string, object>;
            if (row == null)
            {
                throw new BridgeException(400, "bad_request", "lines 的每一行必须是对象");
            }
            Only(row, LineKeys);
            MoLine line = new MoLine();
            line.Seq = seq;
            line.InvCode = Str(row, "inv_code", 60, true);
            line.Qty = Qty(MfgReq.Raw(row, "qty"));
            line.Start = Date(row, "start_date");
            line.Due = Date(row, "due_date");
            if (string.CompareOrdinal(line.Due, line.Start) < 0)
            {
                throw BridgeException.BadField("due_date", "due_date 不能早于 start_date");
            }
            line.MoType = Str(row, "mo_type", 20, true);
            line.Dept = Str(row, "dept_code", 12, true);
            line.Wh = Str(row, "wh_code", 10, false);
            string own = Str(row, "remark", 255, false);
            line.Remark = own.Length > 0 ? own : remark;
            return line;
        }

        internal static void Only(Dictionary<string, object> map, string[] keys)
        {
            if (map == null)
            {
                return;
            }
            foreach (string key in map.Keys)
            {
                string low = key == null ? "" : key.ToLowerInvariant();
                if (Array.IndexOf(keys, low) < 0)
                {
                    throw BridgeException.BadField(key, "不能设置字段 " + key).WithHint(FieldPath.WritableHint);
                }
            }
        }

        // 编码类字段只收字符串；去掉首尾空白后不能含控制字符。
        internal static string Str(Dictionary<string, object> map, string key, int max, bool required)
        {
            object value = MfgReq.Raw(map, key);
            if (value == null)
            {
                if (required)
                {
                    throw BridgeException.BadField(key, "缺少字段 " + key);
                }
                return "";
            }
            string text = value as string;
            if (text == null)
            {
                throw BridgeException.BadField(key, key + " 必须是字符串");
            }
            text = text.Trim();
            if (required && text.Length == 0)
            {
                throw BridgeException.BadField(key, "缺少字段 " + key);
            }
            if (text.Length > max)
            {
                throw BridgeException.BadField(key, key + " 不能超过 " + max.ToString(CultureInfo.InvariantCulture) + " 个字符");
            }
            for (int i = 0; i < text.Length; i++)
            {
                if (char.IsControl(text[i]))
                {
                    throw BridgeException.BadField(key, key + " 含控制字符");
                }
            }
            return text;
        }

        internal static string Date(Dictionary<string, object> map, string key)
        {
            string text = Str(map, key, 10, true);
            DateTime parsed;
            if (!DateTime.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out parsed))
            {
                throw BridgeException.BadField(key, key + " 必须是 yyyy-MM-dd");
            }
            return text;
        }

        // 数量是大于 0、不超过 1000000000000、最多 6 位小数的 JSON 数（mom_orderdetail.Qty 是 6 位小数）。
        internal static decimal Qty(object value)
        {
            decimal qty;
            if (!TryNum(value, out qty) || qty <= 0m || qty > QtyMax)
            {
                throw BridgeException.BadField("qty", "qty 必须是大于 0 且不超过 1000000000000 的数");
            }
            if (decimal.Round(qty, 6) != qty)
            {
                throw BridgeException.BadField("qty", "qty 最多 6 位小数");
            }
            return qty;
        }

        static bool TryNum(object value, out decimal qty)
        {
            qty = 0m;
            try
            {
                if (value is int || value is long || value is decimal)
                {
                    qty = Convert.ToDecimal(value, CultureInfo.InvariantCulture);
                    return true;
                }
                if (value is double && !double.IsNaN((double)value) && !double.IsInfinity((double)value))
                {
                    qty = (decimal)(double)value;
                    return true;
                }
            }
            catch (OverflowException)
            {
                return false;
            }
            return false;
        }
    }

    internal sealed class MoCreateAsk
    {
        public string Code = "";
        public List<MoLine> Lines = new List<MoLine>();
        // 调用前 mom_order 的最大 MoId 和同一查询取的数据库时间（style 121）：拿不到新 MoId 时按它们找本次新建的订单。
        public int MaxBefore;
        public string Since = "";
        // 账套的存货数量小数位（AccInformation AA iStrsQuanDecDgt），每个请求读一次；读不到按 6。
        public int QtyDigits = 6;
    }

    internal sealed class MoLine
    {
        public int Seq;
        public string InvCode;
        public decimal Qty;
        public string Start;
        public string Due;
        public string MoType;
        public string Dept;
        public string Wh;
        public string Remark;

        public string QtyText
        {
            get { return Qty.ToString("0.######", CultureInfo.InvariantCulture); }
        }
    }
}
