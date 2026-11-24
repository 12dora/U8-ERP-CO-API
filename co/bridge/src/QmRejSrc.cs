using System;
using System.Collections.Generic;

namespace U8Co
{
    internal sealed class QmRejJob
    {
        public WorkContext Ctx;
        public QmRejSpec Spec;
        public QmRejAsk Ask;
        // 检验单在参照视图里的行。
        public Dictionary<string, object> Check;
        public bool Wf;
        // 表体部门、检验单的辅计量单位与换算率、件数小数位、扩展自定义项的模板字段名（QmRejUnits.Fill）。
        public QmRejUnit Unit = new QmRejUnit();
        public readonly Dictionary<string, string> DefineNames = new Dictionary<string, string>(StringComparer.Ordinal);
    }

    // 不良品处理单的来源：检验单（QM03 / QM04），按 U8 参照用的同一视图读（待处理不良数量 FDISQUANTITYS、关闭人 PU_CBCLOSER）。
    // 来源必须已审核、未关闭、未生成过不良品处理单（BREJFLAG）、待处理不良数量大于 0，各行处理数量之和必须等于它
    // （U8 参照生单整单带入、保存时同样核对）。处理方式、不良原因（质检类 iReasontype=1）、降级后存货、仓库按档案核对。
    // 合并检验（BMERGECHECKFLAG=1）的检验单不能参照生成（409）：U8 自己按合并明细（QMMergeCheckDetail）逐条生成，
    // 这类处理单的 MERGECHECKAUTOID 指向明细、不良品处理标记 BREJFLAG 记在明细上，修改、删除读它（MergeIdColumn、Flag）。
    // 来源行就是检验单本身（source_line_id = 检验单 ID，QmRejReq 核对）。
    internal static class QmRejSrc
    {
        const string CheckFlagSql = "select convert(varchar(5), isnull(BREJFLAG,0)) from QMCHECKVOUCHER where ID=?";
        const string MergeFlagSql = "select convert(varchar(5), isnull(BREJFLAG,0)) from QMMergeCheckDetail where AUTOID=?";
        // 处理单所属的合并明细 AUTOID（不是合并检验时 0），QmRejOps.DocSql 带出为 MergeId。
        internal const string MergeIdColumn = "convert(varchar(20), case when isnull(BMERGECHECKFLAG,0)=1"
            + " then isnull(MERGECHECKAUTOID,0) else 0 end) as MergeId";
        const string RejectedSql = "select count(*) from QMREJECTVOUCHER where CHECKID=? and CVOUCHTYPE=?";
        const string DisposeSql = "select CSCRAPDISCODE, isnull(CSCRAPDISNAME,'') as Name,"
            + " convert(varchar(10), isnull(IDISPOSEFLOW,-1)) as Flow from QMSCRAPDISPOSE where CSCRAPDISCODE=?";
        const string ReasonSql = "select cReasonCode, cReasonName from Reason where cReasonCode=? and iReasontype=1";
        const string InvSql = "select count(*) from Inventory where cInvCode=?";
        // 处理流程 2：降级 / 让步接收 / 升级，按处理后存货、处理后数量入库（MfgGenRej）。
        internal const int DimFlow = 2;

        public static QmRejJob Load(WorkContext ctx, QmRejSpec spec, QmRejAsk ask)
        {
            object conn = ctx.Conn;
            QmRejJob job = new QmRejJob();
            job.Ctx = ctx;
            job.Spec = spec;
            job.Ask = ask;
            job.Check = QmSql.One(conn, CheckSql(spec), ask.CheckId, spec.CheckType);
            if (job.Check == null)
            {
                throw new BridgeException(404, "not_found", "检验单不存在");
            }
            Gate(conn, job);
            QmRejReq.RequireExtraColumns(conn, ask);
            for (int i = 0; i < ask.Lines.Count; i++)
            {
                try
                {
                    Archives(conn, ask.Lines[i]);
                    QmRejUnits.Dim(conn, ask.Lines[i]);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
            job.Wf = QmSql.FlowOn(conn, spec.VouchType);
            QmRejUnits.Fill(conn, job);
            return job;
        }

        // 视图名只来自 QmRejSpec 常量。
        internal static string CheckSql(QmRejSpec spec)
        {
            return "select convert(varchar(20), ID) as ID, CCHECKCODE, isnull(CVERIFIER,'') as CVERIFIER,"
                + " convert(varchar(5), isnull(BREJFLAG,0)) as Rej, convert(varchar(40), isnull(FDISQUANTITYS,0)) as Dis,"
                + " convert(varchar(5), isnull(BMERGECHECKFLAG,0)) as Merged,"
                + " isnull(PU_CBCLOSER,'') as Closer, CINVCODE, CVENCODE, CDEPCODE, CWHCODE, CINSPECTDEPCODE,"
                + " convert(varchar(20), INSPECTID) as INSPECTID, CUNITID, convert(varchar(40), isnull(FCHANGRATE,0)) as Rate"
                + " from " + CoRows.Ident(spec.CheckView) + " where ID=? and CVOUCHTYPE=?";
        }

        // 交给 U8 AddVoucherByRef 的来源记录集：检验单在参照视图里的行（未关闭）。
        internal static string RefSql(QmRejJob job, out object[] args)
        {
            args = new object[] { job.Ask.CheckId, job.Spec.CheckType };
            return "select * from " + CoRows.Ident(job.Spec.CheckView) + " where ID=? and CVOUCHTYPE=? and isnull(PU_CBCLOSER,'')=''";
        }

        static void Gate(object conn, QmRejJob job)
        {
            Dictionary<string, object> c = job.Check;
            if (CoRows.Col(c, "Merged") == "1")
            {
                throw new BridgeException(409, "state_mismatch", "合并检验的检验单请在 U8 中按合并明细生成不良品处理单");
            }
            if (CoRows.Col(c, "CVERIFIER").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "检验单未审核");
            }
            if (CoRows.Col(c, "Closer").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "检验单的来源行已关闭");
            }
            if (QmSql.Dec(QmSql.Scalar(conn, RejectedSql, job.Ask.CheckId, job.Spec.VouchType)) > 0m)
            {
                throw new BridgeException(409, "state_mismatch", "检验单已生成不良品处理单");
            }
            if (CoRows.Col(c, "Rej") == "1")
            {
                throw new BridgeException(409, "state_mismatch", "检验单已标记生成不良品处理单，但找不到对应单据，需在 U8 中核对");
            }
            decimal dis = QmSql.Dec(CoRows.Col(c, "Dis"));
            if (dis <= 0m)
            {
                throw new BridgeException(409, "state_mismatch", "检验单没有待处理的不良品数量");
            }
            RequireTotal(job.Ask.Total(), dis, "检验单的不良品数量");
        }

