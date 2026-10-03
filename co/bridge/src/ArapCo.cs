using System;
using System.Collections.Generic;

namespace U8Co
{
    internal delegate bool ArapStep(out string message);

    // 收款单 ar_receipt(48)、付款单 ap_payment(49)、应收单 ar_bill(R0)、应付单 ap_bill(P0)，走 UFAPBO。
    // 读取、新增、审核、弃审、删除；核销、制单、票据不做。
    internal static class ArapCo
    {
        public static ApiResult Load(WorkContext ctx, VoucherKind kind, int id)
        {
            ArapSpec spec = ArapReq.Spec(kind);
            ArapDoc doc = Need(ctx.Conn, kind, spec, id);
            ArapBo bo = null;
            object[] doms = new object[2];
            try
            {
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                bo = ArapBo.Open(ctx, spec);
                string err;
                bool ok = bo.GetData(ArapCond.Load(spec, doc), doms, out err);
                CoRows.Note(ctx.Item, "GetVouchData " + (ok ? "true" : "false") + " " + err);
                if (!ok)
                {
                    throw new BridgeException(409, "u8_rejected", Said(err, "U8 没有返回单据"));
                }
                return Loaded(kind, doc, doms);
            }
            finally
            {
                ComUtil.Final(doms[1]);
                ComUtil.Final(doms[0]);
                Close(bo);
            }
        }

        // 期初单据（bStartFlag=1）只走 openings/arap：那里有测试账套限制、期初权限和第 0 期形态。
        static void RefuseOpening(ArapDoc doc)
        {
            if (doc.Flag("start_flag"))
            {
                throw new BridgeException(409, "state_mismatch", "期初单据请用 openings/arap 审核或弃审");
            }
        }

        public static ApiResult Verify(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            ArapSpec spec = ArapReq.Spec(kind);
            if (action != "verify" && action != "unverify")
            {
                throw new BridgeException(400, "bad_request", "action 只能是 verify 或 unverify");
            }
            ArapDoc doc = Need(ctx.Conn, kind, spec, id);
            RefuseOpening(doc);
            ArapSql.RefuseVerify(spec, doc);
            PurchaseCo.RefuseFlow(ctx.Conn, kind, doc.Row);
            bool undo = action == "unverify";
            bool verified = doc.Col("verifier").Length > 0;
            if (!undo && verified)
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核");
            }
            if (undo && !verified)
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            if (undo)
            {
                ArapSql.RefuseUndo(ctx.Conn, spec, doc);
            }
            string cond = undo ? ArapCond.Unsign(spec, doc) : ArapCond.Sign(spec, doc);
            Write(ctx, spec, undo ? "CancelSign" : "Sign", cond);
            return AfterCommit(ctx, "已提交但未能回读审核状态", delegate
            {
                return Verified(ctx, kind, spec, id, action);
            });
        }

        public static ApiResult Create(WorkContext ctx, VoucherKind kind,
            Dictionary<string, object> head, object[] lines)
        {
            ArapSpec spec = ArapReq.Spec(kind);
            ArapInput input = ArapReq.CheckCreate(kind, head, lines, ctx.HomeCurrency);
            if (input.Date.Length == 0)
            {
                input.Date = ctx.Item == null || ctx.Item.Date == null ? "" : ctx.Item.Date.Trim();
            }
            if (input.Date.Length != 10)
            {
                throw new BridgeException(400, "bad_request", "缺少单据日期");
            }
            ArapRefs.Check(ctx.Conn, spec, input);
            ArapLineFx.ForCreate(ctx.Conn, spec, input);
            ArapBo bo = null;
            object[] doms = new object[2];
            try
            {
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                bo = ArapBo.Open(ctx, spec);
                Template(ctx, bo, spec, doms);
                ArapDom.FillHead(doms[0], spec, input, ctx.Session.OperatorName);
                ArapDom.FillBody(doms[1], spec, input);
                ArapBo saver = bo;
                InTrans(ctx, "SaveVouch", delegate(out string m)
                {
                    bool saved = saver.Save(doms, out m);
                    if (saved)
                    {
                        MarkNew(ctx, kind, spec, doms[0]);
                    }
                    return saved;
                });
                return Created(ctx, kind, spec, doms[0]);
            }
            finally
            {
                ComUtil.Final(doms[1]);
                ComUtil.Final(doms[0]);
                Close(bo);
            }
        }

        public static ApiResult Delete(WorkContext ctx, VoucherKind kind, int id)
        {
            ArapSpec spec = ArapReq.Spec(kind);
            ArapDoc doc = Need(ctx.Conn, kind, spec, id);
            PurchaseCo.RefuseFlow(ctx.Conn, kind, doc.Row);
            if (doc.Col("verifier").Length > 0 || doc.Col("verify_date").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核");
            }
            ArapSql.RefuseDelete(ctx.Conn, spec, doc);
            Write(ctx, spec, "DeleteVouch", ArapCond.Delete(spec, doc));
            return AfterCommit(ctx, "已删除但未能回读确认", delegate
            {
                return Gone(ctx, kind, spec, doc);
            });
        }

