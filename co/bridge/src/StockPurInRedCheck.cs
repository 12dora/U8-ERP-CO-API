using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 红字采购入库（退库，库存是出的方向）的预检，调用 U8 之前、只查不写，与销售出库的批号 / 货位预检
    // 共用同一套可用量 SQL（StockGenSaleBatchCheck）：
    // 批次存货必须填批号，保质期管理的存货暂不支持红字（409）；每行在表头仓库里的可用量（按批号、自由项；批号为空即非批次）
    // 不够 409 stock_shortage；填了货位的行，货位上该存货（批号、自由项）的结存不够 409 stock_shortage。多行按同一键合计。
    // 货位仓库必须填货位、货位存在且末级，由 StockPurInPos.CheckCreate 先查。
    internal static partial class StockGen
    {
        const string RedInvSql = "select convert(varchar(5), isnull(bInvBatch,0)) as batch,"
            + " convert(varchar(5), isnull(bInvQuality,0)) as quality from Inventory where cInvCode=?";

        internal static void CheckRedIn(object conn, Dictionary<string, object> head, object[] lines)
        {
            string wh = CoRows.Col(head, "cwhcode");
            if (wh.Length == 0 || lines == null)
            {
                return;
            }
            Dictionary<string, decimal> stockNeed = new Dictionary<string, decimal>(StringComparer.OrdinalIgnoreCase);
            Dictionary<string, decimal> posNeed = new Dictionary<string, decimal>(StringComparer.OrdinalIgnoreCase);
            Dictionary<string, object[]> args = new Dictionary<string, object[]>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < lines.Length; i++)
            {
                Dictionary<string, object> line = lines[i] as Dictionary<string, object>;
                decimal qty;
                if (line == null || !StockUnits.Dec(CoRows.Col(line, "iquantity"), out qty) || qty <= 0m)
                {
                    continue;
                }
                RefuseRedInv(conn, line);
                RedNeed(line, wh, qty, stockNeed, args);
                RedPosNeed(line, wh, qty, posNeed, args);
            }
            EnoughStock(conn, stockNeed, args, CurStockSql, "存货");
            EnoughStock(conn, posNeed, args, PosStockSql, "货位");
        }

        static void RefuseRedInv(object conn, Dictionary<string, object> line)
        {
            string inv = CoRows.Col(line, "cinvcode");
            Dictionary<string, object> row = Rows.One(conn, RedInvSql, new object[] { inv });
            if (row == null)
            {
                return;
            }
            if (CoRows.FlagOf(row, "quality"))
            {
                throw new BridgeException(409, "state_mismatch", "存货 " + inv + " 启用保质期管理，桥暂不支持红字采购入库");
            }
            if (CoRows.FlagOf(row, "batch") && CoRows.Col(line, "cbatch").Length == 0)
            {
                throw new BridgeException(400, "bad_request", "存货 " + inv + " 启用批次或保质期管理，必须填批号 cbatch");
            }
        }

        // 表头仓库的可用量：（仓库、存货、批号、自由项）。
        static void RedNeed(Dictionary<string, object> line, string wh, decimal qty,
            Dictionary<string, decimal> need, Dictionary<string, object[]> args)
        {
            List<object> key = new List<object>();
            key.Add(wh);
            key.Add(CoRows.Col(line, "cinvcode"));
            key.Add(CoRows.Col(line, "cbatch"));
            AddLineFree(key, line);
            Accumulate(need, args, "S", key.ToArray(), qty);
        }

        // 货位结存：（仓库、货位、存货、批号、批号、自由项），同 PosStockSql 的参数顺序。
        static void RedPosNeed(Dictionary<string, object> line, string wh, decimal qty,
            Dictionary<string, decimal> need, Dictionary<string, object[]> args)
        {
            string pos = CoRows.Col(line, "cposition");
            if (pos.Length == 0)
            {
                return;
            }
            string batch = CoRows.Col(line, "cbatch");
            List<object> key = new List<object>();
            key.Add(wh);
            key.Add(pos);
            key.Add(CoRows.Col(line, "cinvcode"));
            key.Add(batch);
            key.Add(batch);
            AddLineFree(key, line);
            Accumulate(need, args, "P", key.ToArray(), qty);
        }

        static void AddLineFree(List<object> key, Dictionary<string, object> line)
        {
            for (int i = 1; i <= FreeCount; i++)
            {
                key.Add(CoRows.Col(line, "cfree" + i.ToString(CultureInfo.InvariantCulture)));
            }
        }
    }
}
