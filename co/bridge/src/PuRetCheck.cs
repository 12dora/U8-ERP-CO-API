using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 采购退货单的回写核对。生单保存后、删除后都在同一事务里核对，核对不过回滚并 409。
    internal static partial class PuRet
    {
        const string ArrRetSql = "select convert(varchar(40), isnull(fRetQuantity,0)) from PU_ArrivalVouchs where Autoid=?";
        const string PoRetSql = "select convert(varchar(40), isnull(fPoRetQuantity,0)) from PO_Podetails where ID=?";
        const string PoArrSql = "select convert(varchar(40), isnull(iArrQTY,0)) from PO_Podetails where ID=?";

        // ===== 回写核对表：参照到货单三项已实测（生单与删除），参照订单两项已实测（生单、审核、弃审与删除） =====
        // 每行：来源类型（arrival / purchase_order）、键（src = 退货行的来源行 Autoid / 订单行 ID，po = 退货行的 iPOsID）、
        // 说明、读取 SQL、方向。方向 +1：保存后该值增加退货数量（删除后减回）；-1 反之；0：只把前后值记进审计，不核对。
        static readonly RetCheck[] Checks = new RetCheck[]
        {
            Check("arrival", "src", "原到货单行累计退货数量 fRetQuantity", ArrRetSql, 1),
            Check("arrival", "po", "采购订单行累计退货数量 fPoRetQuantity", PoRetSql, 1),
            Check("arrival", "po", "采购订单行累计到货数量 iArrQTY", PoArrSql, -1),
            // 参照采购订单：订单行 iArrQTY 减、fPoRetQuantity 加本次数量（已实测）。
            Check("purchase_order", "src", "采购订单行累计退货数量 fPoRetQuantity", PoRetSql, 1),
            Check("purchase_order", "src", "采购订单行累计到货数量 iArrQTY", PoArrSql, -1)
        };
        // ===== 核对表结束 =====

        static RetCheck Check(string source, string key, string label, string sql, int sign)
        {
            RetCheck check = new RetCheck();
            check.Source = source;
            check.Key = key;
            check.Label = label;
            check.Sql = sql;
            check.Sign = sign;
            return check;
        }

        // 按（核对项, 键值）合并：同一订单行被几行退货引用时，期望变化是这几行数量之和。
        static List<RetSnap> Plan(List<RetKey> keys)
        {
            Dictionary<string, RetSnap> byKey = new Dictionary<string, RetSnap>(StringComparer.Ordinal);
            List<RetSnap> list = new List<RetSnap>();
            for (int i = 0; i < keys.Count; i++)
            {
                for (int c = 0; c < Checks.Length; c++)
                {
                    RetCheck check = Checks[c];
                    int id = check.Key == "po" ? keys[i].Po : keys[i].Src;
                    if (check.Source != keys[i].Source || id <= 0)
                    {
                        continue;
                    }
                    string name = c.ToString(CultureInfo.InvariantCulture) + ":" + id.ToString(CultureInfo.InvariantCulture);
                    RetSnap snap;
                    if (!byKey.TryGetValue(name, out snap))
                    {
                        snap = new RetSnap();
                        snap.Check = check;
                        snap.Id = id;
                        byKey[name] = snap;
                        list.Add(snap);
                    }
                    snap.Qty += keys[i].Qty;
                }
            }
            return list;
        }

        static void ReadBefore(object conn, List<RetSnap> snaps)
        {
            for (int i = 0; i < snaps.Count; i++)
            {
                snaps[i].Before = PuInv.Num(Rows.Scalar(conn, snaps[i].Check.Sql, new object[] { snaps[i].Id }));
            }
        }

        // direction：生单 +1，删除 -1。先把全部前后值记进审计，再逐项核对。
        static void RequireWritten(object conn, WorkItem item, List<RetSnap> snaps, int direction)
        {
            string miss = null;
            for (int i = 0; i < snaps.Count; i++)
            {
                RetSnap snap = snaps[i];
                decimal now = PuInv.Num(Rows.Scalar(conn, snap.Check.Sql, new object[] { snap.Id }));
                CoRows.Note(item, "回写 " + snap.Check.Label + " " + snap.Id.ToString(CultureInfo.InvariantCulture)
                    + " " + Text(snap.Before) + "→" + Text(now));
                decimal expect = direction * snap.Check.Sign * snap.Qty;
                if (snap.Check.Sign != 0 && miss == null && Math.Abs(now - snap.Before - expect) > 0.000001m)
                {
                    miss = snap.Check.Label;
                }
            }
            if (miss != null)
            {
                throw new BridgeException(409, "u8_rejected", "U8 没有按预期回写" + miss);
            }
        }

        static string Text(decimal value)
        {
            return value.ToString("0.######", CultureInfo.InvariantCulture);
        }

        sealed class RetCheck
        {
            public string Source;
            public string Key;
            public string Label;
            public string Sql;
            public int Sign;
        }

        // 一行退货的核对键：来源类型、来源行、订单行和（正的）退货数量。
        sealed class RetKey
        {
            public string Source;
            public int Src;
            public int Po;
            public decimal Qty;
        }

        sealed class RetSnap
        {
            public RetCheck Check;
            public int Id;
            public decimal Qty;
            public decimal Before;
        }
    }
}
