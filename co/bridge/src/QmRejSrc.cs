using System;
using System.Collections.Generic;

namespace U8Co
{
    internal sealed class QmRejJob
    {
        public WorkContext Ctx;
        public QmRejSpec Spec;
        public QmRejAsk Ask;
        public Dictionary<string, object> Check;
        public bool Wf;
        // 表体部门、检验单的辅计量单位与换算率、件数小数位、扩展自定义项的模板字段名（QmRejUnits.Fill）。
        public QmRejUnit Unit = new QmRejUnit();
        public readonly Dictionary<string, string> DefineNames = new Dictionary<string, string>(StringComparer.Ordinal);
    }

    // 不良品处理单的来源：检验单（QM03 / QM04），按 U8 参照用的同一视图读（待处理不良数量 FDISQUANTITYS、关闭人 PU_CBCLOSER）。
    // 检验单必须已审核、未关闭、未生成过不良品处理单（BREJFLAG）、待处理不良数量大于 0，各行处理数量之和必须等于它
    // （U8 参照生单整单带入、保存时同样核对）。处理方式、不良原因（质检类 iReasontype=1）、降级后存货、仓库按档案核对。
    internal static class QmRejSrc
    {
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
        static string CheckSql(QmRejSpec spec)
        {
            return "select convert(varchar(20), ID) as ID, CCHECKCODE, isnull(CVERIFIER,'') as CVERIFIER,"
                + " convert(varchar(5), isnull(BREJFLAG,0)) as Rej, convert(varchar(40), isnull(FDISQUANTITYS,0)) as Dis,"
                + " isnull(PU_CBCLOSER,'') as Closer, CINVCODE, CVENCODE, CDEPCODE, CWHCODE, CINSPECTDEPCODE,"
                + " convert(varchar(20), INSPECTID) as INSPECTID, CUNITID, convert(varchar(40), isnull(FCHANGRATE,0)) as Rate"
                + " from " + CoRows.Ident(spec.CheckView) + " where ID=? and CVOUCHTYPE=?";
        }

        static void Gate(object conn, QmRejJob job)
        {
            Dictionary<string, object> c = job.Check;
            if (CoRows.Col(c, "CVERIFIER").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "检验单未审核");
            }
            if (CoRows.Col(c, "Closer").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "检验单的来源行已关闭");
            }
            bool exists = QmSql.Dec(QmSql.Scalar(conn, RejectedSql, job.Ask.CheckId, job.Spec.VouchType)) > 0m;
            if (exists)
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
            RequireTotal(job.Ask.Total(), dis);
        }

        // 各行处理数量之和必须等于检验单的待处理不良数量（6 位小数内相等）。
        internal static void RequireTotal(decimal total, decimal dis)
        {
            if (decimal.Round(total, 6) != decimal.Round(dis, 6))
            {
                throw BridgeException.BadField("lines", "各行处理数量之和必须等于检验单的不良品数量 " + QmSql.Num(dis)
                    + "，当前为 " + QmSql.Num(total));
            }
        }

        // 处理方式、不良原因、降级后存货、仓库按档案核对，并补上 U8 要的名称与处理流程（编码按档案原样写）。
        static void Archives(object conn, QmRejLine line)
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
