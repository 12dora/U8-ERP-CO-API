using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 产成品入库参照合并检验的产品检验单（QM04，BMERGECHECKFLAG=1）。合并来源在 QMMergeCheckDetail，一个来源一行：
    // source_line_id 是来源 AUTOID（写进 rdrecords10.imergecheckautoid），iMPoIds / cmocode / imoseq 取该来源自己的
    // 生产订单行，icheckidbaks 仍是检验单 ID，表头不写 cmpocode（同 U8 客户端生成的单据）。可入库数量按来源算：
    // FREGQUANTITY + FCONQUANTIY − FSUMQUANTITY；来源 BPROINFLAG=1 视为已入库完毕。
    // U8 的 Insert("10") 回写来源 FSUMQUANTITY、表头 FsumQuantity、订单行 QualifiedInQty（按测试账套存量数据核对，
    // docs/u8-notes.md），提交前在同一事务里核对（MergeGuard），删除时核对减回去（MergeUndo），对不上回滚 409。
    internal static partial class MfgGen
    {
        const string MergeHint = "合并检验的检验单 source_line_id 是合并来源 AUTOID，见 vouchers/load 的 merge_sources";
        const string MergeSql = "select convert(varchar(20), m.AUTOID) as MergeId, convert(varchar(20), m.SOURCEAUTOID) as MoDId,"
            + " convert(varchar(40), isnull(m.FREGQUANTITY,0)) as RegQty, convert(varchar(40), isnull(m.FCONQUANTIY,0)) as ConQty,"
            + " convert(varchar(40), isnull(m.FSUMQUANTITY,0)) as SumQty, convert(varchar(5), isnull(m.BPROINFLAG,0)) as InDone"
            + " from QMMergeCheckDetail m where m.AUTOID=? and m.ID=?";
        // 生单事务里的基准读：加更新锁并保持到提交，和别的请求对同一来源的入库串行。
        const string MergeLockSql = "select convert(varchar(40), isnull(FSUMQUANTITY,0)) as SumQty,"
            + " convert(varchar(40), isnull(FREGQUANTITY,0)+isnull(FCONQUANTIY,0)) as CapQty"
            + " from QMMergeCheckDetail with (updlock, holdlock) where AUTOID=?";
        const string MergeSumSql = "select convert(varchar(40), isnull(FSUMQUANTITY,0)) from QMMergeCheckDetail where AUTOID=?";
        const string CheckSumSql = "select convert(varchar(40), isnull(FsumQuantity,0)) from QMCHECKVOUCHER where ID=?";
        const string CheckLockSql = "select convert(varchar(40), isnull(FsumQuantity,0)) from QMCHECKVOUCHER"
            + " with (updlock, holdlock) where ID=?";
        // 删除：本单挂合并来源的行（imergecheckautoid 大于 0 且确是合并来源；非合并行是 -1 或 0）。
        const string MergeRdSql = "select convert(varchar(20), m.AUTOID) as MergeId, convert(varchar(20), m.ID) as CheckId,"
            + " convert(varchar(20), b.iMPoIds) as MoDId, convert(varchar(40), isnull(b.iQuantity,0)) as Qty"
            + " from rdrecords10 b join QMMergeCheckDetail m on m.AUTOID=b.imergecheckautoid and m.ID=b.iCheckIdBaks"
            + " where b.ID=? and isnull(b.imergecheckautoid,0)>0";
        // 修改闸门（RefuseEdit）：本单有没有挂合并来源的行。
        const string MergeLineSql = "select top 1 convert(varchar(20), b.AutoID) from rdrecords10 b"
            + " where b.ID=? and isnull(b.imergecheckautoid,0)>0";

        // 返回的表头行是第一行来源的生产订单行（部门缺省取它；测试账套同一检验单的来源部门、存货都相同）。
        static Dictionary<string, object> LoadMerged(object conn, int checkId, Dictionary<string, object> check,
            object[] lines, List<MfgLine> into)
        {
            for (int i = 0; i < lines.Length; i++)
            {
                try
                {
                    into.Add(MergeLine(conn, checkId, check, lines[i]));
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
            Dictionary<string, object> head = new Dictionary<string, object>(into[0].Src);
            head["Merged"] = "1";
            return head;
        }

        // 行来源 = 检验单表头（存货、检验单号、检验员、检验日期）叠上该来源的订单行和合并明细（MoDId、各数量）。
        static MfgLine MergeLine(object conn, int checkId, Dictionary<string, object> check, object raw)
        {
            Dictionary<string, object> line = MfgReq.LineOf(raw, true);
            int id = MfgReq.LineId(line);
            decimal qty = MfgReq.LineQty(line);
            Dictionary<string, object> src = Rows.One(conn, MergeSql, new object[] { id, checkId });
            if (src == null)
            {
                throw BridgeException.BadField("lines.source_line_id", "明细行不存在").WithHint(MergeHint);
            }
            if (CoRows.Col(src, "InDone") == "1")
            {
                throw new BridgeException(409, "state_mismatch", "该合并来源已入库完毕");
            }
            Dictionary<string, object> mo = Rows.One(conn, DetailSql, new object[] { CoRows.AsId(CoRows.Col(src, "MoDId")) });
            if (mo == null)
            {
                throw new BridgeException(409, "state_mismatch", "合并来源对应的生产订单不存在");
            }
            RequireReleased(CoRows.Col(mo, "Status"));
            if (CoRows.Col(mo, "ProdCode") != CoRows.Col(check, "CINVCODE"))
            {
                throw new BridgeException(409, "state_mismatch", "合并来源的生产订单存货与检验单不一致");
            }
            if (qty > Num(src, "RegQty") + Num(src, "ConQty") - Num(src, "SumQty"))
            {
                throw new BridgeException(409, "state_mismatch", "超过可生单数量");
            }
            Dictionary<string, object> all = new Dictionary<string, object>(check);
            Overlay(all, mo);
            Overlay(all, src);
            return NewLine(line, id, qty, all);
        }

        static void Overlay(Dictionary<string, object> into, Dictionary<string, object> from)
        {
            foreach (KeyValuePair<string, object> kv in from)
            {
                into[kv.Key] = kv.Value;
            }
        }

        static void GuardMerge(MfgJob job)
        {
            MergeGuard guard = new MergeGuard();
            guard.CheckId = job.SourceId;
            guard.Lines = job.Lines;
            job.Before = guard.Before;
            job.After = guard.After;
        }

        // MfgGen.Delete：来源为产品检验单的产成品入库都装上；本单没有合并来源行时前后都不查。
        static void GuardMergeUndo(StockAt at, int rdId)
        {
            MergeUndo undo = new MergeUndo();
            undo.RdId = rdId;
            at.Before = undo.Before;
            at.After = undo.After;
        }

        static void AddQty(Dictionary<int, decimal> map, int key, decimal qty)
        {
            if (key <= 0)
            {
                return;
            }
            decimal sum;
            map.TryGetValue(key, out sum);
            map[key] = sum + qty;
        }

        // 期望值 = 当前值 + sign × 本次数量（sql 只取一列）。
        static Dictionary<int, decimal> Expect(object conn, string sql, Dictionary<int, decimal> qty, int sign)
        {
            Dictionary<int, decimal> expect = new Dictionary<int, decimal>();
            foreach (KeyValuePair<int, decimal> kv in qty)
            {
                expect[kv.Key] = Num(Rows.Scalar(conn, sql, new object[] { kv.Key })) + sign * kv.Value;
            }
            return expect;
        }

        static void Verify(object conn, string sql, Dictionary<int, decimal> expect, string what)
        {
            foreach (KeyValuePair<int, decimal> kv in expect)
            {
                decimal now = Num(Rows.Scalar(conn, sql, new object[] { kv.Key }));
                if (!Same(now, kv.Value))
                {
                    throw new BridgeException(409, "u8_rejected", what + "（应为 " + Dec(kv.Value) + "，实为 " + Dec(now)
                        + "，标识 " + kv.Key.ToString(CultureInfo.InvariantCulture) + "）");
                }
            }
        }

        // 生单：保存前锁住各来源、检验单表头、订单行并重核剩余；保存后核对三处都加了本次数量。
        sealed class MergeGuard
        {
            public int CheckId;
            public List<MfgLine> Lines;
            Dictionary<int, decimal> _src;
            Dictionary<int, decimal> _check;
            Dictionary<int, decimal> _mo;

            public void Before(object conn)
            {
                Dictionary<int, decimal> src = new Dictionary<int, decimal>();
                Dictionary<int, decimal> mo = new Dictionary<int, decimal>();
                Dictionary<int, decimal> check = new Dictionary<int, decimal>();
                for (int i = 0; i < Lines.Count; i++)
                {
                    MfgLine line = Lines[i];
                    Lock(conn, line);
                    AddQty(src, line.Id, line.Qty);
                    AddQty(mo, CoRows.AsId(CoRows.Col(line.Src, "MoDId")), line.Qty);
                    AddQty(check, CheckId, line.Qty);
                }
                _src = Expect(conn, MergeSumSql, src, 1);
                _check = Expect(conn, CheckLockSql, check, 1);
                _mo = Expect(conn, MoLockSql, mo, 1);
            }

            static void Lock(object conn, MfgLine line)
            {
                Dictionary<string, object> row = Rows.One(conn, MergeLockSql, new object[] { line.Id });
                if (row == null)
                {
                    throw new BridgeException(409, "state_mismatch", "合并来源不存在");
                }
                if (Num(row, "SumQty") + line.Qty > Num(row, "CapQty") + 0.000001m)
                {
                    throw new BridgeException(409, "state_mismatch", "超过可生单数量");
                }
            }

            public void After(object conn)
            {
                Verify(conn, MergeSumSql, _src, "U8 没有回写合并来源累计入库数量");
                Verify(conn, CheckSumSql, _check, "U8 没有回写检验单累计入库数量");
                Verify(conn, MoQtySql, _mo, "U8 没有回写生产订单合格入库数量");
            }
        }

        // 删除：按本单挂合并来源的行汇总，核对来源 FSUMQUANTITY、检验单 FsumQuantity、订单行 QualifiedInQty 都减回去。
        sealed class MergeUndo
        {
            public int RdId;
            Dictionary<int, decimal> _src = new Dictionary<int, decimal>();
            Dictionary<int, decimal> _check = new Dictionary<int, decimal>();
            Dictionary<int, decimal> _mo = new Dictionary<int, decimal>();

            public void Before(object conn)
            {
                List<Dictionary<string, object>> rows = Rows.Query(conn, MergeRdSql, new object[] { RdId }, 500);
                Dictionary<int, decimal> src = new Dictionary<int, decimal>();
                Dictionary<int, decimal> check = new Dictionary<int, decimal>();
                Dictionary<int, decimal> mo = new Dictionary<int, decimal>();
                for (int i = 0; rows != null && i < rows.Count; i++)
                {
                    decimal qty = Num(CoRows.Col(rows[i], "Qty"));
                    AddQty(src, CoRows.AsId(CoRows.Col(rows[i], "MergeId")), qty);
                    AddQty(check, CoRows.AsId(CoRows.Col(rows[i], "CheckId")), qty);
                    AddQty(mo, CoRows.AsId(CoRows.Col(rows[i], "MoDId")), qty);
                }
                _src = Expect(conn, MergeSumSql, src, -1);
                _check = Expect(conn, CheckSumSql, check, -1);
                _mo = Expect(conn, MoQtySql, mo, -1);
            }

            public void After(object conn)
            {
                Verify(conn, MergeSumSql, _src, "U8 没有回退合并来源累计入库数量");
                Verify(conn, CheckSumSql, _check, "U8 没有回退检验单累计入库数量");
                Verify(conn, MoQtySql, _mo, "U8 没有回退生产订单合格入库数量");
            }
        }
    }
}
