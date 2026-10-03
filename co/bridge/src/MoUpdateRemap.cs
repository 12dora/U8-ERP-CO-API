using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 已审核、已领料的生产订单修改：MOrderUpdate 把子件删了重插，AllocateId 全换新，但不改材料出库单行的 rdrecords11.iMPoIds
    // （修改后仍指旧 AllocateId，成了孤儿；已领量 IssQty 随重插带到同一子件的新行）。U8 客户端「变更」之后引用仍然有效，
    // 桥照做：
    // 1. 调用前在请求连接上抓取全部引用行（AutoID、旧 AllocateId、子件的行 / 行号 / 存货、数量），写审计事件 mo_update_refs；
    //    出库行存货与子件不同（替代料出库）的不改，409。任何已领料的子件（不论这一行改没改数量）改后需求少于已领数量都 409：
    //    U8 允许超额领料，这种子件改后桥核对不过，必须在调用前拒绝。
    // 2. U8 提交后在新连接上开自己的事务：UPDLOCK 读现有子件，再 UPDLOCK, HOLDLOCK 重读指向全部旧 AllocateId 的出库行，
    //    抓取之后才出现的（并发领料）一并改写，对不上的 504（MoUpdateRemapLate）；按（MoDId、行号、存货）把旧 AllocateId
    //    对到唯一一行新子件（闸门已保证快照里这三项不重复），按 AutoID 逐行改写 iMPoIds；再核对：每一行都指向同一存货的新子件、
    //    旧 id 不再有引用、新子件已领量等于旧值（有并发领料的子件不比）。全部通过才提交，审计事件 mo_update_remap 记旧→新对照。
    //    需求是否不少于已领不在这里核对：调用前已拒绝，数量由 MoUpdate 回读核对，不为它回滚改写（否则出库行成孤儿）。
    // 任何失败都回滚并 504 outcome_unknown（U8 的修改已提交），消息带修复办法，对照写进审计事件。
    internal static class MoUpdateRemap
    {
        const decimal Eps = 0.000001m;
        const int RefsMax = 100000;
        const string RefSql = "select convert(varchar(20), r.AutoID) as AutoID, convert(varchar(20), r.ID) as ID,"
            + " convert(varchar(20), r.iMPoIds) as AllocateId, r.cInvCode, convert(varchar(40), r.iQuantity) as Qty"
            + " from rdrecords11 r join mom_moallocate a on a.AllocateId=r.iMPoIds"
            + " join mom_orderdetail d on d.MoDId=a.MoDId where d.MoId=? order by r.AutoID";
        const string LockSql = "select convert(varchar(20), a.AllocateId) as AllocateId, convert(varchar(20), a.MoDId) as MoDId,"
            + " convert(varchar(10), a.SortSeq) as SortSeq, a.InvCode, convert(varchar(40), a.Qty) as Qty,"
            + " convert(varchar(40), isnull(a.IssQty,0)) as IssQty"
            + " from mom_moallocate a with (updlock, holdlock) join mom_orderdetail d on d.MoDId=a.MoDId where d.MoId=?";
        const string MoveSql = "update rdrecords11 set iMPoIds=? where AutoID=convert(bigint, ?) and iMPoIds=?";
        const string PointSql = "select convert(varchar(20), r.iMPoIds) as AllocateId, r.cInvCode, a.InvCode as AllocInv"
            + " from rdrecords11 r left join mom_moallocate a on a.AllocateId=r.iMPoIds where r.AutoID=convert(bigint, ?)";
        const string LeftSql = "select convert(varchar(20), count(*)) from rdrecords11 where iMPoIds=?";

        // 调用前：抓取引用行；出库行存货与子件不同的 409。只读，在预演停下之前调用。
        public static MoRefs Capture(object conn, int id, MoSnap before)
        {
            MoRefs refs = new MoRefs();
            List<Dictionary<string, object>> rows = Rows.Query(conn, RefSql, new object[] { id }, RefsMax);
            for (int i = 0; i < rows.Count; i++)
            {
                MoRef r = RefOf(rows[i], before);
                if (!Matches(r))
                {
                    throw new BridgeException(409, "state_mismatch", "材料出库单行的存货 " + r.Inv
                        + " 与生产订单子件不同（替代料出库），请在 U8 客户端修改");
                }
                refs.Items.Add(r);
            }
            for (int i = 0; i < before.Allocs.Count; i++)
            {
                refs.Issued = refs.Issued || before.Allocs[i].IssQty != 0m;
            }
            return refs;
        }

        // 出库行 → MoRef（AutoID、出库单 ID、旧 AllocateId、存货、数量；Alloc 是快照里的子件，找不到为 null）。
        internal static MoRef RefOf(Dictionary<string, object> row, MoSnap before)
        {
            MoRef r = new MoRef();
            r.AutoId = CoRows.Col(row, "AutoID");
            r.RdId = CoRows.Col(row, "ID");
            r.OldId = CoRows.AsId(CoRows.Col(row, "AllocateId"));
            r.Inv = CoRows.Col(row, "cInvCode");
            r.Qty = CoRows.Col(row, "Qty");
            r.Alloc = AllocOf(before, r.OldId);
            return r;
        }

        // 出库行的子件在快照里、存货相同（不是替代料出库）。
        internal static bool Matches(MoRef r)
        {
            return r.Alloc != null && MoAllocRow.Norm(r.Inv) == MoAllocRow.Norm(r.Alloc.InvCode);
        }

        // 有改动时，每个已领料的子件改后需求（没改数量的行是原值，改了的按 MoUpdateAlloc 重算，与 U8 一致）不能少于已领数量。
        // 不论这一行改没改数量：U8 允许超额领料，只改备注、日期也会让重插后的子件需求少于已领。纯逻辑，--selftest 覆盖。
        public static void CheckIssued(MoPlan plan)
        {
            if (!plan.Changed)
            {
                return;
            }
            for (int i = 0; i < plan.Lines.Count; i++)
            {
                MoTarget line = plan.Lines[i];
                List<MoAllocTarget> allocs = plan.Allocs[line.Row.MoDId];
                for (int k = 0; k < allocs.Count; k++)
                {
                    MoAllocRow a = allocs[k].Row;
                    if (a.IssQty > 0m && allocs[k].Qty < a.IssQty - Eps)
                    {
                        throw new BridgeException(409, "state_mismatch", "第 " + N(line.Row.SortSeq) + " 行子件 " + a.InvCode
                            + " 已领 " + D(a.IssQty) + "，改后需求 " + D(allocs[k].Qty) + " 少于已领数量"
                            + (line.QtyChanged ? "，不能修改" : "（已超额领料），桥不能修改，请在 U8 客户端变更"));
                    }
                }
            }
        }

        // 调用 U8 之前把引用行写进审计事件 mo_update_refs（表、AutoID、出库单 ID、旧 AllocateId、子件的行 / 行号 / 存货、数量）。
        public static void LogRefs(WorkContext ctx, int id, string code, MoRefs refs)
        {
            if (refs.Items.Count == 0)
            {
                return;
            }
            List<object> items = new List<object>();
            for (int i = 0; i < refs.Items.Count; i++)
            {
                MoRef r = refs.Items[i];
                Dictionary<string, object> item = new Dictionary<string, object>();
                item["table"] = "rdrecords11";
                item["auto_id"] = r.AutoId;
                item["rd_id"] = r.RdId;
                item["allocate_id"] = r.OldId;
                item["mo_d_id"] = r.Alloc.MoDId;
                item["sort_seq"] = r.Alloc.SortSeq;
                item["inv_code"] = r.Alloc.InvCode;
                item["qty"] = r.Qty;
                items.Add(item);
            }
            Dictionary<string, object> evt = new Dictionary<string, object>();
            evt["mo_id"] = id;
            evt["code"] = code;
            evt["operator"] = ctx.Item == null ? "" : ctx.Item.Operator;
            evt["refs"] = items;
            AuditEvent.Write("mo_update_refs", evt);
        }

        // U8 提交后：改写引用并核对，见类注释。快照里没有子件时什么都不做；没有引用、也没有已领料的子件时
        // 仍要重读一遍旧 AllocateId 的出库行（抓取之后可能有人领料）。
        public static void Run(WorkContext ctx, int id, MoSnap before, MoRefs refs)
        {
            if (before.Allocs.Count == 0)
            {
                return;
            }
            object conn = null;
            bool open = false;
            Dictionary<int, int> map = null;
            MoRefs all = refs;
            try
            {
                conn = ctx.OpenFresh();
                CoTrans.Begin(conn);
                open = true;
                List<MoAllocKey> now = MoAllocKey.Read(Rows.Query(conn, LockSql, new object[] { id }, RefsMax));
                all = MoUpdateRemapLate.Merge(refs, MoUpdateRemapLate.Read(conn, before), before);
                map = Map(Needed(before, all), now, before.Code);
                Move(conn, map, all);
                Verify(conn, map, all, now, before);
                CoTrans.Commit(conn);
                open = false;
                if (all.Items.Count > 0 || map.Count > 0)
                {
                    Log(ctx, id, before.Code, map, all, "");
                    CoRows.Note(ctx.Item, "MoUpdate 改写材料出库引用 " + N(all.Items.Count) + " 行");
                }
            }
            catch (DryRunDone)
            {
                throw;
            }
            catch (Exception ex)
            {
                if (open)
                {
                    TryRollback(conn);
                }
                throw Failed(ctx, id, before.Code, map, all, ex);
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        // 要对照的旧子件：有引用的，和已领量非 0 的。
        static List<MoAllocRow> Needed(MoSnap before, MoRefs refs)
        {
            HashSet<int> ids = new HashSet<int>();
            for (int i = 0; i < refs.Items.Count; i++)
            {
                ids.Add(refs.Items[i].OldId);
            }
            List<MoAllocRow> list = new List<MoAllocRow>();
            for (int i = 0; i < before.Allocs.Count; i++)
            {
                MoAllocRow a = before.Allocs[i];
                if (ids.Contains(a.AllocateId) || a.IssQty != 0m)
                {
                    list.Add(a);
                }
            }
            return list;
        }

        // 旧 AllocateId → 新 AllocateId。旧 id 仍在（U8 没重插这一行）就对到自己；否则按（MoDId、行号、存货）对到恰好一行，
        // 且该行不能是别的旧子件。对不上抛 504。纯逻辑，--selftest 覆盖。
        internal static Dictionary<int, int> Map(List<MoAllocRow> old, List<MoAllocKey> now, string code)
        {
            Dictionary<int, MoAllocKey> byId = new Dictionary<int, MoAllocKey>();
            Dictionary<string, List<MoAllocKey>> byKey = new Dictionary<string, List<MoAllocKey>>();
            for (int i = 0; i < now.Count; i++)
            {
                byId[now[i].Id] = now[i];
                List<MoAllocKey> list;
                if (!byKey.TryGetValue(now[i].Key, out list))
                {
                    list = new List<MoAllocKey>();
                    byKey[now[i].Key] = list;
                }
                list.Add(now[i]);
            }
            Dictionary<int, int> map = new Dictionary<int, int>();
            HashSet<int> taken = new HashSet<int>();
            for (int i = 0; i < old.Count; i++)
            {
                int target = Target(old[i], byId, byKey, code);
                if (!taken.Add(target))
                {
                    throw Lost(code, "子件 " + old[i].InvCode + " 对到的新子件重复");
                }
                map[old[i].AllocateId] = target;
            }
            return map;
        }

        static int Target(MoAllocRow a, Dictionary<int, MoAllocKey> byId, Dictionary<string, List<MoAllocKey>> byKey, string code)
        {
            if (byId.ContainsKey(a.AllocateId))
            {
                return a.AllocateId;
            }
            List<MoAllocKey> hits;
            if (!byKey.TryGetValue(MoAllocKey.KeyOf(a.MoDId, a.SortSeq, a.InvCode), out hits) || hits.Count != 1)
            {
                throw Lost(code, "子件 " + a.InvCode + "（行号 " + N(a.SortSeq) + "）重插后对不上唯一一行");
            }
            return hits[0].Id;
        }

        // 按 AutoID 逐行改写；条件带旧 id，已经指向新 id 的行不动。
        static void Move(object conn, Dictionary<int, int> map, MoRefs refs)
        {
            for (int i = 0; i < refs.Items.Count; i++)
            {
                MoRef r = refs.Items[i];
                int target = map[r.OldId];
                if (target != r.OldId)
                {
                    GlSql.Exec(conn, MoveSql, new object[] { target, r.AutoId, r.OldId });
                }
            }
        }

        static void Verify(object conn, Dictionary<int, int> map, MoRefs refs, List<MoAllocKey> now, MoSnap before)
        {
            for (int i = 0; i < refs.Items.Count; i++)
            {
                MoRef r = refs.Items[i];
                Dictionary<string, object> row = Rows.One(conn, PointSql, new object[] { r.AutoId });
                string bad = PointProblem(r, row, map[r.OldId]);
                if (bad.Length > 0)
                {
                    throw Lost(before.Code, bad);
                }
                if (map[r.OldId] != r.OldId && Rows.Scalar(conn, LeftSql, new object[] { r.OldId }) != "0")
                {
                    throw Lost(before.Code, "仍有材料出库单行指向旧子件 " + N(r.OldId));
                }
            }
            string issued = IssuedProblem(map, now, before, MoUpdateRemapLate.Grown(refs));
            if (issued.Length > 0)
            {
                throw Lost(before.Code, issued);
            }
        }

        // 引用行改写后：仍在、指向对照的新子件、新子件存在且存货与出库行相同。
        internal static string PointProblem(MoRef r, Dictionary<string, object> row, int target)
        {
            if (row == null)
            {
                return "材料出库单行 " + r.AutoId + " 不见了";
            }
            if (CoRows.AsId(CoRows.Col(row, "AllocateId")) != target)
            {
                return "材料出库单行 " + r.AutoId + " 没有指向新子件 " + N(target);
            }
            string inv = CoRows.Col(row, "AllocInv");
            if (inv.Length == 0 || MoAllocRow.Norm(inv) != MoAllocRow.Norm(CoRows.Col(row, "cInvCode")))
            {
                return "材料出库单行 " + r.AutoId + " 指向的子件不存在或存货不同";
            }
            return "";
        }

        // 新子件的已领量等于旧值（U8 重插时带过来）；grown 是抓取之后有并发领料的旧子件，已领量会变，只核对新行还在。
        // 需求少于已领不算改写失败（U8 允许超额领料；调用前已拒绝会这样的修改），见类注释。纯逻辑，--selftest 覆盖。
        internal static string IssuedProblem(Dictionary<int, int> map, List<MoAllocKey> now, MoSnap before, HashSet<int> grown)
        {
            Dictionary<int, MoAllocKey> byId = new Dictionary<int, MoAllocKey>();
            for (int i = 0; i < now.Count; i++)
            {
                byId[now[i].Id] = now[i];
            }
            foreach (KeyValuePair<int, int> kv in map)
            {
                MoAllocRow a = AllocOf(before, kv.Key);
                MoAllocKey n;
                if (a == null || !byId.TryGetValue(kv.Value, out n))
                {
                    return "子件 " + N(kv.Key) + " 的新行不见了";
                }
                if (!grown.Contains(kv.Key) && Math.Abs(n.IssQty - a.IssQty) >= Eps)
                {
                    return "子件 " + a.InvCode + " 的已领数量 " + D(n.IssQty) + "，应为 " + D(a.IssQty);
                }
            }
            return "";
        }

        static MoAllocRow AllocOf(MoSnap snap, int allocateId)
        {
            for (int i = 0; i < snap.Allocs.Count; i++)
            {
                if (snap.Allocs[i].AllocateId == allocateId)
                {
                    return snap.Allocs[i];
                }
            }
            return null;
        }

        // 审计事件 mo_update_remap：旧→新对照（只有主键）、抓取之后才出现的出库行 AutoID（late），error 非空表示没有改成。
        static void Log(WorkContext ctx, int id, string code, Dictionary<int, int> map, MoRefs all, string error)
        {
            List<object> pairs = new List<object>();
            if (map != null)
            {
                foreach (KeyValuePair<int, int> kv in map)
                {
                    Dictionary<string, object> pair = new Dictionary<string, object>();
                    pair["old"] = kv.Key;
                    pair["new"] = kv.Value;
                    pairs.Add(pair);
                }
            }
            Dictionary<string, object> evt = new Dictionary<string, object>();
            evt["mo_id"] = id;
            evt["code"] = code;
            evt["operator"] = ctx.Item == null ? "" : ctx.Item.Operator;
            evt["map"] = pairs;
            evt["late"] = MoUpdateRemapLate.LateIds(all);
            evt["error"] = error;
            AuditEvent.Write("mo_update_remap", evt);
        }

        static BridgeException Failed(WorkContext ctx, int id, string code, Dictionary<int, int> map, MoRefs all, Exception ex)
        {
            BridgeException known = ex as BridgeException;
            BridgeException lost = known != null && known.Status == 504 ? known : Lost(code, "改写时出错：" + MoApi.FirstLine(ex.Message));
            try
            {
                Log(ctx, id, code, map, all, lost.Message);
            }
            catch (Exception)
            {
            }
            CoRows.Note(ctx.Item, "MoUpdate 引用改写失败 " + MoApi.FirstLine(ex.Message));
            return lost;
        }

        static void TryRollback(object conn)
        {
            try
            {
                CoTrans.Rollback(conn);
            }
            catch (Exception)
            {
            }
        }

        internal static BridgeException Lost(string code, string text)
        {
            return new BridgeException(504, "outcome_unknown", "U8 已提交生产订单 " + code + " 的修改，但材料出库单引用没有改到新子件（"
                + text + "）。修改前的子件快照（旧 AllocateId、关联列原值）在审计事件 mo_update_snapshot，引用行在 mo_update_refs，"
                + "旧→新子件对照和之后新增的出库行在 mo_update_remap；请据此修正材料出库单行的子件关联，或到 U8 客户端核对");
        }

        static string N(int n)
        {
            return n.ToString(CultureInfo.InvariantCulture);
        }

        static string D(decimal d)
        {
            return d.ToString("0.######", CultureInfo.InvariantCulture);
        }
    }

    // 调用前抓取的材料出库引用。Issued：有子件已领量非 0。
    internal sealed class MoRefs
    {
        public List<MoRef> Items = new List<MoRef>();
        public bool Issued;
    }

    internal sealed class MoRef
    {
        public string AutoId = "";
        public string RdId = "";
        public int OldId;
        public string Inv = "";
        public string Qty = "";
        public MoAllocRow Alloc;
        // 抓取之后才出现的出库行（U8 提交前并发领料），由 MoUpdateRemapLate 补进来。
        public bool Late;
    }

    // 修改后现有子件的主键、定位和数量（MoUpdateRemap 对照用）。
    internal sealed class MoAllocKey
    {
        public int Id;
        public string Key = "";
        public decimal Qty;
        public decimal IssQty;

        public static List<MoAllocKey> Read(List<Dictionary<string, object>> rows)
        {
            List<MoAllocKey> list = new List<MoAllocKey>();
            for (int i = 0; i < rows.Count; i++)
            {
                list.Add(Of(CoRows.AsId(CoRows.Col(rows[i], "AllocateId")), KeyOf(CoRows.AsId(CoRows.Col(rows[i], "MoDId")),
                    MoUpdateSql.Int(CoRows.Col(rows[i], "SortSeq")), CoRows.Col(rows[i], "InvCode")),
                    MoUpdateSql.Dec(CoRows.Col(rows[i], "Qty")), MoUpdateSql.Dec(CoRows.Col(rows[i], "IssQty"))));
            }
            return list;
        }

        internal static MoAllocKey Of(int id, string key, decimal qty, decimal issQty)
        {
            MoAllocKey k = new MoAllocKey();
            k.Id = id;
            k.Key = key;
            k.Qty = qty;
            k.IssQty = issQty;
            return k;
        }

        // 同 MoUpdateRestore：（行、行号、存货）。
        internal static string KeyOf(int moDId, int seq, string inv)
        {
            return moDId.ToString(CultureInfo.InvariantCulture) + "|" + seq.ToString(CultureInfo.InvariantCulture)
                + "|" + MoAllocRow.Norm(inv);
        }
    }
}
