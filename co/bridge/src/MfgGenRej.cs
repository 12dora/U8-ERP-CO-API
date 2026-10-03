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
        // 未覆盖：全部入库后 U8 是否把 QMREJECTVOUCHERS.BFLAG 置 1。
        static readonly bool RejCheckFlag = true;

        // 生产订单行按 U8 参照视图 QM_RefProReject 的取法：非标订单用 ISOURCEPROORDERAUTOID，否则用 SOURCEAUTOID。
        const string RejSql = "select r.CREJECTCODE, r.CVERIFIER, r.CSOURCE,"
            + " convert(varchar(5), isnull(r.IsWfControlled,0)) as Wf, convert(varchar(5), isnull(r.iVerifyStateNew,0)) as WfState,"
            + " convert(varchar(20), case when isnull(r.ISOURCEPROORDERAUTOID,0)<>0 then r.ISOURCEPROORDERAUTOID"
            + " else r.SOURCEAUTOID end) as MoDId,"
            + " convert(varchar(20), r.CHECKID) as CheckId, r.CCHECKCODE, r.CCHECKPERSON, convert(varchar(10), r.DCHECKDATE, 23) as CheckDate,"
            + " r.CINVCODE, r.CBATCH from QMREJECTVOUCHER r where r.ID=? and r.CVOUCHTYPE='QM06'";
        const string RejLineSql = "select convert(varchar(20), s.AUTOID) as AutoId, isnull(s.CDIMINVCODE,'') as DimInv,"
            + " convert(varchar(40), s.FDIMQUANTITY) as DimQty, convert(varchar(40), isnull(s.FQUANTITY,0)) as RejQty,"
            + " convert(varchar(10), isnull(s.IDISPOSEFLOW,-1)) as Flow, convert(varchar(5), isnull(s.BFLAG,0)) as Done,"
            + " isnull(s.CDIMBATCH,'') as DimBatch from QMREJECTVOUCHERS s where s.AUTOID=? and s.ID=?";
        const string RejUsedSql = "select convert(varchar(40), isnull(sum(iQuantity),0)) from rdrecords10 where iRejectIds=?";
        const string RejFlagSql = "select convert(varchar(5), isnull(BFLAG,0)) from QMREJECTVOUCHERS where AUTOID=?";
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
            guard.LineId = job.Lines[0].Id;
            guard.MoDId = CoRows.AsId(CoRows.Col(job.Src, "MoDId"));
            guard.Qty = job.Lines[0].Qty;
            guard.Base = Num(job.Lines[0].Src, "BaseQty");
            job.Before = guard.Before;
            job.After = guard.After;
            return Insert(job);
        }

        // 返回的表头行是生产订单行（MoId、MoCode、SortSeq、部门、状态）加 MoDId；表体行的来源是不良品处理单表头 + 表体。
        static Dictionary<string, object> LoadReject(object conn, int rejectId, object[] lines, List<MfgLine> into)
        {
            Dictionary<string, object> rej = Rows.One(conn, RejSql, new object[] { rejectId });
            if (rej == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            RequireReject(rej);
            Dictionary<string, object> line = MfgReq.LineOf(lines[0], true);
            int id = MfgReq.LineId(line);
            decimal qty = MfgReq.LineQty(line);
            Dictionary<string, object> body = Rows.One(conn, RejLineSql, new object[] { id, rejectId });
            if (body == null)
            {
                throw new BridgeException(400, "bad_request", "明细行不存在");
            }
            Dictionary<string, object> src = RejectLine(rej, body);
            decimal left = Num(src, "BaseQty") - Num(Rows.Scalar(conn, RejUsedSql, new object[] { id }));
            if (qty > left)
            {
                throw new BridgeException(409, "state_mismatch", "超过可生单数量");
            }
            RequireWhole(qty, left);
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

        // 实测：U8 不接受部分入库（「存货…入库数量合计必须等于降级后数量」），数量必须等于剩余的处理后数量。
        static void RequireWhole(decimal qty, decimal left)
        {
            if (!Same(qty, left))
            {
                throw new BridgeException(409, "state_mismatch",
                    "参照不良品处理单须一次入库全部处理后数量，本行应为 " + Dec(left));
            }
        }

        // 已审核（审批流单据须审批完成）、来自生产订单。
        static void RequireReject(Dictionary<string, object> rej)
        {
            if (CoRows.Col(rej, "CVERIFIER").Length == 0 || !RejWfDone(rej))
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            if (CoRows.Col(rej, "CSOURCE") != "生产订单" || CoRows.AsId(CoRows.Col(rej, "MoDId")) <= 0)
            {
                throw new BridgeException(409, "state_mismatch", "不良品处理单来源不是生产订单");
            }
        }

        // 未覆盖：审批流单据 iVerifyStateNew=2 视为审批完成。
        static bool RejWfDone(Dictionary<string, object> rej)
        {
            return CoRows.Col(rej, "Wf") != "1" || CoRows.Col(rej, "WfState") == "2";
        }

        // 只接处理流程 2（降级 / 让步接收 / 升级，入库）的行；处理后存货、数量为空时退回原存货、不良品数量（未经实测）。
        static Dictionary<string, object> RejectLine(Dictionary<string, object> rej, Dictionary<string, object> body)
        {
            if (CoRows.Col(body, "Flow") != "2")
            {
                throw new BridgeException(409, "state_mismatch", "该不良品处理方式不能入库");
            }
            if (CoRows.Col(body, "Done") == "1")
            {
                throw new BridgeException(409, "state_mismatch", "不良品处理单已入库完毕");
            }
            Dictionary<string, object> src = new Dictionary<string, object>(rej);
            foreach (KeyValuePair<string, object> kv in body)
            {
                src[kv.Key] = kv.Value;
            }
            string inv = CoRows.Col(body, "DimInv");
            src["InvCode"] = inv.Length > 0 ? inv : CoRows.Col(rej, "CINVCODE");
            string dim = CoRows.Col(body, "DimQty");
            src["BaseQty"] = dim.Length > 0 ? dim : CoRows.Col(body, "RejQty");
            return src;
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

        // Insert 前后在同一事务里核对：本行已入库数量加了本次数量；生产订单行合格入库数量加了本次数量；
        // 入库满额时 BFLAG 为 1。对不上就回滚（409），审计 detail 带前后值。
        sealed class RejGuard
        {
            public int LineId;
            public int MoDId;
            public decimal Qty;
            public decimal Base;
            decimal _used;
            decimal _mo;

            public void Before(object conn)
            {
                _used = Num(Rows.Scalar(conn, RejUsedSql, new object[] { LineId }));
                _mo = Num(Rows.Scalar(conn, MoQtySql, new object[] { MoDId }));
            }

            public void After(object conn)
            {
                decimal used = Num(Rows.Scalar(conn, RejUsedSql, new object[] { LineId }));
                if (!Same(used, _used + Qty))
                {
                    throw Mismatch("U8 没有把入库行挂到不良品处理单", _used, used);
                }
                if (RejCheckMoQty)
                {
                    decimal mo = Num(Rows.Scalar(conn, MoQtySql, new object[] { MoDId }));
                    if (!Same(mo, _mo + Qty))
                    {
                        throw Mismatch("U8 没有回写生产订单合格入库数量", _mo, mo);
                    }
                }
                if (RejCheckFlag && used >= Base && Num(Rows.Scalar(conn, RejFlagSql, new object[] { LineId })) != 1m)
                {
                    throw new BridgeException(409, "u8_rejected", "U8 没有把不良品处理单标为已入库");
                }
            }
        }

        static BridgeException Mismatch(string what, decimal before, decimal after)
        {
            return new BridgeException(409, "u8_rejected", what + "（前 " + Dec(before) + "，后 " + Dec(after) + "）");
        }

        static bool Same(decimal a, decimal b)
        {
            return Math.Abs(a - b) <= 0.000001m;
        }

        static string Dec(decimal value)
        {
            return value.ToString("0.######", CultureInfo.InvariantCulture);
        }

        static decimal Num(string text)
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
