using System;
using System.Collections.Generic;

namespace U8Co
{
    // 不良品处理单修改的一项表体操作（QmRejEditReq.Parse）。字段为 null 表示没送（update 只改送了的字段）。
    internal sealed class QmRejEditOp
    {
        public const string Add = "add";
        public const string Update = "update";
        public const string Delete = "delete";

        public string Op = "";
        public int LineId;
        public decimal? Qty;
        public decimal? DimQty;
        public string Dispose;
        public string Reason;
        public string DimInv;
        public string Wh;
    }

    // 校验过的不良品处理单修改请求。Head 沿用生单的表头（ddate、cdefine1–16、chdefine11–16）；Head.Lines 是合并后的整单处理行
    // （QmRejEditLoad 填），Ops 是请求的表体操作。
    internal sealed class QmRejEditAsk
    {
        public readonly QmRejAsk Head = new QmRejAsk();
        public readonly List<QmRejEditOp> Ops = new List<QmRejEditOp>();
    }

    // 不良品处理单修改（vouchers/update）的请求校验：登录前和处理函数里各一次，字段名不分大小写。head 与 lines 至少一个非空。
    // head：ddate（送了就不能为空）、cdefine1–16、chdefine11–16（同生单 QmRejReq）。
    // lines（至多 200 项）按 op 增删改处理行：add {quantity, cscrapdiscode, creasoncode, cdiminvcode?, fdimquantity?, cbwhcode?}；
    // update {line_id（表体 AUTOID）, 上述字段至少一个}；delete {line_id}。同一 line_id 只能出现一次。
    // 检验单、存货、来源等字段一律不收（处理单的来源与存货不能改）。
    internal static class QmRejEditReq
    {
        public const string UpdatePath = "/u8co/v1/vouchers/update";
        static readonly string[] Fields = new string[]
        {
            "quantity", "cscrapdiscode", "creasoncode", "cdiminvcode", "fdimquantity", "cbwhcode"
        };

        public static QmRejEditAsk Parse(QmRejSpec spec, Dictionary<string, object> head, object[] lines)
        {
            bool noHead = head == null || head.Count == 0;
            if (noHead && (lines == null || lines.Length == 0))
            {
                throw BridgeException.BadField("head", "没有要修改的内容");
            }
            if (lines != null && lines.Length > QmRejReq.LinesMax)
            {
                throw BridgeException.BadField("lines", "lines 至多 200 项");
            }
            QmRejEditAsk ask = new QmRejEditAsk();
            ask.Head.Spec = spec;
            ParseHead(ask.Head, head ?? new Dictionary<string, object>());
            ParseOps(ask, lines);
            return ask;
        }

        // 表头同生单（QmRejReq.ParseHead），送了 ddate 就不能为空。
        static void ParseHead(QmRejAsk ask, Dictionary<string, object> head)
        {
            try
            {
                QmRejReq.ParseHead(ask, head);
            }
            catch (BridgeException ex)
            {
                throw FieldPath.Under(ex, "head");
            }
            if (ask.Head.ContainsKey("ddate") && ask.Date.Length == 0)
            {
                throw BridgeException.BadField("head.ddate", "单据日期必须是 yyyy-MM-dd");
            }
        }

