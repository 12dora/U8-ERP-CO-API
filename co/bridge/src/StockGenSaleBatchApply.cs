using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 销售出库生单的第 3 步（未经实测）：数量已按请求改好（OutPart.Settle 的第 2 步）之后，按请求写批号 / 货位、拆行。
    // 同第 2 步的改单路径：USERPCO Load 在事务外，新事务里 Update("32")：
    // 发货行对应的第 1 行改成请求的第 1 次（数量、cBatch、cPosition，editprop=M），多出的次数克隆这一行（editprop=A，
    // 同一 iDLsID），多出的行 editprop=D。货位 DOM 传空的，由 U8 写 InvPosition（同无来源采购入库）。
    // U8 已按发货行带出了同样的批号 / 货位、数量也一样时不改。单据已有货位记录（InvPosition）又要改时拒绝（ClearPosition 后改未实测）。
    // 提交前核对：发货行 fOutQuantity = 基线 + 应出；每个发货行的出库行与请求一一对上；有货位的行 InvPosition 有对应记录；
    // 指定了批号的行批次属性与 AA_BatchProperty 一致。只有数量的行（没有批号、货位）不动 U8 生成的行。
    // 任何失败都由 OutPart.Settle 走补偿（删掉本次生成的全部出库单）。
    internal static partial class StockGen
    {
        static readonly string OutRowsSql = "select convert(varchar(20), b.ID) as id, convert(varchar(20), b.AutoID) as autoid,"
            + " convert(varchar(20), b.iDLsID) as line, convert(varchar(40), isnull(b.iQuantity,0)) as qty,"
            + " isnull(b.cBatch,'') as batch, isnull(b.cPosition,'') as pos,"
            + " (select count(*) from InvPosition p where p.RdID=b.ID and p.RdsID=b.AutoID and p.cvouchtype='32'"
            + " and p.cPosCode=b.cPosition) as binned, " + PropOkExpr() + " as propok from rdrecords32 b where b.ID=?";

        sealed class OutRow
        {
            public int Id;
            public string Line;
            public decimal Qty;
            public string Batch;
            public string Pos;
            public bool Binned;
            public bool PropOk;
        }

        sealed class OutBatch
        {
            public OutAsk Ask;
            OutPart job;
            object co;
            List<int> ids;
            List<StockLoaded> loads;
            HashSet<int> lines;

            public void Apply(OutPart owner, List<int> kept)
            {
                job = owner;
                lines = new HashSet<int>();
                ids = Changing(job.Ctx.Conn, kept);
                if (ids.Count == 0)
                {
                    return;
                }
                loads = new List<StockLoaded>();
                try
                {
                    co = StockCall.OpenCo(job.Ctx);
                    for (int i = 0; i < ids.Count; i++)
                    {
                        loads.Add(LoadFor(ids[i]));
                    }
                    InTx(job.Ctx, ApplyIn);
                }
                finally
                {
                    ReleaseLoads(loads);
                    ComUtil.Final(co);
                    co = null;
                }
            }

            // 要改的出库单 id（升序），同时记下要改的发货行。一个发货行的行分在多张出库单上时拒绝。
            List<int> Changing(object conn, List<int> kept)
            {
                Dictionary<int, List<OutRow>> byLine = new Dictionary<int, List<OutRow>>();
                for (int i = 0; i < kept.Count; i++)
                {
                    List<OutRow> rows = ReadOutRows(conn, kept[i]);
                    for (int j = 0; j < rows.Count; j++)
                    {
                        int line = CoRows.AsId(rows[j].Line);
                        if (!byLine.ContainsKey(line))
                        {
                            byLine[line] = new List<OutRow>();
                        }
                        byLine[line].Add(rows[j]);
                    }
                }
                List<int> change = new List<int>();
                foreach (KeyValuePair<int, List<OutSpec>> kv in Ask.ByLine)
                {
                    List<OutRow> rows;
                    // 只有数量、没有批号货位的行保留 U8 生成的行（数量合计已由第 2 步核对），不并行。
                    if (PlainSpec(kv.Value) || !byLine.TryGetValue(kv.Key, out rows) || Matches(rows, kv.Value))
                    {
                        continue;
                    }
                    int id = OneVoucher(rows);
                    lines.Add(kv.Key);
                    if (!change.Contains(id))
                    {
                        change.Add(id);
                    }
                }
                change.Sort();
                return change;
            }

            static int OneVoucher(List<OutRow> rows)
            {
                for (int i = 1; i < rows.Count; i++)
                {
                    if (rows[i].Id != rows[0].Id)
                    {
                        throw new BridgeException(409, "u8_rejected", "同一发货行生成在多张销售出库单上，桥不支持指定批号或货位");
                    }
                }
                return rows[0].Id;
            }

            // 事务外 Load。有货位记录的单据不改。
            StockLoaded LoadFor(int id)
            {
                if (StockPosGuard.BinnedLines(job.Ctx.Conn, job.Kind, id).Count > 0)
                {
                    throw new BridgeException(409, "u8_rejected", "生成的销售出库单已有货位记录，桥暂不支持改批号或货位");
                }
                StockLoaded loaded = new StockLoaded();
                loaded.Head = Rows.NewDom();
                loaded.Body = Rows.NewDom();
                loaded.Pos = Rows.NewDom();
                StockCall.CallLoad(job.Ctx, co, job.Kind, id, loaded);
                ComUtil.Final(loaded.Pos);
                loaded.Pos = Rows.NewDom();
                OutDom mark = new OutDom();
                mark.Ctx = job.Ctx;
                mark.Ask = Ask;
                mark.Lines = lines;
                mark.Mark(loaded);
                return loaded;
            }

            void ApplyIn(object conn)
            {
                for (int i = 0; i < loads.Count; i++)
                {
                    UpdateLoaded(loads[i]);
                }
                StockCall.AfterCheck(job.Ctx, CheckIn,
                    "发货单 " + job.DlId.ToString(CultureInfo.InvariantCulture) + " 出库单 " + IdList(ids));
            }

            void UpdateLoaded(StockLoaded loaded)
            {
                object msg = Rows.NewDom();
                try
                {
                    int[] refs;
                    object[] args = StockCall.UpdateArgs(job.Kind, StockCall.Forms(loaded.Head, loaded.Body,
                        loaded.Pos, job.Ctx.Conn, msg), out refs);
                    CallIn(job.Ctx, co, StockCall.AtFor("Update", args, refs, null));
                }
                finally
                {
                    ComUtil.Final(msg);
                }
            }

            void CheckIn(object conn)
            {
                Dictionary<int, decimal[]> now = OpenLines(conn, job.DlId);
                foreach (KeyValuePair<int, decimal[]> kv in job.Before)
                {
                    decimal plus;
                    Ask.Want.TryGetValue(kv.Key, out plus);
                    decimal[] cur;
                    if (!now.TryGetValue(kv.Key, out cur) || Math.Abs(cur[1] - kv.Value[1] - plus) > 0.000001m)
                    {
                        throw new BridgeException(409, "u8_rejected",
                            "发货单行 " + kv.Key.ToString(CultureInfo.InvariantCulture) + " 的累计出库数量与请求不符");
                    }
                }
                CheckRows(conn);
            }

            void CheckRows(object conn)
            {
                Dictionary<int, List<OutRow>> byLine = new Dictionary<int, List<OutRow>>();
                for (int i = 0; i < ids.Count; i++)
                {
                    List<OutRow> rows = ReadOutRows(conn, ids[i]);
                    for (int j = 0; j < rows.Count; j++)
                    {
                        int line = CoRows.AsId(rows[j].Line);
                        if (!byLine.ContainsKey(line))
                        {
                            byLine[line] = new List<OutRow>();
                        }
                        byLine[line].Add(rows[j]);
                    }
                }
                foreach (int line in lines)
                {
                    List<OutRow> rows;
                    if (!byLine.TryGetValue(line, out rows) || !Matches(rows, Ask.ByLine[line]) || !BinsWritten(rows)
                        || !PropsOk(rows, Ask.ByLine[line]))
                    {
                        throw new BridgeException(409, "u8_rejected",
                            "发货单行 " + line.ToString(CultureInfo.InvariantCulture) + " 保存后的批号、货位或数量与请求不符");
                    }
                }
            }

            // 请求里给了批号的发货行：出库行的批次属性要与档案一致。
            static bool PropsOk(List<OutRow> rows, List<OutSpec> specs)
            {
                bool lot = false;
                for (int i = 0; i < specs.Count; i++)
                {
                    lot = lot || specs[i].Batch.Length > 0;
                }
                for (int i = 0; lot && i < rows.Count; i++)
                {
                    if (!rows[i].PropOk)
                    {
                        return false;
                    }
                }
                return true;
            }

            static bool BinsWritten(List<OutRow> rows)
            {
                for (int i = 0; i < rows.Count; i++)
                {
                    if (rows[i].Pos.Length > 0 && !rows[i].Binned)
                    {
                        return false;
                    }
                }
                return true;
            }

            static void ReleaseLoads(List<StockLoaded> all)
            {
                for (int i = 0; all != null && i < all.Count; i++)
                {
                    ComUtil.Final(all[i].Pos);
                    ComUtil.Final(all[i].Body);
                    ComUtil.Final(all[i].Head);
                }
            }
        }

        static List<OutRow> ReadOutRows(object conn, int id)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, OutRowsSql, new object[] { id }, 5000);
            List<OutRow> list = new List<OutRow>();
            for (int i = 0; rows != null && i < rows.Count; i++)
            {
                OutRow row = new OutRow();
                row.Id = CoRows.AsId(CoRows.Col(rows[i], "id"));
                row.Line = CoRows.Col(rows[i], "line");
                row.Qty = DecOf(CoRows.Col(rows[i], "qty"));
                row.Batch = CoRows.Col(rows[i], "batch");
                row.Pos = CoRows.Col(rows[i], "pos");
                row.Binned = DecOf(CoRows.Col(rows[i], "binned")) > 0m;
                row.PropOk = DecOf(CoRows.Col(rows[i], "propok")) > 0m;
                list.Add(row);
            }
            return list;
        }

        static bool PlainSpec(List<OutSpec> specs)
        {
            return specs.Count == 1 && specs[0].Batch.Length == 0 && specs[0].Pos.Length == 0;
        }

        // 出库行与请求的各次一一对上：行数相同，每一次都有一行数量相等、批号相同（请求给了才比）、货位相同（同上）。
        static bool Matches(List<OutRow> rows, List<OutSpec> specs)
        {
            if (rows.Count != specs.Count)
            {
                return false;
            }
            bool[] used = new bool[rows.Count];
            for (int i = 0; i < specs.Count; i++)
            {
                int hit = -1;
                for (int j = 0; j < rows.Count && hit < 0; j++)
                {
                    if (!used[j] && RowFits(rows[j], specs[i]))
                    {
                        hit = j;
                    }
                }
                if (hit < 0)
                {
                    return false;
                }
                used[hit] = true;
            }
            return true;
        }

        static bool RowFits(OutRow row, OutSpec spec)
        {
            if (Math.Abs(row.Qty - spec.Qty) > 0.000001m)
            {
                return false;
            }
            if (spec.Batch.Length > 0 && !string.Equals(row.Batch, spec.Batch, StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }
            return spec.Pos.Length == 0 || string.Equals(row.Pos, spec.Pos, StringComparison.OrdinalIgnoreCase);
        }
    }
}
