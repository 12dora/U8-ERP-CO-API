using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using System.Xml;

namespace U8Co
{
    // 一行期初结存：请求下标、按 storeqc 标签整理好的值（已含单据日期、补算的单价或金额）。
    internal sealed class OpeningEntry
    {
        public int Line;
        public string Wh;
        public string Inv;
        public decimal Qty;
        // 请求给的或桥补算的单价、金额（回读核对用）。
        public decimal Cost;
        public decimal Price;
        public bool HasCost;
        public Dictionary<string, string> Tags = new Dictionary<string, string>(StringComparer.Ordinal);
    }

    // EAI 返回的一个 <item>：succeed="0" 成功，其余为失败，dsc 是 U8 原文。
    internal sealed class OpeningReply
    {
        public string Succeed;
        public string Dsc;

        public bool Ok
        {
            get { return Succeed == "0"; }
        }
    }

    // 库存期初结存的 EAI 报文（U8 官方导入 roottag='storeqc'，经 U8Distribute.iDistribute.ProcessEx）。纯函数，--selftest 覆盖。
    // 每个 <entry> 由 U8 建成一张单据（一张一行）。表头字段发进每个 entry，行上的 cwhcode 覆盖表头。
    // 字段与标签照 U8 的 storeqc 对照表（列名 iinvexchrate 发 irate、imassdate 发 imassDate、crdcode 发 rdcode）；
    // 没有标签的字段（如 ccuscode、cbmemo）拒绝。
    internal static class StockOpeningEai
    {
        internal const string ProgId = "U8Distribute.iDistribute";
        internal const string Method = "ProcessEx";
        const decimal QtyMax = 1000000000000m;

        // 表头：列名 → 标签。ddate 允许送但不用（单据日期由桥固定）。
        static readonly Dictionary<string, string> HeadTags = Map(new string[]
        {
            "cwhcode", "cwhcode", "crdcode", "rdcode", "cdepcode", "cdepcode", "cpersoncode", "cpersoncode",
            "cmemo", "cmemo", "cvencode", "cvencode"
        });

        static readonly Dictionary<string, string> LineTags = Map(new string[]
        {
            "cwhcode", "cwhcode", "cinvcode", "cinvcode", "iquantity", "iquantity", "inum", "inum",
            "iunitcost", "iunitcost", "iprice", "iprice", "cbatch", "cbatch", "cposition", "cposition",
            "dmadedate", "dmadedate", "dvdate", "dvdate", "imassdate", "imassDate", "cmassunit", "cmassunit",
            "cassunit", "cassunit", "iinvexchrate", "irate", "citem_class", "citem_class", "citemcode", "citemcode"
        });

        // 报文里标签的次序，照 U8 对照表；cfree*、cdefine* 在 Ordered 里按编号插入。
        static readonly string[] Order = new string[]
        {
            "cwhcode", "ddate", "cinvcode", "cfree", "iquantity", "cassunit", "irate", "inum", "iunitcost", "iprice",
            "cbatch", "dmadedate", "dvdate", "cposition", "citem_class", "citemcode", "cdefine", "imassDate", "cmassunit", "cmemo",
            "cvencode", "cdepcode", "cpersoncode", "rdcode"
        };

        static Dictionary<string, string> Map(string[] pairs)
        {
            Dictionary<string, string> map = new Dictionary<string, string>(StringComparer.Ordinal);
            for (int i = 0; i + 1 < pairs.Length; i += 2)
            {
                map[pairs[i]] = pairs[i + 1];
            }
            return map;
        }

        // 字段名单（StockDom.Allowed、meta 也用它）：low 为小写列名。
        internal static bool Allowed(bool head, string low)
        {
            return TagOf(head, low) != null || (head && low == "ddate");
        }

        static string TagOf(bool head, string low)
        {
            string tag;
            if ((head ? HeadTags : LineTags).TryGetValue(low ?? "", out tag))
            {
                return tag;
            }
            if (head)
            {
                return Span(low, "cdefine", 1, 16) ? low : null;
            }
            return Span(low, "cfree", 1, 10) || Span(low, "cdefine", 22, 37) ? low : null;
        }

        // 请求的表头、明细 → 每行一个 entry。字段不在名单、缺仓库 / 存货 / 数量、数字不合法都在这里 400。
        internal static List<OpeningEntry> Entries(Dictionary<string, object> head, object[] lines, string day)
        {
            Dictionary<string, string> common = new Dictionary<string, string>(StringComparer.Ordinal);
            Fill(common, head, true, "head");
            List<OpeningEntry> list = new List<OpeningEntry>();
            for (int i = 0; i < lines.Length; i++)
            {
                string at = FieldPath.Item("lines", i);
                OpeningEntry e = new OpeningEntry();
                e.Line = i;
                foreach (KeyValuePair<string, string> kv in common)
                {
                    e.Tags[kv.Key] = kv.Value;
                }
                Fill(e.Tags, lines[i] as Dictionary<string, object>, false, at);
                e.Tags["ddate"] = day;
                Settle(e, at);
                list.Add(e);
            }
            return list;
        }

        static void Fill(Dictionary<string, string> tags, Dictionary<string, object> fields, bool head, string at)
        {
            if (fields == null)
            {
                return;
            }
            foreach (KeyValuePair<string, object> kv in fields)
            {
                string low = kv.Key == null ? "" : kv.Key.ToLowerInvariant();
                if (head && low == "ddate")
                {
                    continue;
                }
                string tag = TagOf(head, low);
                if (tag == null)
                {
                    throw BridgeException.BadField(FieldPath.Join(at, kv.Key), "期初结存单不能设置字段 " + kv.Key)
                        .WithHint(FieldPath.WritableHint);
                }
                string text = StockOpeningValue.Text(tag, kv.Value, FieldPath.Join(at, kv.Key));
                if (text != null)
                {
                    tags[tag] = text;
                }
            }
        }

