using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 生产订单修改里没有 U8 字段要改的请求（MoPlan.Changed 为 false）：
    // - 替代料也没变：不调用 U8，直接返回（预演停在这里）。
    // - 只改替代料：不调用 MOrderUpdate（它会重插全部子件、换 AllocateId、改写材料出库关联），在请求连接上开事务：
    //   UPDLOCK, HOLDLOCK 锁住要改的子件行（确认仍是快照里的 AllocateId、行、行号、存货），锁读现有替代料，按 MoUpdateSubs.Step
    //   删除、插入（主键 newid()），同一事务里重读核对等于目标才提交；对不上回滚。预演时提交钩子回滚并报告。
    // 之后在新连接上完整回读核对（MoUpdate.Confirm）。
    internal static class MoUpdateSubsWrite
    {
        const string LockHead = "select convert(varchar(20), a.AllocateId) as AllocateId, convert(varchar(20), a.MoDId) as MoDId,"
            + " convert(varchar(10), a.SortSeq) as SortSeq, a.InvCode from mom_moallocate a with (updlock, holdlock) where a.AllocateId in (";

        public static ApiResult Run(WorkContext ctx, VoucherKind kind, int id, MoSnap before, MoPlan plan)
        {
            if (!plan.SubsChanged)
            {
                // 请求的值与现值相同：不调用 U8（MOrderUpdate 会重写全部子件，没必要冒险）。
                DryRun.Stop(ctx, "无改动，不调用 U8");
                return MoUpdateCheck.Result(kind, id, before, plan);
            }
            Write(ctx, plan, before.Code);
            MoSnap after = MoUpdate.Reread(ctx, id, before);
            MoUpdate.Confirm(ctx, plan, before, after, null);
            return MoUpdateCheck.Result(kind, id, after, plan);
        }

        // 要改的子件：快照 AllocateId → 目标（调用方给了且与现有不同的）。
        internal static Dictionary<int, MoAllocTarget> Pairs(MoPlan plan)
        {
            Dictionary<int, MoAllocTarget> pairs = new Dictionary<int, MoAllocTarget>();
            foreach (List<MoAllocTarget> list in plan.Allocs.Values)
            {
                for (int i = 0; i < list.Count; i++)
                {
                    if (list[i].Given && list[i].SubsChanged)
                    {
                        pairs[list[i].Row.AllocateId] = list[i];
                    }
                }
            }
            return pairs;
        }

        static void Write(WorkContext ctx, MoPlan plan, string code)
        {
            Dictionary<int, MoAllocTarget> pairs = Pairs(plan);
            List<int> ids = new List<int>(pairs.Keys);
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                Locked(conn, pairs);
                Dictionary<int, List<MoSubRow>> now = MoSubs.ByAlloc(MoSubs.Read(conn, ids, true));
                int inserted = 0;
                foreach (KeyValuePair<int, MoAllocTarget> kv in pairs)
                {
                    MoSubStep step = MoUpdateSubs.Step(kv.Value, MoUpdateSubs.Of(now, kv.Key));
                    if (step == null)
                    {
                        throw Changed(kv.Value.Row, "的替代料");
                    }
                    inserted += MoUpdateSubs.Apply(conn, kv.Key, step);
                }
                string bad = MoUpdateSubs.Mismatch(pairs, MoSubs.ByAlloc(MoSubs.Read(conn, ids, true)));
                if (bad.Length > 0)
                {
                    throw new BridgeException(500, "internal", "生产订单 " + code + " 的替代料写入后核对不符，已回滚：" + bad);
                }
                open = false;
                Commit(ctx, code);
                CoRows.Note(ctx.Item, "MoUpdate 替代料：子件 " + N(pairs.Count) + " 个，插入 " + N(inserted) + " 行");
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
                if (ex is BridgeException)
                {
                    throw;
                }
                CoRows.Note(ctx.Item, "MoUpdate 替代料 " + MoApi.FirstLine(ex.Message));
                throw new BridgeException(500, "internal", "写入生产订单 " + code + " 的替代料失败，已回滚：" + MoApi.FirstLine(ex.Message));
            }
        }

        // 提交本身出错时结果未知（504）；预演时提交钩子回滚并抛 DryRunDone。
        static void Commit(WorkContext ctx, string code)
        {
            try
            {
                CoTrans.Commit(ctx.Conn);
            }
            catch (DryRunDone)
            {
                throw;
            }
            catch (Exception ex)
            {
                TryRollback(ctx.Conn);
                CoRows.Note(ctx.Item, "MoUpdate 替代料提交 " + MoApi.FirstLine(ex.Message));
                throw new BridgeException(504, "outcome_unknown", "生产订单 " + code + " 的替代料提交时出错，结果未知，请读取订单核对");
            }
        }

        // 锁住要改的子件：每个都还在、仍是快照里的行、行号、存货（否则读取之后被改过，409）。
        static void Locked(object conn, Dictionary<int, MoAllocTarget> pairs)
        {
            List<object> args = new List<object>();
            System.Text.StringBuilder sql = new System.Text.StringBuilder(LockHead);
            foreach (int id in pairs.Keys)
            {
                sql.Append(args.Count == 0 ? "?" : ",?");
                args.Add(id);
            }
            List<Dictionary<string, object>> rows = Rows.Query(conn, sql.Append(")").ToString(), args.ToArray(), pairs.Count + 1);
            Dictionary<int, Dictionary<string, object>> byId = new Dictionary<int, Dictionary<string, object>>();
            for (int i = 0; i < rows.Count; i++)
            {
                byId[CoRows.AsId(CoRows.Col(rows[i], "AllocateId"))] = rows[i];
            }
            foreach (KeyValuePair<int, MoAllocTarget> kv in pairs)
            {
                Dictionary<string, object> row;
                MoAllocRow a = kv.Value.Row;
                if (!byId.TryGetValue(kv.Key, out row) || CoRows.AsId(CoRows.Col(row, "MoDId")) != a.MoDId
                    || MoUpdateSql.Int(CoRows.Col(row, "SortSeq")) != a.SortSeq
                    || MoAllocRow.Norm(CoRows.Col(row, "InvCode")) != MoAllocRow.Norm(a.InvCode))
                {
                    throw Changed(a, "");
                }
            }
        }

        static BridgeException Changed(MoAllocRow a, string what)
        {
            return new BridgeException(409, "state_mismatch", "子件 " + a.InvCode + "（行号 " + N(a.SortSeq) + "）" + what
                + "在读取后被改过（可能有人在 U8 客户端修改），未修改，请重试");
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

        // 响应的 allocations：替代料改了的子件（回读的新值），每项 line_id、allocate_id、sort_seq、inv_code、substitutes
        // （形状同 vouchers/load）。没有改替代料时为 null（响应不带这个键）。
        internal static List<object> Output(MoPlan plan, MoSnap after)
        {
            List<object> items = new List<object>();
            foreach (MoAllocTarget t in Pairs(plan).Values)
            {
                MoAllocRow now = Match(after, t.Row);
                Dictionary<string, object> item = new Dictionary<string, object>();
                item["line_id"] = t.Row.MoDId;
                item["allocate_id"] = now == null ? 0 : now.AllocateId;
                item["sort_seq"] = t.Row.SortSeq;
                item["inv_code"] = t.Row.InvCode;
                List<object> subs = new List<object>();
                List<MoSubRow> rows = now == null || now.SubRows == null ? new List<MoSubRow>() : now.SubRows;
                for (int i = 0; i < rows.Count; i++)
                {
                    subs.Add(MoSubs.Output(rows[i]));
                }
                item[MoSubs.Key] = subs;
                items.Add(item);
            }
            return items.Count == 0 ? null : items;
        }

        static MoAllocRow Match(MoSnap after, MoAllocRow old)
        {
            List<MoAllocRow> allocs = after.AllocsOf(old.MoDId);
            for (int i = 0; i < allocs.Count; i++)
            {
                if (allocs[i].SortSeq == old.SortSeq && MoAllocRow.Norm(allocs[i].InvCode) == MoAllocRow.Norm(old.InvCode))
                {
                    return allocs[i];
                }
            }
            return null;
        }

        static string N(int n)
        {
            return n.ToString(CultureInfo.InvariantCulture);
        }
    }
}
