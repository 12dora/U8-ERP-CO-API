using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 一次修改的上下文：类型、UFAPBO 规格、修改前的表头快照和修改计划。
    internal sealed class ArapEditJob
    {
        public VoucherKind Kind;
        public ArapSpec Spec;
        public ArapDoc Doc;
        public ArapEditPlan Plan;
    }

    // 收款单、付款单（clsCloseBill）和应收单、应付单（clsAPVouch）的修改：GetVouchData 载入 → 改 DOM →
    // SaveVouch(h, b, err, IsAdd=false)，在请求连接的 CoTrans 里保存，提交前核对。只改未审核的手工单据，闸门同删除。
    internal static class ArapEdit
    {
        internal static ApiResult Update(WorkContext ctx, VoucherKind kind, int id, Dictionary<string, object> head, object[] lines)
        {
            ArapSpec spec = ArapReq.Spec(kind);
            ArapEditReq req = ArapEditReq.Parse(kind, head, lines);
            ArapDoc doc = ArapSql.Head(ctx.Conn, kind, spec, id);
            if (doc == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            Refuse(ctx.Conn, kind, spec, doc);
            ArapBo bo = null;
            object[] doms = new object[2];
            try
            {
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                bo = ArapBo.Open(ctx, spec);
                Load(ctx, bo, spec, doc, doms);
                ArapEditJob job = new ArapEditJob();
                job.Kind = kind;
                job.Spec = spec;
                job.Doc = doc;
                job.Plan = Prepare(ctx, job, req, doms);
                Save(ctx, job, bo, doms);
            }
            finally
            {
                ComUtil.Final(doms[1]);
                ComUtil.Final(doms[0]);
                if (bo != null)
                {
                    bo.Dispose();
                }
            }
            return Readback(ctx, kind, spec, id, doc.Code);
        }

        internal static bool MetaAllowed(VoucherKind kind, bool head, string lowerField)
        {
            if (kind == null || kind.Family != "ar")
            {
                return false;
            }
            return ArapEditReq.Allowed(ArapReq.Spec(kind), head, lowerField);
        }

        // 同删除：未走审批流、未审核、未制单、手工录入、非票据/网银、未核销、没有往来明细。
        static void Refuse(object conn, VoucherKind kind, ArapSpec spec, ArapDoc doc)
        {
            PurchaseCo.RefuseFlow(conn, kind, doc.Row);
            if (doc.Col("verifier").Length > 0 || doc.Col("verify_date").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核");
            }
            ArapSql.RefuseEdit(conn, spec, doc);
        }

        static void Load(WorkContext ctx, ArapBo bo, ArapSpec spec, ArapDoc doc, object[] doms)
        {
            string err;
            bool ok = bo.GetData(ArapCond.Load(spec, doc), doms, out err);
            CoRows.Note(ctx.Item, "GetVouchData " + (ok ? "true" : "false") + " " + err);
            if (!ok)
            {
                throw new BridgeException(409, "u8_rejected", ArapCo.Said(err, "U8 没有返回单据"));
            }
        }

        // 算出修改后的行和合计，过期间与档案检查（原日期、新日期所在月份都不能已结账），再写进 DOM。
        static ArapEditPlan Prepare(WorkContext ctx, ArapEditJob job, ArapEditReq req, object[] doms)
        {
            ArapSpec spec = job.Spec;
            List<object> heads = DomRows.RowsOf(doms[0]);
            List<object> body = DomRows.RowsOf(doms[1]);
            try
            {
                if (heads.Count != 1 || DomRows.Get(heads[0], job.Kind.CodeColumn).Trim() != job.Doc.Code)
                {
                    throw new BridgeException(409, "state_mismatch", "U8 返回的单据与请求不一致");
                }
                ArapEditPlan plan = ArapEditBuild.Build(spec, job.Kind, req, heads[0], body, ctx.HomeCurrency);
                ArapArch.RefuseClosed(ctx.Conn, spec, plan.OldDate);
                ArapRefs.Check(ctx.Conn, spec, ArapEditBuild.CheckInput(plan));
                ArapLineFx.ForEdit(ctx.Conn, spec, plan);
                ArapEditDom.Head(ctx, spec, plan, doms[0], heads[0]);
                ArapEditDom.Body(spec, job.Doc, plan, doms[1]);
                return plan;
            }
            finally
            {
                Release(heads);
                Release(body);
            }
        }

        static void Save(WorkContext ctx, ArapEditJob job, ArapBo bo, object[] doms)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                string msg;
                bool ok = bo.Save(doms, false, out msg);
                ctx.Item.TranAfter = CoTrans.Count(conn);
                CoRows.Note(ctx.Item, "SaveVouch edit " + (ok ? "true" : "false") + (msg.Length > 0 ? " " + msg : ""));
                if (!ok)
                {
                    CoTrans.Rollback(conn);
                    open = false;
                    throw new BridgeException(409, "u8_rejected", ArapCo.Said(msg, "U8 拒绝了修改"));
                }
                StockCall.AfterCheck(ctx, delegate(object c) { ArapEditCheck.After(ctx, job, c); }, "单据 " + job.Doc.Code);
                CoTrans.CommitSeen(conn);
                open = false;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
        }

        // 已提交：同新增在新连接上按表头回读（行数经表头外键统计，应收/应付单是 cLink），回读失败一律 504，不能让调用方再投一次。
        static ApiResult Readback(WorkContext ctx, VoucherKind kind, ArapSpec spec, int id, string code)
        {
            try
            {
                ArapDoc doc = ArapSql.Fresh(ctx, kind, spec, id);
                if (doc == null)
                {
                    throw new BridgeException(404, "not_found", "单据不存在");
                }
                return ArapMsg.Created(kind, doc);
            }
            catch (Exception ex)
            {
                BridgeException known = ex as BridgeException;
                if (known != null && known.Status == 504)
                {
                    throw;
                }
                CoRows.Note(ctx.Item, "readback " + ex.Message);
                throw new BridgeException(504, "outcome_unknown",
                    "已修改但未能回读确认，单号 " + code + "，标识 " + id.ToString(CultureInfo.InvariantCulture));
            }
        }

        static void Release(List<object> nodes)
        {
            for (int i = 0; i < nodes.Count; i++)
            {
                ComUtil.ReleaseOne(nodes[i]);
            }
        }
    }

    // 提交前的核对（同一事务、同一连接）：仍未审核、仍过删除闸门（没有往来明细、未核销），
    // 表头金额 = 请求合计，未核销 = 金额，行数与表体合计一致；收付款单跨月改日期时 iPeriod = 新月份。
    // 不符 → 409 u8_rejected，期望与实际记进审计 detail。
    internal static class ArapEditCheck
    {
        public static void After(WorkContext ctx, ArapEditJob job, object conn)
        {
            VoucherKind kind = job.Kind;
            ArapSpec spec = job.Spec;
            ArapDoc doc = job.Doc;
            ArapDoc after = ArapSql.Head(conn, kind, spec, doc.Id);
            if (after == null)
            {
                Bad(ctx, "单据", "存在", "不存在");
            }
            if (after.Col("verifier").Length > 0)
            {
                Bad(ctx, "审核人", "", after.Col("verifier"));
            }
            try
            {
                ArapSql.RefuseEdit(conn, spec, after);
            }
            catch (BridgeException ex)
            {
                Bad(ctx, "闸门", "通过", ex.Message);
            }
            ArapInput input = job.Plan.Input;
            Same(ctx, "表头金额", input.Sum, after.Col("amount"));
            Same(ctx, "表头原币金额", input.SumF, after.Col("amount_f"));
            Same(ctx, "未核销金额", input.Sum, after.Col("ramount"));
            Same(ctx, "未核销原币金额", input.SumF, after.Col("ramount_f"));
            Same(ctx, "行数", input.Lines.Count, after.Col("lines"));
            Same(ctx, "表体金额合计", input.Sum, BodySum(conn, kind, spec, doc));
            if (spec.Close && ArapEditBuild.CrossMonth(job.Plan))
            {
                Same(ctx, "期间", ArapEditBuild.NewMonth(job.Plan), Period(conn, kind, doc));
            }
        }

        static string Period(object conn, VoucherKind kind, ArapDoc doc)
        {
            string sql = "select convert(varchar(10), iPeriod) from " + CoRows.Ident(kind.HeadTable)
                + " where " + CoRows.Ident(kind.IdColumn) + "=?";
            return Rows.Scalar(conn, sql, new object[] { doc.Id }) ?? "";
        }

        static string BodySum(object conn, VoucherKind kind, ArapSpec spec, ArapDoc doc)
        {
            string col = spec.Close ? "iAmt" : "iAmount";
            string sql = "select convert(varchar(40), sum(b." + col + ")) from " + CoRows.Ident(kind.BodyTable)
                + " b where b." + CoRows.Ident(kind.BodyFk) + "=?";
            object key = spec.Close ? (object)doc.Id : doc.Link;
            return Rows.Scalar(conn, sql, new object[] { key }) ?? "";
        }

        static void Same(WorkContext ctx, string label, decimal want, string actual)
        {
            decimal got;
            bool ok = decimal.TryParse((actual ?? "").Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out got);
            if (!ok || got != want)
            {
                Bad(ctx, label, want.ToString(CultureInfo.InvariantCulture), actual ?? "");
            }
        }

        static void Bad(WorkContext ctx, string label, string want, string actual)
        {
            CoRows.Note(ctx.Item, "修改核对 " + label + " 期望 [" + want + "] 实际 [" + actual + "]");
            throw new BridgeException(409, "u8_rejected", "U8 保存后的单据与请求不一致：" + label);
        }
    }
}
