using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    internal static class SoFields
    {
        internal const string HeadExact = "ccuscode,cstcode,cdepcode,cpersoncode,cbustype,cexch_name,iexchrate,itaxrate,"
            + "ddate,cmemo,ccusoaddress,cshipaddress,cscode,cpaycode,dpredatebt,dpremodatebt";
        internal const string LineExact = "cinvcode,iquantity,inum,cunitid,cgroupcode,igrouptype,ccomunitcode,iinvexchrate,"
            + "iquotedprice,iunitprice,itaxunitprice,imoney,itax,isum,inatunitprice,inatmoney,inattax,inatsum,"
            + "inatdiscount,idiscount,kl,kl2,itaxrate,dpredate,dpremodate,cmemo";

        public static List<Dictionary<string, string>> Lines(object[] lines, Dictionary<string, string> fields)
        {
            List<Dictionary<string, string>> list = new List<Dictionary<string, string>>();
            for (int i = 0; i < lines.Length; i++)
            {
                try
                {
                    list.Add(Map(lines[i], fields, false, "表体行必须是对象"));
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
            return list;
        }

        public static Dictionary<string, string> Map(object raw, Dictionary<string, string> fields, bool head, string label)
        {
            Dictionary<string, object> map = raw as Dictionary<string, object>;
            if (map == null)
            {
                throw BridgeException.BadField(FieldPath.Side(head), label);
            }
            Dictionary<string, string> clean = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (KeyValuePair<string, object> pair in map)
            {
                try
                {
                    Put(clean, fields, pair.Key, pair.Value, head);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Under(ex, FieldPath.Join(FieldPath.Side(head), pair.Key));
                }
            }
            return clean;
        }

        static void Put(Dictionary<string, string> clean, Dictionary<string, string> fields, string key, object value, bool head)
        {
            string lower = key == null ? "" : key.ToLowerInvariant();
            if (!Allowed(lower, head))
            {
                throw new BridgeException(400, "bad_request", "不能设置字段 " + key).WithHint(FieldPath.WritableHint);
            }
            string canon;
            if (!fields.TryGetValue(key, out canon))
            {
                throw new BridgeException(400, "bad_request", "未知字段 " + key).WithHint(FieldPath.WritableHint);
            }
            if (clean.ContainsKey(canon))
            {
                throw new BridgeException(400, "bad_request", "字段重复 " + key);
            }
            string text = Cell(value);
            if (text != null)
            {
                clean[canon] = text;
            }
        }

        internal static bool Allowed(string key, bool head)
        {
            if (head)
            {
                return Listed(key, HeadExact) || Span(key, "cdefine", 1, 16);
            }
            if (Listed(key, LineExact) || Span(key, "cfree", 1, 10))
            {
                return true;
            }
            return Span(key, "cdefine", 22, 37);
        }

        static bool Listed(string key, string csv)
        {
            return ("," + csv + ",").IndexOf("," + key + ",", StringComparison.Ordinal) >= 0;
        }

        static bool Span(string key, string prefix, int from, int to)
        {
            if (key.Length <= prefix.Length || !key.StartsWith(prefix, StringComparison.Ordinal))
            {
                return false;
            }
            int n;
            if (!int.TryParse(key.Substring(prefix.Length), NumberStyles.None, CultureInfo.InvariantCulture, out n))
            {
                return false;
            }
            return n >= from && n <= to;
        }

        static string Cell(object value)
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
