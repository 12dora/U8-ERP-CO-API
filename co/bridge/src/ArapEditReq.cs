using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 修改请求的一行：op 为 add / update / delete。Raw 去掉了 op、line_id；Fields 已过白名单（键小写）。
    internal sealed class ArapEditOp
    {
        public string Op;
        public int LineId;
        public Dictionary<string, object> Raw;
        public Dictionary<string, string> Fields;
    }

    // 应收应付单据修改的请求校验（不连库）。可写字段 = 新增的字段表，去掉往来单位和币种、汇率。
    internal sealed class ArapEditReq
    {
        public Dictionary<string, string> Head;
        public List<ArapEditOp> Ops;

        // 换往来单位要重挑全部科目和档案，换币种、汇率要重算全部行，本期都不做。
        static readonly string[] Fixed = new string[] { "cdwcode", "cexch_name", "iexchrate" };

        public static ArapEditReq Parse(VoucherKind kind, Dictionary<string, object> head, object[] lines)
        {
            ArapSpec spec = ArapReq.Spec(kind);
            ArapEditReq req = new ArapEditReq();
            try
            {
                req.Head = HeadOf(spec, head);
            }
            catch (BridgeException ex)
            {
                throw FieldPath.Under(ex, "head");
            }
            req.Ops = new List<ArapEditOp>();
            HashSet<int> seen = new HashSet<int>();
            object[] rows = lines ?? new object[0];
            if (rows.Length > 200)
            {
                throw BridgeException.BadField("lines", "lines 不能超过 200 行");
            }
            for (int i = 0; i < rows.Length; i++)
            {
                req.Ops.Add(OneAt(spec, rows[i], seen, i));
            }
            if (req.Head.Count == 0 && req.Ops.Count == 0)
            {
                throw new BridgeException(400, "bad_request", "没有要修改的内容");
            }
            return req;
        }

        static Dictionary<string, string> HeadOf(ArapSpec spec, Dictionary<string, object> head)
        {
            Dictionary<string, object> raw = head ?? new Dictionary<string, object>();
            foreach (string key in raw.Keys)
            {
                if (IsFixed(key == null ? "" : key.ToLowerInvariant()))
                {
                    throw BridgeException.BadField(key, "不能修改字段 " + key);
                }
            }
            Dictionary<string, string> fields = FieldsOf(raw, spec.Close ? ArapReq.CloseHead : ArapReq.VouchHead, 1, 16);
            string date;
            if (fields.TryGetValue("dvouchdate", out date))
            {
                ArapReq.CheckDate(date);
            }
            return fields;
        }

        // 给 meta：true = 修改时可写。
        public static bool Allowed(ArapSpec spec, bool head, string low)
        {
            string name = low ?? "";
            if (head)
            {
                if (IsFixed(name))
                {
                    return false;
                }
                return Listed(name, spec.Close ? ArapReq.CloseHead : ArapReq.VouchHead) || ArapReq.Span(name, "cdefine", 1, 16);
            }
            return Listed(name, spec.Close ? ArapReq.CloseLine : ArapReq.VouchLine) || ArapReq.Span(name, "cdefine", 22, 37);
        }

        // 行内校验的 field 相对该行，这里补上 lines.<下标>。
        static ArapEditOp OneAt(ArapSpec spec, object raw, HashSet<int> seen, int i)
        {
            try
            {
                return One(spec, raw, seen);
            }
            catch (BridgeException ex)
            {
                throw FieldPath.Under(ex, FieldPath.Item("lines", i));
            }
        }

        static ArapEditOp One(ArapSpec spec, object raw, HashSet<int> seen)
        {
            Dictionary<string, object> row = raw as Dictionary<string, object>;
            if (row == null)
            {
                throw new BridgeException(400, "bad_request", "lines 的每一行必须是对象");
            }
            ArapEditOp op = new ArapEditOp();
            op.Op = OpOf(row);
            op.Raw = Rest(row);
            op.Fields = FieldsOf(op.Raw, spec.Close ? ArapReq.CloseLine : ArapReq.VouchLine, 22, 37);
            if (op.Op == "add")
            {
                if (row.ContainsKey("line_id"))
                {
                    throw BridgeException.BadField("line_id", "新增行不能带 line_id");
                }
                return op;
            }
            op.LineId = IdOf(row);
            if (!seen.Add(op.LineId))
            {
                throw BridgeException.BadField("line_id", "明细行重复");
            }
            if (op.Op == "delete" && op.Raw.Count > 0)
            {
                throw new BridgeException(400, "bad_request", "删除行只能带 line_id");
            }
            if (op.Op == "update" && op.Fields.Count == 0)
            {
                throw new BridgeException(400, "bad_request", "修改行至少要改一个字段");
            }
            return op;
        }

        // 同新增的字段表，但摘要、备注、自定义项写 JSON null 或空串表示清空（""），与采购修改一致；
        // 金额、日期、编码类字段空值仍当没传。
        static Dictionary<string, string> FieldsOf(Dictionary<string, object> raw, string csv, int defineFrom, int defineTo)
        {
            Dictionary<string, string> fields = ArapReq.Fields(raw, csv, defineFrom, defineTo);
            foreach (KeyValuePair<string, object> pair in raw)
            {
                string key = pair.Key == null ? "" : pair.Key.ToLowerInvariant();
                if (Blank(pair.Value) && Clearable(key, defineFrom, defineTo))
                {
                    fields[key] = "";
                }
            }
            return fields;
        }

        static bool Blank(object value)
        {
            if (value == null || value is DBNull)
            {
                return true;
            }
            string text = value as string;
            return text != null && text.Trim().Length == 0;
        }

        static bool Clearable(string key, int defineFrom, int defineTo)
        {
            return key == "cdigest" || key == "cmemo" || ArapReq.Span(key, "cdefine", defineFrom, defineTo);
        }

        static string OpOf(Dictionary<string, object> row)
        {
            object value;
            string op = row.TryGetValue("op", out value) ? value as string : null;
            if (op != "add" && op != "update" && op != "delete")
            {
                throw BridgeException.BadField("op", "op 只能是 add、update 或 delete");
            }
            return op;
        }

        static Dictionary<string, object> Rest(Dictionary<string, object> row)
        {
            Dictionary<string, object> rest = new Dictionary<string, object>();
            foreach (KeyValuePair<string, object> pair in row)
            {
                if (pair.Key == "op" || pair.Key == "line_id")
                {
                    continue;
                }
                rest[pair.Key] = pair.Value;
            }
            return rest;
        }

        // line_id：正整数（JSON 里是整数，或没有小数部分的数）。
        static int IdOf(Dictionary<string, object> row)
        {
            object value;
            if (!row.TryGetValue("line_id", out value) || value == null || value is bool || value is string)
            {
                throw BridgeException.BadField("line_id", "缺少 line_id");
            }
            IConvertible num = value as IConvertible;
            decimal d;
            try
            {
                d = num == null ? 0m : num.ToDecimal(CultureInfo.InvariantCulture);
            }
            catch (Exception)
            {
                d = 0m;
            }
            if (d < 1m || d > int.MaxValue || decimal.Truncate(d) != d)
            {
                throw BridgeException.BadField("line_id", "line_id 必须是正整数");
            }
            return (int)d;
        }

        static bool IsFixed(string low)
        {
            return Array.IndexOf(Fixed, low) >= 0;
        }

        static bool Listed(string key, string csv)
        {
            return key.Length > 0 && ("," + csv + ",").IndexOf("," + key + ",", StringComparison.Ordinal) >= 0;
        }
    }
}
