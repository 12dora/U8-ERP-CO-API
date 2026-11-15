using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 报检单生单（QM01 参照到货单、QM02 参照生产订单）：UFQMCo VoucherOperate("add")，在请求连接的 CoTrans 里。
    // 实测 QM01：U8 先回写到货行 fInspectQuantity 再校验，没有外层事务时失败也会留下累计 → 任何错误（含非空 error 串）都回滚。
    // 编号：U8 自己按 VoucherHistory 取号覆盖占位值，但保存前单号不能为空 → 送占位值，响应的单号从表里回读；
    // 卡片允许手工改号（VoucherNumber.bAllowHandWork=1）时改为按编号规则取真号（QmNo）。U8 没换掉占位号时照回读的给，审计记一笔。
    // 制单人（模板标签「报检人」）是操作员姓名。U8 按 bArrInspectAutoVerify / bProInspectAutoVerify 自动审核，状态从回读取。
    // 提交前在事务里核对来源行累计加上了本次数量；提交后在新连接上核对新单存在、制单人和来源行，读不到 504。
    internal static class QmGenInspect
    {
        public static ApiResult Run(WorkContext ctx, QmSpec spec, int sourceId, Dictionary<string, object> head,
            object[] lines)
        {
            QmAsk ask = QmReq.Parse(spec, head, lines);
            PermContext perm = QmGen.RequireRule(ctx, spec, "create");
            QmInsJob job = QmInsSrc.Load(ctx, spec, sourceId, ask);
            QmGen.CheckRows(perm, spec, "create", QmInsSrc.PermRows(job));
            object[] doms = null;
            QmCo co = null;
            try
            {
                co = QmCo.Open(ctx, spec);
                doms = QmDom.BlankPair(ctx.Conn, spec);
                FillHead(job, doms[0]);
                FillBody(job, doms[1]);
                QmTplReq.Require(ctx.Conn, spec, doms, "");
                if (QmNo.HandAllowed(ctx.Conn, spec.VouchType))
                {
                    QmNo.Allocate(ctx, spec.VouchType, doms[0], "CINSPECTCODE");
                    job.Placeholder = "";
                }
                int id = AddTran(job, co, doms);
                return QmSaved.Inspect(ctx, spec, id, job);
            }
            finally
            {
                QmDom.Release(doms);
                if (co != null)
                {
                    co.Dispose();
                }
            }
        }

        static void FillHead(QmInsJob job, object dom)
        {
            QmSpec spec = job.Spec;
            using (QmRow r = QmRow.Add(dom))
            {
                r.Put("CVOUCHTYPE", spec.VouchType);
                job.Placeholder = QmNo.Placeholder();
                r.Put("CINSPECTCODE", job.Placeholder);
                r.Put("CSOURCE", spec.SourceText);
                r.Put("CSOURCEID", job.SourceId.ToString(CultureInfo.InvariantCulture));
                r.Put("CSOURCECODE", job.SourceCode);
                r.Put("DDATE", job.Date);
                r.Put("DARRIVALDATE", job.ArrDate);
                r.Put("CVENCODE", job.Vendor);
                r.Put("CDEPCODE", job.Dept);
                r.Put("CINSPECTDEPCODE", job.InspectDep);
                r.Put("IVTID", spec.Vt.ToString(CultureInfo.InvariantCulture));
                r.Put("CCHECKTYPECODE", spec.CheckType);
                r.Put("CMAKER", job.Ctx.OperatorName);
                PutDefines(r, job.Ask);
                r.Raw("editprop", QmDom.Added);
            }
        }

        static void FillBody(QmInsJob job, object dom)
        {
            for (int i = 0; i < job.Lines.Count; i++)
            {
                QmInsLine line = job.Lines[i];
                using (QmRow r = QmRow.Add(dom))
                {
                    r.Put("SOURCEAUTOID", line.Ask.SourceLineId.ToString(CultureInfo.InvariantCulture));
                    r.Put("CINVCODE", CoRows.Col(line.Src, "cInvCode"));
                    r.Put("FQUANTITY", line.Ask.Qty);
                    r.Put("CWHCODE", QmInsSrc.WhOf(line));
                    r.Put("ITESTSTYLE", CoRows.Col(line.Src, "TestStyle"));
                    r.Put("IORDERTYPE", "0");
                    r.Put("BEXIGENCY", "0");
                    PutUnit(r, line);
                    if (job.Spec.Incoming)
                    {
                        r.Put("CPOCODE", CoRows.Col(line.Src, "cordercode"));
                        r.Put("CBATCH", CoRows.Col(line.Src, "cBatch"));
                    }
                    else
                    {
                        PutMo(r, job, line);
                    }
                    r.Raw("editprop", QmDom.Added);
                }
            }
        }

        // 辅计量：来源行有辅单位且换算率大于 0 时带上，件数 = 数量 ÷ 换算率（6 位小数）。
        static void PutUnit(QmRow r, QmInsLine line)
        {
            string unit = CoRows.Col(line.Src, "cUnitID");
            decimal rate = QmSql.Dec(CoRows.Col(line.Src, "Rate"));
            if (unit.Length == 0 || rate <= 0m)
            {
                return;
            }
            r.Put("CUNITID", unit);
            r.Put("FCHANGRATE", rate);
            r.Put("FNUM", decimal.Round(line.Ask.Qty / rate, 6, MidpointRounding.AwayFromZero));
        }

        // 产品报检单行挂生产订单（照 U8 界面生成的 QM02 行：IPROORDERID / CPROORDERCODE / IPROORDERAUTOID = 行号）。
        static void PutMo(QmRow r, QmInsJob job, QmInsLine line)
        {
            r.Put("IPROORDERID", job.SourceId.ToString(CultureInfo.InvariantCulture));
            r.Put("CPROORDERCODE", job.SourceCode);
            r.Put("IPROORDERAUTOID", CoRows.Col(line.Src, "SortSeq"));
            r.Put("CDEPCODE", CoRows.Col(line.Src, "MDeptCode"));
            r.Put("CBYPRODUCT", "0");
        }

        internal static void PutDefines(QmRow r, QmAsk ask)
        {
            List<string> names = ask.Defines();
            for (int i = 0; i < names.Count; i++)
            {
                if (names[i].StartsWith("chdefine", StringComparison.Ordinal))
                {
                    r.Raw(names[i], ask.Get(names[i]));
                }
                else
                {
                    r.Put(names[i], ask.Get(names[i]));
                }
            }
        }

        // 在请求连接的事务里：重读来源行累计并再查剩余，add，核对新主键与累计回写，提交。任何错误都回滚。
        static int AddTran(QmInsJob job, QmCo co, object[] doms)
        {
            WorkContext ctx = job.Ctx;
            bool open = false;
            try
            {
                int before = QmSql.MaxId(ctx.Conn, "QMINSPECTVOUCHER");
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                ReadBase(ctx.Conn, job);
                QmOutcome outcome = co.Operate(doms, "add", "");
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                QmGen.Refuse(ctx, outcome);
                int id = QmDom.HeadId(doms[0]);
                StockCall.AfterCheck(ctx, delegate(object conn) { id = RequireWritten(conn, job, id, before); },
                    job.Spec.VouchType + " 来源 " + job.SourceId.ToString(CultureInfo.InvariantCulture));
                // 预演（回滚模式）：登记新报检单和回写了累计报检数量的来源单，CommitSeen 生成预览后回滚。
                DryRun.Created(job.Spec.KindOf(), id);
                DryRun.Touched(Kinds.Find(job.Spec.Incoming ? "arrival" : "production_order"), job.SourceId);
                CoTrans.CommitSeen(ctx.Conn);
                open = false;
                return id;
            }
            catch (Exception)
            {
                CoRows.CatchTran(ctx.Conn, ctx.Item, open);
                throw;
            }
        }

        static void ReadBase(object conn, QmInsJob job)
        {
            string sql = job.Spec.Incoming ? QmSql.ArrUsedSql : QmSql.MoUsedSql;
            for (int i = 0; i < job.Lines.Count; i++)
            {
                QmInsLine line = job.Lines[i];
                line.Base = QmSql.Dec(QmSql.Scalar(conn, sql, line.Ask.SourceLineId));
                decimal left = QmMath.Remaining(QmSql.Dec(CoRows.Col(line.Src, "Total")), line.Base);
                QmMath.RequireLeft(line.Ask.Qty, left, "来源行 " + line.Ask.SourceLineId);
            }
        }

        // 新主键先认 DOM 回写的 ID，没有就按调用前的 max(ID) 找本来源、本操作员的新单；累计必须正好加上本次数量。
        static int RequireWritten(object conn, QmInsJob job, int id, int before)
        {
            if (id <= 0 || !QmSaved.InspectExists(conn, job.Spec, id))
            {
                id = QmSaved.FindInspect(conn, job.Spec, before, job.SourceId, job.Ctx.OperatorName);
            }
            if (id <= 0)
            {
                throw new BridgeException(409, "u8_rejected", "U8 没有生成" + job.Spec.KindOf().Title);
            }
            string sql = job.Spec.Incoming ? QmSql.ArrUsedSql : QmSql.MoUsedSql;
            for (int i = 0; i < job.Lines.Count; i++)
            {
                QmInsLine line = job.Lines[i];
                decimal now = QmSql.Dec(QmSql.Scalar(conn, sql, line.Ask.SourceLineId));
                if (Math.Abs(now - line.Base - line.Ask.Qty) > 0.000001m)
                {
                    throw new BridgeException(409, "u8_rejected",
                        "U8 没有回写" + (job.Spec.Incoming ? "到货单累计报检数量" : "生产订单累计报检数量"));
                }
            }
            return id;
        }
    }
}
