using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 修改的回写核对，都在 RunAt 的同一事务里：Before 在调 U8 之前带 UPDLOCK, HOLDLOCK 读本单各行和来源累计数；
    // After 在提交前重读：本单各行数量要等于请求（没改的行不变、删掉的行不在），每个来源累计数的变化
    // 要等于引用它的本单各行（数量或件数）变化之和。不符 409 u8_rejected，前后值记进审计。
    // U8 在保存里自行提交时由 StockCall.AfterCheck 改报 504。
    internal static partial class StockEditSrc
    {
        // ===== 回写核对表 =====
        // 每项：类型、来源（表头 cSource）、本单表体上指向来源行的列、说明、读取 SQL（参数是该列的值）、
        // 是否按件数（iNum）核对。按件数的项在来源值为 NULL 时只记审计不核对。
        // 未覆盖：修改时的回写全部按生单 / 删除的实测结果推断（销售出库 fOutQuantity 实测按差额回写）。
        static readonly SrcCheck[] Checks = new SrcCheck[]
        {
            Check("sale_out", "发货单", "iDLsID", "发货单行累计出库数量 fOutQuantity", Read("DispatchLists", "fOutQuantity", "iDLsID"), false),
            Check("sale_out", "发货单", "iDLsID", "发货单行累计出库件数 fOutNum", Read("DispatchLists", "fOutNum", "iDLsID"), true),
            Check("purchase_in", "采购订单", "iPOsID", "采购订单行累计入库数量 iReceivedQTY", Read("PO_Podetails", "iReceivedQTY", "ID"), false),
            Check("purchase_in", "来料检验单", "iCheckIdBaks", "来料检验单累计入库数量 FsumQuantity", Read("QMCHECKVOUCHER", "FsumQuantity", "ID"), false),
            Check("purchase_in", "来料检验单", "iArrsId", "到货单行合格入库数量 fValidInQuan", Read("PU_ArrivalVouchs", "fValidInQuan", "Autoid"), false),
            Check("purchase_in", "来料检验单", "iPOsID", "采购订单行累计入库数量 freceivedqty", Read("PO_Podetails", "freceivedqty", "ID"), false),
            Check("product_in", "产品检验单", "iMPoIds", "生产订单行合格入库数量 QualifiedInQty", Read("mom_orderdetail", "QualifiedInQty", "MoDId"), false),
            Check("product_in", "产品检验单", "iCheckIdBaks", "产品检验单累计入库数量 FsumQuantity", Read("QMCHECKVOUCHER", "FsumQuantity", "ID"), false),
            Check("material_out", "生产订单", "iMPoIds", "生产订单子件已领数量 IssQty", Read("mom_moallocate", "IssQty", "AllocateId"), false)
        };
        // ===== 核对表结束 =====

        static SrcCheck Check(string kind, string source, string link, string label, string sql, bool num)
        {
            SrcCheck check = new SrcCheck();
            check.Kind = kind;
            check.Source = source;
            check.Link = link;
            check.Label = label;
            check.Sql = sql;
            check.Num = num;
            return check;
        }

        // 来源累计列的读取：保存前后都带 UPDLOCK, HOLDLOCK。表名、列名只来自核对表常量。
        static string Read(string table, string column, string key)
        {
            return "select convert(varchar(40), " + column + ") from " + table
                + " with (updlock, holdlock) where " + key + "=?";
        }

        static SrcGuard GuardOf(WorkContext ctx, EditReq req, Dictionary<string, decimal> plan)
        {
            SrcGuard guard = new SrcGuard();
            guard.Item = ctx.Item;
            guard.Id = req.Id;
            guard.Plan = plan;
            guard.Checks = new List<SrcCheck>();
            for (int i = 0; i < Checks.Length; i++)
            {
                if (Checks[i].Kind == req.Kind.Name && Checks[i].Source == req.Source)
                {
                    guard.Checks.Add(Checks[i]);
                }
            }
            guard.RowSql = RowSql(req.Kind, guard.Checks);
            return guard;
        }

        // 表名来自 VoucherKind，列名来自核对表常量。
        static string RowSql(VoucherKind kind, List<SrcCheck> checks)
        {
            string sql = "select convert(varchar(20), AutoID) as AutoID, convert(varchar(40), iQuantity) as Qty,"
                + " convert(varchar(40), iNum) as Num";
            List<string> seen = new List<string>();
            for (int i = 0; i < checks.Count; i++)
            {
                if (seen.Contains(checks[i].Link))
                {
                    continue;
                }
                seen.Add(checks[i].Link);
                sql = sql + ", convert(varchar(20), " + checks[i].Link + ") as " + checks[i].Link;
            }
            return sql + " from " + kind.BodyTable + " with (updlock, holdlock) where " + kind.BodyFk + "=?";
        }

        static Dictionary<string, SrcRow> ReadRows(object conn, string sql, int id)
        {
            Dictionary<string, SrcRow> map = new Dictionary<string, SrcRow>(StringComparer.Ordinal);
            List<Dictionary<string, object>> rows = Rows.Query(conn, sql, new object[] { id }, 1000);
            for (int i = 0; rows != null && i < rows.Count; i++)
            {
                SrcRow row = new SrcRow();
                row.Qty = Dec(CoRows.Col(rows[i], "Qty"));
                row.Num = Dec(CoRows.Col(rows[i], "Num"));
                row.Cells = rows[i];
                map[IdText(CoRows.Col(rows[i], "AutoID"))] = row;
            }
            return map;
        }

        static decimal Dec(string text)
        {
            decimal value;
            return StockUnits.Dec(text, out value) ? value : 0m;
        }

        static string IdText(string text)
        {
            decimal num;
            if (!StockUnits.Dec(text, out num))
            {
                return "";
            }
            return decimal.Truncate(num).ToString(CultureInfo.InvariantCulture);
        }

        static string Show(decimal value)
        {
            return value.ToString("0.######", CultureInfo.InvariantCulture);
        }

        sealed class SrcCheck
        {
            public string Kind;
            public string Source;
            public string Link;
            public string Label;
            public string Sql;
            public bool Num;
        }

        sealed class SrcRow
        {
            public decimal Qty;
            public decimal Num;
            public Dictionary<string, object> Cells;
        }

        // 一个来源行的一项累计：保存前的值（NULL 记 Null）。
        sealed class SrcSnap
        {
            public SrcCheck Check;
            public string Key;
            public decimal Before;
            public bool Null;
        }

        sealed class SrcGuard
        {
            public WorkItem Item;
            public int Id;
            public Dictionary<string, decimal> Plan;
            public List<SrcCheck> Checks;
            public string RowSql;
            Dictionary<string, SrcRow> _rows;
            readonly List<SrcSnap> _snaps = new List<SrcSnap>();

            public void Before(object conn)
            {
                _rows = ReadRows(conn, RowSql, Id);
                foreach (KeyValuePair<string, decimal> kv in Plan)
                {
                    if (!_rows.ContainsKey(kv.Key))
                    {
                        throw new BridgeException(409, "state_mismatch", "明细行已被修改，请重新读取");
                    }
                }
                for (int c = 0; c < Checks.Count; c++)
                {
                    SnapAll(conn, Checks[c]);
                }
            }

            void SnapAll(object conn, SrcCheck check)
            {
                List<string> keys = new List<string>();
                foreach (SrcRow row in _rows.Values)
                {
                    string key = IdText(CoRows.Col(row.Cells, check.Link));
                    if (key.Length == 0 || key == "0" || keys.Contains(key))
                    {
                        continue;
                    }
                    keys.Add(key);
                    SrcSnap snap = new SrcSnap();
                    snap.Check = check;
                    snap.Key = key;
                    string raw = Rows.Scalar(conn, check.Sql, new object[] { int.Parse(key, CultureInfo.InvariantCulture) });
                    snap.Null = raw == null;
                    snap.Before = Dec(raw);
                    _snaps.Add(snap);
                }
            }

            public void After(object conn)
            {
                Dictionary<string, SrcRow> now = ReadRows(conn, RowSql, Id);
                RequireRows(now);
                string miss = null;
                for (int i = 0; i < _snaps.Count; i++)
                {
                    if (!Written(conn, _snaps[i], now) && miss == null)
                    {
                        miss = _snaps[i].Check.Label;
                    }
                }
                if (miss != null)
                {
                    throw new BridgeException(409, "u8_rejected", "U8 没有按预期回写" + miss);
                }
            }

            // 本单各行：改了数量的等于请求，删掉的不在，其余不变；也不能多出行。
            void RequireRows(Dictionary<string, SrcRow> now)
            {
                foreach (KeyValuePair<string, SrcRow> kv in _rows)
                {
                    decimal want;
                    if (!Plan.TryGetValue(kv.Key, out want))
                    {
                        want = kv.Value.Qty;
                    }
                    SrcRow got;
                    decimal have = now.TryGetValue(kv.Key, out got) ? got.Qty : 0m;
                    bool gone = want == 0m && got == null;
                    if (!gone && (got == null || Math.Abs(have - want) > 0.000001m))
                    {
                        CoRows.Note(Item, "行 " + kv.Key + " 数量 " + Show(kv.Value.Qty) + "→" + Show(have) + " 应为 " + Show(want));
                        throw new BridgeException(409, "u8_rejected", "U8 保存后的明细数量与请求不符");
                    }
                }
                if (now.Count > _rows.Count)
                {
                    throw new BridgeException(409, "u8_rejected", "U8 保存后的明细行数与请求不符");
                }
            }

            bool Written(object conn, SrcSnap snap, Dictionary<string, SrcRow> now)
            {
                decimal delta = Delta(snap, now);
                string raw = Rows.Scalar(conn, snap.Check.Sql, new object[] { int.Parse(snap.Key, CultureInfo.InvariantCulture) });
                decimal after = Dec(raw);
                CoRows.Note(Item, "回写 " + snap.Check.Label + " " + snap.Key + " " + Show(snap.Before) + "→" + Show(after)
                    + " 期望变化 " + Show(delta));
                if (snap.Check.Num && snap.Null)
                {
                    return true;
                }
                return Math.Abs(after - snap.Before - delta) <= 0.000001m;
            }

            // 引用该来源行的本单各行，保存后减保存前（删掉的行保存后按 0）。
            decimal Delta(SrcSnap snap, Dictionary<string, SrcRow> now)
            {
                decimal delta = 0m;
                foreach (KeyValuePair<string, SrcRow> kv in _rows)
                {
                    if (IdText(CoRows.Col(kv.Value.Cells, snap.Check.Link)) != snap.Key)
                    {
                        continue;
                    }
                    SrcRow got;
                    now.TryGetValue(kv.Key, out got);
                    decimal before = snap.Check.Num ? kv.Value.Num : kv.Value.Qty;
                    decimal after = got == null ? 0m : (snap.Check.Num ? got.Num : got.Qty);
                    delta = delta + after - before;
                }
                return delta;
            }
        }
    }
}