        static ArapDoc Need(object conn, VoucherKind kind, ArapSpec spec, int id)
        {
            ArapDoc doc = ArapSql.Head(conn, kind, spec, id);
            if (doc == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            return doc;
        }

        internal static void Template(WorkContext ctx, ArapBo bo, ArapSpec spec, object[] doms)
        {
            string err;
            bool ok = bo.GetData(ArapCond.Template(spec), doms, out err);
            if (!ok)
            {
                CoRows.Note(ctx.Item, "template " + err);
                throw new BridgeException(409, "u8_rejected", Said(err, "U8 没有返回单据模板"));
            }
        }

        internal static void Write(WorkContext ctx, ArapSpec spec, string method, string cond)
        {
            ArapBo bo = null;
            try
            {
                bo = ArapBo.Open(ctx, spec);
                ArapBo runner = bo;
                InTrans(ctx, method, delegate(out string m) { return runner.Run(method, cond, out m); });
            }
            finally
            {
                Close(bo);
            }
        }

        // 请求连接上的 UFAPBO 写都包在 CoTrans 里（SaveVouch 已在测试账套上实测可回滚）。false → 回滚，U8 原文 409。
        internal static void InTrans(WorkContext ctx, string label, ArapStep step)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                string msg;
                bool ok = step(out msg);
                ctx.Item.TranAfter = CoTrans.Count(conn);
                CoRows.Note(ctx.Item, label + " " + (ok ? "true" : "false") + (msg.Length > 0 ? " " + msg : ""));
                if (!ok)
                {
                    CoTrans.Rollback(conn);
                    open = false;
                    throw new BridgeException(409, "u8_rejected", Said(msg, "U8 拒绝了 " + label));
                }
                CoTrans.CommitSeen(conn);
                open = false;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
        }

        // 坏账收回及其取消（ArapBadRecover、ArapProcCancelBad）用：在调用方已开的事务里对一张单据走 UFAPBO 的 Sign / CancelSign，
        // 条件同 vouchers/verify（ArapCond），弃审先过同一套弃审闸门（ArapSql.RefuseUndo）。bo 由调用方在开事务之前打开；
        // 这里不开事务、不提交。U8 返回 false → 409 u8_rejected；调用前后 @@TRANCOUNT 变了（组件自行提交或回滚）→ 504。
        internal static void SignIn(WorkContext ctx, ArapBo bo, VoucherKind kind, int id, bool undo)
        {
            ArapSpec spec = ArapReq.Spec(kind);
            ArapDoc doc = Need(ctx.Conn, kind, spec, id);
            if (undo)
            {
                ArapSql.RefuseUndo(ctx.Conn, spec, doc);
            }
            string method = undo ? "CancelSign" : "Sign";
            string cond = undo ? ArapCond.Unsign(spec, doc) : ArapCond.Sign(spec, doc);
            string before = CoTrans.Count(ctx.Conn);
            string msg;
            bool ok = bo.Run(method, cond, out msg);
            string after = CoTrans.Count(ctx.Conn);
            CoRows.Note(ctx.Item, method + " " + (ok ? "true" : "false") + " @@TRANCOUNT " + before + " → " + after
                + (msg.Length > 0 ? " " + msg : ""));
            if (after != before)
            {
                throw new BridgeException(504, "outcome_unknown", "U8 " + (undo ? "弃审" : "审核") + "组件改变了事务，结果未知；请先在 U8 里核对单据 "
                    + doc.Code);
            }
            if (!ok)
            {
                throw new BridgeException(409, "u8_rejected", Said(msg, "U8 拒绝了 " + method));
            }
        }

