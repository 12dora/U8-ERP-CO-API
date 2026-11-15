using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 物料清单（vouchers/create、vouchers/update，type=bom）的登录前校验。字段名不分大小写，存进 BomChange 时转小写。
    // 新增：表头 inv_code 必填（母件），version、version_desc、eff_date、parent_scrap 可省；明细 1 到 200 行，
    // inv_code、base_qty_n 必填，base_qty_d（缺省 1）、comp_scrap（0）、wip_type（3 领用）、wh_code、remark、
    // op_seq（"0000"）、sort_seq（按 10、20… 往后排）可省。
    // 修改：表头只收 version_desc、eff_date、parent_scrap；明细按 sort_seq 定位：update 至少再改一个字段、不能改存货，
    // delete 只能有 op 和 sort_seq，add 同新增的行。
    internal static class BomReq
    {
        public const int LinesMax = 200;
        const decimal QtyMax = 1000000000000m;
        const int SeqMax = 99999;
        static readonly string[] CreateHead = new string[]
        {
            "inv_code", "version", "version_desc", "eff_date", "parent_scrap"
        };
        static readonly string[] EditHead = new string[] { "version_desc", "eff_date", "parent_scrap" };
        static readonly string[] AddKeys = new string[]
        {
            "sort_seq", "op_seq", "inv_code", "base_qty_n", "base_qty_d", "comp_scrap", "wip_type", "wh_code", "remark"
        };
        static readonly string[] EditKeys = new string[]
        {
            "op_seq", "base_qty_n", "base_qty_d", "comp_scrap", "wip_type", "wh_code", "remark"
        };

        // meta 用：新增表头、明细名单，修改表头名单，新增必填。
        internal static readonly string[] RequiredHead = new string[] { "inv_code" };
        internal static readonly string[] RequiredLine = new string[] { "inv_code", "base_qty_n" };

        internal static string[] CreateHeadNames()
        {
            return (string[])CreateHead.Clone();
        }

        internal static string[] EditHeadNames()
        {
            return (string[])EditHead.Clone();
        }

        internal static string[] LineNames()
        {
            return (string[])AddKeys.Clone();
        }

        public static BomAsk ParseCreate(Dictionary<string, object> head, object[] lines)
        {
            BomAsk ask = new BomAsk();
            try
            {
                Only(head, CreateHead);
                ask.InvCode = Str(head, "inv_code", 60, true);
                object version = MfgReq.Raw(head, "version");
                if (version != null)
                {
                    ask.Version = Int(version, "version", 1, int.MaxValue);
                }
                ReadHead(ask, head);
            }
            catch (BridgeException ex)
            {
                throw FieldPath.Under(ex, "head");
            }
            if (lines == null || lines.Length < 1 || lines.Length > LinesMax)
            {
                throw BridgeException.BadField("lines", "lines 必须是 1 到 200 行");
            }
            HashSet<int> seqs = new HashSet<int>();
            for (int i = 0; i < lines.Length; i++)
            {
                try
                {
                    ask.Changes.Add(AddLine(Row(lines[i]), seqs));
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
            return ask;
        }

        public static BomAsk ParseUpdate(Dictionary<string, object> head, object[] lines)
        {
            BomAsk ask = new BomAsk();
            try
            {
                Only(head, EditHead);
                ReadHead(ask, head);
            }
            catch (BridgeException ex)
            {
                throw FieldPath.Under(ex, "head");
            }
            object[] rows = lines ?? new object[0];
            if (rows.Length > LinesMax)
            {
                throw BridgeException.BadField("lines", "lines 不能超过 200 行");
            }
            HashSet<int> keyed = new HashSet<int>();
            HashSet<int> added = new HashSet<int>();
            for (int i = 0; i < rows.Length; i++)
            {
                try
                {
                    ask.Changes.Add(EditLine(Row(rows[i]), keyed, added));
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
            if (ask.Head.Count == 0 && ask.Changes.Count == 0)
            {
                throw new BridgeException(400, "bad_request", "没有要修改的内容");
            }
            return ask;
        }

        static void ReadHead(BomAsk ask, Dictionary<string, object> head)
        {
            if (MfgReq.Raw(head, "version_desc") != null)
            {
                ask.Head["version_desc"] = Str(head, "version_desc", 255, false);
            }
            if (MfgReq.Raw(head, "eff_date") != null)
            {
                ask.Head["eff_date"] = Date(head, "eff_date");
            }
            object scrap = MfgReq.Raw(head, "parent_scrap");
            if (scrap != null)
            {
                ask.Head["parent_scrap"] = Rate(scrap, "parent_scrap");
            }
        }

        static BomChange EditLine(Dictionary<string, object> row, HashSet<int> keyed, HashSet<int> added)
        {
            string op = MfgReq.Raw(row, "op") as string;
            Dictionary<string, object> rest = Without(row, "op");
            if (op == "add")
            {
                return AddLine(rest, added);
            }
            if (op != "update" && op != "delete")
            {
                throw BridgeException.BadField("op", "op 只能是 add、update 或 delete");
            }
            BomChange change = new BomChange();
            change.Op = op;
            object seq = MfgReq.Raw(rest, "sort_seq");
            if (seq == null)
            {
                throw BridgeException.BadField("sort_seq", "修改和删除行必须带 sort_seq");
            }
            change.Seq = Int(seq, "sort_seq", 1, SeqMax);
            if (!keyed.Add(change.Seq))
            {
                throw BridgeException.BadField("sort_seq", "明细行重复：sort_seq " + Num(change.Seq));
            }
            Dictionary<string, object> fields = Without(rest, "sort_seq");
            if (op == "delete" && fields.Count > 0)
            {
                throw new BridgeException(400, "bad_request", "删除行只能带 op 和 sort_seq");
            }
            if (op == "update")
            {
                EditFields(change, fields);
            }
            return change;
        }

        static void EditFields(BomChange change, Dictionary<string, object> fields)
        {
            if (MfgReq.Raw(fields, "inv_code") != null)
            {
                throw BridgeException.BadField("inv_code", "不能修改存货编码，请删除该行后新增");
            }
            Only(fields, EditKeys);
            if (fields.Count == 0)
            {
                throw new BridgeException(400, "bad_request", "修改行至少要改一个字段");
            }
            ReadFields(change, fields);
            // 接口不清空仓库和备注：U8 修改时空值的处理没有实测过。
            if (Blank(change, "wh_code") || Blank(change, "remark"))
            {
                throw BridgeException.BadField(Blank(change, "wh_code") ? "wh_code" : "remark", "修改行不能把 wh_code、remark 改成空");
            }
        }

        static bool Blank(BomChange change, string key)
        {
            object value;
            return change.Fields.TryGetValue(key, out value) && ((string)value).Length == 0;
        }

        static BomChange AddLine(Dictionary<string, object> row, HashSet<int> seqs)
        {
            Only(row, AddKeys);
            BomChange change = new BomChange();
            Str(row, "inv_code", 60, true);
            if (MfgReq.Raw(row, "base_qty_n") == null)
            {
                throw BridgeException.BadField("base_qty_n", "缺少字段 base_qty_n");
            }
            object seq = MfgReq.Raw(row, "sort_seq");
            if (seq != null)
            {
                change.Seq = Int(seq, "sort_seq", 1, SeqMax);
                if (!seqs.Add(change.Seq))
                {
                    throw BridgeException.BadField("sort_seq", "sort_seq 重复：" + Num(change.Seq));
                }
            }
            ReadFields(change, Without(row, "sort_seq"));
            return change;
        }

        static void ReadFields(BomChange change, Dictionary<string, object> row)
        {
            foreach (KeyValuePair<string, object> kv in row)
            {
                string key = kv.Key.ToLowerInvariant();
                change.Fields[key] = Field(row, key, kv.Value);
            }
        }

        static object Field(Dictionary<string, object> row, string key, object value)
        {
            switch (key)
            {
                case "inv_code":
                    return Str(row, key, 60, true);
                case "op_seq":
                    return Str(row, key, 4, true);
                case "wh_code":
                    return Str(row, key, 10, false);
                case "remark":
                    return Str(row, key, 255, false);
                case "wip_type":
                    return Int(value, key, 1, 5);
                case "comp_scrap":
                    return Rate(value, key);
            }
            return Qty(value, key);
        }

        static Dictionary<string, object> Row(object raw)
        {
            Dictionary<string, object> row = raw as Dictionary<string, object>;
            if (row == null)
            {
                throw new BridgeException(400, "bad_request", "lines 的每一行必须是对象");
            }
            return row;
        }

        static Dictionary<string, object> Without(Dictionary<string, object> row, string skip)
        {
            Dictionary<string, object> rest = new Dictionary<string, object>();
            foreach (KeyValuePair<string, object> kv in row)
            {
                if (!string.Equals(kv.Key, skip, StringComparison.OrdinalIgnoreCase))
                {
                    rest[kv.Key] = kv.Value;
                }
            }
            return rest;
        }

        static void Only(Dictionary<string, object> map, string[] keys)
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

        // 编码和文本只收字符串；去掉首尾空白后不能含控制字符。
        static string Str(Dictionary<string, object> map, string key, int max, bool required)
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
                throw BridgeException.BadField(key, key + " 不能超过 " + Num(max) + " 个字符");
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

        static string Date(Dictionary<string, object> map, string key)
        {
            string text = Str(map, key, 10, true);
            DateTime parsed;
            if (!DateTime.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out parsed))
            {
                throw BridgeException.BadField(key, key + " 必须是 yyyy-MM-dd");
            }
            return text;
        }

        // JSON 整数（不收布尔、字符串、小数）。
        static int Int(object value, string key, int min, int max)
        {
            long n;
            if (value is int)
            {
                n = (int)value;
            }
            else if (value is long)
            {
                n = (long)value;
            }
            else
            {
                throw BridgeException.BadField(key, key + " 必须是整数");
            }
            if (n < min || n > max)
            {
                throw BridgeException.BadField(key,
                    key + " 必须是 " + Num(min) + " 到 " + Num(max) + " 的整数");
            }
            return (int)n;
        }

        // 用量是大于 0、不超过 1000000000000、最多 6 位小数的数（bom_opcomponent.BaseQtyN / BaseQtyD 是 6 位小数）。
        static decimal Qty(object value, string key)
        {
            decimal qty;
            if (!TryNum(value, out qty) || qty <= 0m || qty > QtyMax)
            {
                throw BridgeException.BadField(key, key + " 必须是大于 0 且不超过 1000000000000 的数");
            }
            if (decimal.Round(qty, 6) != qty)
            {
                throw BridgeException.BadField(key, key + " 最多 6 位小数");
            }
            return qty;
        }

        // 损耗率（%）：0 到小于 100，最多 3 位小数（Udt_Rate）。
        static decimal Rate(object value, string key)
        {
            decimal rate;
            if (!TryNum(value, out rate) || rate < 0m || rate >= 100m)
            {
                throw BridgeException.BadField(key, key + " 必须是 0 到小于 100 的数");
            }
            if (decimal.Round(rate, 3) != rate)
            {
                throw BridgeException.BadField(key, key + " 最多 3 位小数");
            }
            return rate;
        }

        static bool TryNum(object value, out decimal number)
        {
            number = 0m;
            try
            {
                if (value is int || value is long || value is decimal)
                {
                    number = Convert.ToDecimal(value, CultureInfo.InvariantCulture);
                    return true;
                }
                if (value is double && !double.IsNaN((double)value) && !double.IsInfinity((double)value))
                {
                    number = (decimal)(double)value;
                    return true;
                }
            }
            catch (OverflowException)
            {
                return false;
            }
            return false;
        }

        internal static string Num(int n)
        {
            return n.ToString(CultureInfo.InvariantCulture);
        }
    }
}
