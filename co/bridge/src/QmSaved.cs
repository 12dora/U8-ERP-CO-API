using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 质量单据保存后的确认与回读。新增已提交（报检单）或 U8 已自行提交（检验单）之后，回读失败一律 504 outcome_unknown，
    // 消息带上已知的新主键，不让调用方重投。
    internal static class QmSaved
    {
        const string InsExistsSql = "select count(*) from QMINSPECTVOUCHER where ID=? and CVOUCHTYPE=?";
        const string InsFindSql = "select convert(varchar(20), ID) as ID from QMINSPECTVOUCHER"
            + " where ID>? and CVOUCHTYPE=? and CSOURCEID=? and CMAKER=? order by ID";
        const string InsReadSql = "select CINSPECTCODE as code, CMAKER as maker, CVERIFIER as verifier,"
            + " convert(varchar(19), DVERIFYDATE, 120) as verify_date from QMINSPECTVOUCHER where ID=? and CVOUCHTYPE=?";
        const string InsLinesSql = "select convert(varchar(20), SOURCEAUTOID) as src from QMINSPECTVOUCHERS where ID=?";
        const string ChkFindSql = "select convert(varchar(20), ID) as ID, CMAKER from QMCHECKVOUCHER"
            + " where ID>? and CVOUCHTYPE=? and INSPECTAUTOID=? order by ID";
        const string ChkReadSql = "select CCHECKCODE as code, CMAKER as maker, CVERIFIER as verifier,"
            + " convert(varchar(19), DVERIFYDATE, 120) as verify_date, convert(varchar(20), INSPECTAUTOID) as src,"
            + " convert(varchar(5), isnull(IsWfControlled,0)) as wf, convert(varchar(10), isnull(iVerifyStateNew,0)) as vs"
            + " from QMCHECKVOUCHER where ID=? and CVOUCHTYPE=?";
        const string ChkItemsSql = "select count(*) from QMCHECKVOUCHERS where ID=?";

        internal static bool InspectExists(object conn, QmSpec spec, int id)
        {
            return QmSql.Dec(QmSql.Scalar(conn, InsExistsSql, id, spec.VouchType)) > 0m;
        }

        // 调用前 max(ID) 之后、本来源、本操作员建的报检单：只认唯一一张。
        internal static int FindInspect(object conn, QmSpec spec, int before, int sourceId, string maker)
        {
            List<Dictionary<string, object>> rows = QmSql.Many(conn, InsFindSql, 2, before, spec.VouchType,
                sourceId, maker ?? "");
            return rows.Count == 1 ? CoRows.AsId(CoRows.Col(rows[0], "ID")) : 0;
        }

        // 调用前 max(ID) 之后、挂在该报检单行上的检验单（只认唯一一张；制单人不是本操作员时返回 -1）。
        internal static int FindCheck(object conn, QmSpec spec, int before, int inspectLine, string maker)
        {
            List<Dictionary<string, object>> rows = QmSql.Many(conn, ChkFindSql, 2, before, spec.VouchType, inspectLine);
            if (rows.Count != 1)
            {
                return rows.Count == 0 ? 0 : -1;
            }
            if (CoRows.Col(rows[0], "CMAKER") != (maker ?? "").Trim())
            {
                return -1;
            }
            return CoRows.AsId(CoRows.Col(rows[0], "ID"));
        }

        public static ApiResult Inspect(WorkContext ctx, QmSpec spec, int id, QmInsJob job)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                Dictionary<string, object> row = QmSql.One(conn, InsReadSql, id, spec.VouchType);
                List<Dictionary<string, object>> lines = QmSql.Many(conn, InsLinesSql, 500, id);
                if (row != null && CoRows.Col(row, "maker") == ctx.OperatorName.Trim() && Covers(lines, job))
                {
                    Dictionary<string, object> body = Body(spec, id, row, lines.Count);
                    if (job.Placeholder.Length > 0 && CoRows.Col(row, "code") == job.Placeholder)
                    {
                        // U8 没有换号（账套编号设置与实测不同）：单据已保存，单号照回读的给，审计里记一笔。
                        CoRows.Note(ctx.Item, "U8 保留了占位单号 " + job.Placeholder);
                    }
                    body["source_type"] = spec.SourceKind();
                    body["source_id"] = job.SourceId;
                    return ApiResult.Ok(body);
                }
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "QmSaved " + ex.Message);
            }
            finally
            {
                AdoXml.Close(conn);
            }
            throw Unknown(spec, id);
        }

        public static ApiResult Check(WorkContext ctx, QmSpec spec, int id, int inspectId, int inspectLine)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                Dictionary<string, object> row = QmSql.One(conn, ChkReadSql, id, spec.VouchType);
                if (row != null && CoRows.Col(row, "maker") == ctx.OperatorName.Trim()
                    && CoRows.AsId(CoRows.Col(row, "src")) == inspectLine)
                {
                    int items = (int)QmSql.Dec(QmSql.Scalar(conn, ChkItemsSql, id));
                    Dictionary<string, object> body = Body(spec, id, row, 1);
                    body["items"] = items;
                    body["source_type"] = spec.SourceKind();
                    body["source_id"] = inspectId;
                    body["wf"] = Wf(row);
                    return ApiResult.Ok(body);
                }
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "QmSaved " + ex.Message);
            }
            finally
            {
                AdoXml.Close(conn);
            }
            throw Unknown(spec, id);
        }

        // 新检验单的审批状态（与 workflow/state 的 wf 同名字段）：刚保存时未提交。
        static Dictionary<string, object> Wf(Dictionary<string, object> row)
        {
            bool controlled = CoRows.Col(row, "wf") == "1";
            int vs = (int)QmSql.Dec(CoRows.Col(row, "vs"));
            Dictionary<string, object> wf = new Dictionary<string, object>();
            wf["controlled"] = controlled;
            wf["verify_state_new"] = vs;
            wf["status"] = !controlled ? "not_controlled" : (vs == 0 ? "not_submitted" : "in_approval");
            return wf;
        }

        static bool Covers(List<Dictionary<string, object>> lines, QmInsJob job)
        {
            HashSet<int> seen = new HashSet<int>();
            for (int i = 0; i < lines.Count; i++)
            {
                seen.Add(CoRows.AsId(CoRows.Col(lines[i], "src")));
            }
            for (int i = 0; i < job.Lines.Count; i++)
            {
                if (!seen.Contains(job.Lines[i].Ask.SourceLineId))
                {
                    return false;
                }
            }
            return lines.Count == job.Lines.Count;
        }

        internal static Dictionary<string, object> Body(QmSpec spec, int id, Dictionary<string, object> row, int lines)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = spec.Kind;
            body["id"] = id;
            body["code"] = CoRows.Col(row, "code");
            body["state"] = CoRows.StateOf(row);
            body["lines"] = lines;
            return body;
        }

        internal static BridgeException Unknown(QmSpec spec, int id)
        {
            return new BridgeException(504, "outcome_unknown", "已保存" + spec.KindOf().Title + "但未能回读，标识 "
                + id.ToString(CultureInfo.InvariantCulture));
        }
    }
}
