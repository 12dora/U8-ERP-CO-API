using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 检验单生单（QM03 参照来料报检单、QM04 参照产品报检单）：UFQMCo VoucherOperate("add")。实测 QM03 的 add 自己提交
    // （调用后 @@TRANCOUNT 为 0），不包 CoTrans：调用前查完能查的，调用后在新连接上确认。U8 保留送来的 CCHECKCODE
    // （占位值也会存进去），单号必须由桥按 U8 编号规则取（BillNo，按 U8 单据编号规则生成，形如 QMIN202601010001）。
    // 保存后 U8 回写报检单行 FSUMCHECKQTY / BFLAG，新单 IsWfControlled 按审批流程、iVerifyStateNew=0，之后走 workflow/submit。
    internal static class QmGenCheck
    {
        const string ExistsSql = "select count(*) from QMCHECKVOUCHER where ID=? and CVOUCHTYPE=? and INSPECTAUTOID=?";

        public static ApiResult Run(WorkContext ctx, QmSpec spec, int inspectId, Dictionary<string, object> head,
            object[] lines)
        {
            QmAsk ask = QmReq.Parse(spec, head, lines);
            PermContext perm = QmGen.RequireRule(ctx, spec, "create");
            QmChkJob job = QmChkSrc.Load(ctx, spec, inspectId, ask);
            QmGen.CheckRows(perm, spec, "create", QmChkSrc.PermRows(job));
            object[] doms = null;
            QmCo co = null;
            try
            {
                co = QmCo.Open(ctx, spec);
                doms = QmDom.BlankPair(ctx.Conn, spec);
                QmChkDom.FillHead(job, doms[0]);
                QmItems.Fill(job, doms[1]);
                QmTplReq.Require(ctx.Conn, spec, doms, "CCHECKCODE");
                // 预演（校验模式）：add 自己提交，取号也不退，闸门和模板核对完就停，detail 带上要送给 U8 的表头、表体。
                DryInput(job, doms);
                DryRun.Stop(ctx, "UFQMCo.VoucherOperate(add)");
                QmNo.Allocate(ctx, spec.VouchType, doms[0], "CCHECKCODE");
                int id = Add(job, co, doms);
                return QmSaved.Check(ctx, spec, id, inspectId, job.LineAsk.SourceLineId);
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

        static void DryInput(QmChkJob job, object[] doms)
        {
            if (!DryRun.Active)
            {
                return;
            }
            Dictionary<string, object> input = new Dictionary<string, object>();
            input["source_id"] = job.InspectId;
            input["source_line_id"] = job.LineAsk.SourceLineId;
            input["quantity"] = job.LineAsk.Qty;
            List<Dictionary<string, object>> heads = Rows.FromDom(doms[0], 1);
            if (heads != null && heads.Count > 0)
            {
                input["head"] = heads[0];
            }
            input["lines"] = Rows.FromDom(doms[1], 200);
            DryRun.Set("input", input);
        }

        static int Add(QmChkJob job, QmCo co, object[] doms)
        {
            WorkContext ctx = job.Ctx;
            int line = job.LineAsk.SourceLineId;
            int before = QmSql.MaxId(ctx.Conn, "QMCHECKVOUCHER");
            decimal used = QmSql.Dec(QmSql.Scalar(ctx.Conn, QmSql.InsUsedSql, line));
            QmOutcome outcome = co.Operate(doms, "add", "");
            if (!outcome.Ok)
            {
                CoRows.Note(ctx.Item, "VoucherOperate " + QmGen.Text(outcome));
            }
            if (QmGen.TranLeft(ctx))
            {
                throw new BridgeException(504, "outcome_unknown",
                    "U8 新增" + job.Spec.KindOf().Title + "后请求连接上仍有未结束的事务，已回滚，结果未知");
            }
            int domId = outcome.Ok ? QmDom.HeadId(doms[0]) : 0;
            return Confirm(job, outcome, domId, before, used);
        }

        // 新连接上确认：找到本次的新单（DOM 回写的 ID，或调用前 max(ID) 之后挂在该报检单行上的唯一一张）且 U8 没报错才算成功。
        // 没找到时：报检单行累计检验数量变了 504（需人工核对）；U8 原文 409；其他异常或「成功却没有单据」504。
        static int Confirm(QmChkJob job, QmOutcome outcome, int domId, int before, decimal used)
        {
            WorkContext ctx = job.Ctx;
            int found = 0;
            decimal now = used;
            bool read = false;
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                found = domId > 0 && Exists(conn, job, domId) ? domId
                    : QmSaved.FindCheck(conn, job.Spec, before, job.LineAsk.SourceLineId, ctx.OperatorName);
                now = QmSql.Dec(QmSql.Scalar(conn, QmSql.InsUsedSql, job.LineAsk.SourceLineId));
                read = true;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "QmGenCheck " + ex.Message);
            }
            finally
            {
                AdoXml.Close(conn);
            }
            return Judge(job, outcome, read, found, now != used);
        }

        static int Judge(QmChkJob job, QmOutcome outcome, bool read, int found, bool moved)
        {
            string title = job.Spec.KindOf().Title;
            if (!read)
            {
                throw new BridgeException(504, "outcome_unknown", "已调用 U8 新增" + title + "，回读失败，结果未知");
            }
            if (found > 0 && outcome.Ok)
            {
                return found;
            }
            if (found != 0)
            {
                string id = found > 0 ? "，标识 " + found.ToString(CultureInfo.InvariantCulture) : "";
                throw new BridgeException(504, "outcome_unknown", "U8 可能已保存" + title + id + "，结果未知："
                    + QmGen.Text(outcome));
            }
            if (moved)
            {
                throw new BridgeException(504, "outcome_unknown", "U8 没有生成" + title + "，但报检单累计检验数量已变化，需要人工核对");
            }
            if (outcome.Error.Length > 0)
            {
                throw new BridgeException(409, "u8_rejected", outcome.Error);
            }
            string text = outcome.Lost == null ? "U8 返回成功但回读不到新" + title : "U8 新增" + title + "调用异常：" + QmGen.Text(outcome);
            throw new BridgeException(504, "outcome_unknown", text + "，结果未知");
        }

        static bool Exists(object conn, QmChkJob job, int id)
        {
            return QmSql.Dec(QmSql.Scalar(conn, ExistsSql, id, job.Spec.VouchType, job.LineAsk.SourceLineId)) > 0m;
        }
    }
}
