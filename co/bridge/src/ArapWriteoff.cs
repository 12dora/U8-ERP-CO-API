using System;
using System.Collections.Generic;
using System.Globalization;
using System.Runtime.InteropServices;

namespace U8Co
{
    // 应收 / 应付核销（arap/writeoff）：收款单（付款单）的一行对销售发票、应收单（采购发票、应付单）核销。
    // U8ApCancel.cLsCancel：Init(login, "AR"|"AP", 请求连接) 引用 {0,2}；Save(xml) 引用 {0}，返回 true 为成功。
    // 不设任何属性（dDate、cRPFlag 等由 Save 从报文读）。测试账套实测：Save 在请求连接的事务里（@@TRANCOUNT 1，回滚能撤销），
    // 写 Ar_Detail / Ap_Detail 的 9P 行（新核销号 HXAR… / HXAP…）、扣 Ap_CloseBills.iRAmt*、回写发票累计核销；照常包 CoTrans。
    // cCancelNo 不是可读属性（DISP_E_UNKNOWNNAME），核销号从新写的明细行取。取消核销是 arap/writeoff/cancel（ArapUnwriteoff）。
    internal static class ArapWriteoff
    {
        const string ProgId = "U8ApCancel.cLsCancel";

        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "缺少 U8 登录");
            }
            WriteoffAsk ask = ArapWriteoffReq.Parse(ctx.Item.Body);
            PermRule rule = PermRegistry.ForKey(PermRegistry.WriteoffKey(ask.Flag));
            PermCheck.RequireRule(PermCheck.Of(ctx), rule);
            DateTime day = Day(ctx.Item.Date);
            object hx = null;
            WriteoffPlan plan;
            try
            {
                hx = Open(ctx, ask.Flag);
                plan = Tran(ctx, hx, ask, day, rule);
            }
            finally
            {
                ComUtil.Final(hx);
            }
            return ArapWriteoffCheck.Reread(ctx, plan, ctx.Item.Date);
        }

        // 建核销组件并 Init（手工核销与自动核销 ArapAutoWriteoff 共用）；Init 失败时释放组件再抛出，成功时由调用方 ComUtil.Final。
        internal static object Open(WorkContext ctx, string flag)
        {
            object hx = ComUtil.Create(ProgId);
            if (hx == null)
            {
                throw new BridgeException(503, "com_unavailable", "组件无法创建 " + ProgId);
            }
            try
            {
                Init(ctx, hx, flag);
                return hx;
            }
            catch (Exception)
            {
                ComUtil.Final(hx);
                throw;
            }
        }

        internal static DateTime Day(string date)
        {
            DateTime day;
            if (date == null || !DateTime.TryParseExact(date.Trim(), "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out day))
            {
                throw new BridgeException(400, "bad_request", "登录日期无效");
            }
            return day;
        }

        static void Init(WorkContext ctx, object hx, string flag)
        {
            ctx.DropLogin();
            object[] args = new object[] { ctx.Session.Login, flag, ctx.Conn };
            object ret = ComUtil.CallRef(hx, "Init", args, new int[] { 0, 2 });
            CoRows.Note(ctx.Item, "cLsCancel.Init " + Values.Text(ret));
            if (!Values.Flag(ret))
            {
                throw new BridgeException(409, "u8_rejected", "U8 核销组件初始化失败");
            }
        }

        static WriteoffPlan Tran(WorkContext ctx, object hx, WriteoffAsk ask, DateTime day, PermRule rule)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                WriteoffPlan plan = ArapWriteoffGate.Plan(conn, ask, day, ctx.Item.Acc);
                SaveOne(ctx, hx, plan, rule);
                if (DryRun.Active)
                {
                    ArapWriteoffDry.Writeoff(plan, ArapWriteoffCheck.Body(ctx, plan, ctx.Item.Date));
                }
                CoTrans.CommitSeen(conn);
                open = false;
                CoRows.Note(ctx.Item, "核销号 " + plan.CancelNo);
                return plan;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
        }

        // 核销的共用核心（手工核销和自动核销的每一批）：在调用方的事务里、闸门（ArapWriteoffGate.Plan）已过的一张计划，
        // 查数据权限、按计划生成报文 Save（一个 close 下挂全部 vouch），然后在事务里核对余额和新核销号，写回 plan.CancelNo。
        internal static void SaveOne(WorkContext ctx, object hx, WriteoffPlan plan, PermRule rule)
        {
            object conn = ctx.Conn;
            ArapWriteoffCheck.Allowed(ctx, rule, plan);
            int after = WriteoffSql.MaxDetail(conn, plan.Flag);
            plan.LineMax = WriteoffSql.MaxCloseLine(conn, plan.ReceiptId);
            Save(ctx, hx, WriteoffXml.Build(plan, ctx.Item.Date));
            ctx.Item.TranAfter = CoTrans.Count(conn);
            // Save 在桥的事务里（实测），核对失败照常回滚；事务已被数据库回滚（如死锁）时 503（ArapWriteoffCheck.Guard）。
            ArapWriteoffCheck.Guard(conn, delegate { plan.CancelNo = ArapWriteoffCheck.InTran(conn, plan, after); });
        }

        // U8 以 VB 错误拒绝时（如余额不足、单据不存在）原文带回；返回 false 同样 409。
        static void Save(WorkContext ctx, object hx, string xml)
        {
            object ret;
            try
            {
                ret = ComUtil.CallRef(hx, "Save", new object[] { xml }, new int[] { 0 });
            }
            catch (COMException ex)
            {
                CoRows.Note(ctx.Item, "cLsCancel.Save " + ex.Message);
                throw new BridgeException(409, "u8_rejected", ArapCo.Said(ex.Message, "U8 拒绝了核销"));
            }
            CoRows.Note(ctx.Item, "cLsCancel.Save " + Values.Text(ret));
            if (!Values.Flag(ret))
            {
                throw new BridgeException(409, "u8_rejected", "U8 拒绝了核销");
            }
        }
    }

    // 提交前的核对和提交后的回读。
    internal static class ArapWriteoffCheck
    {
        const decimal Tolerance = 0.005m;

        // 数据权限：收付款单表头和每张被核销单据的表头（往来单位、部门、业务员），一张不在权限内就 403。
        public static void Allowed(WorkContext ctx, PermRule rule, WriteoffPlan plan)
        {
            List<Dictionary<string, object>> heads = new List<Dictionary<string, object>>();
            foreach (WriteoffTarget t in plan.Targets)
            {
                heads.Add(t.Head);
            }
            Allowed(ctx, rule, plan.Head, heads);
        }

        // 同上，按表头（取消核销 ArapUnwriteoff 用）。
        public static void Allowed(WorkContext ctx, PermRule rule, Dictionary<string, object> receipt,
            List<Dictionary<string, object>> heads)
        {
            PermContext p = PermCheck.Of(ctx);
            bool ok = PermCheck.RowAllowed(p, rule, receipt);
            foreach (Dictionary<string, object> head in heads)
            {
                ok = ok && PermCheck.RowAllowed(p, rule, head);
            }
            if (!ok)
            {
                throw new BridgeException(403, "no_permission", PermCheck.DeniedData);
            }
        }

        // 事务里的核对：事务还在时照常抛出（调用方回滚）；数据库已经回滚了事务（@@TRANCOUNT 为 0 或读不到，
        // 如被选为死锁牺牲品）时什么都没写进去，503 u8_unavailable，可以稍后重试。
        public static void Guard(object conn, Action check)
        {
            try
            {
                check();
            }
            catch (Exception ex)
            {
                BridgeException known = ex as BridgeException;
                if (Alive(conn) || (known != null && known.Status == 504))
                {
                    throw;
                }
                throw new BridgeException(503, "u8_unavailable", "事务已被数据库回滚（未写入），可以稍后重试");
            }
        }

        static bool Alive(object conn)
        {
            try
            {
                return CoTrans.Count(conn) != "0";
            }
            catch (Exception)
            {
                return false;
            }
        }

        // 提交前：收付款单该行余额减了合计、每个目标余额减了各自金额、本收付款单上新写的 9P 行只有一个核销号（HX 开头）。
        public static string InTran(object conn, WriteoffPlan plan, int after)
        {
            string problem = Mismatch(conn, plan);
            if (problem != null)
            {
                throw new BridgeException(409, "u8_rejected", "U8 核销后余额不符：" + problem);
            }
            Dictionary<string, int> nos = WriteoffSql.NewCancelNos(conn, plan.Flag, after, plan.ReceiptType, plan.ReceiptCode);
            string no = null;
            foreach (string key in nos.Keys)
            {
                no = key;
            }
            if (nos.Count != 1 || no == null || !no.StartsWith("HX", StringComparison.Ordinal))
            {
                throw new BridgeException(409, "u8_rejected", "U8 没有写入核销记录");
            }
            return no;
        }

        // 与目标余额不符时返回原因，符合返回 null。收付款单一侧按行主键重读该行，再加上 Save 新插的行
        // （预收 / 预付行核销时 U8 可能拆行另插 Ap_CloseBills）的余额，合计应等于核销前余额减本次合计。
        // 一律按原币核对：单据汇率与收付款单不同时，单据本币余额不按原币比例减少（差额是期末汇兑损益的事），不比本币。
        static string Mismatch(object conn, WriteoffPlan plan)
        {
            decimal remain = WriteoffSql.ReceiptRemain(conn, plan.Line)
                + WriteoffSql.AddedRemain(conn, plan.ReceiptId, plan.LineMax);
            if (Math.Abs(remain - (plan.RemainBefore - plan.Sum)) > Tolerance)
            {
                return "收付款单余额 " + ArapWriteoffGate.Money(remain);
            }
            foreach (WriteoffTarget t in plan.Targets)
            {
                decimal now = WriteoffSql.Balance(conn, plan.Flag, t.VType, t.Code, plan.Dw, t.Line);
                if (Math.Abs(now - (t.Before - t.Amount)) > Tolerance)
                {
                    return t.Kind.Title + " " + t.Code + " 余额 " + ArapWriteoffGate.Money(now);
                }
            }
            return null;
        }

        // 提交后只核对本次写进去的东西：该核销号的核销行在、每个单据行上的金额和收付款单的冲减合计对得上。
        // 余额不再逐项比（提交后 U8 客户端可能已经在同一张单据上又做了处理，那不是本次的问题）。
        public static string Written(object conn, WriteoffPlan plan)
        {
            Dictionary<string, decimal> got = WriteoffSql.CancelAmounts(conn, plan.Flag, plan.CancelNo);
            if (got.Count == 0)
            {
                return "没有核销号 " + plan.CancelNo + " 的明细行";
            }
            decimal self;
            got.TryGetValue(WriteoffSql.AmountKey(plan.ReceiptType, plan.ReceiptCode, 0), out self);
            if (Math.Abs(self + plan.Sum) > Tolerance)
            {
                return "收付款单冲减合计 " + ArapWriteoffGate.Money(-self);
            }
            foreach (WriteoffTarget t in plan.Targets)
            {
                decimal one;
                got.TryGetValue(WriteoffSql.AmountKey(t.VType, t.Code, t.Line), out one);
                if (Math.Abs(one - t.Amount) > Tolerance)
                {
                    return t.Kind.Title + " " + t.Code + " 核销金额 " + ArapWriteoffGate.Money(one);
                }
            }
            return null;
        }

        // 已提交。新连接（不加 NOLOCK）回读；读不出来或对不上都是 504 outcome_unknown（已提交，先核对，不要重投）。
        public static ApiResult Reread(WorkContext ctx, WriteoffPlan plan, string date)
        {
            object conn = null;
            string problem;
            try
            {
                conn = ctx.OpenFresh();
                problem = Written(conn, plan);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "ArapWriteoff " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "已提交但未能回读核销结果，核销号 " + plan.CancelNo);
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (problem != null)
            {
                throw new BridgeException(504, "outcome_unknown", "核销已提交但回读不符（核销号 " + plan.CancelNo + "）：" + problem
                    + "；请先查核销记录核对，不要直接重试");
            }
            return ApiResult.Ok(Body(ctx, plan, date));
        }

        internal static Dictionary<string, object> Body(WorkContext ctx, WriteoffPlan plan, string date)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["acc"] = ctx.Item.Acc;
            body["cancel_no"] = plan.CancelNo;
            body["date"] = date;
            body["receipt"] = ReceiptOut(plan);
            body["items"] = ItemsOut(plan);
            return body;
        }

        // 响应里的收付款单行（自动核销的每一批同样用它）：remaining 是核销后的余额。
        public static Dictionary<string, object> ReceiptOut(WriteoffPlan plan)
        {
            Dictionary<string, object> receipt = new Dictionary<string, object>();
            receipt["type"] = plan.ReceiptKind;
            receipt["id"] = plan.ReceiptId;
            receipt["line_id"] = plan.Line;
            receipt["code"] = plan.ReceiptCode;
            receipt["remaining"] = plan.RemainBefore - plan.Sum;
            return receipt;
        }

        public static List<object> ItemsOut(WriteoffPlan plan)
        {
            List<object> items = new List<object>();
            foreach (WriteoffTarget t in plan.Targets)
            {
                Dictionary<string, object> one = new Dictionary<string, object>();
                one["type"] = t.Kind.Name;
                one["id"] = t.Id;
                one["line_id"] = t.Line > 0 ? (object)t.Line : null;
                one["code"] = t.Code;
                one["amount"] = t.Amount;
                one["remaining"] = t.Before - t.Amount;
                items.Add(one);
            }
            return items;
        }
    }
}
