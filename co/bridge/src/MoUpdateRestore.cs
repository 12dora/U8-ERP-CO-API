using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // MOrderUpdate 之后，把 U8 重插子件时丢掉的列按快照写回（实测：OpComponentId → 0，VirOpComponentIds、cSubSysBarCode、
    // SoDId → NULL，UpperMoQty → 0）。U8 的修改已经提交；这里在新连接上开自己的事务：UPDLOCK 重读这张订单的全部子件，
    // 按（MoDId、行号、存货）把每个快照子件对上恰好一行现有子件（闸门已保证快照里这三项不重复），逐行 UPDATE
    // MoUpdateCols.Restore 各列和 UpperMoQty（都写回快照原值），提交。
    // 写之前确认 U8 真的重插了：行数相同、现有 AllocateId 没有一个在快照里；每行要写的列必须仍是 U8 重建后的缺省
    // （NULL、空串、0）或已等于快照值，否则可能是别人在 U8 客户端改过，不覆盖。
    // 任何失败都回滚并 504（U8 的修改已提交；快照值在调用前已写进审计事件 mo_update_snapshot，可据此手工恢复）。
    // 之后由 MoUpdate 做完整回读核对。
    internal static class MoUpdateRestore
    {
        // 开关：关掉时不写回，回读会把这些列的变化报成 504。static readonly 免得编译器报不可达代码。
        internal static readonly bool Enabled = true;
        static readonly string LockSql = "select convert(varchar(20), a.AllocateId) as AllocateId, convert(varchar(20), a.MoDId) as MoDId,"
            + " convert(varchar(10), a.SortSeq) as SortSeq, a.InvCode, convert(varchar(40), a.UpperMoQty) as UpperMoQty"
            + MoUpdateCols.SelectList()
            + " from mom_moallocate a with (updlock, holdlock) join mom_orderdetail d on d.MoDId=a.MoDId where d.MoId=?"
            + " order by a.MoDId, a.SortSeq, a.AllocateId";
        static readonly string UpdateSql = MoUpdateCols.RestoreSql();

        public static void Run(WorkContext ctx, int id, MoPlan plan, string code)
        {
            if (!Enabled)
            {
                return;
            }
            object conn = null;
            bool open = false;
            try
            {
                conn = ctx.OpenFresh();
                CoTrans.Begin(conn);
                open = true;
                List<Dictionary<string, object>> rows = Rows.Query(conn, LockSql, new object[] { id }, 100000);
                Dictionary<int, MoAllocTarget> pairs = Pair(plan, rows, code);
                foreach (KeyValuePair<int, MoAllocTarget> kv in pairs)
                {
                    GlSql.Exec(conn, UpdateSql, Args(kv.Value, kv.Key));
                }
                CoTrans.Commit(conn);
                open = false;
                CoRows.Note(ctx.Item, "MoUpdate 写回子件关联列 " + pairs.Count.ToString(CultureInfo.InvariantCulture) + " 行");
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
                BridgeException known = ex as BridgeException;
                if (known != null && known.Status == 504)
                {
                    throw;
                }
                CoRows.Note(ctx.Item, "MoUpdate 写回 " + MoApi.FirstLine(ex.Message));
                throw Lost(code, "写回子件关联列失败：" + MoApi.FirstLine(ex.Message));
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        // 调用 U8 之前把每个子件要写回的快照值和旧 AllocateId 写进审计日志（事件 mo_update_snapshot），写回失败时据此手工恢复。
        public static void LogSnapshot(WorkContext ctx, int id, MoSnap before)
        {
            List<object> items = new List<object>();
            for (int i = 0; i < before.Allocs.Count; i++)
            {
                MoAllocRow a = before.Allocs[i];
                Dictionary<string, object> item = new Dictionary<string, object>();
                item["allocate_id"] = a.AllocateId;
                item["mo_d_id"] = a.MoDId;
                item["sort_seq"] = a.SortSeq;
                item["inv_code"] = a.InvCode;
                for (int c = 0; c < MoUpdateCols.Restore.Length; c++)
                {
                    item[MoUpdateCols.Restore[c]] = Cell(a.Keep, MoUpdateCols.Restore[c]);
                }
                item["UpperMoQty"] = a.UpperMoQty;
                items.Add(item);
            }
            Dictionary<string, object> evt = new Dictionary<string, object>();
            evt["mo_id"] = id;
            evt["code"] = before.Code;
            evt["operator"] = ctx.Item == null ? "" : ctx.Item.Operator;
            evt["allocates"] = items;
            AuditEvent.Write("mo_update_snapshot", evt);
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

        // 现有子件主键 → 对应的快照子件目标。每个快照子件恰好对上一行，且没有多余的行；先确认是 U8 重插的新行、没被别人改过。
        static Dictionary<int, MoAllocTarget> Pair(MoPlan plan, List<Dictionary<string, object>> rows, string code)
        {
            Reinserted(plan, rows, code);
            Dictionary<string, List<int>> byKey = new Dictionary<string, List<int>>();
            for (int i = 0; i < rows.Count; i++)
            {
                string key = Key(CoRows.AsId(CoRows.Col(rows[i], "MoDId")), MoUpdateSql.Int(CoRows.Col(rows[i], "SortSeq")),
                    CoRows.Col(rows[i], "InvCode"));
                List<int> ids;
                if (!byKey.TryGetValue(key, out ids))
                {
                    ids = new List<int>();
                    byKey[key] = ids;
                }
                ids.Add(CoRows.AsId(CoRows.Col(rows[i], "AllocateId")));
            }
            Dictionary<int, MoAllocTarget> pairs = new Dictionary<int, MoAllocTarget>();
            foreach (List<MoAllocTarget> list in plan.Allocs.Values)
            {
                for (int i = 0; i < list.Count; i++)
                {
                    MoAllocRow a = list[i].Row;
                    List<int> ids;
                    if (!byKey.TryGetValue(Key(a.MoDId, a.SortSeq, a.InvCode), out ids) || ids.Count != 1)
                    {
                        throw Lost(code, "子件 " + a.InvCode + "（行号 " + a.SortSeq.ToString(CultureInfo.InvariantCulture)
                            + "）重插后对不上唯一一行，关联列没有写回");
                    }
                    pairs[ids[0]] = list[i];
                }
            }
            Untampered(pairs, rows, code);
            if (pairs.Count != rows.Count)
            {
                throw Lost(code, "重插后的子件行数 " + rows.Count.ToString(CultureInfo.InvariantCulture) + " 与修改前 "
                    + pairs.Count.ToString(CultureInfo.InvariantCulture) + " 不同，关联列没有写回");
            }
            return pairs;
        }

        // U8 重插过：现有行的 AllocateId 一个都不在快照里。
        static void Reinserted(MoPlan plan, List<Dictionary<string, object>> rows, string code)
        {
            HashSet<int> old = new HashSet<int>();
            foreach (List<MoAllocTarget> list in plan.Allocs.Values)
            {
                for (int i = 0; i < list.Count; i++)
                {
                    old.Add(list[i].Row.AllocateId);
                }
            }
            for (int i = 0; i < rows.Count; i++)
            {
                if (old.Contains(CoRows.AsId(CoRows.Col(rows[i], "AllocateId"))))
                {
                    throw Lost(code, "子件没有全部重插（仍有修改前的 AllocateId），关联列没有写回");
                }
            }
        }

        // 要写回的列必须仍是 U8 重建后的缺省或已等于快照值；否则可能是别人在 U8 客户端改过，不覆盖。
        static void Untampered(Dictionary<int, MoAllocTarget> pairs, List<Dictionary<string, object>> rows, string code)
        {
            for (int i = 0; i < rows.Count; i++)
            {
                MoAllocTarget t;
                if (!pairs.TryGetValue(CoRows.AsId(CoRows.Col(rows[i], "AllocateId")), out t))
                {
                    continue;
                }
                Dictionary<string, string> now = MoUpdateCols.Read(rows[i]);
                for (int c = 0; c < MoUpdateCols.Restore.Length; c++)
                {
                    string col = MoUpdateCols.Restore[c];
                    if (!Rebuilt(Cell(now, col)) && Cell(now, col) != Cell(t.Row.Keep, col))
                    {
                        throw Lost(code, "子件 " + t.Row.InvCode + " 的 " + col + " 已被改过，关联列没有写回");
                    }
                }
                string upper = CoRows.Col(rows[i], "UpperMoQty");
                if (!Rebuilt(upper) && MoUpdateSql.Dec(upper) != MoUpdateSql.Dec(t.Upper))
                {
                    throw Lost(code, "子件 " + t.Row.InvCode + " 的 UpperMoQty 已被改过，关联列没有写回");
                }
            }
        }

        static string Cell(Dictionary<string, string> keep, string col)
        {
            string value;
            return keep.TryGetValue(col, out value) ? value ?? "" : "";
        }

        // U8 重建子件后的缺省：NULL、空串或数值 0。
        static bool Rebuilt(string value)
        {
            if (value == null || value.Length == 0 || value == MoUpdateCols.NullMark)
            {
                return true;
            }
            decimal n;
            return decimal.TryParse(value, NumberStyles.Number, CultureInfo.InvariantCulture, out n) && n == 0m;
        }

        static string Key(int moDId, int seq, string inv)
        {
            return moDId.ToString(CultureInfo.InvariantCulture) + "|" + seq.ToString(CultureInfo.InvariantCulture)
                + "|" + MoAllocRow.Norm(inv);
        }

        // 参数顺序同 MoUpdateCols.RestoreSql：Restore 各列（NullMark → NULL），UpperMoQty（空 → NULL），AllocateId。
        static object[] Args(MoAllocTarget t, int allocateId)
        {
            List<object> args = new List<object>();
            for (int i = 0; i < MoUpdateCols.Restore.Length; i++)
            {
                string value;
                t.Row.Keep.TryGetValue(MoUpdateCols.Restore[i], out value);
                args.Add(value == null || value == MoUpdateCols.NullMark ? null : value);
            }
            args.Add(t.Upper.Length == 0 ? null : t.Upper);
            args.Add(allocateId);
            return args.ToArray();
        }

        static BridgeException Lost(string code, string text)
        {
            return new BridgeException(504, "outcome_unknown",
                "U8 已提交生产订单 " + code + " 的修改，但" + text + "。修改前的子件关联列原值在审计事件 mo_update_snapshot，"
                + "可据此手工恢复；请到 U8 客户端核对用料");
        }
    }
}
