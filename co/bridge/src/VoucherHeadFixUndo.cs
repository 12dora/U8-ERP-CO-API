using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // 回滚模式的数据源：按 id 取下游行。取不到（不存在或不属于这张发货单）返回 null。
    // 出库单、发票必须经明细行的 iDLsID 关联到这张发货单；Ar_Detail 行必须属于关联到这张发货单的某张发票
    // （cVouchType = SaleBillVouch.cVouchType 且 cVouchID = SaleBillVouch.cSBVCode）。
    internal interface IHeadFixSource
    {
        HeadFixDoc Doc(string table, int id);
        HeadFixArRow Ar(int autoId);
    }

    // 历史发货单表头修正的 undo：正向响应里列出「实际写过的行」，回滚请求带回同样的结构，只动这些行。
    // 形状：{"addr_code": {"rdrecord32": [id…], "SaleBillVouch": [id…]},
    //        "person_code": {"rdrecord32": […], "SaleBillVouch": […], "Ar_Detail": [Auto_ID…]}}。
    // undo 必须是先前某次正向响应里返回的、未经修改的 undo（原样带回）：它被当作「正向写过哪些行」的凭据，
    // 手工拼接或改动的 undo 只能靠逐行的 expect / 归属检查兜底。带 undo 的调用在审计 detail 里标 mode=rollback。
    // 这样回滚不依赖「现在再发现一遍」：被合并发票的兄弟单据已是新值、下游已经是新值的行、Ar_Detail 里本来就是新业务员的行，
    // 都不会被回滚误改。键必须是 fields 的子集；表名固定；Ar_Detail 只属于 person_code；每个列表最多 500 个、不重复。
    internal static class HeadFixUndo
    {
        public const string OutTable = "rdrecord32";
        public const string InvTable = "SaleBillVouch";
        public const int MaxIds = 500;
        public const string Foreign = "undo_foreign";

        // 登录前：解析请求体里的 undo（缺省 = 正向模式）。
        public static void Parse(Dictionary<string, object> body, HeadFixAsk ask)
        {
            if (body == null || !body.ContainsKey("undo"))
            {
                return;
            }
            Dictionary<string, object> undo = body["undo"] as Dictionary<string, object>;
            if (undo == null)
            {
                throw ArcReq.Bad("undo 必须是对象", "undo");
            }
            ask.HasUndo = true;
            foreach (KeyValuePair<string, object> pair in undo)
            {
                if (ask.Find(pair.Key) == null)
                {
                    throw ArcReq.Bad("undo 里的 " + pair.Key + " 不在 fields 中或不支持", FieldPath.Clean("undo." + pair.Key));
                }
                ask.Undo[pair.Key] = Tables(pair.Value, pair.Key);
            }
        }

        static Dictionary<string, List<int>> Tables(object raw, string key)
        {
            Dictionary<string, object> map = raw as Dictionary<string, object>;
            if (map == null)
            {
                throw ArcReq.Bad("undo." + key + " 必须是对象", FieldPath.Clean("undo." + key));
            }
            Dictionary<string, List<int>> tables = new Dictionary<string, List<int>>();
            foreach (KeyValuePair<string, object> pair in map)
            {
                string at = "undo." + key + "." + pair.Key;
                bool known = pair.Key == OutTable || pair.Key == InvTable
                    || (pair.Key == HeadFixRow.ArTable && key == VoucherHeadFix.PersonKey);
                if (!known)
                {
                    throw ArcReq.Bad("undo 里不支持 " + key + " 的表 " + pair.Key, FieldPath.Clean(at));
                }
                tables[pair.Key] = Ids(pair.Value, at);
            }
            return tables;
        }

        static List<int> Ids(object raw, string at)
        {
            IList list = raw as IList;
            if (list == null || raw is string)
            {
                throw ArcReq.Bad(at + " 必须是数组", FieldPath.Clean(at));
            }
            if (list.Count > MaxIds)
            {
                throw ArcReq.Bad(at + " 最多 " + MaxIds.ToString() + " 个", FieldPath.Clean(at));
            }
            List<int> ids = new List<int>();
            HashSet<int> seen = new HashSet<int>();
            for (int i = 0; i < list.Count; i++)
            {
                int id = One(list[i], at);
                if (!seen.Add(id))
                {
                    throw ArcReq.Bad(at + " 有重复", FieldPath.Clean(at));
                }
                ids.Add(id);
            }
            return ids;
        }

        static int One(object value, string at)
        {
            long number = value is int ? (int)value : (value is long ? (long)value : 0);
            if (number < 1 || number > int.MaxValue)
            {
                throw ArcReq.Bad(at + " 的每一项必须是正整数", FieldPath.Clean(at));
            }
            return (int)number;
        }

        // 响应里的 undo：计划里 action=updated 的行，按字段 → 表 → id 列出（Ar_Detail 列 Auto_ID）。没有下游更新时是 {}。
        public static Dictionary<string, object> Map(List<HeadFixRow> plan)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            for (int i = 0; plan != null && i < plan.Count; i++)
            {
                HeadFixRow row = plan[i];
                if (row.Action != HeadFixRow.Updated)
                {
                    continue;
                }
                for (int j = 0; j < row.Set.Count; j++)
                {
                    if (row.IsAr)
                    {
                        AddAll(map, row.Set[j].Key, row.Table, row.AutoIds);
                        continue;
                    }
                    List<int> one = new List<int>();
                    one.Add(row.Id);
                    AddAll(map, row.Set[j].Key, row.Table, one);
                }
            }
            return map;
        }

        static void AddAll(Dictionary<string, object> map, string key, string table, List<int> ids)
        {
            if (!map.ContainsKey(key))
            {
                map[key] = new Dictionary<string, object>();
            }
            Dictionary<string, object> tables = (Dictionary<string, object>)map[key];
            if (!tables.ContainsKey(table))
            {
                tables[table] = new List<object>();
            }
            List<object> list = (List<object>)tables[table];
            for (int i = 0; i < ids.Count; i++)
            {
                list.Add(ids[i]);
            }
        }

        // 回滚模式的计划：只处理 undo 里列出的行。每一行必须属于这张发货单（取不到 409 undo_foreign），
        // 且当前值等于本次调用的 expect（字段的 Old，NULL 与 "" 相等、不分大小写），否则 409 state_mismatch 并点名表和 id。
        // 不做 shared / differs 发现，不碰其他行。undo 的字段必须真的会变，否则 409。
        // 处理（加行锁）顺序固定，与 JSON 里的顺序无关：出库单按 id 升序，再发票升序，最后 Ar_Detail 按 Auto_ID 升序。
        // 与正向不完全相同：正向在每张发票之后马上锁它的 Ar_Detail 行，回滚先锁完全部发票再锁 Ar_Detail。
        // 并发的正向与回滚碰到共用发票时可能互相等待成死锁，SQL Server 只会终止其中一个事务，另一个照常完成。
        public static List<HeadFixRow> Build(HeadFixAsk ask, List<HeadFixField> changing, IHeadFixSource src)
        {
            foreach (string key in ask.Undo.Keys)
            {
                FieldOf(changing, key);
            }
            List<HeadFixRow> rows = new List<HeadFixRow>();
            string[] tables = new string[] { OutTable, InvTable };
            for (int t = 0; t < tables.Length; t++)
            {
                foreach (KeyValuePair<int, List<HeadFixField>> pair in Sorted(ask, changing, tables[t]))
                {
                    for (int i = 0; i < pair.Value.Count; i++)
                    {
                        AddDoc(rows, src, tables[t], pair.Key, pair.Value[i]);
                    }
                }
            }
            List<HeadFixRow> ars = new List<HeadFixRow>();
            foreach (KeyValuePair<int, List<HeadFixField>> pair in Sorted(ask, changing, HeadFixRow.ArTable))
            {
                AddAr(ars, pair.Key, pair.Value[0], src);
            }
            rows.AddRange(ars);
            return rows;
        }

        // 某张表在 undo 里列出的 id（跨字段合并），按 id 升序，每个 id 带上列它的字段（字段顺序同请求）。
        static SortedDictionary<int, List<HeadFixField>> Sorted(HeadFixAsk ask, List<HeadFixField> changing, string table)
        {
            SortedDictionary<int, List<HeadFixField>> byId = new SortedDictionary<int, List<HeadFixField>>();
            for (int f = 0; f < ask.Fields.Count; f++)
            {
                List<int> ids = IdsOf(ask, ask.Fields[f].Key, table);
                for (int i = 0; i < ids.Count; i++)
                {
                    if (!byId.ContainsKey(ids[i]))
                    {
                        byId[ids[i]] = new List<HeadFixField>();
                    }
                    byId[ids[i]].Add(FieldOf(changing, ask.Fields[f].Key));
                }
            }
            return byId;
        }

        static List<int> IdsOf(HeadFixAsk ask, string key, string table)
        {
            Dictionary<string, List<int>> tables;
            List<int> ids;
            if (ask.Undo.TryGetValue(key, out tables) && tables.TryGetValue(table, out ids))
            {
                return ids;
            }
            return new List<int>();
        }

        // 回滚模式下表头已经是目标值（没有字段要变）而 undo 还列着行：409 state_mismatch，不悄悄当成无事可做。
        public static void RefuseIdle(HeadFixAsk ask)
        {
            if (!ask.HasUndo)
            {
                return;
            }
            foreach (Dictionary<string, List<int>> tables in ask.Undo.Values)
            {
                foreach (List<int> ids in tables.Values)
                {
                    if (ids.Count > 0)
                    {
                        throw new BridgeException(409, "state_mismatch", "发货单表头已是目标值，但 undo 还列着下游行");
                    }
                }
            }
        }

        // 写入之前核对：任何一个字段的任何一张表要写的行数超过一个回滚请求能带的 500 个，就拒绝（改得了就必须回滚得了）。
        public static void CheckSize(List<HeadFixRow> plan)
        {
            foreach (KeyValuePair<string, object> field in Map(plan))
            {
                foreach (KeyValuePair<string, object> table in (Dictionary<string, object>)field.Value)
                {
                    if (((List<object>)table.Value).Count > MaxIds)
                    {
                        throw new BridgeException(409, "too_many", field.Key + " 在 " + table.Key + " 上要改的行超过 "
                            + MaxIds.ToString() + " 个，回滚请求装不下，已拒绝");
                    }
                }
            }
        }

        static HeadFixField FieldOf(List<HeadFixField> changing, string key)
        {
            for (int i = 0; i < changing.Count; i++)
            {
                if (changing[i].Key == key)
                {
                    return changing[i];
                }
            }
            throw new BridgeException(409, "state_mismatch", "undo 里的 " + key + " 对应的发货单字段没有变化");
        }

        static void AddDoc(List<HeadFixRow> rows, IHeadFixSource src, string table, int id, HeadFixField field)
        {
            HeadFixDoc doc = src.Doc(table, id);
            if (doc == null)
            {
                throw new BridgeException(409, Foreign, table + " " + id.ToString() + " 不属于这张发货单");
            }
            string current;
            doc.Values.TryGetValue(field.Key, out current);
            if (!HeadFixPlan.Same(current, field.Old))
            {
                throw Mismatch(table + " " + id.ToString(), field, current);
            }
            HeadFixRow row = null;
            for (int i = 0; i < rows.Count && row == null; i++)
            {
                row = rows[i].Table == table && rows[i].Id == id ? rows[i] : null;
            }
            if (row == null)
            {
                row = new HeadFixRow();
                row.Table = table;
                row.Id = id;
                row.Code = doc.Code;
                row.VouchType = doc.VouchType;
                row.Action = HeadFixRow.Updated;
                rows.Add(row);
            }
            row.Set.Add(field);
        }

        // Ar_Detail：按发票 (VouchType, Code) 归并成一行，Auto_ID 核对后加入。
        static void AddAr(List<HeadFixRow> ars, int id, HeadFixField person, IHeadFixSource src)
        {
            HeadFixArRow ar = src.Ar(id);
            if (ar == null)
            {
                throw new BridgeException(409, Foreign, HeadFixRow.ArTable + " " + id.ToString() + " 不属于这张发货单的发票");
            }
            if (!HeadFixPlan.Same(ar.Person, person.Old))
            {
                throw Mismatch(HeadFixRow.ArTable + " " + id.ToString(), person, ar.Person);
            }
            HeadFixRow row = null;
            for (int j = 0; j < ars.Count && row == null; j++)
            {
                row = ars[j].VouchType == ar.VouchType && ars[j].Code == ar.Code ? ars[j] : null;
            }
            if (row == null)
            {
                row = new HeadFixRow();
                row.Table = HeadFixRow.ArTable;
                row.Code = ar.Code;
                row.VouchType = ar.VouchType;
                row.Action = HeadFixRow.Updated;
                row.Set.Add(person);
                ars.Add(row);
            }
            row.AutoIds.Add(ar.AutoId);
        }

        static BridgeException Mismatch(string what, HeadFixField field, string current)
        {
            string shown = (current ?? "").Trim();
            return new BridgeException(409, "state_mismatch", what + " 的 " + field.Key + " 当前是 "
                + (shown.Length == 0 ? "空" : shown) + "，不是 expect 的 " + (field.Old.Length == 0 ? "空" : field.Old));
        }
    }
}
