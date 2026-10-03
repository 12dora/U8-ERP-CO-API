using System;
using System.Collections.Generic;

namespace U8Co
{
    internal sealed class QmChkJob
    {
        public WorkContext Ctx;
        public QmSpec Spec;
        public QmAsk Ask;
        public QmLineAsk LineAsk;
        public int InspectId;
        public Dictionary<string, object> Head;
        public Dictionary<string, object> Line;
        public Dictionary<string, object> Project;
        public List<Dictionary<string, object>> ProjectLines;
        public string Date = "";
        public string Time = "";
        public string DepCode = "";
        public string DepName = "";
        public string InspectDepName = "";
        public string PersonName = "";
        public string TypeName = "";
        public string Conclusion = "";
        public decimal Dt;
        public bool Wf;
        // 让步接收核准人 {编码, 姓名, 日期}（QmYield.Resolve）；编码为空时不写。
        public string[] Yield;
    }

    // 检验单的来源：QM03 参照来料报检单（QM01）、QM04 参照产品报检单（QM02）。报检单必须已审核；
    // 行 = 报检单行 AUTOID，剩余 = FQUANTITY − FSUMCHECKQTY。检验员（Person）、检验部门、检验方案（QMCHECKPROJECT，未停用）
    // 必须存在；方案缺省取同存货最近一张同类检验单的 PROJECTID（账套里没有存货与方案的对照表）。
    internal static class QmChkSrc
    {
        public static QmChkJob Load(WorkContext ctx, QmSpec spec, int inspectId, QmAsk ask)
        {
            object conn = ctx.Conn;
            QmChkJob job = new QmChkJob();
            job.Ctx = ctx;
            job.Spec = spec;
            job.Ask = ask;
            job.LineAsk = ask.Lines[0];
            job.InspectId = inspectId;
            Inspect(conn, job);
            job.Date = ask.Date.Length > 0 ? ask.Date : (ctx.Item.Date ?? "").Trim();
            job.Time = QmDom.Now();
            job.PersonName = Person(conn, ask.Get("ccheckpersoncode"));
            job.DepCode = Or(ask.Get("cdepcode"), CoRows.Col(job.Head, "CINSPECTDEPCODE"));
            job.DepName = QmSql.DeptName(conn, job.DepCode);
            job.InspectDepName = QmSql.DeptName(conn, CoRows.Col(job.Head, "CINSPECTDEPCODE"));
            job.Project = Project(conn, job);
            job.ProjectLines = QmSql.Many(conn, QmSql.ProjectLinesSql, 500, CoRows.AsId(CoRows.Col(job.Project, "ID")));
            if (job.ProjectLines.Count == 0)
            {
                throw new BridgeException(409, "state_mismatch", "检验方案没有检验指标：" + CoRows.Col(job.Project, "CPROJECTCODE"));
            }
            job.TypeName = QmSql.CheckTypeName(conn, CoRows.Col(job.Head, "CCHECKTYPECODE"));
            job.Wf = QmSql.FlowOn(conn, spec.VouchType);
            job.Dt = ask.Dt > 0m ? ask.Dt : 1m;
            job.Conclusion = Or(ask.Get("cchkconclusion"), QmMath.Conclusion(job.LineAsk.Dis));
            // 有让步数量时来料 / 产品检验单要有让步接收核准人（模板必输）。
            QmYield.Require(ask.Get(QmYield.CodeKey), "", job.LineAsk.Con, true, "head." + QmYield.CodeKey);
            job.Yield = QmYield.Resolve(conn, ask.Get(QmYield.CodeKey), ask.Get(QmYield.DateKey), job.Date, "head.");
            return job;
        }

        static void Inspect(object conn, QmChkJob job)
        {
            string source = job.Spec.Incoming ? "QM01" : "QM02";
            job.Head = QmSql.One(conn, QmSql.InsHeadSql, job.InspectId, source);
            if (job.Head == null)
            {
                throw new BridgeException(404, "not_found", "报检单不存在");
            }
            if (CoRows.Col(job.Head, "CVERIFIER").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "报检单未审核");
            }
            job.Line = QmSql.One(conn, QmSql.InsLineSql, job.LineAsk.SourceLineId, job.InspectId);
            if (job.Line == null)
            {
                throw new BridgeException(400, "bad_request", "明细行不存在：" + job.LineAsk.SourceLineId);
            }
            decimal left = QmMath.Remaining(QmSql.Dec(CoRows.Col(job.Line, "Total")), QmSql.Dec(CoRows.Col(job.Line, "Used")));
            QmMath.RequireLeft(job.LineAsk.Qty, left, "报检单行 " + job.LineAsk.SourceLineId);
        }

        internal static string Person(object conn, string code)
        {
            string name = Rows.Scalar(conn, QmSql.PersonSql, new object[] { code });
            if (name == null)
            {
                throw new BridgeException(400, "bad_request", "检验员不存在 " + code);
            }
            return name.Trim();
        }

        // 送了 project_code 按编码找；没送取同存货最近一张同类检验单的方案，找不到 400。停用的方案不收。
        // 其他检验单生单（QmOthChk）同样用它（job.Spec 是 QmOthSpec.CheckAsk，同类即 QM15）。
        internal static Dictionary<string, object> Project(object conn, QmChkJob job)
        {
            string code = job.Ask.Get("project_code");
            Dictionary<string, object> row;
            if (code.Length > 0)
            {
                row = QmSql.One(conn, QmSql.ProjectSql, code);
                if (row == null)
                {
                    throw new BridgeException(400, "bad_request", "检验方案不存在 " + code);
                }
            }
            else
            {
                int last = CoRows.AsId(QmSql.Scalar(conn, QmSql.LastProjectSql, CoRows.Col(job.Line, "CINVCODE"),
                    job.Spec.VouchType));
                row = last > 0 ? QmSql.One(conn, QmSql.ProjectByIdSql, last) : null;
                if (row == null)
                {
                    throw new BridgeException(400, "bad_request", "请指定检验方案 project_code");
                }
            }
            if (CoRows.Col(row, "Stopped") == "1")
            {
                throw new BridgeException(400, "bad_request", "检验方案已停用 " + CoRows.Col(row, "CPROJECTCODE")
                    + (code.Length > 0 ? "" : "，请指定检验方案 project_code"));
            }
            return row;
        }

        // 数据权限：该检验单一行（供应商、部门取报检单表头，存货、仓库取报检单行）。
        public static List<Dictionary<string, object>> PermRows(QmChkJob job)
        {
            Dictionary<string, object> row = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            row["CVENCODE"] = CoRows.Col(job.Head, "CVENCODE");
            row["CDEPCODE"] = job.DepCode;
            row["CINVCODE"] = CoRows.Col(job.Line, "CINVCODE");
            row["CWHCODE"] = CoRows.Col(job.Line, "CWHCODE");
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            rows.Add(row);
            return rows;
        }

        static string Or(string value, string fallback)
        {
            return value != null && value.Length > 0 ? value : fallback ?? "";
        }
    }
}
