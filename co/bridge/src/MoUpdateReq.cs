using System;
using System.Collections.Generic;

namespace U8Co
{
    // 生产订单修改（vouchers/update，type=production_order，id=MoId）的登录前校验。字段名不分大小写。
    // 表头只收 remark：MOrderUpdate 不读表头的任何字段（只按 MoCode 找单），表头 remark 作为本次没单独给备注的各行的备注。
    // 明细按 line_id（MoDId）定位，op 只能是 update：本期不能新增行、不能删行（U8 按 DSortSeq 对行，没有删行的接口）。
    // 行上可改 qty、due_date、remark、文本型自定义项 define22–25、define28–33；inv_code 只能送当前值（不能换存货）。
    // start_date 一律 400：U8 改开工日期时会按工作日历 + 偏置期重算子件需求日期，MOrderUpdate 重建子件却沿用 Load 带出的旧日期（实测）。
    // U8 对空串不写（IsValidString），所以 remark 和自定义项不能清空。
    internal static class MoUpdateReq
    {
        internal static readonly string[] Defines = new string[]
        {
            "define22", "define23", "define24", "define25",
            "define28", "define29", "define30", "define31", "define32", "define33"
        };
        static readonly string[] HeadKeys = new string[] { "remark" };
        static readonly string[] FieldKeys = new string[] { "qty", "due_date", "remark", "inv_code" };
        public const string NoStart = "暂不支持修改开工日期（U8 重建子件时不重算需求日期）";
        // mom_orderdetail.Define22–25 是 nvarchar(60)，Define28–33 是 nvarchar(120)（sys.columns 的 max_length 是字节数 120 / 240）。
        const int DefineShort = 60;
        const int DefineLong = 120;

        // meta 用：表头、表体可写字段（不含 op、line_id）。
        internal static string[] HeadNames()
        {
            return (string[])HeadKeys.Clone();
        }

        internal static string[] LineNames()
        {
            List<string> all = new List<string>(FieldKeys);
            all.AddRange(Defines);
            return all.ToArray();
        }

        public static MoUpdateAsk Parse(Dictionary<string, object> head, object[] lines)
        {
            MoUpdateAsk ask = new MoUpdateAsk();
            try
            {
                MoCreateReq.Only(head, HeadKeys);
                if (MfgReq.Raw(head, "remark") != null)
                {
                    ask.HeadRemark = NotEmpty(MoCreateReq.Str(head, "remark", 255, false), "remark");
                }
            }
            catch (BridgeException ex)
            {
                throw FieldPath.Under(ex, "head");
            }
            int count = lines == null ? 0 : lines.Length;
            if (count > MoCreateReq.LinesMax)
            {
                throw BridgeException.BadField("lines", "lines 不能超过 50 行");
            }
            HashSet<int> seen = new HashSet<int>();
            for (int i = 0; i < count; i++)
            {
                MoEdit edit;
                try
                {
                    edit = Line(lines[i]);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
                if (!seen.Add(edit.LineId))
                {
                    throw BridgeException.BadField(FieldPath.Item("lines", i) + ".line_id", "line_id 重复");
                }
                ask.Edits.Add(edit);
            }
            if (ask.HeadRemark == null && ask.Edits.Count == 0)
            {
                throw new BridgeException(400, "bad_request", "没有要修改的内容");
            }
            return ask;
        }

        static MoEdit Line(object raw)
        {
            Dictionary<string, object> row = raw as Dictionary<string, object>;
            if (row == null)
            {
                throw new BridgeException(400, "bad_request", "lines 的每一行必须是对象");
            }
            string op = MfgReq.Raw(row, "op") as string;
            if (op == "add")
            {
                throw BridgeException.BadField("op", "生产订单修改暂不能新增行，请在 U8 客户端修改");
            }
            if (op == "delete")
            {
                throw BridgeException.BadField("op", "生产订单修改暂不能删除行，请在 U8 客户端修改");
            }
            if (op != "update")
            {
                throw BridgeException.BadField("op", "op 只能是 update");
            }
            CheckKeys(row);
            MoEdit edit = new MoEdit();
            edit.LineId = LineId(MfgReq.Raw(row, "line_id"));
            Fields(row, edit);
            return edit;
        }

        static void CheckKeys(Dictionary<string, object> row)
        {
            List<string> allowed = new List<string>(LineNames());
            allowed.Add("op");
            allowed.Add("line_id");
            allowed.Add("start_date");
            MoCreateReq.Only(row, allowed.ToArray());
            // 只数值不为 null 的字段：{"qty": null} 不算改动。
            int fields = 0;
            foreach (KeyValuePair<string, object> kv in row)
            {
                string low = (kv.Key ?? "").ToLowerInvariant();
                if (kv.Value != null && low != "op" && low != "line_id")
                {
                    fields++;
                }
            }
            if (fields == 0)
            {
                throw new BridgeException(400, "bad_request", "修改行至少要改一个字段");
            }
        }

        static void Fields(Dictionary<string, object> row, MoEdit edit)
        {
            if (MfgReq.Raw(row, "qty") != null)
            {
                edit.HasQty = true;
                edit.Qty = MoCreateReq.Qty(MfgReq.Raw(row, "qty"));
            }
            if (MfgReq.Raw(row, "start_date") != null)
            {
                throw BridgeException.BadField("start_date", NoStart);
            }
            if (MfgReq.Raw(row, "due_date") != null)
            {
                edit.Due = MoCreateReq.Date(row, "due_date");
            }
            if (MfgReq.Raw(row, "remark") != null)
            {
                edit.Remark = NotEmpty(MoCreateReq.Str(row, "remark", 255, false), "remark");
            }
            if (MfgReq.Raw(row, "inv_code") != null)
            {
                edit.InvCode = MoCreateReq.Str(row, "inv_code", 60, true);
            }
            for (int i = 0; i < Defines.Length; i++)
            {
                string key = Defines[i];
                if (MfgReq.Raw(row, key) != null)
                {
                    int max = string.CompareOrdinal(key, "define28") < 0 ? DefineShort : DefineLong;
                    edit.Defines[key] = NotEmpty(MoCreateReq.Str(row, key, max, false), key);
                }
            }
        }

        static string NotEmpty(string text, string key)
        {
            if (text.Length == 0)
            {
                throw BridgeException.BadField(key, key + " 不能清空（U8 接口不写空值）");
            }
            return text;
        }

        // line_id 是严格整数 1 到 2147483647（Json 解析出的 int / long，不收布尔、字符串、浮点）。
        static int LineId(object value)
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
                throw BridgeException.BadField("line_id", "line_id 无效");
            }
            if (n < 1 || n > int.MaxValue)
            {
                throw BridgeException.BadField("line_id", "line_id 无效");
            }
            return (int)n;
        }

        internal static string Define(MoEdit edit, string key)
        {
            string value;
            return edit.Defines.TryGetValue(key, out value) ? value : null;
        }
    }

    internal sealed class MoUpdateAsk
    {
        // 表头 remark：null 表示没给。
        public string HeadRemark;
        public List<MoEdit> Edits = new List<MoEdit>();
    }

    internal sealed class MoEdit
    {
        public int LineId;
        public bool HasQty;
        public decimal Qty;
        // 下面几项 null 表示这次没给。
        public string Due;
        public string Remark;
        public string InvCode;
        public Dictionary<string, string> Defines = new Dictionary<string, string>(StringComparer.Ordinal);
    }
}
