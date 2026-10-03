using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 生产订单修改的并发领料：调用前的抓取（MoUpdateRemap.Capture）在请求连接上、不加锁，到 U8 提交之间可能有人在 U8 客户端
    // 或别的路由按旧子件领料（DocLocks 只挡桥自己由这张订单生单）。MoUpdateRemap 在改写事务里用 UPDLOCK, HOLDLOCK
    // 重读指向快照全部旧 AllocateId 的 rdrecords11 行（ix_rdrecords11_iMPoIds 有索引；范围锁挡住之后再插的行），
    // 抓取时没有的补进来一并改写；子件不在快照里或存货不同（替代料出库）的改不准，504。
    internal static class MoUpdateRemapLate
    {
        const int Chunk = 100;
        const int RowsMax = 100000;
        const string ReadHead = "select convert(varchar(20), r.AutoID) as AutoID, convert(varchar(20), r.ID) as ID,"
            + " convert(varchar(20), r.iMPoIds) as AllocateId, r.cInvCode, convert(varchar(40), r.iQuantity) as Qty"
            + " from rdrecords11 r with (updlock, holdlock) where r.iMPoIds in (";

        // 指向快照里任一旧 AllocateId 的出库行（每批 Chunk 个参数，SQL 里只有 ?）。
        public static List<Dictionary<string, object>> Read(object conn, MoSnap before)
        {
            List<Dictionary<string, object>> all = new List<Dictionary<string, object>>();
            for (int start = 0; start < before.Allocs.Count; start += Chunk)
            {
                int n = System.Math.Min(Chunk, before.Allocs.Count - start);
                object[] args = new object[n];
                for (int i = 0; i < n; i++)
                {
                    args[i] = before.Allocs[start + i].AllocateId;
                }
                all.AddRange(Rows.Query(conn, ReadSql(n), args, RowsMax));
            }
            return all;
        }

        internal static string ReadSql(int n)
        {
            StringBuilder sb = new StringBuilder(ReadHead);
            for (int i = 0; i < n; i++)
            {
                sb.Append(i == 0 ? "?" : ",?");
            }
            return sb.Append(")").ToString();
        }

        // 抓取的行 + 重读到而抓取时没有的行（按 AutoID 去重，标 Late）。补进来的行子件对不上快照或存货不同 504。纯逻辑，--selftest 覆盖。
        internal static MoRefs Merge(MoRefs refs, List<Dictionary<string, object>> rows, MoSnap before)
        {
            MoRefs all = new MoRefs();
            all.Issued = refs.Issued;
            all.Items.AddRange(refs.Items);
            HashSet<string> seen = new HashSet<string>();
            for (int i = 0; i < refs.Items.Count; i++)
            {
                seen.Add(refs.Items[i].AutoId);
            }
            for (int i = 0; i < rows.Count; i++)
            {
                MoRef r = MoUpdateRemap.RefOf(rows[i], before);
                if (!seen.Add(r.AutoId))
                {
                    continue;
                }
                if (!MoUpdateRemap.Matches(r))
                {
                    throw MoUpdateRemap.Lost(before.Code, "调用期间新增的材料出库单行 " + r.AutoId + "（存货 " + r.Inv
                        + "）与子件对不上，桥改不准");
                }
                r.Late = true;
                all.Items.Add(r);
            }
            return all;
        }

        // 有补进来的出库行的旧 AllocateId（这些子件的已领量会随并发领料变化）。
        internal static HashSet<int> Grown(MoRefs refs)
        {
            HashSet<int> ids = new HashSet<int>();
            for (int i = 0; i < refs.Items.Count; i++)
            {
                if (refs.Items[i].Late)
                {
                    ids.Add(refs.Items[i].OldId);
                }
            }
            return ids;
        }

        // 审计事件用：补进来的出库行 AutoID。
        internal static List<object> LateIds(MoRefs refs)
        {
            List<object> ids = new List<object>();
            if (refs == null)
            {
                return ids;
            }
            for (int i = 0; i < refs.Items.Count; i++)
            {
                if (refs.Items[i].Late)
                {
                    ids.Add(refs.Items[i].AutoId);
                }
            }
            return ids;
        }
    }
}
