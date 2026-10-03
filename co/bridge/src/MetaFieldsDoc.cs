using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // meta/fields 的单据部分：字段名 = meta kinds[].writable 同一来源（MetaWritable / MetaGenerate，含展开的
    // cdefine / cfree 区间；line_control 列另放 line_control），标题、类型、必输、枚举取本账套的单据模板（MetaFieldsVt 选 VT）。
    internal static class MetaFieldsDoc
    {
        // line_control 列（桥自己的定位 / 生单字段，不在 U8 模板里）：固定标题和类型。
        static readonly string[][] Control = new string[][]
        {
            new string[] { "op", "行操作", "string" },
            new string[] { "line_id", "明细行 id", "int" },
            new string[] { "source_line_id", "来源明细行 id", "int" },
            new string[] { "quantity", "数量", "decimal" },
            new string[] { "sort_seq", "子件行号", "int" }
        };

        // 生产订单、物料清单的请求字段名是桥自己起的（MoCreateReq、BomReq），模板字段名要换算：
        // 去下划线；表体加 D 前缀；defineNN → Define_NN；下面几个不按规则。
        static readonly string[][] Alias = new string[][]
        {
            new string[] { "production_order", "B", "mo_type", "DMoTypeCode" },
            new string[] { "production_order", "B", "dept_code", "DMDeptCode" },
            new string[] { "bom", "T", "eff_date", "VersionEffDate" }
        };

        internal static Dictionary<string, object> Build(WorkContext ctx, MetaFieldsAsk ask)
        {
            VoucherKind kind = ask.Kind;
            Dictionary<string, object> spec = SpecOf(ask);
            VtPick pick = MetaFieldsVt.Pick(ctx.Conn, kind, ctx.Item.Operator);
            Dictionary<string, TplRow> head = MetaFieldsTpl.Index(pick.Rows, false);
            Dictionary<string, TplRow> body = MetaFieldsTpl.Index(pick.Rows, true);
            string[] headNames = Names(Requests.Field(spec, "head"), null);
            string[] lineNames = Names(Requests.Field(spec, "lines"), null);
            string[] control = Requests.Field(spec, "lines") == null ? new string[0]
                : (Requests.Field(spec, "line_control") as string[] ?? new string[0]);
            Dictionary<string, List<object>> enums = MetaFieldsTpl.Enums(ctx.Conn, Used(kind, headNames, head, lineNames, body));
            Dictionary<string, object> result = new Dictionary<string, object>();
            result["ok"] = true;
            result["type"] = kind.Name;
            result["op"] = ask.Op;
            if (ask.Source != null)
            {
                result["source"] = ask.Source.Name;
            }
            result["vt_id"] = pick.VtId > 0 ? (object)pick.VtId : null;
            result["card"] = pick.Card;
            result["vt_source"] = pick.Source;
            string[][] required = RequiredOf(spec);
            result["head"] = Side(kind, headNames, head, false, required[0], enums);
            result["lines"] = Side(kind, lineNames, body, true, required[1], enums);
            // 定位 / 生单列（meta 的 line_control）单列，不混进 lines；固定标题。
            result["line_control"] = ControlSide(control, required[1]);
            result["fields_revision"] = MetaFieldsRoute.Revision(result["head"], result["lines"], result["line_control"]);
            return result;
        }

        // 与 meta 的 kinds[].writable 完全同源。create / update 为 null（不支持）时 400 op。
        static Dictionary<string, object> SpecOf(MetaFieldsAsk ask)
        {
            Dictionary<string, object> spec;
            if (ask.Op == "generate")
            {
                spec = MetaGenerate.Of(ask.Kind.Name, ask.Source.Name);
            }
            else
            {
                spec = MetaWritable.Of(ask.Kind)[ask.Op] as Dictionary<string, object>;
            }
            if (spec == null)
            {
                throw BridgeException.BadField("op", "该单据类型不支持 " + ask.Op);
            }
            return spec;
        }

        // head / lines 的 {exact, spans} 展开成名字：先 exact，再各区间（prefix + 编号）；给了 extra 就接在最后，去重。
        internal static string[] Names(object side, string[] control)
        {
            List<string> names = new List<string>();
            Dictionary<string, object> d = side as Dictionary<string, object>;
            if (d != null)
            {
                AddAll(names, Requests.Field(d, "exact") as string[]);
                object[] spans = Requests.Field(d, "spans") as object[];
                for (int i = 0; spans != null && i < spans.Length; i++)
                {
                    AddSpan(names, spans[i] as Dictionary<string, object>);
                }
            }
            if (d != null)
            {
                AddAll(names, control);
            }
            return names.ToArray();
        }

        static void AddSpan(List<string> names, Dictionary<string, object> span)
        {
            if (span == null)
            {
                return;
            }
            string prefix = span["prefix"] as string;
            int from = Convert.ToInt32(span["from"], CultureInfo.InvariantCulture);
            int to = Convert.ToInt32(span["to"], CultureInfo.InvariantCulture);
            for (int n = from; n <= to; n++)
            {
                Add(names, prefix + n.ToString(CultureInfo.InvariantCulture));
            }
        }

        static void AddAll(List<string> names, string[] list)
        {
            for (int i = 0; list != null && i < list.Length; i++)
            {
                Add(names, list[i]);
            }
        }

        static void Add(List<string> names, string name)
        {
            if (!string.IsNullOrEmpty(name) && !names.Contains(name))
            {
                names.Add(name);
            }
        }

        static string[][] RequiredOf(Dictionary<string, object> spec)
        {
            Dictionary<string, object> req = Requests.Field(spec, "required") as Dictionary<string, object>;
            string[] head = Requests.Field(req, "head") as string[];
            string[] lines = Requests.Field(req, "lines") as string[];
            return new string[][] { head ?? new string[0], lines ?? new string[0] };
        }

        static List<TplRow> Used(VoucherKind kind, string[] headNames, Dictionary<string, TplRow> head,
            string[] lineNames, Dictionary<string, TplRow> body)
        {
            List<TplRow> used = new List<TplRow>();
            for (int i = 0; i < headNames.Length; i++)
            {
                AddRow(used, Find(head, TplName(kind.Name, headNames[i], false)));
            }
            for (int i = 0; i < lineNames.Length; i++)
            {
                AddRow(used, Find(body, TplName(kind.Name, lineNames[i], true)));
            }
            return used;
        }

        static void AddRow(List<TplRow> used, TplRow row)
        {
            if (row != null)
            {
                used.Add(row);
            }
        }

        static List<object> Side(VoucherKind kind, string[] names, Dictionary<string, TplRow> rows, bool body,
            string[] required, Dictionary<string, List<object>> enums)
        {
            List<object> list = new List<object>();
            for (int i = 0; i < names.Length; i++)
            {
                TplRow row = Find(rows, TplName(kind.Name, names[i], body));
                bool must = Array.IndexOf(required, names[i]) >= 0;
                List<object> values = null;
                if (row != null && row.IsEnum && row.EnumType.Length > 0)
                {
                    enums.TryGetValue(row.EnumType, out values);
                }
                list.Add(MetaFieldsTpl.Entry(names[i], row, must, values));
            }
            return list;
        }

        // line_control 的条目：固定标题和类型（没登记的列 label、type 为 null）；required 照该操作的 required.lines。
        internal static List<object> ControlSide(string[] control, string[] required)
        {
            List<object> list = new List<object>();
            for (int i = 0; i < control.Length; i++)
            {
                string[] row = ControlOf(control[i]);
                Dictionary<string, object> d = new Dictionary<string, object>();
                d["name"] = control[i];
                d["label"] = row == null ? null : row[1];
                d["type"] = row == null ? null : row[2];
                d["required"] = Array.IndexOf(required, control[i]) >= 0;
                list.Add(d);
            }
            return list;
        }

        static string[] ControlOf(string name)
        {
            for (int i = 0; i < Control.Length; i++)
            {
                if (Control[i][0] == name)
                {
                    return Control[i];
                }
            }
            return null;
        }

        static TplRow Find(Dictionary<string, TplRow> rows, string name)
        {
            TplRow row;
            return rows.TryGetValue(name, out row) ? row : null;
        }

        // 请求字段名 → 模板字段名（比较不分大小写）。只有生产订单、物料清单要换算，其余类型的请求字段名就是列名。
        internal static string TplName(string kind, string name, bool body)
        {
            if (kind != "production_order" && kind != BomRoutes.KindName)
            {
                return name;
            }
            string alias = AliasOf(kind, body ? "B" : "T", name);
            if (alias != null)
            {
                return alias;
            }
            string flat = name.StartsWith("define", StringComparison.Ordinal) && name.Length > 6 && char.IsDigit(name[6])
                ? "Define_" + name.Substring(6)
                : name.Replace("_", "");
            return body ? "D" + flat : flat;
        }

        static string AliasOf(string kind, string section, string name)
        {
            for (int i = 0; i < Alias.Length; i++)
            {
                if (Alias[i][0] == kind && Alias[i][1] == section && Alias[i][2] == name)
                {
                    return Alias[i][3];
                }
            }
            return null;
        }
    }
}
