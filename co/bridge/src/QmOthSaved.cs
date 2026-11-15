using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 其他报检单新增、其他检验单生单保存之后：AddVoucher 已在 U8 自己的连接上提交，回读一律在新连接上做。
    // 找到恰好一张、制单人是本操作员、U8 没报错才算成功（U8 换了单号只记审计）；之后核对表体，对不上 504（已保存但不完整）。
    // 找到了但 U8 报错或有多张：504（可能已保存）；没找到：U8 原文 409，其他异常或「成功却没有单据」504。
    internal static class QmOthSaved
    {
        const string InsByCodeSql = "select convert(varchar(20), ID) as ID, CINSPECTCODE as code, CMAKER as maker,"
            + " CVERIFIER as verifier, convert(varchar(19), DVERIFYDATE, 120) as verify_date"
            + " from QMINSPECTVOUCHER where CVOUCHTYPE=? and CINSPECTCODE=? order by ID";
        const string InsByMakerSql = "select convert(varchar(20), ID) as ID, CINSPECTCODE as code, CMAKER as maker,"
            + " CVERIFIER as verifier, convert(varchar(19), DVERIFYDATE, 120) as verify_date"
            + " from QMINSPECTVOUCHER where CVOUCHTYPE=? and ID>? and CMAKER=? order by ID";
        const string InsLinesSql = "select CINVCODE, convert(varchar(40), isnull(FQUANTITY,0)) as qty"
            + " from QMINSPECTVOUCHERS where ID=? order by AUTOID";
        const string ChkFoundSql = "select convert(varchar(20), ID) as ID, CCHECKCODE as code, CMAKER as maker,"
            + " CVERIFIER as verifier, convert(varchar(19), DVERIFYDATE, 120) as verify_date"
            + " from QMCHECKVOUCHER where INSPECTAUTOID=? and CVOUCHTYPE=? order by ID";
        const string ChkItemsSql = "select count(*) from QMCHECKVOUCHERS where ID=?";
        internal const string LineFlagSql = "select convert(varchar(5), isnull(BFLAG,0)) from QMINSPECTVOUCHERS where AUTOID=?";

        public static ApiResult Inspect(QmOthInsJob job, QmOutcome outcome, string code, int before)
        {
            WorkContext ctx = job.Ctx;
            object conn = null;
            List<Dictionary<string, object>> found = null;
            List<Dictionary<string, object>> lines = null;
            try
            {
                conn = ctx.OpenFresh();
                found = QmSql.Many(conn, InsByCodeSql, 3, job.Spec.VouchType, code);
                if (found.Count == 0)
                {
                    found = QmSql.Many(conn, InsByMakerSql, 3, job.Spec.VouchType, before, ctx.OperatorName.Trim());
                }
                if (found.Count == 1)
                {
                    lines = QmSql.Many(conn, InsLinesSql, QmOthReq.LinesMax + 1, CoRows.AsId(CoRows.Col(found[0], "ID")));
                }
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "QmOthSaved " + ex.Message);
                found = null;
            }
            finally
            {
                AdoXml.Close(conn);
            }
            Judge(ctx, job.Spec.Title, outcome, found, code);
            string head = "U8 已保存" + job.Spec.Title + " " + CoRows.Col(found[0], "code") + "，但";
            if (!Covers(job, lines))
            {
                throw new BridgeException(504, "outcome_unknown", head + "回读的表体（存货、数量）与请求不一致，需在 U8 中核对");
            }
            return ApiResult.Ok(Body(job.Spec, found[0], lines.Count));
        }

        public static ApiResult Check(QmChkJob job, QmOthSpec spec, QmOutcome outcome, string code)
        {
            WorkContext ctx = job.Ctx;
            int line = job.LineAsk.SourceLineId;
            object conn = null;
            List<Dictionary<string, object>> found = null;
            int items = 0;
            string flag = "";
            try
            {
                conn = ctx.OpenFresh();
                found = QmSql.Many(conn, ChkFoundSql, 3, line, spec.VouchType);
                if (found.Count == 1)
                {
                    items = (int)QmSql.Dec(QmSql.Scalar(conn, ChkItemsSql, CoRows.AsId(CoRows.Col(found[0], "ID"))));
                    flag = QmSql.Scalar(conn, LineFlagSql, line);
                }
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "QmOthSaved " + ex.Message);
                found = null;
            }
            finally
            {
                AdoXml.Close(conn);
            }
            Judge(ctx, spec.Title, outcome, found, code);
            CheckDone(spec, CoRows.Col(found[0], "code"), items, job.ProjectLines.Count, flag);
            Dictionary<string, object> body = Body(spec, found[0], 1);
            body["items"] = items;
            body["source_type"] = QmOthSpec.InspectKind;
            body["source_id"] = job.InspectId;
            return ApiResult.Ok(body);
        }

        // 检验项目行数等于方案行数、报检单行 BFLAG 已置 1，否则 504（已保存但不完整）。
        static void CheckDone(QmOthSpec spec, string code, int items, int want, string flag)
        {
            string head = "U8 已保存" + spec.Title + " " + code + "，但";
            if (items != want)
            {
                throw new BridgeException(504, "outcome_unknown", head + "检验项目回读到 " + items.ToString(CultureInfo.InvariantCulture)
                    + " 行，检验方案是 " + want.ToString(CultureInfo.InvariantCulture) + " 行，需在 U8 中核对");
            }
            if (flag != "1")
            {
                throw new BridgeException(504, "outcome_unknown", head + "其他报检单行的检验标记（BFLAG）没有置 1，需在 U8 中核对");
            }
        }

        internal static void Judge(WorkContext ctx, string title, QmOutcome outcome, List<Dictionary<string, object>> found,
            string code)
        {
            if (found == null)
            {
                throw new BridgeException(504, "outcome_unknown", "已调用 U8 新增" + title + "（单号 " + code + "），回读失败，结果未知");
            }
            if (found.Count == 1 && outcome.Ok && CoRows.Col(found[0], "maker") == ctx.OperatorName.Trim())
            {
                if (CoRows.Col(found[0], "code") != code)
                {
                    CoRows.Note(ctx.Item, "U8 换了单号 " + code + " → " + CoRows.Col(found[0], "code"));
                }
                return;
            }
            if (found.Count > 0)
            {
                throw new BridgeException(504, "outcome_unknown", "U8 可能已保存" + title + "（单号 " + code + "），结果未知："
                    + QmGen.Text(outcome));
            }
            if (outcome.Error.Length > 0)
            {
                throw new BridgeException(409, "u8_rejected", outcome.Error);
            }
            string text = outcome.Lost == null ? "U8 返回成功但回读不到新" + title : "U8 新增" + title + "调用异常：" + QmGen.Text(outcome);
            throw new BridgeException(504, "outcome_unknown", text + "，结果未知");
        }

        // 表体与请求逐行相同（存货不分大小写、数量按 6 位小数），行数相同。
        internal static bool Covers(QmOthInsJob job, List<Dictionary<string, object>> lines)
        {
            if (lines == null || lines.Count != job.Ask.Lines.Count)
            {
                return false;
            }
            List<string> want = new List<string>();
            List<string> got = new List<string>();
            for (int i = 0; i < lines.Count; i++)
            {
                want.Add(LineKey(CoRows.Col(job.Invs[i], "cInvCode"), job.Ask.Lines[i].Qty));
                got.Add(LineKey(CoRows.Col(lines[i], "CINVCODE"), QmSql.Dec(CoRows.Col(lines[i], "qty"))));
            }
            want.Sort(StringComparer.Ordinal);
            got.Sort(StringComparer.Ordinal);
            return string.Join("\n", want.ToArray()) == string.Join("\n", got.ToArray());
        }

        static string LineKey(string inv, decimal qty)
        {
            return (inv ?? "").Trim().ToUpperInvariant() + "|" + QmSql.Num(decimal.Round(qty, 6));
        }

        static Dictionary<string, object> Body(QmOthSpec spec, Dictionary<string, object> row, int lines)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = spec.Kind;
            body["id"] = CoRows.AsId(CoRows.Col(row, "ID"));
            body["code"] = CoRows.Col(row, "code");
            body["state"] = CoRows.StateOf(row);
            body["lines"] = lines;
            return body;
        }
    }
}
