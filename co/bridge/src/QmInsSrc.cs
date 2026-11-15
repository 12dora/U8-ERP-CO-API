using System;
using System.Collections.Generic;

namespace U8Co
{
    internal sealed class QmInsJob
    {
        public WorkContext Ctx;
        public QmSpec Spec;
        public QmAsk Ask;
        public int SourceId;
        public string SourceCode = "";
        public string Vendor = "";
        public string Dept = "";
        public string InspectDep = "";
        public string Date = "";
        public string ArrDate = "";
        // 送给 U8 的占位单号（允许手工改号时取了真号，为空）。
        public string Placeholder = "";
        public readonly List<QmInsLine> Lines = new List<QmInsLine>();
    }

    internal sealed class QmInsLine
    {
        public QmLineAsk Ask;
        public Dictionary<string, object> Src;
        // 事务里重读的累计（到货行 fInspectQuantity / 生产订单行 DeclaredQty），保存后核对加上了本次数量。
        public decimal Base;
    }

    // 报检单的来源：QM01 参照到货单（已审核、蓝字、行 bGsp=1 未关闭，剩余 = iQuantity − fInspectQuantity），
    // QM02 参照生产订单（行已审核 Status=3、未关闭，剩余 = Qty − DeclaredQty）。source_line_id 必须是本来源的行。
    internal static class QmInsSrc
    {
        public static QmInsJob Load(WorkContext ctx, QmSpec spec, int sourceId, QmAsk ask)
        {
            QmInsJob job = new QmInsJob();
            job.Ctx = ctx;
            job.Spec = spec;
            job.Ask = ask;
            job.SourceId = sourceId;
            job.Date = ask.Date.Length > 0 ? ask.Date : (ctx.Item.Date ?? "").Trim();
            if (spec.Incoming)
            {
                Arrival(ctx.Conn, job);
            }
            else
            {
                Mo(ctx.Conn, job);
            }
            job.Dept = Or(ask.Get("cdepcode"), job.Dept);
            job.InspectDep = Or(ask.Get("cinspectdepcode"), job.Dept);
            QmSql.DeptName(ctx.Conn, job.Dept);
            QmSql.DeptName(ctx.Conn, job.InspectDep);
            return job;
        }

        static void Arrival(object conn, QmInsJob job)
        {
            Dictionary<string, object> head = QmSql.One(conn, QmSql.ArrHeadSql, job.SourceId);
            if (head == null)
            {
                throw new BridgeException(404, "not_found", "到货单不存在");
            }
            if (CoRows.Col(head, "iBillType") != "0")
            {
                throw new BridgeException(400, "bad_request", "只能参照蓝字到货单报检");
            }
            if (CoRows.Col(head, "cverifier").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "到货单未审核");
            }
            job.SourceCode = CoRows.Col(head, "cCode");
            job.Vendor = CoRows.Col(head, "cVenCode");
            job.Dept = CoRows.Col(head, "cDepCode");
            job.ArrDate = CoRows.Col(head, "dDate");
            for (int i = 0; i < job.Ask.Lines.Count; i++)
            {
                QmInsLine line = Line(conn, job, QmSql.ArrLineSql, job.Ask.Lines[i]);
                if (CoRows.Col(line.Src, "bGsp") != "1")
                {
                    throw new BridgeException(409, "state_mismatch", "到货单行不需要报检（未设质检）：" + line.Ask.SourceLineId);
                }
                RequireFresh(conn, line);
                job.Lines.Add(line);
            }
        }

        static void Mo(object conn, QmInsJob job)
        {
            Dictionary<string, object> head = QmSql.One(conn, QmSql.MoHeadSql, job.SourceId);
            if (head == null)
            {
                throw new BridgeException(404, "not_found", "生产订单不存在");
            }
            job.SourceCode = CoRows.Col(head, "MoCode");
            for (int i = 0; i < job.Ask.Lines.Count; i++)
            {
                QmInsLine line = Line(conn, job, QmSql.MoLineSql, job.Ask.Lines[i]);
                if (CoRows.Col(line.Src, "Status") != "3")
                {
                    throw new BridgeException(409, "state_mismatch", "生产订单行未审核：" + line.Ask.SourceLineId);
                }
                if (CoRows.Col(line.Src, "QcFlag") != "1")
                {
                    throw new BridgeException(409, "state_mismatch", "生产订单行不需要报检（未设质检）：" + line.Ask.SourceLineId);
                }
                if (job.Dept.Length == 0)
                {
                    job.Dept = CoRows.Col(line.Src, "MDeptCode");
                }
                job.Lines.Add(line);
            }
        }

        // 同 U8 参照规则（视图 QM_QREFARR 要求 binspect=0）：bInspect 是「已全部报检」（实测：部分报检后仍为 0，
        // 报检满额才置 1），可分次报检，余量另按剩余数量核对。产品报检的参照视图 QM_RefOrder 同样按剩余数量。
        static void RequireFresh(object conn, QmInsLine line)
        {
            if (CoRows.Col(line.Src, "bInspect") == "1")
            {
                throw new BridgeException(409, "state_mismatch", "到货单行已全部报检：" + line.Ask.SourceLineId);
            }
        }

        // 行必须属于来源单据、未关闭，且不超过剩余可报检数量；调用方送的仓库必须存在。
        static QmInsLine Line(object conn, QmInsJob job, string sql, QmLineAsk ask)
        {
            Dictionary<string, object> src = QmSql.One(conn, sql, ask.SourceLineId, job.SourceId);
            if (src == null)
            {
                throw new BridgeException(400, "bad_request", "明细行不存在：" + ask.SourceLineId);
            }
            if (CoRows.Col(src, "Closer").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "来源行已关闭：" + ask.SourceLineId);
            }
            decimal left = QmMath.Remaining(QmSql.Dec(CoRows.Col(src, "Total")), QmSql.Dec(CoRows.Col(src, "Used")));
            QmMath.RequireLeft(ask.Qty, left, "来源行 " + ask.SourceLineId);
            if (ask.Wh.Length > 0 && Rows.Scalar(conn, QmSql.WhSql, new object[] { ask.Wh }) == null)
            {
                throw new BridgeException(400, "bad_request", "仓库不存在 " + ask.Wh);
            }
            QmInsLine line = new QmInsLine();
            line.Ask = ask;
            line.Src = src;
            return line;
        }

        // 数据权限按每一行判断（列名同 PermRegistryQm）。
        public static List<Dictionary<string, object>> PermRows(QmInsJob job)
        {
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            for (int i = 0; i < job.Lines.Count; i++)
            {
                Dictionary<string, object> row = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
                row["CVENCODE"] = job.Vendor;
                row["CDEPCODE"] = job.Dept;
                row["CINVCODE"] = CoRows.Col(job.Lines[i].Src, "cInvCode");
                row["CWHCODE"] = WhOf(job.Lines[i]);
                rows.Add(row);
            }
            return rows;
        }

        public static string WhOf(QmInsLine line)
        {
            return Or(line.Ask.Wh, CoRows.Col(line.Src, "cWhCode"));
        }

        static string Or(string value, string fallback)
        {
            return value != null && value.Length > 0 ? value : fallback ?? "";
        }
    }
}
