using System;
using System.Collections.Generic;

namespace U8Co
{
    // 历史发货单表头修正的一个字段：请求值、expect、读到的当前值。addr_code 允许 ""（清空，写 NULL），
    // person_code 不能为空。比较一律用 HeadFixPlan.Same（NULL 与 "" 相等，去两端空格，不分大小写）。
    internal sealed class HeadFixField
    {
        public string Key;
        public string Column;
        public string New = "";
        public string Expect = "";
        public string Old = "";

        public bool Changes
        {
            get { return !HeadFixPlan.Same(Old, New); }
        }

        public HeadFixField Copy()
        {
            HeadFixField f = new HeadFixField();
            f.Key = Key;
            f.Column = Column;
            f.New = New;
            f.Expect = Expect;
            f.Old = Old;
            return f;
        }
    }

    // 请求：发货单 id 和要改的字段（顺序固定：addr_code、person_code）。
    // HasUndo：请求带了 undo（回滚模式，只动 Undo 里列出的下游行）；Undo：字段 → 表 → 行 id（Ar_Detail 为 Auto_ID）。
    internal sealed class HeadFixAsk
    {
        public int Id;
        public bool HasUndo;
        public Dictionary<string, Dictionary<string, List<int>>> Undo = new Dictionary<string, Dictionary<string, List<int>>>();
        public List<HeadFixField> Fields = new List<HeadFixField>();

        public HeadFixField Find(string key)
        {
            for (int i = 0; i < Fields.Count; i++)
            {
                if (Fields[i].Key == key)
                {
                    return Fields[i];
                }
            }
            return null;
        }

        // 真正会变的字段（请求值与当前值不同）。
        public List<HeadFixField> Changing()
        {
            List<HeadFixField> list = new List<HeadFixField>();
            for (int i = 0; i < Fields.Count; i++)
            {
                if (Fields[i].Changes)
                {
                    list.Add(Fields[i]);
                }
            }
            return list;
        }
    }

    // 一张下游单据（销售出库单 rdrecord32 或销售发票 SaleBillVouch）的事实，由 SQL 收集，供 Decide 判断。
    // Foreign：出库单含非本发货单的行；发票含无来源（iDLsID 为空或对不上发货单）的行。
    // Others：发票的其他来源发货单的当前值（键 → 值）；出库单不用。ArIds：发票在 Ar_Detail 里 cPerson 等于改前业务员的行（Auto_ID）。
    internal sealed class HeadFixDoc
    {
        public string Table;
        public int Id;
        public string Code = "";
        public string VouchType = "";
        public bool Foreign;
        public Dictionary<string, string> Values = new Dictionary<string, string>();
        public List<Dictionary<string, string>> Others = new List<Dictionary<string, string>>();
        public List<int> ArIds = new List<int>();
    }

    // Ar_Detail 的一行（回滚模式按 Auto_ID 取）：所属发票 (VouchType, Code) 和当前业务员。
    internal sealed class HeadFixArRow
    {
        public int AutoId;
        public string VouchType = "";
        public string Code = "";
        public string Person = "";
    }

    // 下游一行的处理结论（响应 downstream 的一项）。Set 是要写的字段；Skipped 是被跳过的字段和原因。
    internal sealed class HeadFixRow
    {
        public const string Updated = "updated";
        public const string SkippedAction = "skipped";
        public const string ArTable = "Ar_Detail";
        public string Table;
        public int Id;
        public string Code = "";
        public string VouchType = "";
        public string Action;
        public string Reason;
        public List<int> AutoIds = new List<int>();
        public List<HeadFixField> Set = new List<HeadFixField>();
        public Dictionary<string, string> Skipped = new Dictionary<string, string>();
        public Dictionary<string, string> Value = new Dictionary<string, string>();

        public int Rows
        {
            get { return AutoIds.Count; }
        }

        public bool IsAr
        {
            get { return Table == ArTable; }
        }

        public Dictionary<string, object> Map()
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            map["table"] = Table;
            if (IsAr)
            {
                map["invoice"] = Code;
                map["rows"] = Rows;
                map["action"] = Action;
                return map;
            }
            map["id"] = Id;
            map["code"] = Code;
            map["action"] = Action;
            if (Set.Count > 0)
            {
                map["fields"] = Keys(Set);
            }
            if (Skipped.Count > 0)
            {
                AddSkipped(map);
            }
            return map;
        }

        // 全部跳过时 reason / value 写在行上；部分更新时写 skipped_fields（键 → 原因）。
        void AddSkipped(Dictionary<string, object> map)
        {
            if (Set.Count == 0)
            {
                map["reason"] = Reason;
                Dictionary<string, object> value = new Dictionary<string, object>();
                foreach (KeyValuePair<string, string> pair in Value)
                {
                    value[pair.Key] = pair.Value;
                }
                map["value"] = value;
                return;
            }
            Dictionary<string, object> skipped = new Dictionary<string, object>();
            foreach (KeyValuePair<string, string> pair in Skipped)
            {
                skipped[pair.Key] = pair.Value;
            }
            map["skipped_fields"] = skipped;
        }

