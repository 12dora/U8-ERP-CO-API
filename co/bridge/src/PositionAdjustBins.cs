using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 货位调整单的一行：仓库在表头；结存按（货位、存货、批号、自由项 1–10）合计 InvPositionSum。
    internal sealed class BinLine
    {
        public string Inv = "";
        public string From = "";
        public string To = "";
        public string Batch = "";
        public string[] Free = new string[10];
        public decimal Qty;
    }

    // 货位调整单的货位闸：仓库须是货位管理（Warehouse.bWhPos=1）、调出 / 调入货位都是本仓库的末级货位（Position.bPosEnd=1）、
    // 调出货位结存够。已在测试账套核对：U8 审核时写货位台账 InvPosition（cvouchtype=19，每行一出 bRdFlag=0、一入 bRdFlag=1，
    // RdID = 表头 Id、RdsID = 表体 autoID）并改 InvPositionSum，保存时不写。结存键只拼参数，标识符都是常量。
    internal static class PositionAdjustBins
    {
        internal const char Sep = '\u0001';
        const string WhSql = "select convert(varchar(5), isnull(bWhPos, 0)) as pos, convert(varchar(5), isnull(bProxyWh, 0)) as proxy"
            + " from Warehouse where cWhCode=?";
        const string PosSql = "select convert(varchar(5), isnull(bPosEnd, 0)) as leaf from Position where cWhCode=? and cPosCode=?";
        const string LedgerSql = "select count(*) as n from InvPosition where RdID=? and cvouchtype=N'19'";
        const string ListSql = "select AutoID as id, RdsID as line_id, bRdFlag as inbound, cPosCode as pos, cInvCode as inv,"
            + " cBatch as batch, iQuantity as qty, dDate as date, cHandler as handler"
            + " from InvPosition where RdID=? and cvouchtype=N'19' order by AutoID";
        static readonly string LinesSql = "select cInvCode as inv, cBPosCode as bpos, cAPosCode as apos,"
            + " convert(varchar(40), iQuantity) as qty, cBatch as batch" + FreeCols() + " from AdjustPVouchs where ID=? order by irowno, autoID";
        static readonly string SumSql = "select convert(varchar(40), isnull(sum(iQuantity), 0)) as q from InvPositionSum"
            + " where cWhCode=? and cPosCode=? and cInvCode=? and isnull(cBatch, N'')=?" + FreeWhere();

        static string FreeCols()
        {
            string cols = "";
            for (int i = 1; i <= 10; i++)
            {
                cols += ", cFree" + i.ToString(CultureInfo.InvariantCulture) + " as f" + i.ToString(CultureInfo.InvariantCulture);
            }
            return cols;
        }

        static string FreeWhere()
        {
            string where = "";
            for (int i = 1; i <= 10; i++)
            {
                where += " and isnull(cFree" + i.ToString(CultureInfo.InvariantCulture) + ", N'')=?";
            }
            return where;
        }

        // 新增前：DOM 行（StockDom.AdjustBody 的 rows，列名不分大小写）。
        internal static List<BinLine> FromDom(List<Dictionary<string, string>> rows)
        {
            List<BinLine> list = new List<BinLine>();
            for (int i = 0; i < rows.Count; i++)
            {
                Dictionary<string, object> row = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
                foreach (KeyValuePair<string, string> kv in rows[i])
                {
                    row[kv.Key] = kv.Value;
                }
                list.Add(Line(row, DomCols));
            }
            return list;
        }

        // 审核、弃审前：库里的表体行。
        internal static List<BinLine> FromDb(object conn, int id)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, LinesSql, new object[] { id }, 5000);
            List<BinLine> list = new List<BinLine>();
            for (int i = 0; rows != null && i < rows.Count; i++)
            {
                list.Add(Line(rows[i], DbCols));
            }
            return list;
        }

        // 列名：存货、调出货位、调入货位、数量、批号、自由项前缀（后接 1–10）。
        static readonly string[] DomCols = new string[] { "cInvCode", "cBPosCode", "cAPosCode", "iQuantity", "cBatch", "cFree" };
        static readonly string[] DbCols = new string[] { "inv", "bpos", "apos", "qty", "batch", "f" };

        static BinLine Line(Dictionary<string, object> row, string[] cols)
        {
            BinLine line = new BinLine();
            line.Inv = StockMsg.Col(row, cols[0]);
            line.From = StockMsg.Col(row, cols[1]);
            line.To = StockMsg.Col(row, cols[2]);
            line.Batch = StockMsg.Col(row, cols[4]);
            for (int i = 0; i < 10; i++)
            {
                line.Free[i] = StockMsg.Col(row, cols[5] + (i + 1).ToString(CultureInfo.InvariantCulture));
            }
            StockUnits.Dec(StockMsg.Col(row, cols[3]), out line.Qty);
            return line;
        }

        // 新增前（任何 COM 调用之前）：仓库、货位档案、存货管理方式（PositionAdjustInv）、调出货位结存。
        internal static void CheckNew(object conn, string wh, List<BinLine> lines)
        {
            RequireWh(conn, wh);
            for (int i = 0; i < lines.Count; i++)
            {
                string at = FieldPath.Item("lines", i);
                RequireLeaf(conn, wh, lines[i].From, FieldPath.Join(at, "cbposcode"));
                RequireLeaf(conn, wh, lines[i].To, FieldPath.Join(at, "caposcode"));
                PositionAdjustInv.Require(conn, wh, lines[i], at);
            }
            RequireStock(conn, wh, lines, false);
        }

        // 代管仓（bProxyWh=1，VMI）的结存另按供应商 cvmivencode 分行，桥不写这一维，拒绝。
        static void RequireWh(object conn, string wh)
        {
            Dictionary<string, object> row = Rows.One(conn, WhSql, new object[] { wh });
            if (row == null)
            {
                throw BridgeException.BadField("head.cwhcode", "仓库 " + wh + " 不存在");
            }
            if (StockMsg.Col(row, "pos") != "1")
            {
                throw BridgeException.BadField("head.cwhcode", "仓库 " + wh + " 不是货位管理仓库");
            }
            if (StockMsg.Col(row, "proxy") == "1")
            {
                throw BridgeException.BadField("head.cwhcode", "仓库 " + wh + " 是代管仓（VMI），暂不支持货位调整");
            }
        }

        static void RequireLeaf(object conn, string wh, string pos, string field)
        {
            string leaf = Rows.Scalar(conn, PosSql, new object[] { wh, pos });
            if (leaf == null)
            {
                throw BridgeException.BadField(field, "货位 " + pos + " 不是仓库 " + wh + " 的货位");
            }
            if (leaf.Trim() != "1")
            {
                throw BridgeException.BadField(field, "货位 " + pos + " 不是末级货位");
            }
        }

        // 按结存键的净变动核对（审核、新增按调出算，弃审反过来）：同一张单据里调入本键的行先抵掉调出的行
        // （A→B 10、B→C 10 且 B 原有 0 时 B 净变动 0，放行），净减少的键不能超过当前结存。U8 若逐行处理、
        // 在中间状态拒绝，原文 409 u8_rejected、整笔回滚。
        internal static void RequireStock(object conn, string wh, List<BinLine> lines, bool undo)
        {
            foreach (KeyValuePair<string, decimal> kv in Deltas(lines, undo))
            {
                if (kv.Value >= 0m)
                {
                    continue;
                }
                decimal have = Sum(conn, wh, kv.Key);
                if (have + kv.Value < -0.000001m)
                {
                    string[] part = kv.Key.Split(Sep);
                    throw new BridgeException(409, "stock_shortage", "货位 " + part[0] + " 存货 " + part[1] + " 结存 "
                        + StockUnits.Price(have) + "，" + (undo ? "弃审净退回 " : "净调出 ") + StockUnits.Price(-kv.Value) + "，结存不足");
                }
            }
        }

        // 审核：调出货位减、调入货位加；弃审反过来。键相同的行合计（A→B、B→C 时 B 净变动为两行之差）。
        internal static Dictionary<string, decimal> Deltas(List<BinLine> lines, bool undo)
        {
            Dictionary<string, decimal> map = new Dictionary<string, decimal>(StringComparer.OrdinalIgnoreCase);
            decimal sign = undo ? -1m : 1m;
            for (int i = 0; i < lines.Count; i++)
            {
                Add(map, Key(lines[i], lines[i].From), -sign * lines[i].Qty);
                Add(map, Key(lines[i], lines[i].To), sign * lines[i].Qty);
            }
            return map;
        }

        static void Add(Dictionary<string, decimal> map, string key, decimal qty)
        {
            decimal old;
            map.TryGetValue(key, out old);
            map[key] = old + qty;
        }

        // 结存键：货位、存货、批号、自由项 1–10，去空格后用 Sep 连起来（编码里不会有控制字符）。
        internal static string Key(BinLine line, string pos)
        {
            string[] parts = new string[13];
            parts[0] = pos.Trim();
            parts[1] = line.Inv.Trim();
            parts[2] = line.Batch.Trim();
            for (int i = 0; i < 10; i++)
            {
                parts[3 + i] = (line.Free[i] ?? "").Trim();
            }
            return string.Join(Sep.ToString(), parts);
        }

        internal static decimal Sum(object conn, string wh, string key)
        {
            string[] parts = key.Split(Sep);
            object[] args = new object[14];
            args[0] = wh;
            for (int i = 0; i < 13; i++)
            {
                args[i + 1] = parts[i];
            }
            decimal value;
            StockUnits.Dec(Rows.Scalar(conn, SumSql, args), out value);
            return value;
        }

        // 涉及的每个结存键的当前结存合计。
        internal static Dictionary<string, decimal> Snapshot(object conn, string wh, IEnumerable<string> keys)
        {
            Dictionary<string, decimal> map = new Dictionary<string, decimal>(StringComparer.OrdinalIgnoreCase);
            foreach (string key in keys)
            {
                map[key] = Sum(conn, wh, key);
            }
            return map;
        }

        internal static int LedgerCount(object conn, int id)
        {
            Dictionary<string, object> row = Rows.One(conn, LedgerSql, new object[] { id });
            return row == null ? 0 : CoRows.AsId(CoRows.Col(row, "n"));
        }

        // vouchers/load 的 positions：本单的货位台账（未审核时为空），最多 LedgerCap 行，多了截断并另给 positions_truncated。
        internal const int LedgerCap = 1000;

        internal static void PutLedger(object conn, int id, Dictionary<string, object> body)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, ListSql, new object[] { id }, LedgerCap + 1)
                ?? new List<Dictionary<string, object>>();
            if (rows.Count > LedgerCap)
            {
                rows.RemoveRange(LedgerCap, rows.Count - LedgerCap);
                body["positions_truncated"] = true;
            }
            body["positions"] = rows;
        }
    }
}
