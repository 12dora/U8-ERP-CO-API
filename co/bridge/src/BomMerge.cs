using System;
using System.Collections.Generic;

namespace U8Co
{
    // 物料清单要写给 U8 的整套行：新增按缺省建行；修改把调用方的 add / update / delete 叠到库里的现有行上。
    // 修改走 UpdateByDiff=true，没送的行号会被 U8 删掉（实测 B4），所以这里算出的是修改后的全部行，每行带全标志。
    internal static class BomMerge
    {
        const int SeqStep = 10;

        // 新增：没给行号的行接在已用的最大行号后面，每次加 10（10、20…）。
        internal static List<BomRow> Build(BomAsk ask)
        {
            List<BomRow> rows = new List<BomRow>();
            AddRows(ask, rows, new HashSet<int>());
            Sort(rows);
            return rows;
        }

        static void Sort(List<BomRow> rows)
        {
            rows.Sort(delegate(BomRow x, BomRow y) { return x.Seq.CompareTo(y.Seq); });
        }

        // 修改：表头缺省取现值；update / delete 的行号必须在现有行里，add 的行号不能与现有行重复。
        internal static void Merge(BomAsk ask, Dictionary<string, object> head, List<Dictionary<string, object>> lines)
        {
            string oldEff = CoRows.Col(head, "eff_date");
            ask.InvCode = CoRows.Col(head, "inv_code");
            ask.Version = BomSql.Int(CoRows.Col(head, "version"));
            ask.PartId = CoRows.AsId(CoRows.Col(head, "part_id"));
            ask.VersionDesc = ask.HeadText("version_desc") ?? CoRows.Col(head, "version_desc");
            ask.EffDate = ask.HeadText("eff_date") ?? oldEff;
            object scrap;
            ask.ParentScrap = ask.Head.TryGetValue("parent_scrap", out scrap) ? (decimal)scrap
                : BomSql.Dec(CoRows.Col(head, "parent_scrap"));
            List<BomRow> rows = new List<BomRow>();
            HashSet<int> used = new HashSet<int>();
            for (int i = 0; i < lines.Count; i++)
            {
                BomRow row = BomRead.RowOf(lines[i]);
                rows.Add(row);
                used.Add(row.Seq);
            }
            ask.Existing = used;
            Edit(ask, rows);
            Shift(rows, oldEff, ask.EffDate);
            AddRows(ask, rows, used);
            if (rows.Count == 0)
            {
                throw BomSql.Bad("不能删除全部明细");
            }
            Sort(rows);
            ask.Rows = rows;
        }

        static void Edit(BomAsk ask, List<BomRow> rows)
        {
            for (int i = 0; i < ask.Changes.Count; i++)
            {
                BomChange change = ask.Changes[i];
                if (change.Op == "add")
                {
                    continue;
                }
                int at = rows.FindIndex(delegate(BomRow r) { return r.Seq == change.Seq; });
                if (at < 0)
                {
                    throw BomSql.Bad("明细行不存在：sort_seq " + BomReq.Num(change.Seq));
                }
                if (change.Op == "delete")
                {
                    rows.RemoveAt(at);
                }
                else
                {
                    rows[at].Apply(change.Fields);
                }
            }
        }

        // 版本生效日期改了：子件生效日期原来等于旧版本日期或早于新日期的，跟到新日期（子件日期不能早于版本日期）。
        static void Shift(List<BomRow> rows, string oldEff, string newEff)
        {
            if (oldEff == newEff)
            {
                return;
            }
            for (int i = 0; i < rows.Count; i++)
            {
                if (rows[i].EffBeg == oldEff || string.CompareOrdinal(rows[i].EffBeg, newEff) < 0)
                {
                    rows[i].EffBeg = newEff;
                }
            }
        }

        static void AddRows(BomAsk ask, List<BomRow> rows, HashSet<int> used)
        {
            int next = 0;
            foreach (int seq in used)
            {
                next = Math.Max(next, seq);
            }
            for (int i = 0; i < ask.Changes.Count; i++)
            {
                next = Math.Max(next, ask.Changes[i].Op == "add" ? ask.Changes[i].Seq : 0);
            }
            for (int i = 0; i < ask.Changes.Count; i++)
            {
                BomChange change = ask.Changes[i];
                if (change.Op != "add")
                {
                    continue;
                }
                if (change.Seq > 0 && used.Contains(change.Seq))
                {
                    throw BomSql.Bad("sort_seq 已存在：" + BomReq.Num(change.Seq));
                }
                BomRow row = new BomRow();
                row.EffBeg = ask.EffDate;
                row.Apply(change.Fields);
                if (change.Seq > 0)
                {
                    row.Seq = change.Seq;
                }
                else
                {
                    next += SeqStep;
                    row.Seq = next;
                }
                rows.Add(row);
            }
        }
    }
}
