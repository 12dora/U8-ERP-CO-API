using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 生产订单子件的替代料（mom_moallocatesub，按 AllocateId 挂在 mom_moallocate 下；PartId 指 bas_part）。
    // 只有优先级 Sequence、替代存货 PartId、替代系数 Factor（不是数量）、生效 / 失效日期、是否替换 ReplaceFlag、Define22–37，没有数量列。
    // vouchers/load 把每个子件的替代料挂在 substitutes 下（Attach）；生产订单修改调用前读快照、修改后写回（MoUpdateSubs）。
    // 表名、列名是常量；值都走 ADO 参数。读出一律转成文本（NULL 读成 MoUpdateCols.NullMark，文本不去首尾空白），写回时按列的
    // 类型转回去：日期按 style 121、float 按 style 3 的 17 位有效数字文本（逐位还原），不受会话的语言和日期格式影响。
    // float 另读一份 style 2 的文本（列名加 _t）只用于对外显示。对外形状由 SubstituteRows 统一（与物料清单替代料相同）。
    internal static class MoSubs
    {
        const int Chunk = 100;
        const int RowsMax = 100000;
        // 列名、类型：n 数值，d 日期时间，f float，b bit，i int，s 文本。顺序即 InsertSql 里值的顺序。
        static readonly string[][] Cols = new string[][]
        {
            new string[] { "Factor", "n" }, new string[] { "EffBegDate", "d" }, new string[] { "EffEndDate", "d" },
            new string[] { "ReplaceFlag", "b" },
            new string[] { "Define22", "s" }, new string[] { "Define23", "s" }, new string[] { "Define24", "s" },
            new string[] { "Define25", "s" }, new string[] { "Define26", "f" }, new string[] { "Define27", "f" },
            new string[] { "Define28", "s" }, new string[] { "Define29", "s" }, new string[] { "Define30", "s" },
            new string[] { "Define31", "s" }, new string[] { "Define32", "s" }, new string[] { "Define33", "s" },
            new string[] { "Define34", "i" }, new string[] { "Define35", "i" }, new string[] { "Define36", "d" },
            new string[] { "Define37", "d" }
        };
        public const string Key = "substitutes";
        // float 列的显示文本别名后缀。
        internal const string ShownSuffix = "_t";
        // 字段权限：子件列被拒绝时替代料的对应键一并置空（PermColumnMap 按生产订单绑定到遮蔽范围）。
        // 替代存货对应子件存货 InvCode；表体自定义项按同号对应 Define22–37。系数、日期、是否替换在子件上没有同类列，不遮。
        internal static readonly string[][] MaskAliases = BuildAliases();

        static string[][] BuildAliases()
        {
            List<string[]> list = new List<string[]>();
            list.Add(new string[] { "InvCode", "inv_code" });
            for (int i = 0; i < SubstituteRows.Defines.Length; i++)
            {
                string n = SubstituteRows.Defines[i][0];
                list.Add(new string[] { "Define" + n, "define" + n });
            }
            return list.ToArray();
        }

        // 这些子件的全部替代料（每批 Chunk 个 AllocateId）。locked：写回事务里加 UPDLOCK, HOLDLOCK。
        public static List<MoSubRow> Read(object conn, List<int> allocateIds, bool locked)
        {
            List<MoSubRow> all = new List<MoSubRow>();
            for (int start = 0; start < allocateIds.Count; start += Chunk)
            {
                int n = Math.Min(Chunk, allocateIds.Count - start);
                object[] args = new object[n];
                for (int i = 0; i < n; i++)
                {
                    args[i] = allocateIds[start + i];
                }
                List<Dictionary<string, object>> rows = Rows.Query(conn, ReadSql(n, locked), args, RowsMax);
                for (int i = 0; i < rows.Count; i++)
                {
                    all.Add(MoSubRow.Of(rows[i]));
                }
            }
            return all;
        }

        internal static string ReadSql(int n, bool locked)
        {
            StringBuilder sb = new StringBuilder("select convert(varchar(20), s.AllocateId) as AllocateId,"
                + " convert(varchar(10), s.[Sequence]) as Seq, convert(varchar(20), s.PartId) as PartId,"
                + " isnull(p.InvCode, N'') as InvCode");
            for (int i = 0; i < Cols.Length; i++)
            {
                sb.Append(", ").Append(SelectExpr(Cols[i][0], Cols[i][1])).Append(" as ").Append(Cols[i][0]);
                if (Cols[i][1] == "f")
                {
                    sb.Append(", isnull(convert(varchar(40), s.").Append(Cols[i][0]).Append(", 2), '").Append(MoUpdateCols.NullMark)
                        .Append("') as ").Append(Cols[i][0]).Append(ShownSuffix);
                }
            }
            sb.Append(" from mom_moallocatesub s").Append(locked ? " with (updlock, holdlock)" : "")
                .Append(" left join bas_part p on p.PartId=s.PartId where s.AllocateId in (");
            for (int i = 0; i < n; i++)
            {
                sb.Append(i == 0 ? "?" : ",?");
            }
            return sb.Append(") order by s.AllocateId, s.[Sequence], s.PartId").ToString();
        }

        static string SelectExpr(string col, string type)
        {
            if (type == "d")
            {
                return "isnull(convert(varchar(23), s." + col + ", 121), '" + MoUpdateCols.NullMark + "')";
            }
            if (type == "f")
            {
                return "isnull(convert(varchar(30), s." + col + ", 3), '" + MoUpdateCols.NullMark + "')";
            }
            return "isnull(convert(nvarchar(4000), s." + col + "), N'" + MoUpdateCols.NullMark + "')";
        }

        // 插入一行替代料：主键 newid()，参数依次为 AllocateId、Sequence、PartId、Cols 各列（NullMark → NULL）。
        internal static string InsertSql()
        {
            StringBuilder names = new StringBuilder("insert into mom_moallocatesub (MoallocateSubId, AllocateId, [Sequence], PartId");
            StringBuilder values = new StringBuilder(") values (newid(), ?, ?, ?");
            for (int i = 0; i < Cols.Length; i++)
            {
                names.Append(", ").Append(Cols[i][0]);
                values.Append(", ").Append(ValueExpr(Cols[i][1]));
            }
            return names.Append(values).Append(")").ToString();
        }

        static string ValueExpr(string type)
        {
            if (type == "d")
            {
                return "convert(datetime, ?, 121)";
            }
            return type == "f" ? "convert(float, ?)" : "?";
        }

        internal static bool IsFloat(string col)
        {
            for (int i = 0; i < Cols.Length; i++)
            {
                if (Cols[i][0] == col)
                {
                    return Cols[i][1] == "f";
                }
            }
            return false;
        }

        internal static object[] InsertArgs(MoSubRow sub, int allocateId)
        {
            List<object> args = new List<object>();
            args.Add(allocateId);
            args.Add(sub.Sequence);
            args.Add(sub.PartId);
            for (int i = 0; i < Cols.Length; i++)
            {
                string value = sub.Cell(Cols[i][0]);
                args.Add(value == MoUpdateCols.NullMark ? null : value);
            }
            return args.ToArray();
        }

        // 删除一个子件的全部替代料（调用方整组替换、清空时）。
        internal const string DeleteSql = "delete from mom_moallocatesub where AllocateId=?";

        // 比较用的键：优先级、替代存货、各列原文（NULL 也算一种值）。数值列按值比（系数的小数位、float 的写法不同不算不同）。
        internal static string KeyOf(MoSubRow sub)
        {
            StringBuilder sb = new StringBuilder();
            sb.Append(sub.Sequence.ToString(CultureInfo.InvariantCulture)).Append('|')
                .Append(sub.PartId.ToString(CultureInfo.InvariantCulture));
            for (int i = 0; i < Cols.Length; i++)
            {
                sb.Append('|').Append(Canon(sub.Cell(Cols[i][0]), Cols[i][1]));
            }
            return sb.ToString();
        }

        // n：decimal 去掉末尾的 0；f：double 的 64 位原样（十六进制）。解析不了的保持原文。
        internal static string Canon(string cell, string type)
        {
            if (type == "n")
            {
                decimal d;
                if (decimal.TryParse(cell, NumberStyles.Number, CultureInfo.InvariantCulture, out d))
                {
                    return (d / 1.000000000000000000000000000000000m).ToString(CultureInfo.InvariantCulture);
                }
            }
            if (type == "f")
            {
                double f;
                if (double.TryParse(cell, NumberStyles.Float, CultureInfo.InvariantCulture, out f))
                {
                    return BitConverter.DoubleToInt64Bits(f).ToString("X16", CultureInfo.InvariantCulture);
                }
            }
            return cell;
        }

        // 一组替代料的键，排好序（多重集合比较用）。
        internal static List<string> Keys(List<MoSubRow> subs)
        {
            List<string> keys = new List<string>();
            for (int i = 0; subs != null && i < subs.Count; i++)
            {
                keys.Add(KeyOf(subs[i]));
            }
            keys.Sort(StringComparer.Ordinal);
            return keys;
        }

        internal static bool SameSet(List<MoSubRow> a, List<MoSubRow> b)
        {
            List<string> x = Keys(a);
            List<string> y = Keys(b);
            if (x.Count != y.Count)
            {
                return false;
            }
            for (int i = 0; i < x.Count; i++)
            {
                if (x[i] != y[i])
                {
                    return false;
                }
            }
            return true;
        }

        // AllocateId → 该子件的替代料（顺序同读出）。
        internal static Dictionary<int, List<MoSubRow>> ByAlloc(List<MoSubRow> subs)
        {
            Dictionary<int, List<MoSubRow>> map = new Dictionary<int, List<MoSubRow>>();
            for (int i = 0; i < subs.Count; i++)
            {
                List<MoSubRow> list;
                if (!map.TryGetValue(subs[i].AllocateId, out list))
                {
                    list = new List<MoSubRow>();
                    map[subs[i].AllocateId] = list;
                }
                list.Add(subs[i]);
            }
            return map;
        }

        // vouchers/load：每个子件行（SELECT * FROM mom_moallocate）加 substitutes（没有替代料为空数组）。
        public static void Attach(object conn, List<Dictionary<string, object>> allocs)
        {
            List<int> ids = new List<int>();
            for (int i = 0; i < allocs.Count; i++)
            {
                int id = CoRows.AsId(CoRows.Col(allocs[i], "AllocateId"));
                if (id > 0)
                {
                    ids.Add(id);
                }
            }
            Dictionary<int, List<MoSubRow>> map = ByAlloc(ids.Count == 0 ? new List<MoSubRow>() : Read(conn, ids, false));
            for (int i = 0; i < allocs.Count; i++)
            {
                List<MoSubRow> subs;
                map.TryGetValue(CoRows.AsId(CoRows.Col(allocs[i], "AllocateId")), out subs);
                List<object> items = new List<object>();
                for (int k = 0; subs != null && k < subs.Count; k++)
                {
                    items.Add(Output(subs[k]));
                }
                allocs[i][Key] = items;
            }
        }

        // 对外的一项（SubstituteRows：与物料清单替代料同一形状）。
        internal static Dictionary<string, object> Output(MoSubRow sub)
        {
            Dictionary<string, object> item = SubstituteRows.Item(sub.Sequence, sub.InvCode, new string[]
            {
                sub.Cell("Factor"), sub.Cell("ReplaceFlag"), sub.Cell("EffBegDate"), sub.Cell("EffEndDate")
            });
            SubstituteRows.AddDefines(item, n => sub.Shown("Define" + n));
            return item;
        }

        // 审计事件用：原文（NullMark 原样），写回失败时据此手工恢复。
        internal static Dictionary<string, object> Raw(MoSubRow sub)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["sequence"] = sub.Sequence;
            item["part_id"] = sub.PartId;
            item["inv_code"] = sub.InvCode;
            for (int i = 0; i < Cols.Length; i++)
            {
                item[Cols[i][0]] = sub.Cell(Cols[i][0]);
            }
            return item;
        }

        internal static string[] Names()
        {
            string[] names = new string[Cols.Length];
            for (int i = 0; i < Cols.Length; i++)
            {
                names[i] = Cols[i][0];
            }
            return names;
        }
    }

    // 一行替代料（原文）。
    internal sealed class MoSubRow
    {
        public int AllocateId;
        public int Sequence;
        public int PartId;
        public string InvCode = "";
        // 列名 → 原文（NULL 为 MoUpdateCols.NullMark；float 列是 style 3 的 17 位文本，另有 <列>_t 的显示文本）。
        public Dictionary<string, string> Cells = new Dictionary<string, string>(StringComparer.Ordinal);

        public static MoSubRow Of(Dictionary<string, object> row)
        {
            MoSubRow sub = new MoSubRow();
            sub.AllocateId = CoRows.AsId(CoRows.Col(row, "AllocateId"));
            sub.Sequence = MoUpdateSql.Int(CoRows.Col(row, "Seq"));
            sub.PartId = CoRows.AsId(CoRows.Col(row, "PartId"));
            sub.InvCode = CoRows.Col(row, "InvCode");
            string[] names = MoSubs.Names();
            for (int i = 0; i < names.Length; i++)
            {
                sub.Cells[names[i]] = Untrimmed(row, names[i]);
                if (MoSubs.IsFloat(names[i]))
                {
                    sub.Cells[names[i] + MoSubs.ShownSuffix] = Untrimmed(row, names[i] + MoSubs.ShownSuffix);
                }
            }
            return sub;
        }

        // 不去首尾空白：文本型自定义项原样写回。
        static string Untrimmed(Dictionary<string, object> row, string name)
        {
            object value;
            return row.TryGetValue(name, out value) ? Values.Text(value) : "";
        }

        // 对外显示的原文：float 列取 style 2 的文本，其余同 Cell。
        public string Shown(string col)
        {
            string value;
            if (MoSubs.IsFloat(col) && Cells.TryGetValue(col + MoSubs.ShownSuffix, out value) && value != null)
            {
                return value;
            }
            return Cell(col);
        }

        public string Cell(string col)
        {
            string value;
            return Cells.TryGetValue(col, out value) && value != null ? value : MoUpdateCols.NullMark;
        }
    }
}
