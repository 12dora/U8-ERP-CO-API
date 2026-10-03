using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 货位闸。实测：单据有货位台账 InvPosition 时，Delete 的 bList（第 9 个参数）为 false 会在服务里弹模态界面卡死；
    // 为 true 而不先清货位则回「存货[..]已经指定了货位」。所以库存删除在事务里先 ClearPosition，再以 bList=true 删除
    // （ClearOnDelete，u8-notes §8；库存删除与材料出库、产成品入库的删除 MfgGen.Delete 都走它）。修改时有货位的行只能改备注和自定义项，
    // 已有行不改货位（Update 改货位未实测）。
    // InvPosition.cvouchtype 与库存单据类型短码一致（01、09、32 已在账套数据上核对），按 RdID + 短码查。
    internal static class StockPosGuard
    {
        const string CountSql = "select count(*) as n from InvPosition where RdID=? and cvouchtype=?";

        static int Count(object conn, VoucherKind kind, int id)
        {
            if (kind == null || kind.StType == null || kind.StType.Length == 0)
            {
                return 0;
            }
            Dictionary<string, object> row = Rows.One(conn, CountSql, new object[] { id, kind.StType });
            return row == null ? 0 : CoRows.AsId(CoRows.Col(row, "n"));
        }

        // 库存删除（StockCo.Delete）：Before 里有货位记录就 ClearPosition 并把 Delete 的 bList 改成 true，
        // 没有货位记录照旧 bList=false；After 里确认货位记录和表头都已没有。采购入库另查发票、结算。原有 Before / After 照常执行。
        public static void ClearOnDelete(WorkContext ctx, object co, StockAt at, VoucherKind kind, int id)
        {
            DelClear clear = new DelClear();
            clear.Ctx = ctx;
            clear.Co = co;
            clear.At = at;
            clear.Kind = kind;
            clear.Id = id;
            clear.Inner = at.Before;
            clear.InnerAfter = at.After;
            at.Before = clear.Before;
            at.After = clear.After;
        }

        // USERPCO ClearPosition("<RdID>", err, "<类型短码>", conn)，by-ref {1,2,3}。返回 false 或 err 非空即 409。
        static void Clear(WorkContext ctx, object co, VoucherKind kind, int id)
        {
            object[] args = new object[] { id.ToString(CultureInfo.InvariantCulture), "", kind.StType, ctx.Conn };
            try
            {
                object ret = ComUtil.CallRef(co, "ClearPosition", args, new int[] { 1, 2, 3 });
                string err = Values.Text(args[1]).Trim();
                if (!Values.Flag(ret) || err.Length > 0)
                {
                    CoRows.Note(ctx.Item, "ClearPosition " + err);
                    throw new BridgeException(409, "u8_rejected", err.Length > 0 ? err : "U8 清除货位失败");
                }
            }
            finally
            {
                StockCall.SeenConn(ctx, ctx.Conn, args, 3);
            }
        }

        const string LinesSql = "select convert(varchar(20), RdsID) as rds from InvPosition where RdID=? and cvouchtype=?";

        // 单据在 InvPosition 里有记录的行（RdsID）。没有记录返回空集。
        public static HashSet<int> BinnedLines(object conn, VoucherKind kind, int id)
        {
            HashSet<int> set = new HashSet<int>();
            if (kind == null || kind.StType == null || kind.StType.Length == 0)
            {
                return set;
            }
            List<Dictionary<string, object>> rows = Rows.Query(conn, LinesSql, new object[] { id, kind.StType }, 5000);
            for (int i = 0; rows != null && i < rows.Count; i++)
            {
                set.Add(CoRows.AsId(CoRows.Col(rows[i], "rds")));
            }
            return set;
        }

        // 有货位记录的单据：不能换仓库。
        public static void RefuseWhChange(HashSet<int> bins, object head, Dictionary<string, object> fields)
        {
            if (bins == null || bins.Count == 0 || !StockPurIn.Sent(fields, "cwhcode"))
            {
                return;
            }
            string next = CoRows.Col(fields, "cwhcode");
            if (!string.Equals(next, DomRows.Get(head, "cWhCode").Trim(), StringComparison.OrdinalIgnoreCase))
            {
                throw new BridgeException(409, "state_mismatch", "单据有货位记录，桥暂不支持换仓库，请在 U8 客户端修改");
            }
        }

        // 有货位的行（InvPosition 有该行，或行上 cPosition 非空）：不能删行，只能改备注和自定义项。
        // 货位本身的改动另由 RefuseLineChange 判断。
        public static void RefuseBinnedEdit(HashSet<int> bins, object row, string lineCol, string op,
            Dictionary<string, object> fields)
        {
            if (row == null || !Binned(bins, row, lineCol))
            {
                return;
            }
            if (op == "delete")
            {
                throw new BridgeException(409, "state_mismatch", "该行有货位记录，桥暂不支持删除，请在 U8 客户端修改");
            }
            foreach (KeyValuePair<string, object> kv in fields)
            {
                string low = kv.Key == null ? "" : kv.Key.ToLowerInvariant();
                if (low == "cbmemo" || low == "cposition" || low.StartsWith("cdefine", StringComparison.Ordinal))
                {
                    continue;
                }
                throw new BridgeException(409, "state_mismatch", "该行有货位记录，桥暂只能改备注和自定义项，请在 U8 客户端修改");
            }
        }

        static bool Binned(HashSet<int> bins, object row, string lineCol)
        {
            if (bins == null || bins.Count == 0)
            {
                return false;
            }
            if (DomRows.Get(row, "cPosition").Trim().Length > 0)
            {
                return true;
            }
            return bins.Contains(CoRows.AsId(DomRows.Get(row, lineCol)));
        }

        // 修改已有行：送了 cposition 且与行上现值不同就拒绝。新增行不受限。
        public static void RefuseLineChange(object row, Dictionary<string, object> fields)
        {
            if (!StockPurIn.Sent(fields, "cposition"))
            {
                return;
            }
            string next = CoRows.Col(fields, "cposition");
            string prev = DomRows.Get(row, "cPosition").Trim();
            if (!string.Equals(next, prev, StringComparison.OrdinalIgnoreCase))
            {
                throw new BridgeException(400, "bad_request", "暂不支持修改已有行的货位，请在 U8 客户端修改");
            }
        }

        sealed class DelClear
        {
            const string HeadLeft = "select count(*) as n from {0} where {1}=?";

            public WorkContext Ctx;
            public object Co;
            public StockAt At;
            public VoucherKind Kind;
            public int Id;
            public StockCheck Inner;
            public StockCheck InnerAfter;
            bool binned;

            public void Before(object conn)
            {
                if (Kind.StType == "01" && StockCo.Billed(conn, Kind, Id))
                {
                    throw new BridgeException(409, "state_mismatch", "单据已有下游单据");
                }
                if (Inner != null)
                {
                    Inner(conn);
                }
                if (Count(conn, Kind, Id) == 0)
                {
                    return;
                }
                Clear(Ctx, Co, Kind, Id);
                At.Args[8] = true;
                binned = true;
            }

            public void After(object conn)
            {
                if (InnerAfter != null)
                {
                    InnerAfter(conn);
                }
                if (!binned)
                {
                    return;
                }
                string sql = string.Format(CultureInfo.InvariantCulture, HeadLeft, Kind.HeadTable, Kind.IdColumn);
                Dictionary<string, object> row = Rows.One(conn, sql, new object[] { Id });
                int head = row == null ? 0 : CoRows.AsId(CoRows.Col(row, "n"));
                if (Count(conn, Kind, Id) > 0 || head > 0)
                {
                    throw new BridgeException(409, "u8_rejected", "U8 删除后货位记录或单据仍在");
                }
            }
        }
    }
}
