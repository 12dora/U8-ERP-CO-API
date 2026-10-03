using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // 校验过的质量单据生单请求（QmReq.Parse）。Head 的键是小写字段名，值已去两端空白。
    internal sealed class QmAsk
    {
        public QmSpec Spec;
        public string Date = "";
        public decimal Dt;
        public readonly Dictionary<string, string> Head = new Dictionary<string, string>(StringComparer.Ordinal);
        public readonly List<QmLineAsk> Lines = new List<QmLineAsk>();
        // 检验单表头 items：null 表示没送（全部取检验方案的缺省）。
        public List<QmItemAsk> Items;

        public string Get(string low)
        {
            string value;
            return Head.TryGetValue(low, out value) ? value ?? "" : "";
        }

        // 自定义项（cdefine1–16、chdefine11–16），按请求原样写进表头。
        public List<string> Defines()
        {
            List<string> names = new List<string>();
            foreach (string key in Head.Keys)
            {
                if (key.StartsWith("cdefine", StringComparison.Ordinal) || key.StartsWith("chdefine", StringComparison.Ordinal))
                {
                    names.Add(key);
                }
            }
            return names;
        }
    }

    internal sealed class QmLineAsk
    {
        public int SourceLineId;
        public decimal Qty;
        public string Wh = "";
        public decimal Reg;
        public decimal Con;
        public decimal Dis;
    }

    // 检验单的检验项目覆盖：按（检验项目, 检验指标）对到方案行，覆盖检验值和单项判定。
    internal sealed class QmItemAsk
    {
        public string ItemCode = "";
        public string GuideCode = "";
        // null 表示没送（检验值缺省取标准值）。
        public string Value;
        public string Judge;

        internal static List<QmItemAsk> ParseList(object raw)
        {
            IList list = raw as IList;
            if (list == null)
            {
                throw BridgeException.BadField("items", "items 必须是 JSON 数组");
            }
            if (list.Count < 1 || list.Count > QmReq.ItemsMax)
            {
                throw BridgeException.BadField("items", "items 必须是 1 到 50 项");
            }
            List<QmItemAsk> items = new List<QmItemAsk>();
            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < list.Count; i++)
            {
                QmItemAsk item;
                try
                {
                    item = One(list[i]);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "items", i);
                }
                if (!seen.Add(item.ItemCode + "\n" + item.GuideCode))
                {
                    throw BridgeException.BadField(FieldPath.Item("items", i), "items 有重复的检验项目和指标");
                }
                items.Add(item);
            }
            return items;
        }

        static QmItemAsk One(object raw)
        {
            Dictionary<string, object> row = raw as Dictionary<string, object>;
            if (row == null)
            {
                throw new BridgeException(400, "bad_request", "items 的每一项必须是对象");
            }
            string[] keys = QmReq.ItemNames();
            foreach (string key in row.Keys)
            {
                if (Array.IndexOf(keys, key == null ? "" : key.ToLowerInvariant()) < 0)
                {
                    throw BridgeException.BadField(key, "items 不能设置字段 " + key);
                }
            }
            QmItemAsk item = new QmItemAsk();
            item.ItemCode = QmReq.Text(MfgReq.Raw(row, "cchkitemcode"), "cchkitemcode", QmReq.ItemTextMax);
            item.GuideCode = QmReq.Text(MfgReq.Raw(row, "cchkguidecode"), "cchkguidecode", QmReq.ItemTextMax);
            if (item.ItemCode.Length == 0 || item.GuideCode.Length == 0)
            {
                throw BridgeException.BadField(item.ItemCode.Length == 0 ? "cchkitemcode" : "cchkguidecode",
                    "items 每一项必须有 cchkitemcode 和 cchkguidecode");
            }
            item.Value = Optional(row, "ccheckvalue");
            item.Judge = Optional(row, "ctargetqjug");
            if (item.Judge != null && item.Judge != QmReq.Qualified && item.Judge != QmReq.Unqualified)
            {
                throw BridgeException.BadField("ctargetqjug", "ctargetqjug 只能是 合格 或 不合格");
            }
            return item;
        }

        static string Optional(Dictionary<string, object> row, string name)
        {
            object value = MfgReq.Raw(row, name);
            return value == null ? null : QmReq.Text(value, name, QmReq.ItemTextMax);
        }
    }

    // 数量算式（--selftest 覆盖）。
    internal static class QmMath
    {
        // 合格数量：没送时 = 检验数量 − 让步 − 不良；三者之和必须等于检验数量。
        internal static decimal Qualified(decimal qty, bool given, decimal reg, decimal con, decimal dis)
        {
            if (!given)
            {
                reg = qty - con - dis;
                if (reg < 0m)
                {
                    throw BridgeException.BadField("fconquantiy", "让步和不良数量之和不能超过检验数量");
                }
            }
            if (reg + con + dis != qty)
            {
                throw BridgeException.BadField("fregquantity", "合格、让步、不良数量之和必须等于检验数量");
            }
            return reg;
        }

        // 剩余可生单数量 = 总数 − 已用（已报检 / 已申报 / 已检验），不截成 0。
        internal static decimal Remaining(decimal total, decimal used)
        {
            return total - used;
        }

        // 超过剩余数量 409（与其他生单一致）。
        internal static void RequireLeft(decimal qty, decimal left, string what)
        {
            if (qty > left)
            {
                throw new BridgeException(409, "state_mismatch", "超过可生单数量" + (what.Length > 0 ? "：" + what : ""));
            }
        }

        // 缺省结论：有不良数量为「不合格」，否则「合格」。
        internal static string Conclusion(decimal dis)
        {
            return dis > 0m ? QmReq.Unqualified : QmReq.Qualified;
        }
    }
}
