using System;
using System.Collections.Generic;

namespace U8Co
{
    // 不良品处理单修改之后：U8 已在自己的连接上提交，回读一律在新连接上做。成功 = UFTS 变了、请求的表头字段都读到了、
    // 仍未审核、不良品处理标记（检验单或合并明细的 BREJFLAG，QmRejSrc.Flag）仍为 1、表体与合并后的处理行一致（按不良原因 + 处理方式对行：数量、处理后存货与数量、仓库；
    // 删除的行已不在）、没有出现下游单据、U8 没报错。UFTS 没变：U8 原文 409 u8_rejected，否则 504（QmEditSaved.Unchanged）；
    // UFTS 变了但有任何不符或 U8 报了错：504 outcome_unknown（单据已经被改过）。
    internal static class QmRejEditSaved
    {
        const string HeadSql = "select * from QMREJECTVOUCHER where ID=? and CVOUCHTYPE=?";

        sealed class After
        {
            public Dictionary<string, object> Head;
            public Dictionary<string, object> Extra;
            public Dictionary<string, object> State;
            public List<Dictionary<string, object>> Lines;
            public string Flag = "";
            public string Down = "";
        }

        public static ApiResult Confirm(QmRejEditJob e, QmOutcome outcome)
        {
            string title = e.Spec.Title;
            After after = Read(e);
            if (after == null)
            {
                throw new BridgeException(504, "outcome_unknown", "已调用 U8 修改" + title + "，回读失败，结果未知");
            }
            if (after.Head == null)
            {
                throw new BridgeException(504, "outcome_unknown", "修改" + title + "后回读不到单据，需在 U8 中核对");
            }
            if (CoRows.Col(after.Head, "UFTS") == e.Ufts)
            {
                throw QmEditSaved.Unchanged(outcome);
            }
            List<string> diff = Diff(e, after);
            if (diff.Count > 0)
            {
                throw new BridgeException(504, "outcome_unknown", "U8 已保存" + title + "，但回读与请求不一致（"
                    + string.Join("、", diff.ToArray()) + "），需在 U8 中核对");
            }
            if (!outcome.Ok)
            {
                throw new BridgeException(504, "outcome_unknown", "U8 报错但" + title + "已被修改，需在 U8 中核对：" + QmGen.Text(outcome));
            }
            return ApiResult.Ok(Body(e, after));
        }

        static After Read(QmRejEditJob e)
        {
            WorkContext ctx = e.Job.Ctx;
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                After after = new After();
                after.Head = QmSql.One(conn, HeadSql, e.Id, e.Spec.VouchType);
                if (after.Head == null)
                {
                    return after;
                }
                after.Extra = QmEditLoad.Extra(conn, "QMREJECTVOUCHER", e.Id);
                after.State = QmSql.One(conn, QmRejOps.DocSql, e.Id, e.Spec.VouchType);
                after.Lines = QmSql.Many(conn, QmRejEditLoad.LinesSql, 500, e.Id);
                after.Flag = QmRejSrc.Flag(conn, e.Job.Ask.CheckId, e.MergeId);
                after.Down = QmRejOps.Downstream(conn, e.Id, e.Code);
                return after;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "QmRejEditSaved " + ex.Message);
                return null;
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static List<string> Diff(QmRejEditJob e, After after)
        {
            List<string> diff = new List<string>();
            for (int i = 0; i < e.HeadFields.Count; i++)
            {
                string[] f = e.HeadFields[i];
                string got = CoRows.Col(QmRejEditLoad.IsExtra(f[0]) ? after.Extra : after.Head, f[0]);
                if (!QmEditSaved.Same(f[1], got))
                {
                    diff.Add(f[2]);
                }
            }
            if (QmDoc.Verified(after.State))
            {
                diff.Add("单据变成已审核");
            }
            if (after.Flag != "1")
            {
                diff.Add(e.MergeId > 0 ? "合并明细的不良品处理标记（BREJFLAG）不是 1" : "检验单的不良品处理标记（BREJFLAG）不是 1");
            }
            if (after.Down.Length > 0)
            {
                diff.Add("出现了下游单据");
            }
            if (e.Ask.Ops.Count > 0)
            {
                Lines(e, after.Lines, diff);
            }
            return diff;
        }

