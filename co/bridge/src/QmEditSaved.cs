using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 质量单据修改之后：U8 已在自己的连接上提交，回读一律在新连接上做。成功 = UFTS 变了、请求的每个字段都读到了、
    // 检验单对应的报检单行累计检验数量和检验标记没变（修改不改检验数量）、U8 没报错。
    // UFTS 没变：U8 原文 409 u8_rejected；其他异常或「返回成功却没改」504。UFTS 变了但有任何不符、或 U8 报了错：504 outcome_unknown
    // （单据已经被改过，不能用表示「什么都没写」的 409）。
    internal static class QmEditSaved
    {
        const string ItemsSql = "select CCHKITEMCODE, CCHKGUIDECODE, CCHECKVALUE, CTARGETQJUG from QMCHECKVOUCHERS where ID=?";
        const string HeadSql = "select * from {0} where ID=? and CVOUCHTYPE=?";

        sealed class After
        {
            public Dictionary<string, object> Head;
            public Dictionary<string, object> Extra;
            public List<Dictionary<string, object>> Items;
            public string LineState = "";
            public Dictionary<string, object> State;
            public int Lines;
        }

        public static ApiResult Confirm(QmEditJob job, QmOutcome outcome)
        {
            After after = Read(job);
            string text = QmGen.Text(outcome);
            if (after == null)
            {
                throw new BridgeException(504, "outcome_unknown", "已调用 U8 修改" + job.Title + "，回读失败，结果未知");
            }
            if (after.Head == null)
            {
                throw new BridgeException(504, "outcome_unknown", "修改" + job.Title + "后回读不到单据，需在 U8 中核对");
            }
            if (CoRows.Col(after.Head, "UFTS") == job.Ufts)
            {
                if (outcome.Error.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", outcome.Error);
                }
                throw new BridgeException(504, "outcome_unknown", (outcome.Lost != null ? "U8 修改调用异常：" + text
                    : "U8 返回成功但单据没有变化（ufts 未变）") + "，结果未知");
            }
            List<string> diff = Diff(job, after);
            if (diff.Count > 0)
            {
                throw new BridgeException(504, "outcome_unknown", "U8 已保存" + job.Title + "，但回读与请求不一致（"
                    + string.Join("、", diff.ToArray()) + "），需在 U8 中核对");
            }
            if (!outcome.Ok)
            {
                throw new BridgeException(504, "outcome_unknown", "U8 报错但" + job.Title + "已被修改，需在 U8 中核对：" + text);
            }
            return ApiResult.Ok(Body(job, after));
        }

        static After Read(QmEditJob job)
        {
            WorkContext ctx = job.Ctx;
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                After after = new After();
                after.Head = QmSql.One(conn, string.Format(CultureInfo.InvariantCulture, HeadSql, CoRows.Ident(job.HeadTable)),
                    job.Id, job.VouchType);
                if (after.Head == null)
                {
                    return after;
                }
                after.Extra = QmEditLoad.Extra(conn, job);
                after.Items = job.Ask.Items == null ? null : QmSql.Many(conn, ItemsSql, 500, job.Id);
                after.LineState = job.Ask.Check ? QmEditLoad.LineState(conn, job) : "";
                after.State = QmSql.One(conn, job.Ask.Check ? QmOthOps.ChkDocSql : QmOthOps.InsDocSql, job.Id, job.VouchType);
                string count = "select count(*) from " + (job.Ask.Check ? "QMCHECKVOUCHERS" : "QMINSPECTVOUCHERS") + " where ID=?";
                after.Lines = CoRows.AsId(QmSql.Scalar(conn, count, job.Id));
                return after;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "QmEditSaved " + ex.Message);
                return null;
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static List<string> Diff(QmEditJob job, After after)
        {
            List<string> diff = new List<string>();
            for (int i = 0; i < job.Fields.Count; i++)
            {
                string[] f = job.Fields[i];
                if (f[3] == QmEditJob.DerivedMark)
                {
                    continue;
                }
                bool extra = f[0].StartsWith("chdefine", StringComparison.OrdinalIgnoreCase);
                string got = CoRows.Col(extra ? after.Extra : after.Head, f[0]);
                if (!Same(f[1], got) && !diff.Contains(f[2]))
                {
                    diff.Add(f[2]);
                }
            }
            diff.AddRange(QmEditItems.Diff(job, after.Items));
            if (job.Ask.Check && after.LineState != job.LineState)
            {
                diff.Add("报检单行累计检验数量或检验标记变化");
            }
            return diff;
        }

        // 回读值与请求值是否一致：去空白后相同；或两边都是数（按值比）；或请求是 yyyy-MM-dd、回读是同一天的日期时间。
        internal static bool Same(string want, string got)
        {
            string a = (want ?? "").Trim();
            string b = (got ?? "").Trim();
            if (a == b)
            {
                return true;
            }
            decimal x;
            decimal y;
            if (decimal.TryParse(a, NumberStyles.Float, CultureInfo.InvariantCulture, out x)
                && decimal.TryParse(b, NumberStyles.Float, CultureInfo.InvariantCulture, out y))
            {
                return x == y;
            }
            return a.Length == 10 && b.Length > 10 && b.StartsWith(a, StringComparison.Ordinal) && (b[10] == ' ' || b[10] == 'T');
        }

        static Dictionary<string, object> Body(QmEditJob job, After after)
        {
            Dictionary<string, object> state = CoRows.StateOf(after.State);
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = job.Ask.Kind.Name;
            body["id"] = job.Id;
            body["code"] = CoRows.Col(after.State, "code");
            body["state"] = state;
            body["lines"] = after.Lines;
            return body;
        }
    }
}
