using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 保存后核对的销售字段（核对字段）：销售订单新增 / 修改的表头 cgatheringplan、caddcode、表头自定义项 cdefine1–16，
    // 表体 cunitid、inum、cwhcode 和表体扩展自定义项 cbdefineN（SO_SODetails_extradefine）；发货单参照订单生单的表头
    // caddcode、cdefine1–16 和表头扩展自定义项 chdefineN（DispatchList_extradefine）；蓝字销售发票参照发货单的表体扩展
    // 自定义项 cbdefineN（SaleBillVouchs_extradefine）；发货单参照订单生单的表体扩展自定义项 cbdefineN（DispatchLists_extradefine）。
    // N 为 1–99，列须在账套里存在。保存前按列目录和档案校验
    // （SalePinsCheck），保存后、提交前在同一事务里补写并回读（SalePinsSave），只核对请求给了的字段。
    // 扩展自定义项不放进 DOM；主表列模板里有才交给组件。
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
        // 生单表体：该行的来源行（发票为发货行 iDLsID、发货单为订单行 iSOsID；保存后按它找本次的行主键，SalePinsInv）。
        internal int SrcLine;

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
        // 数值列的 NUMERIC_PRECISION / NUMERIC_SCALE（decimal、numeric 按它们核对，SalePinsRange）。
        internal int Prec;
        internal int Scale;
    }

    internal sealed partial class SalePins
    {
        internal const string Order = "sale_order";
        internal const string Dispatch = "dispatch";
        internal const string Invoice = "sale_invoice";
        // 固定字段 {类型, H 表头 / B 表体, 请求字段（小写）, 表, 列, A 纯标注（模板没有时桥可补写）/ C 只走组件计算}
        // C：cunitid、inum 参与数量 / 换算率 / 金额计算；cgatheringplan 决定 U8 保存时生成的收付款计划和到期日。
        // 模板没有 C 字段时保存前 400，从不在 Save 之后用 SQL 补写。
        static readonly string[][] Spec = new string[][] {
            new string[] { Order, "H", "cgatheringplan", "SO_SOMain", "cGatheringPlan", "C" },
            new string[] { Order, "H", "caddcode", "SO_SOMain", "cAddCode", "A" },
            new string[] { Order, "B", "cunitid", "SO_SODetails", "cUnitID", "C" },
            new string[] { Order, "B", "inum", "SO_SODetails", "iNum", "C" },
            new string[] { Order, "B", "cwhcode", "SO_SODetails", "cWhCode", "A" },
            new string[] { Dispatch, "H", "caddcode", "DispatchList", "cAddCode", "A" }
        };

        // 编号族 {类型, H / B, 请求字段前缀（小写）, 表, 列名前缀, 最大编号}：请求字段 = 前缀 + 编号（1 起，不带前导零），
        // 列 = 列名前缀 + 编号，都是 A。表体扩展自定义项：销售发票只有蓝字参照发货单收，发货单只有参照订单生单收（SalePinsInv）。
        static readonly string[][] Families = new string[][] {
            new string[] { Order, "H", "cdefine", "SO_SOMain", "cDefine", "16" },
            new string[] { Order, "B", "cbdefine", "SO_SODetails_extradefine", "cbdefine", "99" },
            new string[] { Dispatch, "H", "cdefine", "DispatchList", "cDefine", "16" },
            new string[] { Dispatch, "H", "chdefine", "DispatchList_extradefine", "chdefine", "99" },
            new string[] { Dispatch, "B", "cbdefine", "DispatchLists_extradefine", "cbdefine", "99" },
            new string[] { Invoice, "B", "cbdefine", "SaleBillVouchs_extradefine", "cbdefine", "99" }
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

        // {类型, H / B, 请求字段, 表, 列, A / C}；不是核对字段返回 null。lower 须已是小写。
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
            for (int i = 0; i < Families.Length; i++)
            {
                string[] f = Families[i];
                int n = f[0] == kind && f[1] == side ? DefineNo(lower, f[2], MaxOf(f)) : 0;
                if (n > 0)
                {
                    string no = n.ToString(CultureInfo.InvariantCulture);
                    return new string[] { kind, side, f[2] + no, f[3], f[4] + no, "A" };
                }
            }
            return null;
        }

        // key = prefix + 编号（1 到 max，不带前导零）时返回编号，否则 0。key 须已是小写。
        internal static int DefineNo(string key, string prefix, int max)
        {
            if (key == null || key.Length <= prefix.Length || key.Length > prefix.Length + 2
                || !key.StartsWith(prefix, StringComparison.Ordinal) || key[prefix.Length] == '0')
            {
                return 0;
            }
            int n = 0;
            for (int i = prefix.Length; i < key.Length; i++)
            {
                if (key[i] < '0' || key[i] > '9')
                {
                    return 0;
                }
                n = n * 10 + (key[i] - '0');
            }
            return n <= max ? n : 0;
        }

        static int MaxOf(string[] family)
        {
            return int.Parse(family[5], NumberStyles.None, CultureInfo.InvariantCulture);
        }

        // 不写进 DOM 的核对字段：扩展自定义项，或模板（行集 schema）里没有的纯标注主表列。
        // 不是核对字段、或是只走组件的 C 字段返回 false（模板没有时照常 400）。
        internal static bool OffDom(string kind, string lower, bool head, bool inTemplate)
        {
            string[] row = Find(kind, head, lower);
            if (row == null || row[5] == "C")
            {
                return false;
            }
            return ExtraTable(row[3]) || !inTemplate;
        }

        // 主表列（load 补列用，组件 DOM 可能不带）：{表, 列, 请求字段}，编号族逐个展开。
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
            AddFamilyBase(list, kind, side);
            return list;
        }

        static void AddFamilyBase(List<string[]> list, string kind, string side)
        {
            for (int i = 0; i < Families.Length; i++)
            {
                string[] f = Families[i];
                if (f[0] != kind || f[1] != side || ExtraTable(f[3]))
                {
                    continue;
                }
                for (int n = 1; n <= MaxOf(f); n++)
                {
                    string no = n.ToString(CultureInfo.InvariantCulture);
                    list.Add(new string[] { f[3], f[4] + no, f[2] + no });
                }
            }
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
            if (table.StartsWith("SaleBillVouchs", StringComparison.OrdinalIgnoreCase))
            {
                return "AutoID";
            }
            if (table.StartsWith("DispatchLists", StringComparison.OrdinalIgnoreCase))
            {
                return "iDLsID";
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
