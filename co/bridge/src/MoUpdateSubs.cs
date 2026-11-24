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
    // 这和子件关联列的写回是同一类复现写入（见 MoUpdateRestore），不另设开关。
    // 调用方给了替代料的子件（MoAllocTarget.Given）目标换成给的：新子件上没有就插入给的，仍是快照的先删再插（Step）。
    // 只改替代料时不调用 U8，由 MoUpdateSubsWrite 在请求连接的事务里用同样的 Step / Apply / Mismatch 写。
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

        // 在 MoUpdateRestore 的事务里写回（pairs：新 AllocateId → 子件目标），返回插入的行数。失败抛 504，由调用方回滚。
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
                MoSubStep step = Step(kv.Value, Of(now, kv.Key));
                if (step == null)
                {
                    MoAllocRow old = kv.Value.Row;
                    throw MoUpdateRestore.Lost(code, "子件 " + old.InvCode + "（行号 " + N(old.SortSeq) + "）重插后已有不同的替代料，替代料没有写回");
                }
                inserted += Apply(conn, kv.Key, step);
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
                if (t.Given || WantOf(t).Count > 0)
                {
                    return true;
                }
            }
            return false;
        }

        internal static List<MoSubRow> Of(Dictionary<int, List<MoSubRow>> map, int id)
        {
            List<MoSubRow> list;
            return map.TryGetValue(id, out list) ? list : new List<MoSubRow>();
        }

        // 子件的目标替代料：调用方给了的用给的，否则是快照的。
        internal static List<MoSubRow> WantOf(MoAllocTarget t)
        {
            if (t.Given)
            {
                return t.Want ?? new List<MoSubRow>();
            }
            return t.Row.SubRows ?? new List<MoSubRow>();
        }

        // 目标替代料行数（回读核对用；没给的按快照行数）。
        internal static int CountOf(MoAllocTarget t)
        {
            return t.Given ? WantOf(t).Count : t.Row.Subs;
        }

        // 子件上现有替代料 got → 达到目标要做的：已等于目标什么也不做；没有就插入目标；调用方给了替代料且现有的仍是快照
        // （没被别人改过）就先删再插；其余返回 null（可能是别人在 U8 客户端改过，不覆盖）。纯逻辑，--selftest 覆盖。
        internal static MoSubStep Step(MoAllocTarget t, List<MoSubRow> got)
        {
            List<MoSubRow> want = WantOf(t);
            MoSubStep step = new MoSubStep();
            if (MoSubs.SameSet(got, want))
            {
                return step;
            }
            if (got.Count == 0)
            {
                step.Add = want;
                return step;
            }
            if (t.Given && MoSubs.SameSet(got, t.Row.SubRows ?? new List<MoSubRow>()))
            {
                step.Clear = true;
                step.Add = want;
                return step;
            }
            return null;
        }

        // 在调用方的事务里执行一步，返回插入的行数。
        internal static int Apply(object conn, int allocateId, MoSubStep step)
        {
            if (step.Clear)
            {
                GlSql.Exec(conn, MoSubs.DeleteSql, new object[] { allocateId });
            }
            for (int i = 0; i < step.Add.Count; i++)
            {
                GlSql.Exec(conn, InsertSql, MoSubs.InsertArgs(step.Add[i], allocateId));
            }
            return step.Add.Count;
        }

        // 写后核对：每个子件的替代料（行数和全部列）等于目标。空串表示全部相符。纯逻辑，--selftest 覆盖。
        internal static string Mismatch(Dictionary<int, MoAllocTarget> pairs, Dictionary<int, List<MoSubRow>> now)
        {
            foreach (KeyValuePair<int, MoAllocTarget> kv in pairs)
            {
                MoAllocRow old = kv.Value.Row;
                List<MoSubRow> got = Of(now, kv.Key);
                List<MoSubRow> want = WantOf(kv.Value);
                if (!MoSubs.SameSet(got, want))
                {
                    return "子件 " + old.InvCode + "（行号 " + N(old.SortSeq) + "）写入后的替代料 " + N(got.Count) + " 行与目标 "
                        + N(want.Count) + " 行不符，替代料没有写回";
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

    // 一个子件上要做的：Clear 先删全部替代料，再插入 Add。
    internal sealed class MoSubStep
    {
        public bool Clear;
        public List<MoSubRow> Add = new List<MoSubRow>();
    }
}