        // 各行处理数量之和必须等于应处理的不良数量 due（6 位小数内相等）；what 是 due 的名称。
        internal static void RequireTotal(decimal total, decimal due, string what)
        {
            if (decimal.Round(total, 6) != decimal.Round(due, 6))
            {
                throw BridgeException.BadField("lines", "各行处理数量之和必须等于" + what + " " + QmSql.Num(due)
                    + "，当前为 " + QmSql.Num(total));
            }
        }

        // 处理单的不良品处理标记：mergeId（合并明细 AUTOID）大于 0 时读合并明细，否则读检验单；都没有时空串。
        internal static string Flag(object conn, int checkId, int mergeId)
        {
            if (mergeId > 0)
            {
                return QmSql.Scalar(conn, MergeFlagSql, mergeId);
            }
            return checkId > 0 ? QmSql.Scalar(conn, CheckFlagSql, checkId) : "";
        }

        // 处理方式、不良原因、降级后存货、仓库按档案核对，并补上 U8 要的名称与处理流程（编码按档案原样写）。
        internal static void Archives(object conn, QmRejLine line)
        {
            Dictionary<string, object> dispose = QmSql.One(conn, DisposeSql, line.Dispose);
            if (dispose == null)
            {
                throw BridgeException.BadField("cscrapdiscode", "处理方式不存在 " + line.Dispose);
            }
            line.Dispose = CoRows.Col(dispose, "CSCRAPDISCODE");
            line.DisposeName = CoRows.Col(dispose, "Name");
            line.Flow = (int)QmSql.Dec(CoRows.Col(dispose, "Flow"));
            Dictionary<string, object> reason = QmSql.One(conn, ReasonSql, line.Reason);
            if (reason == null)
            {
                throw BridgeException.BadField("creasoncode", "不良原因不存在 " + line.Reason + "（须为质量管理类原因）");
            }
            line.Reason = CoRows.Col(reason, "cReasonCode");
            line.ReasonName = CoRows.Col(reason, "cReasonName");
            DimInv(conn, line);
            if (line.Wh.Length > 0 && Rows.Scalar(conn, QmSql.WhSql, new object[] { line.Wh }) == null)
            {
                throw BridgeException.BadField("cbwhcode", "仓库不存在 " + line.Wh);
            }
        }

        // 处理方式的处理流程（QMSCRAPDISPOSE.IDISPOSEFLOW），档案里没有时 -1。
        internal static int FlowOf(object conn, string dispose)
        {
            Dictionary<string, object> row = QmSql.One(conn, DisposeSql, dispose);
            return row == null ? -1 : (int)QmSql.Dec(CoRows.Col(row, "Flow"));
        }

        internal static void DimInv(object conn, QmRejLine line)
        {
            RequireDim(line);
            if (line.DimInv.Length > 0 && QmSql.Dec(QmSql.Scalar(conn, InvSql, line.DimInv)) <= 0m)
            {
                throw BridgeException.BadField("cdiminvcode", "存货不存在 " + line.DimInv);
            }
        }

        // 处理流程 2 必须给降级后存货；其他流程不收（U8 只在流程 2 用它入库）。
        internal static void RequireDim(QmRejLine line)
        {
            if (line.Flow == DimFlow && line.DimInv.Length == 0)
            {
                throw BridgeException.BadField("cdiminvcode", "处理方式「" + line.DisposeName + "」须指定处理后存货 cdiminvcode");
            }
            if (line.Flow != DimFlow && line.DimInv.Length > 0)
            {
                throw BridgeException.BadField("cdiminvcode", "只有降级类处理方式可以指定处理后存货 cdiminvcode");
            }
        }

        // 数据权限：一行（供应商、部门、存货、仓库取检验单）。
        public static List<Dictionary<string, object>> PermRows(QmRejJob job)
        {
            Dictionary<string, object> row = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            row["CVENCODE"] = CoRows.Col(job.Check, "CVENCODE");
            row["CDEPCODE"] = CoRows.Col(job.Check, "CDEPCODE");
            row["CINVCODE"] = CoRows.Col(job.Check, "CINVCODE");
            row["CWHCODE"] = CoRows.Col(job.Check, "CWHCODE");
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            rows.Add(row);
            return rows;
        }
    }
}
