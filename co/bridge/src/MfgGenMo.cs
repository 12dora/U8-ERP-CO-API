using System.Collections.Generic;

namespace U8Co
{
    // 产成品入库参照生产订单（source_type=production_order，id 是 MoId，source_line_id 是 MoDId，1 行）：
    // 与产品检验单来源同一个 VoucherCO.Insert("10")，csource=生产订单，表体 iMPoIds=MoDId，不写 iCheckIdBaks / cCheckCode。
    // 订单行须已审核未关闭（Status=3）。可入库数量 = Qty − isnull(QualifiedInQty,0)（测试账套 2246 行里 2237 行
    // QualifiedInQty 等于 rdrecords10 按 iMPoIds 的合计，其余是跨年度单据）。库存选项 ST.bOverMPIn（允许超生产订单入库）
    // 打开时不按剩余数量拦，交给 U8 按存货的入库超额上限查；关闭时超过剩余 409「超过可生单数量」。
    // 需要检验：库存选项 ST.bQuality（质检）打开且订单行 QcFlag=1 时 409，请参照产品检验单入库。
    // 提交前在同一事务里核对 QualifiedInQty 加了本次数量；删除时核对减回去（MoUndo）。
    // 未覆盖：U8 对 QcFlag=1 的行是否另有拒绝原文；表头是否还要 iproorderid / cpspcode（现只写 cmpocode，同检验单来源）。
    internal static partial class MfgGen
    {
        const string MoSourceTitle = "生产订单";
        const string MoLineSql = "select convert(varchar(20), d.MoDId) as MoDId, convert(varchar(20), o.MoId) as MoId, o.MoCode,"
            + " convert(varchar(20), d.SortSeq) as MoSeq, d.InvCode as ProdCode, convert(varchar(40), d.Qty) as MoQty,"
            + " convert(varchar(40), isnull(d.QualifiedInQty,0)) as InQty, d.MDeptCode, convert(varchar(10), d.Status) as Status,"
            + " convert(varchar(5), isnull(d.QcFlag,0)) as QcFlag"
            + " from mom_orderdetail d join mom_order o on o.MoId=d.MoId where d.MoDId=? and d.MoId=?";
        // 生单事务里的基准读：加更新锁并保持到提交，和别的来源（检验单等）对同一订单行的入库串行。
        const string MoLockSql = "select convert(varchar(40), isnull(QualifiedInQty,0)) as InQty, convert(varchar(40), Qty) as MoQty"
            + " from mom_orderdetail with (updlock, holdlock) where MoDId=?";
        const string MoRdSql = "select convert(varchar(20), b.iMPoIds) as MoDId, convert(varchar(40), isnull(b.iQuantity,0)) as Qty"
            + " from rdrecords10 b where b.ID=? and isnull(b.iMPoIds,0)<>0";

        static ApiResult ProductInMo(WorkContext ctx, int moId, Dictionary<string, object> head, object[] lines)
        {
            VoucherKind kind = NeedKind("product_in");
            MfgReq.CheckGenerate(kind, head, lines);
            MfgJob job = NewJob(ctx, kind, head, "production_order", moId);
            bool capped = !SrcLess.IsOn(ctx.Conn, "ST", "bOverMPIn", false);
            job.Src = LoadMoLine(ctx.Conn, moId, lines, job.Lines, capped);
            job.RdCode = MfgReq.NeedRd(head, true);
            job.DeptCode = Or(MfgReq.Text(head, "cdepcode"), CoRows.Col(job.Src, "MDeptCode"));
            CheckArchives(ctx.Conn, job, true);
            MoGuard guard = new MoGuard();
            guard.MoDId = CoRows.AsId(CoRows.Col(job.Src, "MoDId"));
            guard.Qty = job.Lines[0].Qty;
            guard.Capped = capped;
            job.Before = guard.Before;
            job.After = guard.After;
            return Insert(job);
        }