        static List<object> Keys(List<HeadFixField> fields)
        {
            List<object> keys = new List<object>();
            for (int i = 0; i < fields.Count; i++)
            {
                keys.Add(fields[i].Key);
            }
            return keys;
        }
    }

    // 下游传播的纯判断（不连库，自检覆盖）和 UPDATE 片段生成。
    internal static class HeadFixPlan
    {
        public const string Shared = "shared";
        public const string Differs = "differs";
        // 下游单据已经是新值：不改、不算错误，也不进 undo（回滚不能动它）。
        public const string AlreadyNew = "already_new";

        // NULL 与 "" 相等；去两端空格；不分大小写（U8 编码查找不分大小写）。
        public static bool Same(string a, string b)
        {
            return string.Equals((a ?? "").Trim(), (b ?? "").Trim(), StringComparison.OrdinalIgnoreCase);
        }

        static string Get(Dictionary<string, string> values, string key)
        {
            string value;
            return values != null && values.TryGetValue(key, out value) ? value : "";
        }

        // 某一字段能不能跟着改：返回 null 表示可以，否则是跳过原因。
        //   shared：单据有别的来源（出库单含别的发货单的行；发票含无来源行，或别的来源发货单还没改成新值——
        //           改完之后这张单据的所有来源发货单必须一致）；
        //   already_new：单据已经是新值（不改，也不进 undo）；
        //   differs：单据当前值不是发货单的改前值（已被改过，不覆盖）。
        public static string Reason(HeadFixDoc doc, HeadFixField field)
        {
            if (doc.Foreign)
            {
                return Shared;
            }
            for (int i = 0; i < doc.Others.Count; i++)
            {
                if (!Same(Get(doc.Others[i], field.Key), field.New))
                {
                    return Shared;
                }
            }
            string current = Get(doc.Values, field.Key);
            if (Same(current, field.New))
            {
                return AlreadyNew;
            }
            return Same(current, field.Old) ? null : Differs;
        }

        // 一张下游单据的结论：逐字段判断；至少一个字段可改为 updated，否则 skipped。
        public static HeadFixRow Decide(HeadFixDoc doc, List<HeadFixField> changing)
        {
            HeadFixRow row = new HeadFixRow();
            row.Table = doc.Table;
            row.Id = doc.Id;
            row.Code = doc.Code;
            row.VouchType = doc.VouchType;
            for (int i = 0; i < changing.Count; i++)
            {
                string reason = Reason(doc, changing[i]);
                if (reason == null)
                {
                    row.Set.Add(changing[i]);
                    continue;
                }
                row.Skipped[changing[i].Key] = reason;
                row.Value[changing[i].Key] = Get(doc.Values, changing[i].Key);
                row.Reason = row.Reason ?? reason;
            }
            row.Action = row.Set.Count > 0 ? HeadFixRow.Updated : HeadFixRow.SkippedAction;
            return row;
        }

        // 全部下游单据的结论；发票的业务员改了时，紧跟一行 Ar_Detail（行数取收集时的计数）。
        public static List<HeadFixRow> DecideAll(List<HeadFixDoc> docs, List<HeadFixField> changing)
        {
            List<HeadFixRow> rows = new List<HeadFixRow>();
            for (int i = 0; i < docs.Count; i++)
            {
                HeadFixRow row = Decide(docs[i], changing);
                rows.Add(row);
                HeadFixField person = PersonOf(row);
                if (person != null && docs[i].Table == "SaleBillVouch")
                {
                    rows.Add(ArRow(docs[i], row, person));
                }
            }
            return rows;
        }

        static HeadFixField PersonOf(HeadFixRow row)
        {
            for (int i = 0; i < row.Set.Count; i++)
            {
                if (row.Set[i].Key == VoucherHeadFix.PersonKey)
                {
                    return row.Set[i];
                }
            }
            return null;
        }

        static HeadFixRow ArRow(HeadFixDoc doc, HeadFixRow invoice, HeadFixField person)
        {
            HeadFixRow ar = new HeadFixRow();
            ar.Table = HeadFixRow.ArTable;
            ar.Code = doc.Code;
            ar.VouchType = doc.VouchType;
            ar.AutoIds.AddRange(doc.ArIds);
            ar.Set.Add(person);
            ar.Action = doc.ArIds.Count > 0 ? HeadFixRow.Updated : "none";
            return ar;
        }

        // UPDATE 的 SET 片段，列名来自 HeadFixField.Column 的固定常量；空串写 NULL（清空），其余绑定参数。
        public static string SetClause(List<HeadFixField> fields, List<object> args)
        {
            List<string> parts = new List<string>();
            for (int i = 0; i < fields.Count; i++)
            {
                string value = (fields[i].New ?? "").Trim();
                if (value.Length == 0)
                {
                    parts.Add(fields[i].Column + "=NULL");
                    continue;
                }
                parts.Add(fields[i].Column + "=?");
                args.Add(value);
            }
            return string.Join(", ", parts.ToArray());
        }

        // 把一组请求字段倒过来（回滚调用）：新值与 expect 互换。
        public static List<HeadFixField> Reverse(List<HeadFixField> fields)
        {
            List<HeadFixField> list = new List<HeadFixField>();
            for (int i = 0; i < fields.Count; i++)
            {
                HeadFixField f = fields[i].Copy();
                f.New = fields[i].Old;
                f.Expect = fields[i].New;
                f.Old = fields[i].New;
                list.Add(f);
            }
            return list;
        }
    }
}
