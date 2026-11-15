using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 采购发票参照采购入库生单的登录前校验。字段名不分大小写。
    // 表头只收 cPBVCode（税务发票号，必填）、cPBVBillType（01 专用 / 02 普通，缺省 01）、dPBVDate、cPBVMemo；
    // 表体 1 到 200 行，只收 source_line_id（rdrecords01.AutoID）和 quantity。
    internal static class PuInvReq
    {
        static readonly string[] HeadKeys = new string[] { "cpbvcode", "cpbvbilltype", "dpbvdate", "cpbvmemo" };

        // 给 meta 路由用。
        internal static string[] HeadKeyList()
        {
            return (string[])HeadKeys.Clone();
        }

        public static void CheckGenerate(Dictionary<string, object> head, object[] lines)
        {
            CheckHead(head);
            CheckLines(lines);
        }

        static void CheckHead(Dictionary<string, object> head)
        {
            if (head == null)
            {
                throw BridgeException.BadField("head.cpbvcode", "缺少发票号 cPBVCode");
            }
            foreach (KeyValuePair<string, object> kv in head)
            {
                string low = kv.Key == null ? "" : kv.Key.ToLowerInvariant();
                if (Array.IndexOf(HeadKeys, low) < 0)
                {
                    throw BridgeException.BadField(FieldPath.Join("head", kv.Key), "不能设置字段 " + kv.Key)
                        .WithHint(FieldPath.WritableHint);
                }
                if (!(kv.Value is string))
                {
                    throw BridgeException.BadField(FieldPath.Join("head", kv.Key), "字段 " + kv.Key + " 必须是字符串");
                }
            }
            string code = Text(head, "cpbvcode");
            if (code.Length == 0)
            {
                throw BridgeException.BadField("head.cpbvcode", "缺少发票号 cPBVCode");
            }
            if (code.Length > 30)
            {
                throw BridgeException.BadField("head.cpbvcode", "发票号不能超过 30 个字符");
            }
            BillType(head);
            string date = Text(head, "dpbvdate");
            DateTime day;
            if (date.Length > 0 && !DateTime.TryParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out day))
            {
                throw BridgeException.BadField("head.dpbvdate", "发票日期必须是 yyyy-MM-dd");
            }
            if (Text(head, "cpbvmemo").Length > 255)
            {
                throw BridgeException.BadField("head.cpbvmemo", "备注不能超过 255 个字符");
            }
        }

        static void CheckLines(object[] lines)
        {
            if (lines == null || lines.Length < 1 || lines.Length > 200)
            {
                throw BridgeException.BadField("lines", "表体须为 1 到 200 行");
            }
            HashSet<int> seen = new HashSet<int>();
            for (int i = 0; i < lines.Length; i++)
            {
                try
                {
                    Dictionary<string, object> line = LineOf(lines[i]);
                    if (!seen.Add(MfgReq.LineId(line)))
                    {
                        throw BridgeException.BadField("lines.source_line_id", "来源明细重复");
                    }
                    MfgReq.LineQty(line);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
        }

        internal static Dictionary<string, object> LineOf(object raw)
        {
            Dictionary<string, object> line = raw as Dictionary<string, object>;
            if (line == null)
            {
                throw BridgeException.BadField("lines", "表体行不是对象");
            }
            foreach (string key in line.Keys)
            {
                string low = key == null ? "" : key.ToLowerInvariant();
                if (low != "source_line_id" && low != "quantity")
                {
                    throw BridgeException.BadField(FieldPath.Join("lines", key), "不能设置字段 " + key);
                }
            }
            return line;
        }

        // 01 专用发票、02 普通发票，缺省 01。
        internal static string BillType(Dictionary<string, object> head)
        {
            string type = Text(head, "cpbvbilltype");
            if (type.Length == 0)
            {
                return "01";
            }
            if (type != "01" && type != "02")
            {
                throw BridgeException.BadField("head.cpbvbilltype", "cPBVBillType 只能是 01 或 02");
            }
            return type;
        }

        internal static string Text(Dictionary<string, object> map, string name)
        {
            return MfgReq.Text(map, name);
        }
    }
}