        static void ParseOps(QmRejEditAsk ask, object[] lines)
        {
            List<int> seen = new List<int>();
            for (int i = 0; lines != null && i < lines.Length; i++)
            {
                try
                {
                    QmRejEditOp op = ParseOp(lines[i]);
                    if (op.LineId > 0 && seen.Contains(op.LineId))
                    {
                        throw BridgeException.BadField("line_id", "line_id 重复");
                    }
                    seen.Add(op.LineId);
                    ask.Ops.Add(op);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
        }

        static QmRejEditOp ParseOp(object raw)
        {
            Dictionary<string, object> row = raw as Dictionary<string, object>;
            if (row == null)
            {
                throw new BridgeException(400, "bad_request", "表体行不是对象");
            }
            RequireKeys(row);
            QmRejEditOp op = new QmRejEditOp();
            op.Op = OpOf(row);
            object id = MfgReq.Raw(row, "line_id");
            if (op.Op == QmRejEditOp.Add)
            {
                if (id != null)
                {
                    throw BridgeException.BadField("line_id", "新增行不能带 line_id");
                }
                Values(op, row);
                RequireAdd(op);
                return op;
            }
            op.LineId = LineId(id);
            int sent = Values(op, row);
            if (op.Op == QmRejEditOp.Delete && sent > 0)
            {
                throw BridgeException.BadField("op", "删除行只能有 op 和 line_id");
            }
            if (op.Op == QmRejEditOp.Update && sent == 0)
            {
                throw BridgeException.BadField("line_id", "修改行至少再改一个字段");
            }
            return op;
        }

        static void RequireKeys(Dictionary<string, object> row)
        {
            foreach (KeyValuePair<string, object> kv in row)
            {
                string low = kv.Key == null ? "" : kv.Key.ToLowerInvariant();
                if (low != "op" && low != "line_id" && !LineKey(low))
                {
                    throw BridgeException.BadField(kv.Key, "不能设置字段 " + kv.Key).WithHint(FieldPath.WritableHint);
                }
            }
        }

        static string OpOf(Dictionary<string, object> row)
        {
            string op = QmReq.Text(MfgReq.Raw(row, "op"), "op", 10);
            if (op != QmRejEditOp.Add && op != QmRejEditOp.Update && op != QmRejEditOp.Delete)
            {
                throw BridgeException.BadField("op", "op 只能是 add、update 或 delete");
            }
            return op;
        }

        // 读出送了的字段，返回个数。送了的编码不能为空串（清空处理方式、原因没有意义；降级后存货随处理方式自动清除）。
        static int Values(QmRejEditOp op, Dictionary<string, object> row)
        {
            int sent = 0;
            for (int i = 0; i < Fields.Length; i++)
            {
                if (MfgReq.Raw(row, Fields[i]) != null)
                {
                    sent++;
                }
            }
            if (MfgReq.Raw(row, "quantity") != null)
            {
                op.Qty = MfgReq.LineQty(row);
            }
            object dim = MfgReq.Raw(row, "fdimquantity");
            if (dim != null)
            {
                op.DimQty = QmReq.Qty(dim, "fdimquantity", false);
            }
            op.Dispose = Code(row, "cscrapdiscode");
            op.Reason = Code(row, "creasoncode");
            op.DimInv = Code(row, "cdiminvcode");
            op.Wh = Code(row, "cbwhcode");
            return sent;
        }

        static string Code(Dictionary<string, object> row, string name)
        {
            object raw = MfgReq.Raw(row, name);
            if (raw == null)
            {
                return null;
            }
            string text = QmReq.Text(raw, name, 60);
            if (text.Length == 0)
            {
                throw BridgeException.BadField(name, name + " 不能为空");
            }
            return text;
        }

        static void RequireAdd(QmRejEditOp op)
        {
            if (!op.Qty.HasValue)
            {
                throw BridgeException.BadField("quantity", "新增行必须指定 quantity");
            }
            if (op.Dispose == null)
            {
                throw BridgeException.BadField("cscrapdiscode", "必须指定处理方式 cscrapdiscode");
            }
            if (op.Reason == null)
            {
                throw BridgeException.BadField("creasoncode", "必须指定不良原因 creasoncode");
            }
        }

        // line_id：表体 AUTOID，正整数。
        static int LineId(object value)
        {
            long number = 0;
            if (value is int)
            {
                number = (int)value;
            }
            else if (value is long)
            {
                number = (long)value;
            }
            if (number < 1 || number > int.MaxValue)
            {
                throw BridgeException.BadField("line_id", "line_id 必须是正整数（表体 AUTOID）");
            }
            return (int)number;
        }

        internal static bool LineKey(string low)
        {
            return Array.IndexOf(Fields, low) >= 0;
        }

        // meta 用的全部候选字段名（其余已在 QmRejReq.MetaNames 里）。
        internal static string[] MetaNames()
        {
            return new string[] { "fdimquantity" };
        }
    }
}
