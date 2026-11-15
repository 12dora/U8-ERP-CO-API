using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 请购单（PU_AppVouch / PU_AppVouchs，卡片 27）可写字段。名字照 U8 视图 pu_AppHead / pu_AppBody。
    // 表体数量是 fquantity；单价只收原币 ioricost（无税）或 ioritaxcost（含税），本币和金额由桥算（PuAppCalc）。
    internal static class PuAppFields
    {
        internal const string HeadExact = "ddate,cdepcode,cpersoncode,cbustype,cmemo";
        internal const string LineExact = "cinvcode,fquantity,drequirdate,darrivedate,cvencode,ioricost,ioritaxcost,ipertaxrate,cbmemo";
        internal static readonly string[] RequiredLines = new string[] { "cinvcode", "fquantity" };
        internal const string Bus = "普通采购";
        const decimal QtyMax = 1000000000000m;

        public static Dictionary<string, string> Head(Dictionary<string, object> raw)
        {
            Dictionary<string, string> head = Map(raw, true, false, "表头必须是对象");
            CheckHead(head);
            return head;
        }

        public static List<Dictionary<string, string>> CreateLines(object[] lines)
        {
            if (lines == null || lines.Length < 1 || lines.Length > 200)
            {
                throw BridgeException.BadField("lines", "表体行数必须在 1 到 200 之间");
            }
            List<Dictionary<string, string>> list = new List<Dictionary<string, string>>();
            for (int i = 0; i < lines.Length; i++)
            {
                try
                {
                    Dictionary<string, string> line = Map(lines[i], false, false, "表体行必须是对象");
                    CheckLine(line, true);
                    list.Add(line);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
            return list;
        }

        // 修改的行操作：add / update / delete，line_id 是 PU_AppVouchs.AutoID。
        public static List<PuOp> Ops(object[] lines)
        {
            List<PuOp> list = new List<PuOp>();
            if (lines == null)
            {
                return list;
            }
            if (lines.Length > 200)
            {
                throw BridgeException.BadField("lines", "表体行数必须在 1 到 200 之间");
            }
            for (int i = 0; i < lines.Length; i++)
            {
                try
                {
                    list.Add(OneOp(lines[i] as Dictionary<string, object>));
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
            return list;
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

        public static bool Has(Dictionary<string, string> map, string key)
        {
            return Get(map, key).Trim().Length > 0;
        }

        public static string Get(Dictionary<string, string> map, string key)
        {
            string text;
            if (map != null && key != null && map.TryGetValue(key, out text) && text != null)
            {
                return text;
            }
            return "";
        }

        static void CheckHead(Dictionary<string, string> head)
        {
            if (head.ContainsKey("ddate"))
            {
                Date(Get(head, "ddate"), "单据日期不正确", "head.ddate");
            }
            if (head.ContainsKey("cbustype") && Get(head, "cbustype").Trim() != Bus)
            {
                throw BridgeException.BadField("head.cbustype", "请购单只支持普通采购");
            }
            if (head.ContainsKey("cdepcode") && !Has(head, "cdepcode"))
            {
                throw BridgeException.BadField("head.cdepcode", "部门编码不能为空");
            }
        }

        static void CheckLine(Dictionary<string, string> line, bool creating)
        {
            if ((creating || line.ContainsKey("cinvcode")) && !Has(line, "cinvcode"))
            {
                throw BridgeException.BadField("lines.cinvcode", "必须填写存货编码");
            }
            if (creating || line.ContainsKey("fquantity"))
            {
                decimal qty = PuCalc.Need(Get(line, "fquantity"), "数量不正确");
                if (qty <= 0m)
                {
                    throw BridgeException.BadField("lines.fquantity", "数量必须大于 0");
                }
                if (qty > QtyMax)
                {
                    throw BridgeException.BadField("lines.fquantity", "数量超出范围");
                }
            }
            if (line.ContainsKey("drequirdate"))
            {
                Date(Get(line, "drequirdate"), "需求日期不正确", "lines.drequirdate");
            }
            if (line.ContainsKey("darrivedate"))
            {
                Date(Get(line, "darrivedate"), "建议订货日期不正确", "lines.darrivedate");
            }
            CheckPrice(line);
        }

        static void CheckPrice(Dictionary<string, string> line)
        {
            if (line.ContainsKey("ioricost") && line.ContainsKey("ioritaxcost"))
            {
                throw BridgeException.BadField("lines.ioritaxcost", "单价和含税单价只能填一个");
            }
            NonNeg(line, "ioricost", "单价不正确");
            NonNeg(line, "ioritaxcost", "单价不正确");
            if (!line.ContainsKey("ipertaxrate"))
            {
                return;
            }
            decimal rate = PuCalc.Need(Get(line, "ipertaxrate"), "税率不正确");
            if (rate < 0m || rate >= 1000m)
            {
                throw BridgeException.BadField("lines.ipertaxrate", "税率不正确");
            }
        }

        static void NonNeg(Dictionary<string, string> line, string key, string message)
        {
            if (!line.ContainsKey(key))
            {
                return;
            }
            decimal value = PuCalc.Need(Get(line, key), message);
            if (value < 0m)
            {
                throw BridgeException.BadField("lines." + key, message);
            }
        }

        static void Date(string text, string message, string field)
        {
            DateTime at;
            if (!DateTime.TryParseExact((text ?? "").Trim(), "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out at))
            {
                throw BridgeException.BadField(field, message);
            }
        }

        static PuOp OneOp(Dictionary<string, object> map)
        {
            if (map == null)
            {
                throw BridgeException.BadField("lines", "表体行必须是对象");
            }
            string op = Values.Text(Pick(map, "op")).Trim();
            if (op != "add" && op != "update" && op != "delete")
            {
                throw BridgeException.BadField("lines.op", "不支持的行操作");
            }
            PuOp item = new PuOp();
            item.Op = op;
            if (op == "add")
            {
                if (Pick(map, "line_id") != null)
                {
                    throw BridgeException.BadField("lines.line_id", "新增行不能带 line_id");
                }
                item.Fields = Map(map, false, true, "表体行必须是对象");
                CheckLine(item.Fields, true);
                return item;
            }
            item.Id = CoRows.AsId(Pick(map, "line_id"));
            if (item.Id <= 0)
            {
                throw BridgeException.BadField("lines.line_id", "明细行不存在");
            }
            item.Fields = Map(map, false, true, "表体行必须是对象");
            CheckOp(item);
            return item;
        }

        static void CheckOp(PuOp item)
        {
            if (item.Op == "delete")
            {
                if (item.Fields.Count > 0)
                {
                    throw BridgeException.BadField("lines", "删除行只能有 op 和 line_id");
                }
                return;
            }
            if (item.Fields.Count == 0)
            {
                throw BridgeException.BadField("lines", "没有要修改的内容");
            }
            CheckLine(item.Fields, false);
        }

        static Dictionary<string, string> Map(object raw, bool head, bool lineOp, string label)
        {
            Dictionary<string, object> map = raw as Dictionary<string, object>;
            if (map == null)
            {
                throw BridgeException.BadField(FieldPath.Side(head), label);
            }
            Dictionary<string, string> clean = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (KeyValuePair<string, object> pair in map)
            {
                string lower = pair.Key == null ? "" : pair.Key.ToLowerInvariant();
                if (lineOp && (lower == "op" || lower == "line_id" || BlankCode(lower, pair.Value)))
                {
                    continue;
                }
                if (!Allowed(lower, head))
                {
                    throw BridgeException.BadField(FieldPath.Join(FieldPath.Side(head), pair.Key), "不能设置字段 " + pair.Key)
                        .WithHint(FieldPath.WritableHint);
                }
                if (clean.ContainsKey(lower))
                {
                    throw BridgeException.BadField(FieldPath.Join(FieldPath.Side(head), pair.Key), "字段重复 " + pair.Key);
                }
                clean[lower] = Cell(pair.Value);
            }
            return clean;
        }

        // 修改的行操作里空的存货编码当作没传（同采购订单 DropBlankCodes）；新增行随后仍要求存货编码。
        static bool BlankCode(string lower, object value)
        {
            return lower == "cinvcode" && Values.Text(value).Trim().Length == 0;
        }

        static string Cell(object value)
        {
            if (value == null || value is DBNull)
            {
                return "";
            }
            if (value is bool)
            {
                return (bool)value ? "1" : "0";
            }
            IFormattable fmt = value as IFormattable;
            if (fmt != null)
            {
                return fmt.ToString(null, CultureInfo.InvariantCulture);
            }
            return Convert.ToString(value, CultureInfo.InvariantCulture);
        }

        static object Pick(Dictionary<string, object> map, string name)
        {
            foreach (KeyValuePair<string, object> pair in map)
            {
                if (string.Equals(pair.Key, name, StringComparison.OrdinalIgnoreCase))
                {
                    return pair.Value;
                }
            }
            return null;
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
            string tail = key.Substring(prefix.Length);
            int n;
            if (!int.TryParse(tail, NumberStyles.None, CultureInfo.InvariantCulture, out n) || n < from || n > to)
            {
                return false;
            }
            return tail == n.ToString(CultureInfo.InvariantCulture);
        }
    }
}