        // 返回的表头行就是订单行（MoId、MoCode、SortSeq、产品、数量、部门）；表体行的来源同一行。
        static Dictionary<string, object> LoadMoLine(object conn, int moId, object[] lines, List<MfgLine> into, bool capped)
        {
            if (Rows.One(conn, MoSql, new object[] { moId }) == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            Dictionary<string, object> line = MfgReq.LineOf(lines[0], true);
            int id = MfgReq.LineId(line);
            decimal qty = MfgReq.LineQty(line);
            Dictionary<string, object> src = Rows.One(conn, MoLineSql, new object[] { id, moId });
            if (src == null)
            {
                throw new BridgeException(400, "bad_request", "明细行不存在");
            }
            RequireReleased(CoRows.Col(src, "Status"));
            RefuseInspect(conn, src);
            decimal left = Num(src, "MoQty") - Num(src, "InQty");
            if (capped && qty > left)
            {
                throw new BridgeException(409, "state_mismatch", "超过可生单数量");
            }
            into.Add(NewLine(line, id, qty, src));
            return src;
        }

        static void RefuseInspect(object conn, Dictionary<string, object> src)
        {
            if (CoRows.Col(src, "QcFlag") == "1" && SrcLess.IsOn(conn, "ST", "bQuality", false))
            {
                throw new BridgeException(409, "state_mismatch",
                    "生产订单行需要检验（QcFlag=1，库存选项 ST.bQuality），请参照产品检验单入库");
            }
        }

        // 产成品行参照生产订单：impoids 是订单行 MoDId；应入库数量写订单数量。辅计量由 ApplyQty 按存货补。
        static void MoLine(MfgJob job, MfgLine line, object dom, object row, List<string> schema)
        {
            Put(dom, row, schema, "cinvcode", CoRows.Col(line.Src, "ProdCode"));
            Put(dom, row, schema, "inquantity", CoRows.Col(line.Src, "MoQty"));
            Put(dom, row, schema, "impoids", CoRows.Col(job.Src, "MoDId"));
        }

        // MfgGen.Delete 的来源核对：本类来源（生产订单 / 产品检验单）照旧；另放行产品不良品处理单（回退核对 "reject"）、
        // 参照生产订单的产成品入库（"mo"）、无来源（库存）的材料出库。返回 GuardDel 用的核对类别。
        static string DelSource(VoucherKind kind, string have, string source)
        {
            bool product = kind.Name == "product_in";
            if (product && have == RejSourceTitle)
            {
                return "reject";
            }
            if (product && have == MoSourceTitle)
            {
                return "mo";
            }
            if (have == source || (kind.Name == "material_out" && have == FreeSourceTitle))
            {
                return "";
            }
            throw new BridgeException(409, "state_mismatch", "只能删除来源为" + source + "的单据");
        }

        static void GuardDel(StockAt at, int id, string guard)
        {
            if (guard == "reject")
            {
                GuardRejUndo(at, id);
            }
            else if (guard == "mo")
            {
                GuardMoUndo(at, id);
            }
        }

        // 删除参照生产订单的产成品入库：U8 回退 QualifiedInQty 未经实测，同一事务里核对，没退就回滚（409）。
        static void GuardMoUndo(StockAt at, int rdId)
        {
            MoUndo undo = new MoUndo();
            undo.RdId = rdId;
            at.Before = undo.Before;
            at.After = undo.After;
        }

        // 生单：保存前重读合格入库数量（开关打开时再核剩余）；保存后核对加了本次数量。
        sealed class MoGuard
        {
            public int MoDId;
            public decimal Qty;
            public bool Capped;
            decimal _mo;

            public void Before(object conn)
            {
                Dictionary<string, object> row = Rows.One(conn, MoLockSql, new object[] { MoDId });
                if (row == null)
                {
                    throw new BridgeException(409, "state_mismatch", "生产订单行不存在");
                }
                _mo = Num(row, "InQty");
                if (Capped && _mo + Qty > Num(row, "MoQty") + 0.000001m)
                {
                    throw new BridgeException(409, "state_mismatch", "超过可生单数量");
                }
            }

            public void After(object conn)
            {
                decimal mo = Num(Rows.Scalar(conn, MoQtySql, new object[] { MoDId }));
                if (!Same(mo, _mo + Qty))
                {
                    throw Mismatch("U8 没有回写生产订单合格入库数量", _mo, mo);
                }
            }
        }

        sealed class MoUndo
        {
            public int RdId;
            readonly Dictionary<int, decimal> _expect = new Dictionary<int, decimal>();

            public void Before(object conn)
            {
                List<Dictionary<string, object>> rows = Rows.Query(conn, MoRdSql, new object[] { RdId }, 500);
                Dictionary<int, decimal> qty = new Dictionary<int, decimal>();
                for (int i = 0; rows != null && i < rows.Count; i++)
                {
                    int mo = CoRows.AsId(CoRows.Col(rows[i], "MoDId"));
                    decimal sum;
                    qty.TryGetValue(mo, out sum);
                    qty[mo] = sum + Num(CoRows.Col(rows[i], "Qty"));
                }
                foreach (KeyValuePair<int, decimal> kv in qty)
                {
                    _expect[kv.Key] = Num(Rows.Scalar(conn, MoQtySql, new object[] { kv.Key })) - kv.Value;
                }
            }

            public void After(object conn)
            {
                foreach (KeyValuePair<int, decimal> kv in _expect)
                {
                    decimal now = Num(Rows.Scalar(conn, MoQtySql, new object[] { kv.Key }));
                    if (!Same(now, kv.Value))
                    {
                        throw Mismatch("U8 没有回退生产订单合格入库数量", kv.Value, now);
                    }
                }
            }
        }
    }
}
