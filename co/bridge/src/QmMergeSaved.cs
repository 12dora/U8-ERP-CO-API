using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 合并检验保存后的确认（新连接）：按本次取的单号找调用前 max(ID) 之后的产品检验单；按单号找不到时（U8 没有保留桥取的单号）
    // 改按 max(ID) 之后、本类型、BMERGECHECKFLAG=1、本操作员、合并明细的来源行恰好是请求的那几条找。恰好一张、制单人是本操作员、
    // BMERGECHECKFLAG=1、U8 没报错才算找到；找到后还要合并明细恰好是请求的来源行、各行检验数量等于请求，来源报检单行的
    // 累计检验数量之和增加了表头数量，否则 504（已保存但不完整，需在 U8 中核对）。没找到：来源累计检验数量变了 504，
    // U8 原文 409，其他异常或「成功却没有单据」504。
    internal static class QmMergeSaved
    {
        const string FoundSql = "select convert(varchar(20), ID) as ID, CCHECKCODE as code, CMAKER as maker,"
            + " CVERIFIER as verifier, convert(varchar(19), DVERIFYDATE, 120) as verify_date,"
            + " convert(varchar(5), isnull(BMERGECHECKFLAG,0)) as merged,"
            + " convert(varchar(5), isnull(IsWfControlled,0)) as wf, convert(varchar(10), isnull(iVerifyStateNew,0)) as vs"
            + " from QMCHECKVOUCHER where ID>? and CVOUCHTYPE=? and CCHECKCODE=? order by ID";
        const string DetailSql = "select convert(varchar(20), INSPECTAUTOID) as src, convert(varchar(40), isnull(FQUANTITY,0)) as qty"
            + " from QMMergeCheckDetail where ID=?";
        const string ItemsSql = "select count(*) from QMCHECKVOUCHERS where ID=?";
        const string ByMakerSql = "select convert(varchar(20), ID) as ID, CCHECKCODE as code, CMAKER as maker,"
            + " CVERIFIER as verifier, convert(varchar(19), DVERIFYDATE, 120) as verify_date,"
            + " convert(varchar(5), isnull(BMERGECHECKFLAG,0)) as merged,"
            + " convert(varchar(5), isnull(IsWfControlled,0)) as wf, convert(varchar(10), isnull(iVerifyStateNew,0)) as vs"
            + " from QMCHECKVOUCHER where ID>? and CVOUCHTYPE=? and isnull(BMERGECHECKFLAG,0)=1 and CMAKER=? order by ID";

        sealed class Readback
        {
            public bool Read;
            public List<Dictionary<string, object>> Found = new List<Dictionary<string, object>>();
            public List<Dictionary<string, object>> Details = new List<Dictionary<string, object>>();
            public int Items;
            public decimal Used;
            // 按单号、按来源各回读到几张（消息与审计里写明）。
            public int ByCode;
            public int BySource;

            public string Counts()
            {
                return "（按单号回读到 " + ByCode.ToString(CultureInfo.InvariantCulture) + " 张，按来源回读到 "
                    + BySource.ToString(CultureInfo.InvariantCulture) + " 张）";
            }
        }

        public static ApiResult Confirm(QmChkJob job, QmOutcome outcome, string code, int before, decimal used)
        {
            Readback back = ReadBack(job, code, before);
            string title = job.Spec.KindOf().Title;
            if (!back.Read)
            {
                throw new BridgeException(504, "outcome_unknown", "已调用 U8 新增" + title + "（合并检验，单号 " + code + "），回读失败，结果未知");
            }
            bool mine = back.Found.Count == 1 && CoRows.Col(back.Found[0], "maker") == job.Ctx.OperatorName.Trim()
                && CoRows.Col(back.Found[0], "merged") == "1";
            if (mine && outcome.Ok)
            {
                Complete(job, back, code, used);
                return Result(job, back);
            }
            if (back.Found.Count > 0)
            {
                throw new BridgeException(504, "outcome_unknown", "U8 可能已保存" + title + "（合并检验，单号 " + code + "）"
                    + back.Counts() + "，结果未知：" + QmGen.Text(outcome));
            }
            if (back.Used != used)
            {
                throw new BridgeException(504, "outcome_unknown", "U8 没有生成" + title + "，但报检单累计检验数量已变化，需要人工核对");
            }
            if (outcome.Error.Length > 0)
            {
                throw new BridgeException(409, "u8_rejected", outcome.Error);
            }
            string text = outcome.Lost == null ? "U8 返回成功但回读不到新" + title : "U8 新增" + title + "调用异常：" + QmGen.Text(outcome);
            throw new BridgeException(504, "outcome_unknown", text + back.Counts() + "，结果未知");
        }

        static Readback ReadBack(QmChkJob job, string code, int before)
        {
            WorkContext ctx = job.Ctx;
            Readback back = new Readback();
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                back.Found = QmSql.Many(conn, FoundSql, 3, before, job.Spec.VouchType, code);
                back.ByCode = back.Found.Count;
                if (back.Found.Count == 0)
                {
                    back.Found = BySources(conn, job, before);
                    back.BySource = back.Found.Count;
                    CoRows.Note(ctx.Item, "QmMergeSaved " + code + back.Counts());
                }
                if (back.Found.Count == 1)
                {
                    int id = CoRows.AsId(CoRows.Col(back.Found[0], "ID"));
                    back.Details = QmSql.Many(conn, DetailSql, QmMergeReq.Max + 1, id);
                    back.Items = (int)QmSql.Dec(QmSql.Scalar(conn, ItemsSql, id));
                }
                back.Used = QmMergeSrc.Used(conn, job.Merge);
                back.Read = true;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "QmMergeSaved " + ex.Message);
            }
            finally
            {
                AdoXml.Close(conn);
            }
            return back;
        }

        // 按来源找：max(ID) 之后本操作员的合并检验单里，合并明细的来源行集合恰好等于请求的那几条。
        static List<Dictionary<string, object>> BySources(object conn, QmChkJob job, int before)
        {
            List<Dictionary<string, object>> hits = new List<Dictionary<string, object>>();
            List<Dictionary<string, object>> rows = QmSql.Many(conn, ByMakerSql, 20, before, job.Spec.VouchType,
                job.Ctx.OperatorName.Trim());
            for (int i = 0; i < rows.Count; i++)
            {
                int id = CoRows.AsId(CoRows.Col(rows[i], "ID"));
                if (SameSources(job.Merge, QmSql.Many(conn, DetailSql, QmMergeReq.Max + 1, id)))
                {
                    hits.Add(rows[i]);
                }
            }
            return hits;
        }

        internal static bool SameSources(QmMergeSet set, List<Dictionary<string, object>> details)
        {
            HashSet<int> want = new HashSet<int>();
            for (int i = 0; i < set.Sources.Count; i++)
            {
                want.Add(set.Sources[i].Ask.SourceLineId);
            }
            HashSet<int> got = new HashSet<int>();
            for (int i = 0; i < details.Count; i++)
            {
                got.Add(CoRows.AsId(CoRows.Col(details[i], "src")));
            }
            return details.Count == want.Count && got.SetEquals(want);
        }

        // 已保存：合并明细必须恰好是请求的来源行、数量一致，来源累计检验数量之和增加了表头数量。
        static void Complete(QmChkJob job, Readback back, string code, decimal used)
        {
            string head = "U8 已保存" + job.Spec.KindOf().Title + "（合并检验）" + code + "，但";
            string wrong = Mismatch(job.Merge, back.Details);
            if (wrong.Length > 0)
            {
                throw new BridgeException(504, "outcome_unknown", head + wrong + "，需在 U8 中核对");
            }
            if (back.Used != used + job.LineAsk.Qty)
            {
                throw new BridgeException(504, "outcome_unknown", head + "来源报检单行的累计检验数量没有按合并明细回写，需在 U8 中核对");
            }
        }

        internal static string Mismatch(QmMergeSet set, List<Dictionary<string, object>> details)
        {
            Dictionary<int, decimal> want = new Dictionary<int, decimal>();
            for (int i = 0; i < set.Sources.Count; i++)
            {
                want[set.Sources[i].Ask.SourceLineId] = set.Sources[i].Ask.Qty;
            }
            if (details.Count != want.Count)
            {
                return "合并明细回读到 " + details.Count.ToString(CultureInfo.InvariantCulture) + " 行，请求是 "
                    + want.Count.ToString(CultureInfo.InvariantCulture) + " 行";
            }
            for (int i = 0; i < details.Count; i++)
            {
                int src = CoRows.AsId(CoRows.Col(details[i], "src"));
                decimal qty;
                if (!want.TryGetValue(src, out qty) || QmSql.Dec(CoRows.Col(details[i], "qty")) != qty)
                {
                    return "合并明细的来源行 " + src.ToString(CultureInfo.InvariantCulture) + " 与请求不符";
                }
                want.Remove(src);
            }
            return "";
        }

        static ApiResult Result(QmChkJob job, Readback back)
        {
            Dictionary<string, object> row = back.Found[0];
            Dictionary<string, object> body = QmSaved.Body(job.Spec, CoRows.AsId(CoRows.Col(row, "ID")), row, back.Details.Count);
            body["items"] = back.Items;
            body["source_type"] = job.Spec.SourceKind();
            body["source_id"] = job.Merge.Lead.InspectId;
            body["source_ids"] = Ids(job.Merge);
            body["wf"] = QmSaved.Wf(row);
            return ApiResult.Ok(body);
        }

        // 请求的来源报检单行（按请求顺序）。
        internal static List<object> Ids(QmMergeSet set)
        {
            List<object> ids = new List<object>();
            for (int i = 0; i < set.Sources.Count; i++)
            {
                ids.Add(set.Sources[i].Ask.SourceLineId);
            }
            return ids;
        }
    }
}
