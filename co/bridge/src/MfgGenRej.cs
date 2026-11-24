using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 产成品入库参照产品不良品处理单（QM06）：处理流程为「降级 / 让步接收 / 升级」（IDISPOSEFLOW=2）的不良品按
    // 处理后存货（CDIMINVCODE）、处理后数量（FDIMQUANTITY）入库，走和产品检验单来源相同的 VoucherCO.Insert("10")。
    // 表体 iRejectIds / cRejectCode 挂不良品处理单，iCheckIdBaks / cCheckCode 挂它的检验单，iMPoIds 是生产订单行 MoDId。
    // 不良品处理单没有累计入库数量列，已入库数量按 rdrecords10.iRejectIds 汇总；U8 入库后把 QMREJECTVOUCHERS.BFLAG 置 1。
    internal static partial class MfgGen
    {
        // 未覆盖：U8 保存时是否把 mom_orderdetail.QualifiedInQty 加上本次数量。
        static readonly bool RejCheckMoQty = true;
        const string MoQtySql = "select convert(varchar(40), isnull(QualifiedInQty,0)) from mom_orderdetail where MoDId=?";

        static ApiResult ProductInReject(WorkContext ctx, int rejectId, Dictionary<string, object> head, object[] lines)
        {
            VoucherKind kind = NeedKind("product_in");
            MfgReq.CheckGenerate(kind, head, lines);
            MfgJob job = NewJob(ctx, kind, head, "qm_product_reject", rejectId);
            job.Src = LoadReject(ctx.Conn, rejectId, lines, job.Lines);
            job.RdCode = MfgReq.NeedRd(head, true);
            job.DeptCode = Or(MfgReq.Text(head, "cdepcode"), CoRows.Col(job.Src, "MDeptCode"));
            CheckArchives(ctx.Conn, job, true);
            RejGuard guard = new RejGuard();
            guard.Line = new RejLineGuard(RejSrc.Product, job.Lines[0].Id, job.Lines[0].Qty, Num(job.Lines[0].Src, "BaseQty"));
            guard.MoDId = CoRows.AsId(CoRows.Col(job.Src, "MoDId"));
            guard.Qty = job.Lines[0].Qty;
            job.Before = guard.Before;
            job.After = guard.After;
            return Insert(job);
        }

        // 返回的表头行是生产订单行（MoId、MoCode、SortSeq、部门、状态）加 MoDId；表体行的来源是不良品处理单表头 + 表体。
        static Dictionary<string, object> LoadReject(object conn, int rejectId, object[] lines, List<MfgLine> into)
        {
            Dictionary<string, object> rej = RejSrc.Head(conn, RejSrc.Product, rejectId);
            RequireMoSource(rej);
            Dictionary<string, object> line = MfgReq.LineOf(lines[0], true);
            int id = MfgReq.LineId(line);
            decimal qty = MfgReq.LineQty(line);
            Dictionary<string, object> src = RejSrc.Line(conn, rej, rejectId, id);
            RejSrc.CheckQty(conn, RejSrc.Product, src, id, qty);
            Dictionary<string, object> detail = Rows.One(conn, DetailSql, new object[] { CoRows.AsId(CoRows.Col(rej, "MoDId")) });
            if (detail == null)
            {
                throw new BridgeException(409, "state_mismatch", "不良品处理单对应的生产订单不存在");
            }
            RequireReleased(CoRows.Col(detail, "Status"));
            MfgLine item = NewLine(line, id, qty, src);
            if (item.Batch.Length == 0)
            {
                item.Batch = CoRows.Col(src, "DimBatch");
            }
            into.Add(item);
            Dictionary<string, object> result = new Dictionary<string, object>(detail);
            result["MoDId"] = CoRows.Col(rej, "MoDId");
            return result;
        }

        static void RequireMoSource(Dictionary<string, object> rej)
        {
            if (CoRows.Col(rej, "CSOURCE") != "生产订单" || CoRows.AsId(CoRows.Col(rej, "MoDId")) <= 0)
            {
                throw new BridgeException(409, "state_mismatch", "不良品处理单来源不是生产订单");
            }
        }

        // 产成品行参照不良品处理单：照 U8 自己生成的行写 iRejectIds（表体 AUTOID）、cRejectCode、检验单号和检验人。
        static void RejLine(MfgJob job, MfgLine line, object dom, object row, List<string> schema)
        {
            Put(dom, row, schema, "cinvcode", CoRows.Col(line.Src, "InvCode"));
            Put(dom, row, schema, "inquantity", CoRows.Col(line.Src, "BaseQty"));
            Put(dom, row, schema, "impoids", CoRows.Col(job.Src, "MoDId"));
            Put(dom, row, schema, "irejectids", line.Id.ToString(CultureInfo.InvariantCulture));
            Put(dom, row, schema, "crejectcode", CoRows.Col(line.Src, "CREJECTCODE"));
            Copy(dom, row, schema, "icheckidbaks", CoRows.Col(line.Src, "CheckId"));
            Copy(dom, row, schema, "ccheckcode", CoRows.Col(line.Src, "CCHECKCODE"));
            Copy(dom, row, schema, "ccheckpersoncode", CoRows.Col(line.Src, "CCHECKPERSON"));
            Copy(dom, row, schema, "dcheckdate", CoRows.Col(line.Src, "CheckDate"));
        }

        // Insert 前后在同一事务里核对：不良品处理单行（RejLineGuard）；生产订单行合格入库数量加了本次数量。
        // 对不上就回滚（409），审计 detail 带前后值。
        sealed class RejGuard
        {
            public RejLineGuard Line;
            public int MoDId;
            public decimal Qty;
            decimal _mo;

            public void Before(object conn)
            {
                Line.Before(conn);
                _mo = Num(Rows.Scalar(conn, MoQtySql, new object[] { MoDId }));
            }

            public void After(object conn)
            {
                decimal used = Line.CheckUsed(conn);
                if (RejCheckMoQty)
                {
                    decimal mo = Num(Rows.Scalar(conn, MoQtySql, new object[] { MoDId }));
                    if (!Same(mo, _mo + Qty))
                    {
                        throw Mismatch("U8 没有回写生产订单合格入库数量", _mo, mo);
                    }
                }
                Line.CheckFlag(conn, used);
            }
        }

        internal static BridgeException Mismatch(string what, decimal before, decimal after)
        {
            return new BridgeException(409, "u8_rejected", what + "（前 " + Dec(before) + "，后 " + Dec(after) + "）");
        }

        internal static bool Same(decimal a, decimal b)
        {
            return Math.Abs(a - b) <= 0.000001m;
        }

        internal static string Dec(decimal value)
        {
            return value.ToString("0.######", CultureInfo.InvariantCulture);
        }

        internal static decimal Num(string text)
        {
            decimal value;
            if (text == null || !StockUnits.Dec(text.Trim(), out value))
            {
                return 0m;
            }
            return value;
        }
    }
}
