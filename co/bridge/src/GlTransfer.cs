using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 期间损益结转 gl/transfer/pnl、自定义转账 gl/transfer/custom（第二级写入，只对测试账套开放）。
    // U8 的自动转账在 VB6 客户端里，没有可无界面调用的组件：桥按 GL_bautotran 的定义和已记账余额算出分录（GlTransferPnl、
    // GlTransferCustom），保存走与 gl/vouchers/create 相同的凭证导入 U8PzInsert.Transact（它自己提交），保存后照 U8 生成的结转凭证
    // 补标记（GlTransferSave：coutsign、coutno_id、idoc=-1、coutsysname 为空），在新连接上回读核对。
    // 闸门（核对模式 exclude_existing 不查，它只能预演）：期间未结账、本期凭证全部记账（取数只取已记账）、本期没有做过同类结转
    // （期间损益只差一张时补生成，见 GlTransferPnlDone）。数据权限同总账查询（GlTransferPerm），在算出任何金额之前查。
    // 预演（validate）在算完、校验完之后停在凭证导入之前，detail.transfer 是要生成的全部凭证和分录。
    internal static class GlTransfer
    {
        const string UnpostedSql = "SELECT TOP 1 csign + N'-' + CONVERT(varchar(12), ino_id) k FROM GL_accvouch"
            + " WHERE iyear=? AND iperiod=? AND ISNULL(CONVERT(int, ibook),0)=0 AND ISNULL(iflag,0)<>1 ORDER BY csign, ino_id";
        // 一张结转凭证的分录上限；超过分录上限请在 U8 客户端结转。
        internal const int MaxLines = 500;

        public static ApiResult Run(WorkContext ctx)
        {
            string path = ctx.Item.Path;
            TestAccountGate.Require(ctx.Item, GlTransferReq.TestOnlyOf(path));
            GlTransferAsk ask = GlTransferReq.Parse(path, ctx.Item.Body, ctx.Item.DryRun, GlState.LoginYear(ctx));
            GlState.Permit(ctx, "create");
            // 核对模式不查结账等闸门，只许预演（登录前已按 dry_run 查过，这里按实际的预演状态再查一次）。
            if (ask.Exclude && !DryRun.Active)
            {
                throw GlReq.Bad("exclude_existing 只能和 dry_run 一起用（只核对，不生成）", "exclude_existing");
            }
            GlTransferPlan plan = Prepare(ctx, ask, PermCheck.Of(ctx));
            Preview(ctx, plan);
            GlTransferSave.All(ctx, plan);
            return GlTransferSave.Readback(ctx, plan);
        }

        static GlTransferPlan Prepare(WorkContext ctx, GlTransferAsk ask, PermContext perm)
        {
            object conn = ctx.Conn;
            GlTransferPlan plan = new GlTransferPlan();
            plan.Ask = ask;
            plan.Perm = perm;
            plan.Date = ask.Date.Length > 0 ? ask.Date : GlTransferReq.LastDay(ask.Year, ask.Period);
            CoRows.Note(ctx.Item, ask.Title());
            if (!ask.Exclude)
            {
                GlState.PeriodOpen(conn, ask.Year, ask.Period);
                string open = Rows.Scalar(conn, UnpostedSql, new object[] { ask.Year, ask.Period });
                if (open != null)
                {
                    throw GlState.Refuse("本期还有未记账的凭证（" + open.Trim() + " 等），请先记账：结转只取已记账的余额");
                }
            }
            GlTransferCodes codes = GlTransferCodes.Load(conn, ask.Year);
            if (ask.Pnl)
            {
                GlTransferPnl.Build(conn, plan, codes);
            }
            else
            {
                GlTransferCustom.Build(conn, plan, codes);
            }
            GlTransferPerm.Lines(perm, plan, codes);
            if (plan.Vouchers.Count == 0)
            {
                throw GlState.Refuse(plan.Skipped.Count > 0 ? "没有生成凭证：" + plan.SkipText() : "没有需要结转的金额");
            }
            plan.Maker = GlState.Operator(ctx);
            foreach (GlTransferVoucher v in plan.Vouchers)
            {
                Check(conn, plan, v);
            }
            return plan;
        }

        // 每张凭证：类别存在、借贷平衡、行数不超限，科目和辅助项照 create 的规则查（400 改为 409：定义或余额的问题，不是请求的），
        // 制单序时。
        static void Check(object conn, GlTransferPlan plan, GlTransferVoucher v)
        {
            GlTransferAsk ask = plan.Ask;
            v.Key = new GlKey();
            v.Key.Year = ask.Year;
            v.Key.Period = ask.Period;
            v.Key.Sign = v.Sign;
            string name = Name(v);
            v.Seq = GlState.SignSeq(conn, v.Sign);
            decimal[] sums = Sums(v.Draft);
            if (sums[0] != sums[1])
            {
                throw GlState.Refuse(name + "借贷不平（借 " + Money(sums[0]) + "，贷 " + Money(sums[1]) + "），请检查 U8 的转账定义");
            }
            if (v.Draft.Lines.Count > MaxLines)
            {
                throw GlState.Refuse(name + "有 " + v.Draft.Lines.Count.ToString(CultureInfo.InvariantCulture) + " 行分录，超过 "
                    + MaxLines.ToString(CultureInfo.InvariantCulture) + " 行，请在 U8 客户端结转");
            }
            try
            {
                GlCheck.Validate(conn, ask.Year, v.Draft);
            }
            catch (BridgeException ex)
            {
                if (ex.Status != 400)
                {
                    throw;
                }
                throw GlState.Refuse(name + "的分录不合格：" + ex.Message);
            }
            GlCheck.Order(conn, v.Key, plan.Date, false);
        }

        internal static decimal[] Sums(GlDraft draft)
        {
            decimal debit = 0;
            decimal credit = 0;
            foreach (GlLine line in draft.Lines)
            {
                debit += line.Debit;
                credit += line.Credit;
            }
            return new decimal[] { debit, credit };
        }

        internal static string Name(GlTransferVoucher v)
        {
            if (v.TranId.Length > 0)
            {
                return "自定义转账 " + v.TranId + " ";
            }
            return "期间损益结转（" + (v.Pack == GlTransferPnl.Income ? "收入" : "支出") + "）";
        }

        internal static string Money(decimal value)
        {
            return value.ToString("0.00", CultureInfo.InvariantCulture);
        }

        // 预演：要生成的凭证和分录、跳过的定义、核对模式去掉的凭证，停在凭证导入之前。
        static void Preview(WorkContext ctx, GlTransferPlan plan)
        {
            if (!DryRun.Active)
            {
                return;
            }
            Dictionary<string, object> detail = Head(plan);
            List<object> list = new List<object>();
            foreach (GlTransferVoucher v in plan.Vouchers)
            {
                Dictionary<string, object> one = Brief(v);
                one["date"] = plan.Date;
                one["maker"] = plan.Maker;
                one["lines"] = GlDryRun.Lines(v.Draft);
                one["lines_total"] = v.Draft.Lines.Count;
                list.Add(one);
            }
            detail["vouchers"] = list;
            if (plan.Ask.Exclude)
            {
                detail["excluded"] = plan.Excluded;
            }
            DryRun.Set("transfer", detail);
            DryRun.Stop(ctx, "U8PzInsert.Transact");
        }

        internal static Dictionary<string, object> Head(GlTransferPlan plan)
        {
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["kind"] = plan.Ask.Pnl ? "pnl" : "custom";
            head["fiscal_year"] = plan.Ask.Year;
            head["period"] = plan.Ask.Period;
            head["voucher_date"] = plan.Date;
            head["out_sign"] = plan.Ask.OutSign;
            head["skipped"] = plan.Skipped;
            if (plan.Existing.Count > 0)
            {
                head["existing"] = plan.Existing;
            }
            return head;
        }

        // 一张凭证的概要：类别、摘要、转账序号或收入 / 支出、借贷合计。
        internal static Dictionary<string, object> Brief(GlTransferVoucher v)
        {
            Dictionary<string, object> one = new Dictionary<string, object>();
            one["sign"] = v.Sign;
            one["digest"] = v.Digest;
            if (v.TranId.Length > 0)
            {
                one["tran_id"] = v.TranId;
            }
            if (v.Pack.Length > 0)
            {
                one["pack"] = v.Pack;
            }
            decimal[] sums = Sums(v.Draft);
            one["debit"] = sums[0];
            one["credit"] = sums[1];
            return one;
        }
    }
}
