using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 材料出库参照生产订单子件、产成品入库参照产品检验单或产品不良品处理单（MfgGenRej）：空白 DOM 手工填好后
    // VoucherCO.Insert（11 / 10），U8 自己回写 mom_moallocate.IssQty、mom_orderdetail.QualifiedInQty、QMCHECKVOUCHER.FsumQuantity。
    internal static partial class MfgGen
    {
        public static ApiResult MaterialOut(WorkContext ctx, int moId, Dictionary<string, object> head, object[] lines)
        {
            VoucherKind kind = NeedKind("material_out");
            MfgReq.CheckGenerate(kind, head, lines);
            MfgJob job = NewJob(ctx, kind, head, "production_order", moId);
            job.Src = LoadMo(ctx.Conn, moId, lines, job.Lines);
            job.RdCode = MfgReq.NeedRd(head, false);
            job.DeptCode = Or(MfgReq.Text(head, "cdepcode"), CoRows.Col(job.Src, "MDeptCode"));
            CheckArchives(ctx.Conn, job, false);
            return Insert(job);
        }

        public static ApiResult ProductIn(WorkContext ctx, int checkId, Dictionary<string, object> head, object[] lines)
        {
            if (ctx.Item != null && ctx.Item.Source != null && ctx.Item.Source.Name == "qm_product_reject")
            {
                return ProductInReject(ctx, checkId, head, lines);
            }
            // 参照生产订单（MfgGenMo，id 是 MoId）。
            if (ctx.Item != null && ctx.Item.Source != null && ctx.Item.Source.Name == "production_order")
            {
                return ProductInMo(ctx, checkId, head, lines);
            }
            VoucherKind kind = NeedKind("product_in");
            MfgReq.CheckGenerate(kind, head, lines, "qm_product_check");
            MfgJob job = NewJob(ctx, kind, head, "qm_product_check", checkId);
            job.Src = LoadCheck(ctx.Conn, checkId, lines, job.Lines);
            job.Merged = CoRows.Col(job.Src, "Merged") == "1";
            job.RdCode = MfgReq.NeedRd(head, true);
            job.DeptCode = Or(MfgReq.Text(head, "cdepcode"), CoRows.Col(job.Src, "MDeptCode"));
            CheckArchives(ctx.Conn, job, true);
            if (job.Merged)
            {
                GuardMerge(job);
            }
            return Insert(job);
        }

        static MfgJob NewJob(WorkContext ctx, VoucherKind kind, Dictionary<string, object> head, string source, int sourceId)
        {
            MfgJob job = new MfgJob();
            job.Ctx = ctx;
            job.Kind = kind;
            job.Head = head;
            job.Source = NeedKind(source);
            job.SourceId = sourceId;
            job.Lines = new List<MfgLine>();
            string date = MfgReq.Text(head, "ddate");
            if (date.Length == 0)
            {
                date = ctx.Item == null || ctx.Item.Date == null ? "" : ctx.Item.Date.Trim();
            }
            job.Date = date;
            return job;
        }

        static ApiResult Insert(MfgJob job)
        {
            WorkContext ctx = job.Ctx;
            CheckPositions(ctx.Conn, job);
            object co = null;
            object domH = null;
            object domB = null;
            object pos = null;
            object msg = null;
            try
            {
                domH = Blank(ctx.Conn, job.Kind, true);
                domB = Blank(ctx.Conn, job.Kind, false);
                StampHead(job, domH);
                FillBody(job, domB);
                pos = Rows.NewDom();
                msg = Rows.NewDom();
                co = StockCall.OpenCo(ctx);
                string code = StockCall.NewCode(ctx, co, job.Kind, HeadSeeds(domH));
                StockDom.SetHeadValue(domH, "cCode", code);
                int[] refs;
                object[] args = StockCall.InsertArgs(job.Kind, StockCall.Forms(domH, domB, pos, ctx.Conn, msg), out refs);
                StockAt at = StockCall.AtFor("Insert", args, refs, null);
                at.Before = job.Before;
                at.After = job.After;
                DryRun.Touched(job.Source, job.SourceId);
                StockCall.RunAt(ctx, co, at);
                int newId = Confirmed(ctx, job.Kind, code, args[6]);
                return AfterSaved(job, newId, code);
            }
            finally
            {
                ComUtil.Final(msg);
                ComUtil.Final(pos);
                ComUtil.Final(domB);
                ComUtil.Final(domH);
                ComUtil.Final(co);
            }
        }

        static object Blank(object conn, VoucherKind kind, bool head)
        {
            string view = StockDom.ViewName(kind.StType, head);
            if (view == null)
            {
                throw new BridgeException(500, "internal", "单据类型配置无效");
            }
            string alias = head ? "m" : "d";
            string sql = "select '' as editprop, " + alias + ".* from " + view + " " + alias + " with(nolock) where 1=2";
            return DomRows.Blank(conn, sql);
        }

        // 编号种子取填好后的表头（cVouchType、cBusType、dDate 等）。
        static Dictionary<string, object> HeadSeeds(object dom)
        {
            List<Dictionary<string, object>> rows = Rows.FromDom(dom, 1);
            if (rows == null || rows.Count == 0)
            {
                throw new BridgeException(500, "internal", "表头模板没有行");
            }
            return rows[0];
        }

        // Insert 已提交。新主键先认 vouchid，再按单号在新连接上找；都读不到就是结果未知。
        static int Confirmed(WorkContext ctx, VoucherKind kind, string code, object raw)
        {
            try
            {
                int id = StockCall.NewId(ctx, raw);
                if (id > 0 && FreshId(ctx, kind, kind.IdColumn, id) > 0)
                {
                    return id;
                }
                string no = code == null ? "" : code.Trim();
                id = no.Length == 0 ? 0 : FreshId(ctx, kind, kind.CodeColumn, no);
                if (id > 0)
                {
                    return id;
                }
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "MfgGen " + ex.Message);
            }
            throw new BridgeException(504, "outcome_unknown", Unknown(0, code));
        }

        static int FreshId(WorkContext ctx, VoucherKind kind, string column, object arg)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                string sql = "select convert(varchar(20), " + kind.IdColumn + ") from " + kind.HeadTable
                    + " where " + column + "=?";
                List<Dictionary<string, object>> rows = Rows.Query(conn, sql, new object[] { arg }, 2);
                if (rows == null || rows.Count != 1)
                {
                    return 0;
                }
                foreach (object value in rows[0].Values)
                {
                    return CoRows.AsId(value);
                }
                return 0;
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static ApiResult AfterSaved(MfgJob job, int id, string code)
        {
            try
            {
                return EditMsg.Saved(job.Ctx, job.Kind, id, job.Source, job.SourceId);
            }
            catch (Exception ex)
            {
                BridgeException bridge = ex as BridgeException;
                if (bridge != null && bridge.Code == "outcome_unknown")
                {
                    throw;
                }
                CoRows.Note(job.Ctx.Item, "Saved " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", Unknown(id, code));
            }
        }

        static string Unknown(int id, string code)
        {
            string msg = "已保存但未能确定单据标识";
            string no = code == null ? "" : code.Trim();
            if (no.Length > 0)
            {
                msg = msg + "，单号 " + no;
            }
            if (id > 0)
            {
                msg = msg + "，标识 " + id.ToString(CultureInfo.InvariantCulture);
            }
            return msg;
        }

        static VoucherKind NeedKind(string name)
        {
            VoucherKind kind = Kinds.Find(name);
            if (kind == null)
            {
                throw new BridgeException(500, "internal", "单据类型配置无效");
            }
            return kind;
        }

        static string Or(string value, string fallback)
        {
            if (value != null && value.Length > 0)
            {
                return value;
            }
            return fallback ?? "";
        }

        sealed class MfgJob
        {
            public WorkContext Ctx;
            public VoucherKind Kind;
            public Dictionary<string, object> Head;
            public VoucherKind Source;
            public int SourceId;
            public string Date;
            public string RdCode;
            public string DeptCode;
            public Dictionary<string, object> Src;
            public List<MfgLine> Lines;
            // 合并检验的产品检验单：表体逐行取来源自己的生产订单行，表头不写 cmpocode（MfgGenMerge）。
            public bool Merged;
            // 事务里的回写核对（产品不良品处理单来源用），为空则不核对。
            public StockCheck Before;
            public StockCheck After;
        }

        sealed class MfgLine
        {
            public int Id;
            public decimal Qty;
            public string Batch;
            public string Pos;
            public string Memo;
            public Dictionary<string, object> Src;
        }
    }
}
