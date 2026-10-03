using System;
using System.Collections.Generic;

namespace U8Co
{
    // 应收坏账收回（arap/bad_debt action=recover，U8 处理方式 9H，处理号 HZAR…）。只对测试账套开放（分派 ArapBad 已查）。
    // U8「坏账收回」界面选一张未审核的收款单（48）后写库（实测）：在收款单上挂一条 9H 借方处理行
    // （cVouchType = cCoVouchType = 48，不另生成应收单），用 SQL 填审核人，收款单行余额清零，坏账准备余额加本币金额。
    // U8 的写法没有收款单自己的审核行（贷方），客户应收余额会多出收回金额；桥改为先用 U8 的审核组件审核收款单（ArapCo.SignIn，
    // 同 vouchers/verify），9H 借方（再确认应收，科目同收款单行的应收科目）与审核行贷方（收款冲应收）相抵，客户应收余额不变、
    // 坏账准备余额加回。制单时 9H 凭证同时回写收款单的审核行和表头凭证号（ArapProcVoucherBad），U8 不会再把收款单列入制单。
    // 在请求连接的一个事务里：公共闸门 → 收款单闸门（ArapBadRecoverGate，已有往来明细在审核前拒绝）→ 数据权限 →
    // 审核组件 → 脚本（sql/arap/bad_recover.sql）→ 核对（处理行、审核人、收款单行余额、坏账准备余额、本收款单的往来明细借贷相抵）→ 提交；
    // 提交后在新连接上确认处理行。审核日期是登录日期（组件按登录日期写，不能指定）。不制单（走 arap/process/voucher）。
    internal static class ArapBadRecover
    {
        internal const string Script = "sql/arap/bad_recover.sql";
        const string Title = "坏账收回";

        // 本收款单上的往来明细合计（借 − 贷，iFlag<3，本币、原币）：审核行和本次 9H 行。闸门已保证收款单上没有别的明细。
        const string NetSql = "select {n} as n, {f} as f from Ar_Detail d where d.cFlag=N'AR' and d.cVouchType=N'48' and d.cVouchID=?"
            + " and d.iFlag<3";

        const string CheckSql = "select isnull(h.cCheckMan,N'') as auditor, {rem} as rem, {remf} as rem_f"
            + " from Ap_CloseBill h inner join Ap_CloseBills b on b.iID=h.iID where h.iID=? and b.ID=?";

        // 脚本拒绝编号 → 中文 409（公共编号 50102–50104 在 ArapBad.Refused；50101 是防御性的，按内部错误）。
        internal static readonly Dictionary<int, string> Texts = BuildTexts();

        static Dictionary<int, string> BuildTexts()
        {
            Dictionary<int, string> map = new Dictionary<int, string>();
            map[50121] = "收款单审核后状态不符（被核销、改动或有本单审核行以外的往来明细），已回滚{0}";
            map[50122] = "收款单回写不符，已回滚{0}";
            return map;
        }

        public static ApiResult Run(WorkContext ctx, ArapBadAsk ask)
        {
            // 审核组件在开事务之前打开（同 vouchers/verify），审核在下面的事务里做。
            ArapBo bo = null;
            BadRecoverPlan plan;
            try
            {
                bo = ArapBo.Open(ctx, ArapReq.Spec(Receipt()));
                plan = Tran(ctx, ask, bo);
            }
            finally
            {
                ArapCo.Close(bo);
            }
            Reread(ctx, plan);
            return ApiResult.Ok(Body(ctx, ask, plan, false));
        }

        internal static VoucherKind Receipt()
        {
            VoucherKind kind = Kinds.Find("ar_receipt");
            if (kind == null)
            {
                throw new BridgeException(500, "internal", "缺少收款单单据类型");
            }
            return kind;
        }

        static BadRecoverPlan Tran(WorkContext ctx, ArapBadAsk ask, ArapBo bo)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                BadRecoverPlan plan = ArapBadRecoverGate.Plan(ctx, ask);
                PermRule rule = PermRegistry.ForKey(PermRegistry.BadDebtKey(ArapBadReq.Recover));
                if (!PermCheck.RowAllowed(PermCheck.Of(ctx), rule, plan.Head))
                {
                    throw new BridgeException(403, "no_permission", PermCheck.DeniedData);
                }
                ArapWriteoffCheck.Guard(conn, delegate { Write(ctx, ask, plan, bo); });
                ArapBadOccurSql.Drop(conn);
                if (DryRun.Active)
                {
                    Dry(plan, Body(ctx, ask, plan, true));
                }
                ArapBad.Finish(ctx, Title);
                open = false;
                CoRows.Note(ctx.Item, Title + " 处理号 " + plan.CancelNo + " 收款单 " + ask.Receipt);
                return plan;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
        }

        static void Write(WorkContext ctx, ArapBadAsk ask, BadRecoverPlan plan, ArapBo bo)
        {
            object conn = ctx.Conn;
            ArapCo.SignIn(ctx, bo, Receipt(), plan.Id, false);
            Prepare(conn, ask, plan);
            ScriptResult script = ArapBad.Script(ctx, Script, "cancel_no", Title, Texts);
            plan.CancelNo = ArapBadOccur.Count(script, "cancel_no");
            if (!ArapBadRule.NoValid(plan.CancelNo))
            {
                throw new BridgeException(409, "u8_rejected", "坏账收回没有得到有效的处理号（" + plan.CancelNo + "），已回滚");
            }
            plan.RemainAfter = WriteoffSql.Num(ArapBadOccur.Count(script, "remain_after"));
            string problem = Mismatch(conn, plan);
            if (problem == null)
            {
                problem = NetProblem(ask.Receipt, Net(conn, ask.Receipt));
            }
            if (problem != null)
            {
                throw new BridgeException(409, "u8_rejected", "坏账收回后核对不符，已回滚：" + problem);
            }
        }