        static void Lines(QmRejEditJob e, List<Dictionary<string, object>> got, List<string> diff)
        {
            List<QmRejLine> want = e.Job.Ask.Lines;
            if (got.Count != want.Count)
            {
                diff.Add("表体回读到 " + got.Count + " 行，应为 " + want.Count + " 行");
            }
            for (int i = 0; i < e.Deleted.Count; i++)
            {
                if (Row(got, e.Deleted[i]) != null)
                {
                    diff.Add("表体行 " + e.Deleted[i] + " 没有删除");
                }
            }
            for (int i = 0; i < want.Count; i++)
            {
                QmRejLine line = want[i];
                Dictionary<string, object> row = Row(got, line);
                string label = "处理行 " + line.Reason + "/" + line.Dispose;
                if (row == null)
                {
                    diff.Add(label + " 不在");
                    continue;
                }
                if (!Same(line, row))
                {
                    diff.Add(label);
                }
            }
        }

        // 数量、处理后存货（非降级行应为空、处理后数量为 0）与处理后数量（降级行；没给时 = 处理数量）、仓库。
        static bool Same(QmRejLine line, Dictionary<string, object> row)
        {
            if (QmSql.Dec(CoRows.Col(row, "FQUANTITY")) != line.Qty || !QmEditSaved.Same(line.Wh, CoRows.Col(row, "CBWHCODE")))
            {
                return false;
            }
            if (line.Flow != QmRejSrc.DimFlow)
            {
                return CoRows.Col(row, "CDIMINVCODE").Trim().Length == 0 && QmSql.Dec(CoRows.Col(row, "FDIMQUANTITY")) == 0m;
            }
            decimal dimQty = line.DimQty > 0m ? line.DimQty : line.Qty;
            return QmEditSaved.Same(line.DimInv, CoRows.Col(row, "CDIMINVCODE")) && QmSql.Dec(CoRows.Col(row, "FDIMQUANTITY")) == dimQty;
        }

        static Dictionary<string, object> Row(List<Dictionary<string, object>> rows, int autoId)
        {
            string want = autoId.ToString(System.Globalization.CultureInfo.InvariantCulture);
            for (int i = 0; i < rows.Count; i++)
            {
                if (CoRows.Col(rows[i], "AUTOID") == want)
                {
                    return rows[i];
                }
            }
            return null;
        }

        // 不良原因 + 处理方式在一张处理单里唯一（QmRejEditLines.RequireDistinct）。
        static Dictionary<string, object> Row(List<Dictionary<string, object>> rows, QmRejLine line)
        {
            string key = QmRejEditLines.Key(line);
            for (int i = 0; i < rows.Count; i++)
            {
                if (QmRejEditLines.Key(CoRows.Col(rows[i], "CREASONCODE"), CoRows.Col(rows[i], "CSCRAPDISCODE")) == key)
                {
                    return rows[i];
                }
            }
            return null;
        }

        static Dictionary<string, object> Body(QmRejEditJob e, After after)
        {
            Dictionary<string, object> state = CoRows.StateOf(after.State);
            state["verified"] = QmDoc.Verified(after.State);
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = e.Spec.Kind;
            body["id"] = e.Id;
            body["code"] = CoRows.Col(after.State, "code");
            body["state"] = state;
            body["lines"] = after.Lines.Count;
            body["source_type"] = e.Spec.CheckKind;
            body["source_id"] = e.Job.Ask.CheckId;
            body["wf"] = QmRejOps.Wf(after.State);
            return body;
        }
    }
}
