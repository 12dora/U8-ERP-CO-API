using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    internal sealed class PuOp
    {
        public string Op;
        public int Id;
        public Dictionary<string, string> Fields;
    }

    // 采购订单可写字段。表头 darrivedate 只作为表体计划到货日的缺省，不写进订单头。
    internal static class PuFields
    {
        internal const string HeadExact = "cvencode,cdepcode,cpersoncode,cptcode,cbustype,cexch_name,nflat,itaxrate,dpodate,cmemo,darrivedate";
        internal const string LineExact = "cinvcode,iquantity,iunitprice,itaxprice,ipertaxrate,darrivedate,cbmemo,cunitid,inum";
        const string InvSql = "select iGroupType, cPUComUnitCode, cComUnitCode from Inventory where cInvCode=?";
        const string RateSql = "select iChangRate from ComputationUnit where cComunitCode=?";

        public static Dictionary<string, string> Head(Dictionary<string, object> raw)
        {
            return Map(raw, true, "表头必须是对象");
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
                    list.Add(Map(lines[i] as Dictionary<string, object>, false, "表体行必须是对象"));
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
            return list;
        }

        public static void RequireCreate(Dictionary<string, string> head, List<Dictionary<string, string>> lines)
        {
            if (!Has(head, "cvencode"))
            {
                throw BridgeException.BadField("head.cvencode", "必须填写供应商");
            }
            if (!Has(head, "cdepcode"))
            {
                throw BridgeException.BadField("head.cdepcode", "必须填写部门");
            }
            CheckHead(head);
            if (lines == null || lines.Count < 1)
            {
                throw BridgeException.BadField("lines", "表体行数必须在 1 到 200 之间");
            }
            for (int i = 0; i < lines.Count; i++)
            {
                try
                {
                    CheckLine(lines[i], true);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
        }

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

        public static void CheckLine(Dictionary<string, string> line, bool creating)
        {
            if (line == null)
            {
                throw BridgeException.BadField("lines", "表体行必须是对象");
            }
            if (creating && !Has(line, "cinvcode"))
            {
                throw BridgeException.BadField("lines.cinvcode", "必须填写存货编码");
            }
            if (Contains(line, "cinvcode") && !Has(line, "cinvcode"))
            {
                throw BridgeException.BadField("lines.cinvcode", "必须填写存货编码");
            }
            CheckQty(line, creating);
            CheckPrice(line, creating);
            CheckRate(line, "ipertaxrate", false, "lines");
            CheckOptional(line, "inum", "件数不正确");
        }

        public static void CheckHead(Dictionary<string, string> head)
        {
            if (Contains(head, "cvencode") && !Has(head, "cvencode"))
            {
                throw BridgeException.BadField("head.cvencode", "必须填写供应商");
            }
            if (Contains(head, "cdepcode") && !Has(head, "cdepcode"))
            {
                throw BridgeException.BadField("head.cdepcode", "必须填写部门");
            }
            if (Contains(head, "dpodate") && !Has(head, "dpodate"))
            {
                throw BridgeException.BadField("head.dpodate", "单据日期不正确");
            }
            CheckRate(head, "nflat", true, "head");
            CheckRate(head, "itaxrate", false, "head");
        }

        // 多计量存货补采购单位、换算率和件数。caller 只含调用方传入的字段。无单位组时不能带 cunitid / inum。
        public static void ApplyUnit(object conn, Dictionary<string, string> line, Dictionary<string, string> caller)
        {
            if (line == null)
            {
                return;
            }
            string inv = Get(line, "cinvcode").Trim();
            if (inv.Length == 0)
            {
                return;
            }
            Dictionary<string, object> row = Rows.One(conn, InvSql, new object[] { inv });
            if (row == null)
            {
                return;
            }
            decimal group;
            if (!PuCalc.TryDec(CoRows.Col(row, "iGroupType"), out group) || group == 0m)
            {
                RefusePlain(caller);
                line.Remove("cunitid");
                line.Remove("inum");
                line.Remove("iinvexchrate");
                return;
            }
            string unit = UnitOf(line, row);
            if (unit.Length == 0)
            {
                return;
            }
            line["cunitid"] = unit;
            decimal rate = RateOf(conn, unit, CoRows.Col(row, "cComUnitCode"));
            line["iinvexchrate"] = PuCalc.Qty6(rate);
            if (Contains(caller, "inum") && Has(caller, "inum"))
            {
                return;
            }
            decimal qty;
            if (PuCalc.TryDec(Get(line, "iquantity"), out qty))
            {
                line["inum"] = PuCalc.Qty6(qty / rate);
            }
        }

        public static bool Has(Dictionary<string, string> map, string key)
        {
            return Get(map, key).Trim().Length > 0;
        }

        public static bool Contains(Dictionary<string, string> map, string key)
        {
            return map != null && map.ContainsKey(key);
        }

        static void RefusePlain(Dictionary<string, string> caller)
        {
            if (!Contains(caller, "cunitid") && !Contains(caller, "inum"))
            {
                return;
            }
            string name = Contains(caller, "cunitid") ? "cunitid" : "inum";
            throw BridgeException.BadField("lines." + name, "不能设置字段 " + name);
        }

        static string UnitOf(Dictionary<string, string> line, Dictionary<string, object> row)
        {
            string unit = Get(line, "cunitid").Trim();
            if (unit.Length > 0)
            {
                return unit;
            }
            unit = CoRows.Col(row, "cPUComUnitCode").Trim();
            if (unit.Length == 0)
            {
                unit = CoRows.Col(row, "cComUnitCode").Trim();
            }
            return unit;
        }

        static decimal RateOf(object conn, string unit, string main)
        {
            if (main != null && string.Equals(unit, main.Trim(), StringComparison.OrdinalIgnoreCase))
            {
                return OneRate(conn, unit, true);
            }
            return OneRate(conn, unit, false);
        }

        static decimal OneRate(object conn, string unit, bool sameMain)
        {
            Dictionary<string, object> row = Rows.One(conn, RateSql, new object[] { unit });
            decimal rate;
            if (row != null && PuCalc.TryDec(CoRows.Col(row, "iChangRate"), out rate) && rate > 0m)
            {
                return rate;
            }
            if (sameMain)
            {
                return 1m;
            }
            throw BridgeException.BadField("lines.cunitid", "换算率无效");
        }

        static PuOp OneOp(Dictionary<string, object> map)
        {
            if (map == null)
            {
                throw BridgeException.BadField("lines", "表体行必须是对象");
            }
            string op = Cell(Pick(map, "op"));
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
                item.Fields = Map(map, false, "表体行必须是对象", true);
                CheckLine(item.Fields, true);
                return item;
            }
            item.Id = CoRows.AsId(Pick(map, "line_id"));
            if (item.Id <= 0)
            {
                throw BridgeException.BadField("lines.line_id", "明细行不存在");
            }
            if (op == "delete")
            {
                RejectExtra(map);
                item.Fields = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                return item;
            }
            item.Fields = Map(map, false, "表体行必须是对象", true);
            if (item.Fields.Count == 0)
            {
                throw BridgeException.BadField("lines", "没有要修改的内容");
            }
            CheckLine(item.Fields, false);
            return item;
        }

        static void RejectExtra(Dictionary<string, object> map)
        {
            foreach (KeyValuePair<string, object> pair in map)
            {
                string key = pair.Key == null ? "" : pair.Key.ToLowerInvariant();
                if (key != "op" && key != "line_id")
                {
                    throw BridgeException.BadField(FieldPath.Join("lines", pair.Key), "不能设置字段 " + pair.Key);
                }
            }
        }

        static void CheckQty(Dictionary<string, string> line, bool creating)
        {
            if (creating || Contains(line, "iquantity"))
            {
                decimal qty = PuCalc.Need(Get(line, "iquantity"), "数量不正确");
                if (qty <= 0m)
                {
                    throw BridgeException.BadField("lines.iquantity", "数量必须大于 0");
                }
            }
        }

        static void CheckPrice(Dictionary<string, string> line, bool creating)
        {
            bool tax = Has(line, "itaxprice");
            bool net = Has(line, "iunitprice");
            if (creating && !tax && !net)
            {
                throw BridgeException.BadField("lines.iunitprice", "必须填写单价或含税单价");
            }
            if (Contains(line, "itaxprice") && !tax)
            {
                throw BridgeException.BadField("lines.itaxprice", "单价不正确");
            }
            if (Contains(line, "iunitprice") && !net)
            {
                throw BridgeException.BadField("lines.iunitprice", "单价不正确");
            }
            if (tax)
            {
                NonNeg(Get(line, "itaxprice"));
            }
            if (net)
            {
                NonNeg(Get(line, "iunitprice"));
            }
        }

        static void NonNeg(string text)
        {
            decimal price = PuCalc.Need(text, "单价不正确");
            if (price < 0m)
            {
                throw new BridgeException(400, "bad_request", "单价不正确");
            }
        }

        static void CheckOptional(Dictionary<string, string> line, string key, string message)
        {
            if (!Contains(line, key))
            {
                return;
            }
            decimal number = PuCalc.Need(Get(line, key), message);
            if (number < 0m)
            {
                throw BridgeException.BadField("lines." + key, message);
            }
        }

        static void CheckRate(Dictionary<string, string> map, string key, bool flat, string at)
        {
            if (!Contains(map, key))
            {
                return;
            }
            string message = flat ? "汇率不正确" : "税率不正确";
            decimal number = PuCalc.Need(Get(map, key), message);
            if (flat && number <= 0m)
            {
                throw BridgeException.BadField(FieldPath.Join(at, key), "汇率必须大于 0");
            }
            if (!flat && (number < 0m || number >= 1000m))
            {
                throw BridgeException.BadField(FieldPath.Join(at, key), "税率不正确");
            }
        }

        static Dictionary<string, string> Map(object raw, bool head, string label)
        {
            return Map(raw, head, label, false);
        }

        static Dictionary<string, string> Map(object raw, bool head, string label, bool lineOp)
        {
            Dictionary<string, object> map = raw as Dictionary<string, object>;
            if (map == null)
            {
                throw BridgeException.BadField(FieldPath.Side(head), label);
            }
            Dictionary<string, string> clean = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (KeyValuePair<string, object> pair in map)
            {
                Put(clean, pair.Key, pair.Value, head, lineOp);
            }
            return clean;
        }

        static void Put(Dictionary<string, string> clean, string key, object value, bool head, bool lineOp)
        {
            string lower = key == null ? "" : key.ToLowerInvariant();
            if (lineOp && (lower == "op" || lower == "line_id"))
            {
                return;
            }
            if (!Allowed(lower, head))
            {
                throw BridgeException.BadField(FieldPath.Join(FieldPath.Side(head), key), "不能设置字段 " + key)
                    .WithHint(FieldPath.WritableHint);
            }
            if (clean.ContainsKey(lower))
            {
                throw BridgeException.BadField(FieldPath.Join(FieldPath.Side(head), key), "字段重复 " + key);
            }
            string text = Cell(value);
            if (text != null)
            {
                clean[lower] = text;
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
            string tail = key.Substring(prefix.Length);
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

        static object Pick(Dictionary<string, object> map, string name)
        {
            object value;
            if (map.TryGetValue(name, out value))
            {
                return value;
            }
            foreach (KeyValuePair<string, object> pair in map)
            {
                if (string.Equals(pair.Key, name, StringComparison.OrdinalIgnoreCase))
                {
                    return pair.Value;
                }
            }
            return null;
        }

        internal static string Get(Dictionary<string, string> map, string key)
        {
            if (map == null || key == null)
            {
                return "";
            }
            string text;
            if (map.TryGetValue(key, out text) && text != null)
            {
                return text;
            }
            return "";
        }
    }
}
