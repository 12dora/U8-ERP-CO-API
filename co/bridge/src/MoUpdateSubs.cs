using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 生产订单修改保留替代料：MOrderUpdate 的扩展实体没有替代料集合，Update 清掉子件后按 DTO 重建，mom_moallocatesub 随旧子件
    // 一起删掉（docs/u8-notes.md）。桥照 U8 客户端「变更」的结果保留：
    // 1. 调用前在请求连接上按旧 AllocateId 读全部替代料（Capture），连同子件关联列一起写进审计事件 mo_update_snapshot。
    // 2. U8 提交后，在 MoUpdateRestore 的事务里（子件已按（MoDId、行号、存货）对上新 AllocateId）UPDLOCK, HOLDLOCK 读新子件的
    //    替代料：没有的按快照逐行插入（新主键 newid()，优先级、替代存货、替代系数、日期、是否替换、Define22–37 原样）；
    //    已有且与快照相同的不动（U8 自己保留了）；已有但不同的不覆盖，504。插入后在同一事务里重读，每个新子件的替代料
    //    （行数和全部列）等于快照才提交，否则回滚、504。替代系数是比例，不随行数量缩放。
    // 这和子件关联列的写回是同一类复现写入（第二级，见 MoUpdateRestore），不另设开关。
    internal static class MoUpdateSubs
    {
        static readonly string InsertSql = MoSubs.InsertSql();

        // 调用前：读快照里每个子件的替代料，挂到 MoAllocRow.SubRows；Subs（行数）以这次读到的为准。
        public static void Capture(object conn, MoSnap snap)
        {
            List<int> ids = new List<int>();
            for (int i = 0; i < snap.Allocs.Count; i++)
            {
                ids.Add(snap.Allocs[i].AllocateId);
            }
            Dictionary<int, List<MoSubRow>> map = MoSubs.ByAlloc(ids.Count == 0 ? new List<MoSubRow>() : MoSubs.Read(conn, ids, false));
            for (int i = 0; i < snap.Allocs.Count; i++)
            {
                List<MoSubRow> subs;
                MoAllocRow a = snap.Allocs[i];
                a.SubRows = map.TryGetValue(a.AllocateId, out subs) ? subs : new List<MoSubRow>();
                a.Subs = a.SubRows.Count;
            }
        }

        // 在 MoUpdateRestore 的事务里写回（pairs：新 AllocateId → 快照子件目标），返回插入的行数。失败抛 504，由调用方回滚。
        public static int Restore(object conn, Dictionary<int, MoAllocTarget> pairs, string code)
        {
            if (!Any(pairs))
            {
                return 0;
            }
            List<int> ids = new List<int>(pairs.Keys);
            Dictionary<int, List<MoSubRow>> now = MoSubs.ByAlloc(MoSubs.Read(conn, ids, true));
            int inserted = 0;
            foreach (KeyValuePair<int, MoAllocTarget> kv in pairs)
            {
                List<MoSubRow> add = ToInsert(kv.Value.Row, Of(now, kv.Key), code);
                for (int i = 0; i < add.Count; i++)
                {
                    GlSql.Exec(conn, InsertSql, MoSubs.InsertArgs(add[i], kv.Key));
                }
                inserted += add.Count;
            }
            string bad = Mismatch(pairs, MoSubs.ByAlloc(MoSubs.Read(conn, ids, true)));
            if (bad.Length > 0)
            {
                throw MoUpdateRestore.Lost(code, bad);
            }
            return inserted;
        }

        static bool Any(Dictionary<int, MoAllocTarget> pairs)
        {
            foreach (MoAllocTarget t in pairs.Values)
            {
                if (t.Row.SubRows != null && t.Row.SubRows.Count > 0)
                {
                    return true;
                }
            }
            return false;
        }

        static List<MoSubRow> Of(Dictionary<int, List<MoSubRow>> map, int id)
        {
            List<MoSubRow> list;
            return map.TryGetValue(id, out list) ? list : new List<MoSubRow>();
        }

        // 要插入的替代料：新子件上没有就是快照全部；已有且与快照相同为空；已有但不同 504（可能是别人在 U8 客户端改过）。纯逻辑，--selftest 覆盖。
        internal static List<MoSubRow> ToInsert(MoAllocRow old, List<MoSubRow> got, string code)
        {
            List<MoSubRow> want = old.SubRows ?? new List<MoSubRow>();
            if (got.Count == 0)
            {
                return want;
            }
            if (MoSubs.SameSet(got, want))
            {
                return new List<MoSubRow>();
            }
            throw MoUpdateRestore.Lost(code, "子件 " + old.InvCode + "（行号 " + N(old.SortSeq) + "）重插后已有不同的替代料，替代料没有写回");
        }

        // 写回后核对：每个新子件的替代料（行数和全部列）等于对应快照子件的。空串表示全部相符。纯逻辑，--selftest 覆盖。
        internal static string Mismatch(Dictionary<int, MoAllocTarget> pairs, Dictionary<int, List<MoSubRow>> now)
        {
            foreach (KeyValuePair<int, MoAllocTarget> kv in pairs)
            {
                MoAllocRow old = kv.Value.Row;
                List<MoSubRow> got = Of(now, kv.Key);
                if (!MoSubs.SameSet(got, old.SubRows ?? new List<MoSubRow>()))
                {
                    return "子件 " + old.InvCode + "（行号 " + N(old.SortSeq) + "）写回后的替代料 " + N(got.Count) + " 行与修改前 "
                        + N(old.SubRows == null ? 0 : old.SubRows.Count) + " 行不符，替代料没有写回";
                }
            }
            return "";
        }

        // 审计事件 mo_update_snapshot 里每个子件的替代料原文。
        internal static List<object> Snapshot(MoAllocRow a)
        {
            List<object> items = new List<object>();
            for (int i = 0; a.SubRows != null && i < a.SubRows.Count; i++)
            {
                items.Add(MoSubs.Raw(a.SubRows[i]));
            }
            return items;
        }

        static string N(int n)
        {
            return n.ToString(CultureInfo.InvariantCulture);
        }
    }
}
