using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 到货单、采购退货单、采购发票的修改（vouchers/update，由 EditMore 分派）。
    // 只改未审核、没有下游的单据，拒绝条件与各自的删除相同；不能新增行，数量只能减少，可删行但至少留一行。
    // CO：GetVoucherDataById 装载，行标 editprop，VoucherSave2(h, b, 1, "<id>") 引用 {3}，返回空为成功
    // （到货单已实测，退货单、发票未经实测）。U8 不重算金额，桥按生单同一公式重算；保存后在同一事务里核对来源累计数。
    // 这里是登录前也能做的请求解析和可写字段；DOM 与事务见 PuEditMoreDom，各类型见 PuEditMoreArr / Ret / Inv。
    internal static partial class PuEditMore
    {
        const decimal QtyMax = 1000000000000m;
        static readonly string[] ArrHead = new string[] { "cmemo", "ddate", "cdepcode" };
        static readonly string[] InvHead = new string[] { "cpbvmemo", "dpbvdate" };
        static readonly string[] DateKeys = new string[] { "ddate", "dpbvdate" };

        internal static ApiResult Update(WorkContext ctx, VoucherKind kind, int id, Dictionary<string, object> head, object[] lines)
        {
            string name = kind == null ? "" : kind.Name;
            if (!Handles(name))
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持修改");
            }
            PuEditReq req = Parse(name, head, lines);
            // 预演：登记上游单据（来源的累计数量随改数量回写）。
            DocMark.Upstream(ctx.Conn, kind, id);
            if (name == "arrival")
            {
                return PuArr.EditUpdate(ctx, kind, id, req);
            }
            if (name == "purchase_return")
            {
                return PuRet.EditUpdate(ctx, kind, id, req);
            }
            return PuInv.EditUpdate(ctx, kind, id, req);
        }

        // true：该字段修改时可写。表头：备注、日期、部门（发票是备注和发票日期）和 cdefine1–16；
        // 表体：数量（到货单、退货单 iquantity，发票 ipbvquantity）、cbmemo 和 cdefine22–37。
        internal static bool MetaAllowed(VoucherKind kind, bool head, string lowerField)
        {
            if (kind == null || lowerField == null)
            {
                return false;
            }
            return Allowed(kind.Name, head, lowerField);
        }

        static bool Handles(string name)
        {
            return name == "arrival" || name == "purchase_return" || name == "purchase_invoice";
        }

        static bool Allowed(string kind, bool head, string low)
        {
            if (!Handles(kind) || low.Length == 0)
            {
                return false;
            }
            if (head)
            {
                string[] list = kind == "purchase_invoice" ? InvHead : ArrHead;
                return Array.IndexOf(list, low) >= 0 || ArapReq.Span(low, "cdefine", 1, 16);
            }
            return low == QtyName(kind) || low == "cbmemo" || ArapReq.Span(low, "cdefine", 22, 37);
        }

        internal static string QtyName(string kind)
        {
            return kind == "purchase_invoice" ? "ipbvquantity" : "iquantity";
        }

        static PuEditReq Parse(string kind, Dictionary<string, object> head, object[] lines)
        {
            PuEditReq req = new PuEditReq();
            req.Kind = kind;
            req.QtyName = QtyName(kind);
            req.Head = HeadFields(kind, head);
            req.Lines = new List<PuEditLine>();
            HashSet<int> seen = new HashSet<int>();
            for (int i = 0; lines != null && i < lines.Length; i++)
            {
                try
                {
                    req.Lines.Add(OneLine(req, lines[i], seen));
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
            if (req.Head.Count == 0 && req.Lines.Count == 0)
            {
                throw new BridgeException(400, "bad_request", "没有要修改的内容");
            }
            return req;
        }

        static Dictionary<string, string> HeadFields(string kind, Dictionary<string, object> head)
        {
            Dictionary<string, string> map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            if (head == null)
            {
                return map;
            }
            foreach (KeyValuePair<string, object> kv in head)
            {
                string low = kv.Key == null ? "" : kv.Key.ToLowerInvariant();
                if (!Allowed(kind, true, low))
                {
                    throw BridgeException.BadField(FieldPath.Join("head", kv.Key), "不能修改字段 " + kv.Key)
                        .WithHint(FieldPath.WritableHint);
                }
                if (map.ContainsKey(low))
                {
                    throw BridgeException.BadField(FieldPath.Join("head", kv.Key), "字段重复 " + kv.Key);
                }
                string text = Cell(kv.Value);
                if (Array.IndexOf(DateKeys, low) >= 0)
                {
                    RequireDate(kv.Key, text);
                }
                map[low] = text;
            }
            return map;
        }

        static PuEditLine OneLine(PuEditReq req, object raw, HashSet<int> seen)
        {
            Dictionary<string, object> map = raw as Dictionary<string, object>;
            if (map == null)
            {
                throw BridgeException.BadField("lines", "表体行必须是对象");
            }
            PuEditLine line = new PuEditLine();
            line.Op = Values.Text(MfgReq.Raw(map, "op")).Trim();
            if (line.Op == "add")
            {
                throw BridgeException.BadField("lines.op", "该单据类型修改不能新增行");
            }
            if (line.Op != "update" && line.Op != "delete")
            {
                throw BridgeException.BadField("lines.op", "op 只能是 add、update 或 delete");
            }
            line.Id = CoRows.AsId(MfgReq.Raw(map, "line_id"));
            if (line.Id <= 0)
            {
                throw BridgeException.BadField("lines.line_id", "明细行不存在");
            }
            if (!seen.Add(line.Id))
            {
                throw BridgeException.BadField("lines.line_id", "明细行重复");
            }
            LineFields(req, map, line);
            RequireShape(line);
            return line;
        }

        static void LineFields(PuEditReq req, Dictionary<string, object> map, PuEditLine line)
        {
            line.Fields = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (KeyValuePair<string, object> kv in map)
            {
                string low = kv.Key == null ? "" : kv.Key.ToLowerInvariant();
                if (low == "op" || low == "line_id")
                {
                    continue;
                }
                if (!Allowed(req.Kind, false, low))
                {
                    throw BridgeException.BadField(FieldPath.Join("lines", kv.Key), "不能修改字段 " + kv.Key)
                        .WithHint(FieldPath.WritableHint);
                }
                if (low == req.QtyName)
                {
                    line.HasQty = true;
                    line.Qty = Qty(kv.Key, kv.Value);
                    continue;
                }
                if (line.Fields.ContainsKey(low))
                {
                    throw BridgeException.BadField(FieldPath.Join("lines", kv.Key), "字段重复 " + kv.Key);
                }
                line.Fields[low] = Cell(kv.Value);
            }
        }

        static void RequireShape(PuEditLine line)
        {
            bool any = line.HasQty || line.Fields.Count > 0;
            if (line.Op == "delete" && any)
            {
                throw BridgeException.BadField("lines", "删除行只能带 line_id");
            }
            if (line.Op == "update" && !any)
            {
                throw BridgeException.BadField("lines", "修改行至少要改一个字段");
            }
        }

        // 数量是正数（退货单也是，桥写回 U8 时取负），不超过 1000000000000，最多 6 位小数。字符串形式的数也收。
        static decimal Qty(string key, object value)
        {
            decimal qty;
            if (!TryQty(value, out qty) || qty <= 0m || qty > QtyMax)
            {
                throw BridgeException.BadField("lines." + key, key + " 必须是大于 0 且不超过 1000000000000 的数");
            }
            if (decimal.Round(qty, 6) != qty)
            {
                throw BridgeException.BadField("lines." + key, key + " 最多 6 位小数");
            }
            return qty;
        }

        static bool TryQty(object value, out decimal qty)
        {
            qty = 0m;
            if (value is double)
            {
                double d = (double)value;
                if (double.IsNaN(d) || double.IsInfinity(d) || Math.Abs(d) > 1e15)
                {
                    return false;
                }
                qty = (decimal)d;
                return true;
            }
            if (value is int || value is long || value is decimal)
            {
                qty = Convert.ToDecimal(value, CultureInfo.InvariantCulture);
                return true;
            }
            string text = value as string;
            return text != null && decimal.TryParse(text.Trim(), NumberStyles.AllowDecimalPoint, CultureInfo.InvariantCulture, out qty);
        }

        static void RequireDate(string key, string text)
        {
            DateTime day;
            if (!DateTime.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out day))
            {
                throw BridgeException.BadField("head." + key, key + " 必须是 yyyy-MM-dd");
            }
        }

        // null 当空串（清空备注、自定义项）；布尔写 1/0；数按不变区域格式。
        static string Cell(object value)
        {
            if (value == null || value is DBNull)
            {
                return "";
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

    // 解析后的修改请求。Head 的键是小写字段名；Lines 只有 update / delete。
    internal sealed class PuEditReq
    {
        public string Kind;
        public string QtyName;
        public Dictionary<string, string> Head;
        public List<PuEditLine> Lines;
    }

    internal sealed class PuEditLine
    {
        public string Op;
        public int Id;
        public bool HasQty;
        public decimal Qty;
        public Dictionary<string, string> Fields;
    }
}
