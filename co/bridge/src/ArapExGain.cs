using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 一次汇兑损益（或取消）的结果：汇率、每个处理号一项（处理号、往来单位、单据类型、单号、行数、差额）、差额合计；
    // 新增时跳过的表头登记发票（SkippedDocs），按日期取消时跳过的 U8 处理号个数（SkippedBatches）。
    internal sealed class ExGainResult
    {
        public decimal Rate;
        public int Year;
        public int Period;
        public int Rows;
        public decimal Total;
        public List<Dictionary<string, object>> Batches = new List<Dictionary<string, object>>();
        public List<string> Nos = new List<string>();
        public List<Dictionary<string, object>> SkippedDocs = new List<Dictionary<string, object>>();
        public int SkippedBatches;
    }

    // 应收 / 应付汇兑损益（arap/exchange_gain，U8 处理方式 9M，处理号 SYRAR… / SYPAP…）。只对测试账套开放（第二级）。
    // U8 没有可无界面调用的组件（没有注册的 ProgID）：桥按 U8「汇兑损益」界面执行的 SQL 写（sql/arap/exgain_*.sql，实测核对），
    // 在请求连接的一个事务里：闸门（会计期间、启用日期、结账、币种、调整汇率）→ 第一段脚本（算差额、编号、建处理行、
    // 回写应收应付单 / 收付款单 / 采购发票的本币余额）→ 销售发票经 U8 的 clsWrite2Bill.UpdateBillForAR 回写 → 第二段脚本写往来明细。
    // 只调本币余额，原币余额不动；一个（往来单位、单据）一个处理号，同 U8。提交后在新连接上确认处理行都在。
    // 登记日期就是登录日期 date（必须在应收 / 应付未结账的期间里）。汇率缺省取该期 exch 的调整汇率（iType=3）。
    internal static class ArapExGain
    {
        internal const int ScriptSeconds = 90;
        const int AccounterMax = 20;
        // 一次最多的处理号个数（脚本结果集读取上限 1000 行，SqlScript）。
        internal const int MaxBatches = 1000;

        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "缺少 U8 登录");
            }
            ExGainAsk ask = ArapExGainReq.Parse(ctx.Item.Path, ctx.Item.Body);
            // 入队后再查一次（汇兑损益是第二级，登录前已查过）。
            ArapExGainReq.TestGate(ctx.Item);
            PermContext p = PermCheck.Of(ctx);
            PermCheck.RequireRule(p, PermRegistry.ForKey(PermRegistry.ExGainKey(ask.Flag, false)));
            Partners(p, ask);
            ExGainResult result = Tran(ctx, ask);
            Reread(ctx, ask, result);
            return ApiResult.Ok(Body(ctx, ask, result));
        }

        // 客户 / 供应商受数据权限控制时：partners 必填，且每个都要有权限（汇兑损益按往来单位整批处理）。
        static void Partners(PermContext p, ExGainAsk ask)
        {
            string obj = ask.Flag == "AP" ? PermObj.Vendor : PermObj.Customer;
            if (!p.Controls(obj))
            {
                return;
            }
            if (ask.Partners.Count == 0)
            {
                throw new BridgeException(403, "no_permission", "往来单位受数据权限控制，请用 partners 指定要处理的往来单位");
            }
            foreach (string code in ask.Partners)
            {
                if (!p.Allow(obj, code))
                {
                    throw new BridgeException(403, "no_permission", "没有往来单位 " + code + " 的数据权限");
                }
            }
        }

        static ExGainResult Tran(WorkContext ctx, ExGainAsk ask)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                ExGainResult result = null;
                ArapWriteoffCheck.Guard(conn, delegate { result = Write(ctx, ask); });
                ArapExGainSql.Drop(conn);
                if (DryRun.Active)
                {
                    DryRun.Set(ArapExGainReq.Action, Body(ctx, ask, result));
                }
                Finish(ctx);
                open = false;
                CoRows.Note(ctx.Item, "已处理汇兑损益 " + result.Nos.Count.ToString(CultureInfo.InvariantCulture) + " 个处理号");
                return result;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
        }

        // 提交前核对事务层数（脚本或 U8 组件自行提交、回滚过就说不清写没写进去，504），然后提交（预演由提交钩子回滚）。
        // 取消汇兑损益（ArapExGainCancel）也用。
        internal static void Finish(WorkContext ctx)
        {
            object conn = ctx.Conn;
            ctx.Item.TranAfter = CoTrans.Count(conn);
            int before;
            int after;
            if (!int.TryParse(ctx.Item.TranBefore, out before) || !int.TryParse(ctx.Item.TranAfter, out after)
                || !IaRun.TranIntact(before, after))
            {
                CoRows.Note(ctx.Item, "汇兑损益事务层数 " + ctx.Item.TranBefore + " → " + ctx.Item.TranAfter);
                throw new BridgeException(504, "outcome_unknown", "汇兑损益处理中事务被提前结束，结果未知；请先在 U8 里核对汇兑损益");
            }
            CoTrans.CommitSeen(conn);
        }

        static ExGainResult Write(WorkContext ctx, ExGainAsk ask)
        {
            object conn = ctx.Conn;
            ExGainResult result = new ExGainResult();
            ExGainArgs args = Args(ctx, ask, result);
            ArapExGainSql.Prepare(conn, args, ask.Partners, new List<string>());
            ScriptResult first = Script(ctx, ArapExGainSql.CreateScript, "com_ar");
            Skipped(ctx, result, first);
            if (Count(first, "batches") > MaxBatches)
            {
                throw ArapWriteoffGate.State("本次汇兑损益超过 " + MaxBatches.ToString(CultureInfo.InvariantCulture)
                    + " 个处理号，请用 partners 分批处理");
            }
            if (Count(first, "com_ar") > 0)
            {
                ArapExGainBill.Sale(ctx);
            }
            ScriptResult second = Script(ctx, ArapExGainSql.PersistScript, "inserted");
            result.Rows = Count(second, "inserted");
            if (result.Rows != Count(first, "rows") || result.Rows == 0)
            {
                throw new BridgeException(409, "u8_rejected", "汇兑损益写入往来明细的行数不符，已回滚");
            }
            Shape(result, second);
            return result;
        }

        // 表头登记（没有发票行）的发票整张跳过：清单进响应 skipped_docs，个数进审计备注。
        static void Skipped(WorkContext ctx, ExGainResult result, ScriptResult first)
        {
            foreach (Dictionary<string, object> row in first.Rows)
            {
                Dictionary<string, object> one = new Dictionary<string, object>();
                one["partner"] = CoRows.Col(row, "partner");
                one["type"] = CoRows.Col(row, "vtype");
                one["id"] = CoRows.Col(row, "vid");
                result.SkippedDocs.Add(one);
            }
            int count = Count(first, "skipped");
            if (count > 0)
            {
                CoRows.Note(ctx.Item, "跳过表头登记的发票 " + count.ToString(CultureInfo.InvariantCulture) + " 张");
            }
        }

        // 会计期间（UA_Period）、应收 / 应付启用日期、结账、币种、汇率；都在脚本之前查，不符 409。
        static ExGainArgs Args(WorkContext ctx, ExGainAsk ask, ExGainResult result)
        {
            object conn = ctx.Conn;
            int[] found = WriteoffSql.PeriodOf(conn, ctx.Item.Acc, ask.Date);
            if (found == null)
            {
                throw ArapWriteoffGate.State("登记日期 " + ask.Date + " 不在 U8 的会计期间里");
            }
            DateTime start;
            if (WriteoffSql.StartDate(conn, ask.Flag, out start) && string.CompareOrdinal(ask.Date, Day(start)) < 0)
            {
                throw ArapWriteoffGate.State("登记日期早于" + Side(ask.Flag) + "系统启用日期 " + Day(start));
            }
            if (WriteoffSql.Closed(conn, ask.Flag, found[0], found[1]))
            {
                throw ArapWriteoffGate.State(Side(ask.Flag) + "该期间已结账，不能处理汇兑损益");
            }
            ArapExGainSql.CheckCurrency(conn, ask.Currency);
            decimal adjust = ask.Rate > 0 ? 0m : ArapExGainSql.AdjustRate(conn, ask.Currency, found[0], found[1]);
            result.Rate = ArapExGainSql.PickRate(ask.Rate, adjust, ask.Currency);
            result.Year = found[0];
            result.Period = found[1];
            ExGainArgs args = new ExGainArgs();
            args.Flag = ask.Flag;
            args.Year = found[0];
            args.Period = found[1];
            args.RegDate = ask.Date;
            args.Accounter = Accounter(ctx);
            args.Currency = ask.Currency;
            args.RateText = ArapExGainSql.RateText(result.Rate);
            args.Settle = ask.SettleCleared;
            return args;
        }

        static string Accounter(WorkContext ctx)
        {
            string name = (ctx.OperatorName ?? "").Trim();
            if (name.Length == 0)
            {
                throw new BridgeException(500, "internal", "读不到登录操作员的姓名");
            }
            if (name.Length > AccounterMax)
            {
                throw ArapWriteoffGate.State("操作员姓名超过 20 个字符，U8 不能记录处理人；请换一个操作员");
            }
            return name;
        }

        // 执行一段脚本：拒绝换成中文（英文原文进审计备注），超时换成本功能的说明。取消汇兑损益也用。
        internal static ScriptResult Script(WorkContext ctx, string name, string key)
        {
            try
            {
                return SqlScript.RunPrepared(ctx.Conn, new string[] { name }, ScriptSeconds, key);
            }
            catch (ScriptRefusal ex)
            {
                CoRows.Note(ctx.Item, "汇兑损益脚本 " + ex.Number.ToString(CultureInfo.InvariantCulture) + " " + ex.Message);
                throw ArapExGainSql.Refused(ex);
            }
            catch (BridgeException ex)
            {
                if (ex.Code == "ia_timeout")
                {
                    throw new BridgeException(503, "ia_timeout", "汇兑损益处理超时，已回滚；可稍后重试，或用 partners 分批处理");
                }
                throw;
            }
        }

        internal static int Count(ScriptResult result, string key)
        {
            string text;
            int n;
            if (result == null || !result.Counts.TryGetValue(key, out text)
                || !int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out n))
            {
                return 0;
            }
            return n;
        }

        // 脚本给出的处理号清单 → 响应的 batches；差额合计取计数里的 total。取消汇兑损益也用。
        internal static void Shape(ExGainResult result, ScriptResult script)
        {
            foreach (Dictionary<string, object> row in script.Rows)
            {
                result.Batches.Add(Batch(row));
                result.Nos.Add(CoRows.Col(row, "cancel_no"));
            }
            string total;
            result.Total = script.Counts.TryGetValue("total", out total) ? WriteoffSql.Num(total) : 0m;
        }

        // 一个处理号：type 是 U8 单据类型代码（26 / 27 / R0 / 48、01 / 02 / P0 / 49），id 是单号，
        // diff 是本币差额 = 借方 − 贷方（往来科目方向，借正贷负）；total 是全部处理行的同口径合计。
        internal static Dictionary<string, object> Batch(Dictionary<string, object> row)
        {
            Dictionary<string, object> one = new Dictionary<string, object>();
            one["cancel_no"] = CoRows.Col(row, "cancel_no");
            one["partner"] = CoRows.Col(row, "partner");
            one["type"] = CoRows.Col(row, "vtype");
            one["id"] = CoRows.Col(row, "vid");
            one["lines"] = CoRows.AsId(CoRows.Col(row, "lines"));
            one["diff"] = WriteoffSql.Num(CoRows.Col(row, "diff"));
            return one;
        }

        // 已提交。新连接（不加 NOLOCK）确认这些处理号的行数；读不出来或不符都是 504 outcome_unknown（已提交，先核对，不要重投）。
        static void Reread(WorkContext ctx, ExGainAsk ask, ExGainResult result)
        {
            object conn = null;
            int found;
            try
            {
                conn = ctx.OpenFresh();
                found = ArapExGainSql.CountRows(conn, ask.Flag, result.Nos);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "ArapExGain " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "汇兑损益已提交但未能回读，处理号 " + Range(result));
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (found != result.Rows)
            {
                throw new BridgeException(504, "outcome_unknown", "汇兑损益已提交，但回读时处理号 " + Range(result)
                    + " 的明细行数不符；请先在 U8 里核对，不要直接重试");
            }
        }

        static string Range(ExGainResult result)
        {
            if (result.Nos.Count == 0)
            {
                return "";
            }
            return result.Nos.Count == 1 ? result.Nos[0] : result.Nos[0] + " 至 " + result.Nos[result.Nos.Count - 1];
        }

        static Dictionary<string, object> Body(WorkContext ctx, ExGainAsk ask, ExGainResult result)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["acc"] = ctx.Item.Acc;
            body["flag"] = ask.Flag;
            body["date"] = ask.Date;
            body["fiscal_year"] = result.Year;
            body["period"] = result.Period;
            body["currency"] = ask.Currency;
            body["rate"] = result.Rate;
            body["rows"] = result.Rows;
            body["batches"] = result.Batches;
            body["total"] = result.Total;
            body["skipped_docs"] = result.SkippedDocs;
            return body;
        }

        internal static string Side(string flag)
        {
            return flag == "AP" ? "应付" : "应收";
        }

        static string Day(DateTime day)
        {
            return day.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        }
    }
}