        static void Prepare(object conn, ArapBadAsk ask, BadRecoverPlan plan)
        {
            UnwriteoffSql.Run(conn, ArapBadOccurSql.PrepSql);
            BadOpen o = plan.Open;
            GlSql.Exec(conn, ArapBadOccurSql.ArgsFill, new object[]
            {
                o.Year, o.Period, o.Date, o.Accounter, plan.Digest, ask.Customer, "", "", o.ParaId, plan.Id, ask.Receipt,
                ArapBadRule.Money(plan.AmountF), ArapBadRule.Money(plan.AmountN)
            });
        }

        // 收款单上往来明细的 [本币, 原币] 借 − 贷合计（事务里，含未提交的行）。
        internal static decimal[] Net(object conn, string receipt)
        {
            string sql = NetSql.Replace("{n}", WriteoffSql.Dec("sum(isnull(d.iDAmount,0)-isnull(d.iCAmount,0))", 2))
                .Replace("{f}", WriteoffSql.Dec("sum(isnull(d.iDAmount_f,0)-isnull(d.iCAmount_f,0))", 2));
            Dictionary<string, object> row = Rows.One(conn, sql, new object[] { receipt });
            return new decimal[] { WriteoffSql.Num(CoRows.Col(row, "n")), WriteoffSql.Num(CoRows.Col(row, "f")) };
        }

        // 收回后收款单上的往来明细应借贷相抵（本币、原币都为 0），客户应收余额因此不变；不为 0 返回原因（带合计），否则 null。
        // 纯函数，--selftest 用。
        internal static string NetProblem(string receipt, decimal[] net)
        {
            if (net[0] == 0m && net[1] == 0m)
            {
                return null;
            }
            return "收款单 " + receipt + " 的往来明细借贷合计为 " + ArapBadRule.Money(net[0]) + "（原币 " + ArapBadRule.Money(net[1])
                + "），应为 0";
        }

        // 提交前：一条 9H 处理行、收款单审核人是本操作员、收款单行余额为 0、坏账准备余额加了本币金额。不符返回原因，符合返回 null。
        static string Mismatch(object conn, BadRecoverPlan plan)
        {
            int rows = ArapBad.CountRows(conn, ArapBadRule.RecoverStyle, plan.CancelNo);
            if (rows != 1)
            {
                return "处理行 " + rows.ToString(System.Globalization.CultureInfo.InvariantCulture) + " 条，应为 1 条";
            }
            string sql = CheckSql.Replace("{rem}", WriteoffSql.Dec("b.iRAmt", 2)).Replace("{remf}", WriteoffSql.Dec("b.iRAmt_f", 2));
            Dictionary<string, object> now = Rows.One(conn, sql, new object[] { plan.Id, plan.Line });
            if (now == null || CoRows.Col(now, "auditor") != plan.Open.Accounter)
            {
                return "收款单没有被本操作员审核";
            }
            if (WriteoffSql.Num(CoRows.Col(now, "rem")) != 0m || WriteoffSql.Num(CoRows.Col(now, "rem_f")) != 0m)
            {
                return "收款单行的余额没有清零";
            }
            return ArapBadOccur.RemainProblem(ArapBad.ParaRemain(conn, plan.Open.ParaId), plan.Open.RemainBefore + plan.AmountN);
        }

        // 已提交。新连接（不加 NOLOCK）确认该处理号的 9H 行；读不出来或不符都是 504 outcome_unknown（先核对，不要重投）。
        static void Reread(WorkContext ctx, BadRecoverPlan plan)
        {
            object conn = null;
            int found;
            try
            {
                conn = ctx.OpenFresh();
                found = ArapBad.CountRows(conn, ArapBadRule.RecoverStyle, plan.CancelNo);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "ArapBadRecover " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "坏账收回已提交但未能回读，处理号 " + plan.CancelNo);
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (found != 1)
            {
                throw new BridgeException(504, "outcome_unknown", "坏账收回已提交，但回读时处理号 " + plan.CancelNo
                    + " 的往来明细行数不符；请先在 U8 里核对，不要直接重试");
            }
        }

        // 响应：amount 是原币（= 收款单金额），amount_native 是本币（坏账准备余额按本币加）。
        internal static Dictionary<string, object> Body(WorkContext ctx, ArapBadAsk ask, BadRecoverPlan plan, bool dry)
        {
            Dictionary<string, object> body = ArapBad.Body(ctx, ask, plan.Open, ArapBadRule.RecoverStyle, dry);
            body["cancel_no"] = plan.CancelNo;
            body["receipt"] = ask.Receipt;
            body["receipt_id"] = plan.Id;
            body["currency"] = plan.Currency;
            body["rate"] = plan.Rate;
            body["digest"] = plan.Digest;
            body["amount"] = plan.AmountF;
            body["amount_native"] = plan.AmountN;
            body["remain_before"] = plan.Open.RemainBefore;
            body["remain_after"] = plan.RemainAfter;
            return body;
        }

        // 预演（rollback）：写和核对都在事务里跑过之后，把处理号、金额和收款单交给 detail，提交钩子随后回滚。
        static void Dry(BadRecoverPlan plan, Dictionary<string, object> body)
        {
            VoucherKind kind = Kinds.Find("ar_receipt");
            if (kind != null && plan.Id > 0)
            {
                DryRun.Touched(kind, plan.Id);
            }
            Dictionary<string, object> copy = new Dictionary<string, object>(body);
            copy.Remove("ok");
            DryRun.Set(ArapBad.DryKey, copy);
        }
    }
}
