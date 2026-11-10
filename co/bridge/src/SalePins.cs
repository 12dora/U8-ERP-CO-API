using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 要落库的销售字段（r50 字段约定）：销售订单新增 / 修改的表头 cgatheringplan、caddcode、cdefine1、cdefine2，
    // 表体 cbdefine5（SO_SODetails_extradefine）、cunitid、inum、cwhcode；发货单参照订单生单的表头 caddcode、
    // cdefine1、cdefine2、chdefine1–4（DispatchList_extradefine）。保存前按列目录和档案校验（SalePinsCheck），
    // 保存后、提交前在同一事务里补写并回读（SalePinsSave）。扩展自定义项不放进 DOM；主表列模板里有才交给组件。
    internal sealed class SalePin
    {
        internal string Path;
        internal string Field;
        internal string Table;
        internal string Column;
        internal string KeyColumn;
        internal int Key;
        // 表头 -1；表体是本次新增行的序号（新增订单为请求行号，修改为第几个 op=add），op=update 行也是 -1。
        internal int Line = -1;
        internal string Inv = "";
        internal string Value = "";
        internal bool OnDom = true;
        // 参与 U8 数量、单位、金额或收付款计划计算的字段（Spec 第 6 列 C）：只走组件，桥不补写。
        internal bool Calc;
        internal SaleCol Col;

        internal bool Extra
        {
            get { return SalePins.ExtraTable(Table); }
        }

        // 组件不落库或看不到的纯标注字段，由桥在 Save 之后写；参与计算的字段从不由桥写。
        internal bool Explicit
        {
            get { return !Calc && (Extra || !OnDom); }
        }
    }

    internal sealed class SaleCol
    {
        internal string Kind;
        // 库里的 DATA_TYPE（小写），日期列写入时按它选显式转换。
        internal string Type = "";
        internal int Len;
    }

    internal sealed partial class SalePins
    {
        internal const string Order = "sale_order";
        internal const string Dispatch = "dispatch";
        // {类型, H 表头 / B 表体, 请求字段（小写）, 表, 列, A 纯标注（模板没有时桥可补写）/ C 只走组件计算}
        // C：cunitid、inum 参与数量 / 换算率 / 金额计算；cgatheringplan 决定 U8 保存时生成的收付款计划和到期日。
        // 模板没有 C 字段时保存前 400，从不在 Save 之后用 SQL 补写。
        static readonly string[][] Spec = new string[][] {
            new string[] { Order, "H", "cgatheringplan", "SO_SOMain", "cGatheringPlan", "C" },
            new string[] { Order, "H", "caddcode", "SO_SOMain", "cAddCode", "A" },
            new string[] { Order, "H", "cdefine1", "SO_SOMain", "cDefine1", "A" },
            new string[] { Order, "H", "cdefine2", "SO_SOMain", "cDefine2", "A" },
            new string[] { Order, "B", "cbdefine5", "SO_SODetails_extradefine", "cbdefine5", "A" },
            new string[] { Order, "B", "cunitid", "SO_SODetails", "cUnitID", "C" },
            new string[] { Order, "B", "inum", "SO_SODetails", "iNum", "C" },
            new string[] { Order, "B", "cwhcode", "SO_SODetails", "cWhCode", "A" },
            new string[] { Dispatch, "H", "caddcode", "DispatchList", "cAddCode", "A" },
            new string[] { Dispatch, "H", "cdefine1", "DispatchList", "cDefine1", "A" },
            new string[] { Dispatch, "H", "cdefine2", "DispatchList", "cDefine2", "A" },
            new string[] { Dispatch, "H", "chdefine1", "DispatchList_extradefine", "chdefine1", "A" },
            new string[] { Dispatch, "H", "chdefine2", "DispatchList_extradefine", "chdefine2", "A" },
            new string[] { Dispatch, "H", "chdefine3", "DispatchList_extradefine", "chdefine3", "A" },
            new string[] { Dispatch, "H", "chdefine4", "DispatchList_extradefine", "chdefine4", "A" }
        };

        internal string Kind;
        // 修改：订单 ID；新增 / 生单：保存后由 Settle 填。
        internal int HeadId;
        internal string Customer = "";
        internal string Date = "";
        internal List<SalePin> Items = new List<SalePin>();
        // 本次新增的表体行数（新增订单 = 请求行数，修改 = op=add 行数）和保存前已有的 iSOsID。
        internal int Adds;
        internal HashSet<int> OldLines = new HashSet<int>();

        internal static bool ExtraTable(string table)
        {
            return table != null && table.EndsWith("_extradefine", StringComparison.OrdinalIgnoreCase);
        }

        internal static string[] Find(string kind, bool head, string lower)
        {
            string side = head ? "H" : "B";
            for (int i = 0; i < Spec.Length; i++)
            {
                if (Spec[i][0] == kind && Spec[i][1] == side && Spec[i][2] == lower)
                {
                    return Spec[i];
                }
            }
            return null;
        }

        // 不写进 DOM 的约定字段：扩展自定义项，或模板（行集 schema）里没有的纯标注主表列。
        // 不是约定字段、或是只走组件的 C 字段返回 false（模板没有时照常 400）。
        internal static bool OffDom(string kind, string lower, bool head, bool inTemplate)
        {
            string[] row = Find(kind, head, lower);
            if (row == null || row[5] == "C")
            {
                return false;
            }
            return ExtraTable(row[3]) || !inTemplate;
        }

        // 主表列（load 补列用，组件 DOM 可能不带）：{表, 列, 请求字段}。
        internal static List<string[]> BaseColumns(string kind, bool head)
        {
            List<string[]> list = new List<string[]>();
            string side = head ? "H" : "B";
            for (int i = 0; i < Spec.Length; i++)
            {
                if (Spec[i][0] == kind && Spec[i][1] == side && !ExtraTable(Spec[i][3]))
                {
                    list.Add(new string[] { Spec[i][3], Spec[i][4], Spec[i][2] });
                }
            }
            return list;
        }

        internal static string KeyOf(string table)
        {
            if (table.StartsWith("SO_SOMain", StringComparison.OrdinalIgnoreCase))
            {
                return "ID";
            }
            if (table.StartsWith("SO_SODetails", StringComparison.OrdinalIgnoreCase))
            {
                return "iSOsID";
            }
            return "DLID";
        }

        // 纯收集（不连库）：新增订单 edit=false，lines 是请求行；修改 edit=true，lines 是 op 行（add / update / delete）。
        internal static SalePins Collect(string kind, Dictionary<string, object> head, object[] lines, bool edit)
        {
            SalePins pins = new SalePins();
            pins.Kind = kind;
            pins.Customer = Text(head, "ccuscode");
            Take(pins, head, true, "head", edit);
            if (lines == null)
            {
                return pins;
            }
            for (int i = 0; i < lines.Length; i++)
            {
                TakeLine(pins, lines[i] as Dictionary<string, object>, i, edit);
            }
            return pins;
        }

        static void TakeLine(SalePins pins, Dictionary<string, object> line, int index, bool edit)
        {
            if (line == null)
            {
                return;
            }
            string op = edit ? Text(line, "op").ToLowerInvariant() : "add";
            if (op == "delete")
            {
                return;
            }
            int before = pins.Items.Count;
            string at = FieldPath.Item("lines", index);
            Take(pins, line, false, at, edit);
            int key = op == "update" ? CoRows.AsId(Raw(line, "line_id")) : 0;
            int slot = op == "update" ? -1 : pins.Adds;
            for (int i = before; i < pins.Items.Count; i++)
            {
                pins.Items[i].Inv = Text(line, "cinvcode");
                pins.Items[i].Key = key;
                pins.Items[i].Line = slot;
            }
            // 计量组可能变的行（SalePinsGroup）：保存前查请求值是否自洽，保存后整组核对。
            if (pins.Kind == Order)
            {
                pins.TakeGroup(line, at, op == "update", key, slot);
            }
            if (op != "update")
            {
                pins.Adds = pins.Adds + 1;
            }
        }

        static void Take(SalePins pins, Dictionary<string, object> map, bool head, string at, bool edit)
        {
            if (map == null)
            {
                return;
            }
            foreach (KeyValuePair<string, object> pair in map)
            {
                string lower = pair.Key == null ? "" : pair.Key.ToLowerInvariant();
                string[] row = Find(pins.Kind, head, lower);
                string value = row == null ? null : Cell(pair.Value, FieldPath.Join(at, pair.Key));
                // 新增时空值等于没给；修改时空串是清空。
                if (value == null || (!edit && value.Trim().Length == 0))
                {
                    continue;
                }
                SalePin pin = new SalePin();
                pin.Path = FieldPath.Join(at, lower);
                if (pins.Has(pin.Path))
                {
                    throw BridgeException.BadField(pin.Path, "字段重复 " + pair.Key);
                }
                pin.Field = lower;
                pin.Table = row[3];
                pin.Column = row[4];
                pin.KeyColumn = KeyOf(row[3]);
                pin.Calc = row[5] == "C";
                pin.Value = value.Trim();
                pins.Items.Add(pin);
            }
        }

        bool Has(string path)
        {
            for (int i = 0; i < Items.Count; i++)
            {
                if (Items[i].Path == path)
                {
                    return true;
                }
            }
            return false;
        }

        // 模板里有没有该列（扩展自定义项一律不进 DOM）。只走组件的字段模板里没有时 400，不留给桥补写。
        internal void MarkDom(Func<string, bool> headHas, Func<string, bool> lineHas)
        {
            for (int i = 0; i < Items.Count; i++)
            {
                SalePin pin = Items[i];
                Func<string, bool> has = pin.Path.StartsWith("head.", StringComparison.Ordinal) ? headHas : lineHas;
                pin.OnDom = !pin.Extra && has != null && has(pin.Field);
                if (pin.Calc && !pin.OnDom)
                {
                    throw BridgeException.BadField(pin.Path, "本账套的单据模板没有 " + pin.Field
                        + "：它参与 U8 的计算，只能由 U8 组件保存，不能设置");
                }
            }
        }

        static object Raw(Dictionary<string, object> map, string name)
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

        static string Text(Dictionary<string, object> map, string name)
        {
            if (map == null)
            {
                return "";
            }
            object value = Raw(map, name);
            string text = value as string;
            return text == null ? "" : text.Trim();
        }

        // 同 SoFields.Cell：字符串、数字、布尔；其余 400。
        static string Cell(object value, string path)
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
                throw BridgeException.BadField(path, "字段值类型不正确");
            }
            return fmt.ToString(null, CultureInfo.InvariantCulture);
        }
    }
}