        // 必填：仓库、存货、数量（大于 0、不超过 1000000000000）；单价、金额只给一个时补另一个（同其他入库单的规则：
        // 金额 = round(数量 × 单价, 2)，单价 = round(金额 / 数量, 6)）。数字已由 StockOpeningValue 规整。
        static void Settle(OpeningEntry e, string at)
        {
            e.Wh = Need(e, "cwhcode", at, "缺少仓库 cwhcode");
            e.Inv = Need(e, "cinvcode", at, "缺少存货编码 cinvcode");
            Need(e, "iquantity", at, "缺少数量 iquantity");
            e.Qty = Number(e, "iquantity");
            if (e.Qty <= 0m || e.Qty > QtyMax)
            {
                throw BridgeException.BadField(FieldPath.Join(at, "iquantity"), "iquantity 必须大于 0 且不超过 1000000000000");
            }
            bool cost = e.Tags.ContainsKey("iunitcost");
            bool price = e.Tags.ContainsKey("iprice");
            if (cost && !price)
            {
                e.Tags["iprice"] = StockOpeningValue.Format(StockUnits.Round2(e.Qty * Number(e, "iunitcost")));
            }
            else if (price && !cost)
            {
                e.Tags["iunitcost"] = StockOpeningValue.Format(StockUnits.Round6(Number(e, "iprice") / e.Qty));
            }
            e.HasCost = cost || price;
            e.Cost = e.HasCost ? Number(e, "iunitcost") : 0m;
            e.Price = e.HasCost ? Number(e, "iprice") : 0m;
        }

        static string Need(OpeningEntry e, string tag, string at, string text)
        {
            string value;
            if (!e.Tags.TryGetValue(tag, out value) || value.Length == 0)
            {
                throw BridgeException.BadField(FieldPath.Join(at, tag), text);
            }
            return value;
        }

        // 数字标签在 Fill 里已校验并规整为不变格式。
        static decimal Number(OpeningEntry e, string tag)
        {
            return decimal.Parse(e.Tags[tag], NumberStyles.Float, CultureInfo.InvariantCulture);
        }

        // 报文：<ufinterface roottag='storeqc' … proc='add'><storeqc><body><entry>…</entry>…</body></storeqc></ufinterface>。
        internal static string Xml(List<OpeningEntry> entries, string docId)
        {
            StringBuilder sb = new StringBuilder();
            sb.Append("<?xml version='1.0' encoding='utf-8'?><ufinterface roottag='storeqc' billtype='' docid='").Append(docId)
                .Append("' receiver='u8' sender='' proc='add' codeexchanged='N' exportneedexch='N' version='2.0'><storeqc><body>");
            for (int i = 0; i < entries.Count; i++)
            {
                sb.Append("<entry>");
                List<string> tags = Ordered(entries[i].Tags);
                for (int j = 0; j < tags.Count; j++)
                {
                    ArcPartnerXml.Tag(sb, tags[j], entries[i].Tags[tags[j]]);
                }
                sb.Append("</entry>");
            }
            sb.Append("</body></storeqc></ufinterface>");
            return sb.ToString();
        }

        static List<string> Ordered(Dictionary<string, string> tags)
        {
            List<string> list = new List<string>();
            for (int i = 0; i < Order.Length; i++)
            {
                if (Order[i] == "cfree" || Order[i] == "cdefine")
                {
                    Numbered(list, tags, Order[i]);
                }
                else if (tags.ContainsKey(Order[i]))
                {
                    list.Add(Order[i]);
                }
            }
            return list;
        }

        static void Numbered(List<string> list, Dictionary<string, string> tags, string prefix)
        {
            for (int n = 1; n <= 37; n++)
            {
                string tag = prefix + n.ToString(CultureInfo.InvariantCulture);
                if (tags.ContainsKey(tag))
                {
                    list.Add(tag);
                }
            }
        }

        // 返回里的 <item>，按 entry 次序；不是 XML 或没有 item 返回 null（结果未知）。
        internal static List<OpeningReply> Parse(string raw)
        {
            if (raw == null || raw.Trim().Length == 0)
            {
                return null;
            }
            XmlDocument doc = new XmlDocument();
            doc.XmlResolver = null;
            try
            {
                doc.LoadXml(raw);
            }
            catch (XmlException)
            {
                return null;
            }
            List<OpeningReply> list = new List<OpeningReply>();
            foreach (XmlNode node in doc.GetElementsByTagName("item"))
            {
                XmlElement el = node as XmlElement;
                OpeningReply r = new OpeningReply();
                r.Succeed = el == null ? "" : el.GetAttribute("succeed").Trim();
                r.Dsc = Short(el == null ? "" : el.GetAttribute("dsc"));
                list.Add(r);
            }
            return list.Count == 0 ? null : list;
        }

        // U8 原文去掉首尾空白，最长 300 字。
        internal static string Short(string text)
        {
            string t = (text ?? "").Trim();
            if (t.Length == 0)
            {
                return "U8 拒绝导入，没有返回原因";
            }
            return t.Length > 300 ? t.Substring(0, 300) : t;
        }

        static bool Span(string low, string prefix, int from, int to)
        {
            if (low == null || !low.StartsWith(prefix, StringComparison.Ordinal))
            {
                return false;
            }
            string tail = low.Substring(prefix.Length);
            int n;
            if (!int.TryParse(tail, NumberStyles.None, CultureInfo.InvariantCulture, out n) || n < from || n > to)
            {
                return false;
            }
            return tail == n.ToString(CultureInfo.InvariantCulture);
        }
    }
}
