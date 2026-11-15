using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 销售出库生单带批号 / 货位时的预检，在 MakeOutVouch 之前、只查不写：
    // 批号只给批次管理的存货（Inventory.bInvBatch），要在发货行的仓库里有足够的可用量（u8-notes §13 的公式，按自由项匹配）；
    // 货位只给货位管理的仓库（Warehouse.bWhPos），要是该仓库的末级货位，货位存量 InvPositionSum 够出。
    // 拆行时批次存货每次都要有批号、货位仓库每次都要有货位。仓库不能改（出库单表头仓库跟发货行，改仓库请在发货单上改）。
    // 可用量只作预检：多行合计按（仓库、存货、批号、自由项）/（仓库、货位、存货、批号、自由项）累加；最终以 U8 保存时的检查为准。
    internal static partial class StockGen
    {
        const int FreeCount = 10;

        static readonly string OutLineSql = BuildOutLineSql();
        static readonly string CurStockSql = "select convert(varchar(40), isnull(sum(case when isnull(bStopFlag,0)=1"
            + " or isnull(bGSPStop,0)=1 then 0 else isnull(iQuantity,0)-isnull(fStopQuantity,0) end),0))"
            + " from CurrentStock where cWhCode=? and cInvCode=? and isnull(cBatch,'')=?" + FreeWhere();
        static readonly string PosStockSql = "select convert(varchar(40), isnull(sum(isnull(iQuantity,0)),0))"
            + " from InvPositionSum where cWhCode=? and cPosCode=? and cInvCode=? and (?='' or isnull(cBatch,'')=?)" + FreeWhere();
        const string OutPosSql = "select convert(varchar(5), isnull(bPosEnd,0)) as posend from Position"
            + " where cPosCode=? and cWhCode=?";

        static string BuildOutLineSql()
        {
            StringBuilder sql = new StringBuilder();
            sql.Append("select convert(varchar(20), d.iDLsID) as line, d.cInvCode as inv, isnull(d.cWhCode,'') as wh,");
            sql.Append(" isnull(d.cBatch,'') as batch,");
            for (int i = 1; i <= FreeCount; i++)
            {
                string n = i.ToString(CultureInfo.InvariantCulture);
                sql.Append(" isnull(d.cFree" + n + ",'') as f" + n + ",");
            }
            sql.Append(" convert(varchar(5), isnull(i.bInvBatch,0)) as invbatch,");
            sql.Append(" convert(varchar(5), isnull(i.bInvQuality,0)) as quality,");
            sql.Append(" convert(varchar(5), isnull(w.bWhPos,0)) as whpos");
            sql.Append(" from DispatchLists d left join Inventory i on i.cInvCode=d.cInvCode");
            sql.Append(" left join Warehouse w on w.cWhCode=d.cWhCode where d.DLID=?");
            return sql.ToString();
        }

        static string FreeWhere()
        {
            StringBuilder sql = new StringBuilder();
            for (int i = 1; i <= FreeCount; i++)
            {
                sql.Append(" and isnull(cFree" + i.ToString(CultureInfo.InvariantCulture) + ",'')=?");
            }
            return sql.ToString();
        }

        // 预检入口：ask.Any 为假时不调用。
        static void CheckOutStock(object conn, int dlid, OutAsk ask)
        {
            Dictionary<int, Dictionary<string, object>> info = OutLineInfo(conn, dlid);
            Dictionary<string, decimal> batchNeed = new Dictionary<string, decimal>(StringComparer.OrdinalIgnoreCase);
            Dictionary<string, decimal> posNeed = new Dictionary<string, decimal>(StringComparer.OrdinalIgnoreCase);
            Dictionary<string, object[]> args = new Dictionary<string, object[]>(StringComparer.OrdinalIgnoreCase);
            foreach (KeyValuePair<int, List<OutSpec>> kv in ask.ByLine)
            {
                Dictionary<string, object> row;
                if (!info.TryGetValue(kv.Key, out row))
                {
                    throw new BridgeException(400, "bad_request", "明细行不存在");
                }
                bool split = kv.Value.Count > 1;
                for (int i = 0; i < kv.Value.Count; i++)
                {
                    OutSpec spec = kv.Value[i];
                    RefuseSpec(conn, row, spec, split);
                    NeedBatch(row, spec, batchNeed, args);
                    NeedPos(row, spec, posNeed, args);
                }
            }
            EnoughStock(conn, batchNeed, args, CurStockSql, "批号");
            EnoughStock(conn, posNeed, args, PosStockSql, "货位");
        }

        static Dictionary<int, Dictionary<string, object>> OutLineInfo(object conn, int dlid)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, OutLineSql, new object[] { dlid }, 5000);
            Dictionary<int, Dictionary<string, object>> map = new Dictionary<int, Dictionary<string, object>>();
            for (int i = 0; rows != null && i < rows.Count; i++)
            {
                map[CoRows.AsId(CoRows.Col(rows[i], "line"))] = rows[i];
            }
            return map;
        }

        static void RefuseSpec(object conn, Dictionary<string, object> row, OutSpec spec, bool split)
        {
            string inv = CoRows.Col(row, "inv");
            string wh = CoRows.Col(row, "wh");
            bool batch = CoRows.FlagOf(row, "invbatch");
            bool binned = CoRows.FlagOf(row, "whpos");
            RefuseLotSpec(row, spec, inv, batch);
            if ((spec.Pos.Length > 0 || spec.Batch.Length > 0) && wh.Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "发货单行没有仓库，不能填批号 cbatch 或货位 cposition");
            }
            if (spec.Pos.Length > 0 && !binned)
            {
                throw new BridgeException(400, "bad_request", "仓库 " + wh + " 未启用货位管理，不能填货位 cposition");
            }
            if (split)
            {
                RefuseSplit(inv, wh, batch, binned, spec);
            }
            if (spec.Pos.Length > 0)
            {
                RefusePosition(conn, wh, spec.Pos);
            }
        }

        static void RefuseLotSpec(Dictionary<string, object> row, OutSpec spec, string inv, bool batch)
        {
            if (spec.Batch.Length > 0 && !batch)
            {
                throw new BridgeException(400, "bad_request", "存货 " + inv + " 未启用批次管理，不能填批号 cbatch");
            }
            if (spec.Batch.Length > 0 && CoRows.FlagOf(row, "quality"))
            {
                throw new BridgeException(409, "state_mismatch", "存货 " + inv + " 启用保质期管理，桥暂不支持出库时指定批号");
            }
        }

        static void RefuseSplit(string inv, string wh, bool batch, bool binned, OutSpec spec)
        {
            if (!batch && !binned)
            {
                throw new BridgeException(400, "bad_request", "存货 " + inv + " 不管批次、仓库 " + wh + " 不管货位，同一发货行不能拆成多行");
            }
            if (batch && spec.Batch.Length == 0)
            {
                throw new BridgeException(400, "bad_request", "存货 " + inv + " 启用批次管理，拆行时每行都要填批号 cbatch");
            }
            if (binned && spec.Pos.Length == 0)
            {
                throw new BridgeException(400, "bad_request", "仓库 " + wh + " 有货位管理，拆行时每行都要填货位 cposition");
            }
        }

        static void RefusePosition(object conn, string wh, string pos)
        {
            Dictionary<string, object> found = Rows.One(conn, OutPosSql, new object[] { pos, wh });
            if (found == null)
            {
                throw new BridgeException(400, "bad_request", "货位不存在或不属于仓库 " + wh + "：" + pos);
            }
            if (!CoRows.FlagOf(found, "posend"))
            {
                throw new BridgeException(400, "bad_request", "货位不是末级：" + pos);
            }
        }

        // 给了批号的：按（仓库、存货、批号、自由项）累加要出的数量。
        static void NeedBatch(Dictionary<string, object> row, OutSpec spec,
            Dictionary<string, decimal> need, Dictionary<string, object[]> args)
        {
            if (spec.Batch.Length == 0)
            {
                return;
            }
            List<object> list = new List<object>();
            list.Add(CoRows.Col(row, "wh"));
            list.Add(CoRows.Col(row, "inv"));
            list.Add(spec.Batch);
            AddFree(list, row);
            Accumulate(need, args, "B", list.ToArray(), spec.Qty);
        }

        // 给了货位的：按（仓库、货位、存货、批号、自由项）累加。批号取请求的，没给取发货行的（U8 照发货行带出）；
        // 批次存货两者都空时不按批号过滤。
        static void NeedPos(Dictionary<string, object> row, OutSpec spec,
            Dictionary<string, decimal> need, Dictionary<string, object[]> args)
        {
            if (spec.Pos.Length == 0)
            {
                return;
            }
            string batch = spec.Batch.Length > 0 ? spec.Batch : CoRows.Col(row, "batch");
            List<object> list = new List<object>();
            list.Add(CoRows.Col(row, "wh"));
            list.Add(spec.Pos);
            list.Add(CoRows.Col(row, "inv"));
            list.Add(batch);
            list.Add(batch);
            AddFree(list, row);
            Accumulate(need, args, "P", list.ToArray(), spec.Qty);
        }

        static void AddFree(List<object> list, Dictionary<string, object> row)
        {
            for (int i = 1; i <= FreeCount; i++)
            {
                list.Add(CoRows.Col(row, "f" + i.ToString(CultureInfo.InvariantCulture)));
            }
        }

        static void Accumulate(Dictionary<string, decimal> need, Dictionary<string, object[]> args,
            string tag, object[] key, decimal qty)
        {
            string[] parts = new string[key.Length];
            for (int i = 0; i < key.Length; i++)
            {
                parts[i] = Values.Text(key[i]);
            }
            string id = tag + "\u0001" + string.Join("\u0001", parts);
            decimal sum;
            need.TryGetValue(id, out sum);
            need[id] = sum + qty;
            args[id] = key;
        }

        static void EnoughStock(object conn, Dictionary<string, decimal> need, Dictionary<string, object[]> args,
            string sql, string what)
        {
            foreach (KeyValuePair<string, decimal> kv in need)
            {
                object[] key = args[kv.Key];
                decimal have;
                if (!StockUnits.Dec(Rows.Scalar(conn, sql, key), out have))
                {
                    have = 0m;
                }
                if (have + 0.000001m < kv.Value)
                {
                    throw new BridgeException(409, "stock_shortage", Shortage(what, key, have, kv.Value));
                }
            }
        }

        // 消息：批号 <批号> 在仓库 <仓库> 可用量 x，不够出 y；货位 <货位> 上存货 <存货> 结存 x，不够出 y。
        static string Shortage(string what, object[] key, decimal have, decimal want)
        {
            string wh = Values.Text(key[0]);
            if (what == "货位")
            {
                return "仓库 " + wh + " 货位 " + Values.Text(key[1]) + " 上存货 " + Values.Text(key[2]) + " 的结存 "
                    + StockUnits.Price(have) + "，不够出 " + StockUnits.Price(want);
            }
            // 批号 / 存货（红字采购入库，批号可空）：存货 X [批号 B] 在仓库 W 的可用量。
            string lot = Values.Text(key[2]);
            string text = "存货 " + Values.Text(key[1]) + (lot.Length > 0 ? " 批号 " + lot : "") + " 在仓库 " + wh + " 的可用量 ";
            return text + StockUnits.Price(have) + "，不够出 " + StockUnits.Price(want);
        }
    }
}
