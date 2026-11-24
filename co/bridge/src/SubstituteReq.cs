using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 替代料数组的登录前校验（物料清单行的 substitutes、生产订单子件的 substitutes 共用）。字段名不分大小写。
    // 每项：inv_code 必填；sequence（优先级 1 到 10000，缺省 1）、factor（替代系数，大于 0、最多 6 位小数，缺省 1）、
    // replace_flag（布尔，缺省 false）、eff_begin / eff_end（yyyy-MM-dd）可省。同一组里存货不能重复（U8 1011809）。
    // 最多 50 项；空数组表示清空。withDefines：每项另收 defines（define22–define37，类型按 SubstituteRows.Defines）。
    internal static class SubstituteReq
    {
        public const int ItemsMax = 50;
        public const string DefinesKey = "defines";
        static readonly string[] SubKeys = new string[] { "inv_code", "sequence", "factor", "replace_flag", "eff_begin", "eff_end" };
        // 文本型自定义项的最大长度：Define22–25 nvarchar(60)，Define28–33 nvarchar(120)（两张表相同）。
        const int DefineShort = 60;
        const int DefineLong = 120;

        internal static List<BomSub> Parse(object value, string key, bool withDefines)
        {
            IList items = value as IList;
            if (items == null)
            {
                throw BridgeException.BadField(key, key + " 必须是数组");
            }
            if (items.Count > ItemsMax)
            {
                throw BridgeException.BadField(key, key + " 不能超过 " + BomReq.Num(ItemsMax) + " 项");
            }
            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            List<BomSub> subs = new List<BomSub>();
            for (int i = 0; i < items.Count; i++)
            {
                try
                {
                    Dictionary<string, object> item = items[i] as Dictionary<string, object>;
                    if (item == null)
                    {
                        throw new BridgeException(400, "bad_request", key + " 的每一项必须是对象");
                    }
                    BomSub sub = Sub(item, withDefines);
                    if (!seen.Add(sub.InvCode))
                    {
                        throw BridgeException.BadField("inv_code", "替代料存货重复：" + sub.InvCode);
                    }
                    subs.Add(sub);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, key, i);
                }
            }
            return subs;
        }

        static BomSub Sub(Dictionary<string, object> item, bool withDefines)
        {
            List<string> keys = new List<string>(SubKeys);
            if (withDefines)
            {
                keys.Add(DefinesKey);
            }
            BomReq.Only(item, keys.ToArray());
            BomSub sub = new BomSub();
            sub.InvCode = BomReq.Str(item, "inv_code", 60, true);
            object seq = MfgReq.Raw(item, "sequence");
            if (seq != null)
            {
                sub.Seq = BomReq.Int(seq, "sequence", 1, 10000);
            }
            object factor = MfgReq.Raw(item, "factor");
            if (factor != null)
            {
                sub.Factor = BomReq.Qty(factor, "factor");
            }
            object flag = MfgReq.Raw(item, "replace_flag");
            if (flag != null)
            {
                sub.Replace = BomReq.Flag(flag, "replace_flag");
            }
            if (MfgReq.Raw(item, "eff_begin") != null)
            {
                sub.EffBeg = BomReq.Date(item, "eff_begin");
            }
            if (MfgReq.Raw(item, "eff_end") != null)
            {
                sub.EffEnd = BomReq.Date(item, "eff_end");
            }
            object defines = MfgReq.Raw(item, DefinesKey);
            if (withDefines && defines != null)
            {
                sub.Defines = Defines(defines);
            }
            return sub;
        }

        // 生效日期晚于失效日期（两项都有时才比）：U8 BRBas.CheckDateRange。
        internal static bool Reversed(BomSub sub)
        {
            return sub.EffBeg.Length > 0 && sub.EffEnd.Length > 0 && string.CompareOrdinal(sub.EffBeg, sub.EffEnd) > 0;
        }

        // defines 对象：键 define22…define37，值 null 或缺省为空（NULL）。返回列号（"22"…"37"）→ 写库的文本：
        // 文本原样（去首尾空白，空串为 NULL）、float 和 int 为数的文本、日期为 yyyy-MM-dd HH:mm:ss.fff（style 121）。
        static Dictionary<string, string> Defines(object raw)
        {
            Dictionary<string, object> map = raw as Dictionary<string, object>;
            if (map == null)
            {
                throw BridgeException.BadField(DefinesKey, "defines 必须是对象");
            }
            Dictionary<string, string> cells = new Dictionary<string, string>(StringComparer.Ordinal);
            try
            {
                List<string> names = new List<string>();
                for (int i = 0; i < SubstituteRows.Defines.Length; i++)
                {
                    names.Add("define" + SubstituteRows.Defines[i][0]);
                }
                BomReq.Only(map, names.ToArray());
                for (int i = 0; i < SubstituteRows.Defines.Length; i++)
                {
                    string n = SubstituteRows.Defines[i][0];
                    string cell = Define(map, "define" + n, SubstituteRows.Defines[i][1]);
                    if (cell != null)
                    {
                        cells[n] = cell;
                    }
                }
            }
            catch (BridgeException ex)
            {
                throw FieldPath.Under(ex, DefinesKey);
            }
            return cells;
        }

        static string Define(Dictionary<string, object> map, string key, string type)
        {
            object value = MfgReq.Raw(map, key);
            if (value == null)
            {
                return null;
            }
            if (type == "s")
            {
                int max = string.CompareOrdinal(key, "define28") < 0 ? DefineShort : DefineLong;
                string text = BomReq.Str(map, key, max, false);
                return text.Length == 0 ? null : text;
            }
            if (type == "i")
            {
                return BomReq.Int(value, key, int.MinValue, int.MaxValue).ToString(CultureInfo.InvariantCulture);
            }
            if (type == "f")
            {
                return Float(value, key);
            }
            return DateTimeCell(map, key);
        }

        static string Float(object value, string key)
        {
            double d;
            if (value is int || value is long || value is decimal)
            {
                d = Convert.ToDouble(value, CultureInfo.InvariantCulture);
            }
            else if (value is double && !double.IsNaN((double)value) && !double.IsInfinity((double)value))
            {
                d = (double)value;
            }
            else
            {
                throw BridgeException.BadField(key, key + " 必须是数");
            }
            return d.ToString("R", CultureInfo.InvariantCulture);
        }

        // 日期型自定义项：yyyy-MM-dd 或 yyyy-MM-dd HH:mm:ss（读取时给的两种形状）。
        static string DateTimeCell(Dictionary<string, object> map, string key)
        {
            string text = BomReq.Str(map, key, 19, true);
            DateTime parsed;
            string[] formats = new string[] { "yyyy-MM-dd", "yyyy-MM-dd HH:mm:ss" };
            if (!DateTime.TryParseExact(text, formats, CultureInfo.InvariantCulture, DateTimeStyles.None, out parsed))
            {
                throw BridgeException.BadField(key, key + " 必须是 yyyy-MM-dd 或 yyyy-MM-dd HH:mm:ss");
            }
            BomReq.NotBeforeMin(parsed, key);
            return parsed.ToString("yyyy-MM-dd HH:mm:ss.fff", CultureInfo.InvariantCulture);
        }
    }

    // 替代料存货的库内规则（物料清单、生产订单共用，400）：存在、允许做 BOM 子件、未停用、有不带自由项的物料（BomSql.PartOf，
    // 同子件规则）；不能是所在子件本身（U8 1011014）、不能是母件本身（U8 1020017）。at 是消息前缀（「第 n 行」「子件 X（行号 n）」）。
    // parts 是存货 → PartId 的缓存（同一存货只查一次）。返回与 subs 一一对应的 PartId。
    internal static class SubstituteCheck
    {
        internal static List<int> Parts(object conn, List<BomSub> subs, string[] selfAndParent, string today,
            Dictionary<string, int> parts, string at)
        {
            List<int> ids = new List<int>();
            for (int k = 0; k < subs.Count; k++)
            {
                string inv = subs[k].InvCode;
                if (string.Equals(inv, selfAndParent[0], StringComparison.OrdinalIgnoreCase))
                {
                    throw BomSql.Bad(at + "：替代料不能是子件本身");
                }
                if (string.Equals(inv, selfAndParent[1], StringComparison.OrdinalIgnoreCase))
                {
                    throw BomSql.Bad(at + "：替代料不能是母件本身");
                }
                if (!parts.ContainsKey(inv))
                {
                    parts[inv] = PartOf(conn, inv, today, at);
                }
                ids.Add(parts[inv]);
            }
            return ids;
        }

        // 子件规则的 400 前面标上「<at>替代料」。
        static int PartOf(object conn, string inv, string today, string at)
        {
            try
            {
                return BomSql.PartOf(conn, inv, false, today);
            }
            catch (BridgeException ex)
            {
                if (ex.Status != 400)
                {
                    throw;
                }
                throw BomSql.Bad(at + "替代料：" + ex.Message);
            }
        }
    }
}
