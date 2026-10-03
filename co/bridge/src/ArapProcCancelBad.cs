using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 取消坏账处理（arap/process/cancel 的 HZAR 处理号）：计提 9F、发生 9G、收回 9H 共用 HZ 编号，按库里的记录区分
    // （往来明细有 9G / 9H 行，或坏账准备参数 Ar_BadPara.cCancelNo 是它）。第二级写入，只对测试账套开放（登录前、入队后各查一次）。
    // U8 没有可调用的取消组件：桥按「取消操作」取消 9F / 9G / 9H 时执行的 SQL 写（sql/arap/bad_cancel.sql，实测核对），
    // 在请求连接的一个事务里：带锁识别 → 数据权限 → 脚本（闸门、坏账准备余额、收款单还原 / 应收单余额、发票回写临时表）→
    // 9G 再经 U8 的 clsWrite2Bill.UpdateBillForAR 减回销售发票累计并核对 → 删 9G 行；9H 再经 U8 的收款单弃审组件
    // （ArapCo.SignIn，同 vouchers/verify 的弃审）删掉收回时审核组件写的审核行和审核人，核对收款单已复原（未审核、余额还原、
    // 收款单上没有往来明细）；提交后在新连接上确认已取消。收款单没有审核行的 9H（U8 客户端或早期版本做的）脚本直接拒绝。
    // 9F 只能取消各年度当前挂在参数行上的处理号（U8 只保留最近一次；同年再计提会累加 iJtAmount，取消时整笔减回）。
    internal static class ArapProcCancelBad
    {
        public const string TestOnly = "坏账处理（计提、发生、收回）只对配置为测试账套的账套开放";
        const int ScriptSeconds = 90;

        sealed class BadCancel
        {
            public string Style;
            public string CancelNo;
            public ScriptResult Result;
            public ArapBo Bo;
        }

        public static ApiResult Run(WorkContext ctx, ProcCancelAsk ask)
        {
            TestAccountGate.Require(ctx.Item, TestOnly);
            PermContext p = PermCheck.Of(ctx);
            PermRule rule = PermRegistry.ForKey(PermRegistry.ProcCancelKey("AR"));
            PermCheck.RequireRule(p, rule);
            BadCancel done = new BadCancel();
            done.CancelNo = ask.CancelNo;
            try
            {
                // 坏账收回要用收款单弃审组件：开事务之前打开（同 vouchers/verify），处理方式在事务里带锁再认一次。
                if (ArapProcCancelBadSql.IsRecover(ctx.Conn, ask.CancelNo))
                {
                    done.Bo = ArapBo.Open(ctx, ArapReq.Spec(ArapBadRecover.Receipt()));
                }
                Tran(ctx, done, p, rule);
            }
            finally
            {
                ArapCo.Close(done.Bo);
            }
            Reread(ctx, done);
            return ApiResult.Ok(Body(ctx, done));
        }

        static void Tran(WorkContext ctx, BadCancel done, PermContext p, PermRule rule)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                ArapWriteoffCheck.Guard(conn, delegate { Write(ctx, done, p, rule); });
                ArapProcCancelBadSql.Drop(conn);
                if (DryRun.Active)
                {
                    Dry(done, Body(ctx, done));
                }
                Commit(ctx);
                open = false;
                CoRows.Note(ctx.Item, "已取消" + ArapProcVoucherReq.Title(done.Style) + " " + done.CancelNo);
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
        }

        static void Write(WorkContext ctx, BadCancel done, PermContext p, PermRule rule)
        {
            object conn = ctx.Conn;
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            done.Style = ArapProcCancelBadSql.Detect(conn, done.CancelNo, rows);
            foreach (Dictionary<string, object> row in rows)
            {
                if (!PermCheck.RowAllowed(p, rule, row))
                {
                    throw new BridgeException(403, "no_permission", PermCheck.DeniedData);
                }
            }
            // U8 在应收系统的取消操作前调 AR_ExistUnAuditCloseBill（同取消核销、取消应收冲应付）。
            if (UnwriteoffSql.UnauditedCloseBill(conn))
            {
                throw ArapProcCancelGate.State(ArapProcCancelGate.Unaudited);
            }
            ArapProcCancelBadSql.Prepare(conn, ctx.Item.Acc, done.Style, done.CancelNo);
            done.Result = Script(ctx, done.CancelNo);
            if (done.Style == "9H")
            {
                Unsign(ctx, done);
            }
            else if (done.Style == "9G")
            {
                DropOccur(ctx, done);
            }
        }

        // 9G：脚本之后经 U8 的 clsWrite2Bill.UpdateBillForAR 减回销售发票累计并核对，再删 9G 行（同 U8 的顺序）。
        static void DropOccur(WorkContext ctx, BadCancel done)
        {
            object conn = ctx.Conn;
            if (ArapExGain.Count(done.Result, "com") > 0)
            {
                ArapUnwriteoffBill.Write2(ctx, "处理号 " + done.CancelNo);
                string line = ArapProcCancelBadSql.BillMismatch(conn);
                if (line != null)
                {
                    throw new BridgeException(409, "u8_rejected", "取消坏账发生后销售发票行 " + line + " 的累计核销不符，已回滚");
                }
            }
            int left = ArapProcCancelBadSql.Delete(conn, "9G", done.CancelNo);
            if (left != 0 || ArapExGain.Count(done.Result, "rows") == 0)
            {
                throw new BridgeException(409, "u8_rejected", "取消坏账发生后处理行没有删干净，已回滚");
            }
        }

        // 9H：脚本已删处理行、还原收款单余额和坏账准备余额（只接受有审核行的处理）；再用 U8 的弃审组件撤销收款单审核，
        // 核对收款单未审核、余额已还原、收款单上没有往来明细（审核行与 9H 行都已删除）。不符 409 u8_rejected（整体回滚）。
        static void Unsign(WorkContext ctx, BadCancel done)
        {
            if (done.Bo == null || ArapExGain.Count(done.Result, "signed") <= 0)
            {
                throw ArapProcCancelGate.State("处理号 " + done.CancelNo + " 在取消过程中发生变化，请重试");
            }
            ArapCo.SignIn(ctx, done.Bo, ArapBadRecover.Receipt(), ArapProcCancelBadSql.ReceiptId(done.Result), true);
            string problem = ArapProcCancelBadSql.ReceiptProblem(ctx.Conn, done.Result);
            if (problem != null)
            {
                throw new BridgeException(409, "u8_rejected", "取消坏账收回后核对不符，已回滚：" + problem);
            }
        }

        static ScriptResult Script(WorkContext ctx, string cancelNo)
        {
            try
            {
                return SqlScript.RunPrepared(ctx.Conn, new string[] { ArapProcCancelBadSql.Script }, ScriptSeconds, "rows");
            }
            catch (ScriptRefusal ex)
            {
                CoRows.Note(ctx.Item, "坏账取消脚本 " + ex.Number.ToString(CultureInfo.InvariantCulture) + " " + ex.Message);
                throw ArapProcCancelBadSql.Refused(ex, cancelNo);
            }
            catch (BridgeException ex)
            {
                if (ex.Code == "ia_timeout")
                {
                    throw new BridgeException(503, "ia_timeout", "取消坏账处理超时，已回滚；可稍后重试");
                }
                throw;
            }
        }

        // 提交前核对事务层数（脚本或 U8 组件自行提交、回滚过就说不清写没写进去，504），然后提交（预演由提交钩子回滚）。
        static void Commit(WorkContext ctx)
        {
            object conn = ctx.Conn;
            ctx.Item.TranAfter = CoTrans.Count(conn);
            int before;
            int after;
            if (!int.TryParse(ctx.Item.TranBefore, out before) || !int.TryParse(ctx.Item.TranAfter, out after)
                || !IaRun.TranIntact(before, after))
            {
                CoRows.Note(ctx.Item, "取消坏账处理事务层数 " + ctx.Item.TranBefore + " → " + ctx.Item.TranAfter);
                throw new BridgeException(504, "outcome_unknown", "取消坏账处理中事务被提前结束，结果未知；请先在 U8 里核对");
            }
            CoTrans.CommitSeen(conn);
        }

        // 已提交。新连接（不加 NOLOCK）确认处理号已取消；读不出来或还在都是 504 outcome_unknown（已提交，先核对，不要重投）。
        static void Reread(WorkContext ctx, BadCancel done)
        {
            object conn = null;
            int left;
            try
            {
                conn = ctx.OpenFresh();
                left = ArapProcCancelBadSql.Count(conn, done.Style, done.CancelNo);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "ArapProcCancelBad " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "已提交但未能回读，处理号 " + done.CancelNo);
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (left != 0)
            {
                throw new BridgeException(504, "outcome_unknown", "取消" + ArapProcVoucherReq.Title(done.Style) + "已提交，但回读时处理号 "
                    + done.CancelNo + " 仍在；请先在 U8 里核对，不要直接重试");
            }
        }

        // 预演（rollback）：登记涉及的销售发票 / 收款单，响应体交给 detail，提交钩子随后回滚。
        static void Dry(BadCancel done, Dictionary<string, object> body)
        {
            foreach (Dictionary<string, object> row in done.Result.Rows)
            {
                string name = ArapProcCancelGate.DocName("AR", CoRows.Col(row, "vtype"));
                VoucherKind kind = name == null ? null : Kinds.Find(name);
                int id = CoRows.AsId(CoRows.Col(row, "doc_id"));
                if (kind != null && id > 0)
                {
                    DryRun.Touched(kind, id);
                }
            }
            Dictionary<string, object> copy = new Dictionary<string, object>(body);
            copy.Remove("ok");
            DryRun.Set(ArapProcCancelReq.Action, copy);
        }

        // 响应：同 arap/process/cancel 的字段（kind 为 bad_debt，restored 为空），另有 amount（本币）、remain_before / remain_after
        // （坏账准备余额）、fiscal_year。
        static Dictionary<string, object> Body(WorkContext ctx, BadCancel done)
        {
            ScriptResult r = done.Result;
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["acc"] = ctx == null ? null : ctx.Item.Acc;
            body["flag"] = "AR";
            body["cancel_no"] = done.CancelNo;
            body["style"] = done.Style;
            body["kind"] = "bad_debt";
            body["ar_rows"] = ArapExGain.Count(r, "rows");
            body["ap_rows"] = 0;
            body["items"] = Items(r);
            body["restored"] = new List<object>();
            body["amount"] = Num(r, "amount");
            body["remain_before"] = Num(r, "remain_before");
            body["remain_after"] = Num(r, "remain_after");
            body["fiscal_year"] = ArapExGain.Count(r, "year");
            return body;
        }

        // items：涉及的单据（9G 每张发票行 / 应收单一项，9H 收款单一项，9F 参数行一项），debit / credit 是处理行的原币借贷。
        internal static List<object> Items(ScriptResult r)
        {
            List<object> items = new List<object>();
            foreach (Dictionary<string, object> row in r.Rows)
            {
                string vtype = CoRows.Col(row, "vtype");
                string name = ArapProcCancelGate.DocName("AR", vtype);
                decimal amount = WriteoffSql.Num(CoRows.Col(row, "amount_f"));
                int line = CoRows.AsId(CoRows.Col(row, "line_id"));
                int id = CoRows.AsId(CoRows.Col(row, "doc_id"));
                string partner = CoRows.Col(row, "partner");
                Dictionary<string, object> one = new Dictionary<string, object>();
                one["ledger"] = "AR";
                one["type"] = name ?? vtype;
                one["id"] = id > 0 ? (object)id : null;
                one["code"] = CoRows.Col(row, "vid");
                one["line_id"] = line > 0 ? (object)line : null;
                one["partner"] = partner.Length > 0 ? partner : null;
                one["debit"] = vtype == "48" ? amount : 0m;
                one["credit"] = vtype == "48" || vtype == "9F" ? 0m : amount;
                items.Add(one);
            }
            return items;
        }

        static object Num(ScriptResult r, string key)
        {
            string text;
            if (r == null || !r.Counts.TryGetValue(key, out text) || text.Trim().Length == 0)
            {
                return null;
            }
            return WriteoffSql.Num(text);
        }
    }
}