        // 提交之后的回读：409（状态不符）照抛，其余失败一律 504。
        internal static ApiResult AfterCommit(WorkContext ctx, string message, Func<ApiResult> read)
        {
            try
            {
                return read();
            }
            catch (Exception ex)
            {
                BridgeException known = ex as BridgeException;
                if (known != null && (known.Status == 409 || known.Status == 504))
                {
                    throw;
                }
                CoRows.Note(ctx.Item, "readback " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", message);
            }
        }

        internal static void Close(ArapBo bo)
        {
            if (bo != null)
            {
                bo.Dispose();
            }
        }

        internal static string Said(string text, string fallback)
        {
            string said = text == null ? "" : text.Trim();
            return said.Length > 0 ? said : fallback;
        }

        static ApiResult Loaded(VoucherKind kind, ArapDoc doc, object[] doms)
        {
            List<Dictionary<string, object>> heads = Rows.FromDom(doms[0], 2);
            if (heads == null || heads.Count == 0)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (heads.Count != 1 || CoRows.Col(heads[0], kind.CodeColumn) != doc.Code)
            {
                throw new BridgeException(409, "state_mismatch", "U8 返回的单据与请求不一致");
            }
            Dictionary<string, object> body = ArapMsg.Base(kind, doc.Id);
            body["code"] = doc.Code;
            body["head"] = heads[0];
            body["lines"] = Rows.FromDom(doms[1], 501);
            body["state"] = ArapMsg.State(doc);
            return ApiResult.Ok(body);
        }

        internal static ApiResult Verified(WorkContext ctx, VoucherKind kind, ArapSpec spec, int id, string action)
        {
            ArapDoc after = ArapSql.Fresh(ctx, kind, spec, id);
            if (after == null)
            {
                throw new BridgeException(409, "state_mismatch", "审核状态与操作不一致");
            }
            string verifier = after.Col("verifier");
            if (action == "unverify")
            {
                if (verifier.Length > 0 || after.Col("verify_date").Length > 0)
                {
                    throw new BridgeException(409, "state_mismatch", "弃审后审核人仍在");
                }
            }
            else
            {
                ArapMsg.RequireVerifier(after, ctx.Session.OperatorName);
            }
            Dictionary<string, object> body = ArapMsg.Base(kind, id);
            body["action"] = action;
            body["state"] = ArapMsg.State(after);
            body["verified_by"] = verifier;
            body["verified_at"] = verifier.Length > 0 ? ArapMsg.When(after) : "";
            return ApiResult.Ok(body);
        }

        internal static ApiResult Gone(WorkContext ctx, VoucherKind kind, ArapSpec spec, ArapDoc doc)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                if (ArapSql.Left(conn, kind, spec, doc))
                {
                    throw new BridgeException(409, "state_mismatch", "U8 返回成功但单据仍在");
                }
            }
            finally
            {
                AdoXml.Close(conn);
            }
            Dictionary<string, object> body = ArapMsg.Base(kind, doc.Id);
            body["deleted"] = true;
            return ApiResult.Ok(body);
        }

        // SaveVouch 把新 iID / Auto_ID 与 cVouchID 写回表头 DOM；先按主键、再按单号在新连接上回读。
        internal static ApiResult Created(WorkContext ctx, VoucherKind kind, ArapSpec spec, object headDom)
        {
            int id = 0;
            string code = "";
            try
            {
                List<Dictionary<string, object>> rows = Rows.FromDom(headDom, 1);
                if (rows != null && rows.Count > 0)
                {
                    id = CoRows.AsId(CoRows.Col(rows[0], kind.IdColumn));
                    code = CoRows.Col(rows[0], kind.CodeColumn);
                }
                ArapDoc doc = FindNew(ctx, kind, spec, id, code);
                if (doc == null)
                {
                    throw new BridgeException(504, "outcome_unknown", ArapMsg.Unknown(id, code));
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
                // 单据已经提交；回读失败时结果未知，不能让调用方再投一次。
                CoRows.Note(ctx.Item, "created " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", ArapMsg.Unknown(id, code));
            }
        }

        // 预演：SaveVouch 成功后、提交前登记新单。主键取表头 DOM，缺了按单号在本连接（事务内）找。
        internal static void MarkNew(WorkContext ctx, VoucherKind kind, ArapSpec spec, object headDom)
        {
            if (!DryRun.Active)
            {
                return;
            }
            int id = 0;
            try
            {
                List<Dictionary<string, object>> rows = Rows.FromDom(headDom, 1);
                string code = "";
                if (rows != null && rows.Count > 0)
                {
                    id = CoRows.AsId(CoRows.Col(rows[0], kind.IdColumn));
                    code = CoRows.Col(rows[0], kind.CodeColumn);
                }
                if (id <= 0 && code.Length > 0)
                {
                    id = ArapSql.IdByCode(ctx.Conn, kind, spec, code);
                }
            }
            catch (Exception ex)
            {
                // 这里没有提交；失败只影响预演回读哪几张单，不影响写入本身。
                CoRows.Note(ctx.Item, "dry_run id " + ex.Message);
            }
            DocMark.Created(ctx.Conn, kind, id, "");
        }

        static ArapDoc FindNew(WorkContext ctx, VoucherKind kind, ArapSpec spec, int id, string code)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                ArapDoc doc = id > 0 ? ArapSql.Head(conn, kind, spec, id) : null;
                if (doc != null || code.Length == 0)
                {
                    return doc;
                }
                int found = ArapSql.IdByCode(conn, kind, spec, code);
                return found > 0 ? ArapSql.Head(conn, kind, spec, found) : null;
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }
    }
}
