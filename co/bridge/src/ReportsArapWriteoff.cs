using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 核销记录查询 arap_writeoffs（只读）：按核销号（Ar_Detail / Ap_Detail.cCancelNo，cProcStyle=9P）列出核销批次，
    // 本接口做的和 U8 客户端做的都列。每批给出收付款单、被核销单据行和金额、操作员、是否已制单，
    // 以及 cancellable：用取消核销（arap/writeoff/cancel）同一套闸门规则（ArapUnwriteoffGate）只读地判断，不能取消时带 reason。
    // 只读判断不加锁，结果只是查询时刻的快照；真正取消时桥在事务里带锁重查。
    internal static class ReportsArapWriteoff
    {
        public static ApiResult Run(WorkContext ctx, ReportArgs a)
        {
            WriteoffListArgs w = ReportsArapWriteoffReq.Parse(ctx.Item.Body);
            string[] after = Reports.Uncursor(a.After, 2);
            List<Dictionary<string, object>> page = WriteoffListSql.Page(ctx, w, after);
            WriteoffListScope scope = new WriteoffListScope(ctx, w.Flag);
            List<string> nos = new List<string>();
            for (int i = 0; i < page.Count && i < w.Limit; i++)
            {
                nos.Add(GlSql.Col(page[i], "no"));
            }
            WriteoffPageData data = WriteoffPageData.Load(scope, nos);
            List<object> items = new List<object>();
            foreach (string no in nos)
            {
                Dictionary<string, object> item = Batch(scope, data, no);
                if (item != null)
                {
                    items.Add(item);
                }
            }
            Dictionary<string, object> body = Reports.Body();
            body["flag"] = w.Flag;
            body["items"] = items;
            body["next"] = page.Count > w.Limit
                ? Reports.Cursor(GlSql.Col(page[w.Limit - 1], "dt"), GlSql.Col(page[w.Limit - 1], "no")) : null;
            return ApiResult.Ok(body);
        }

        // 一个核销号；取页之后被别人取消了（行已不在）返回 null，不列。
        static Dictionary<string, object> Batch(WriteoffListScope scope, WriteoffPageData data, string cancelNo)
        {
            List<Dictionary<string, object>> raw = data.RowsOf(cancelNo);
            if (raw.Count == 0)
            {
                return null;
            }
            List<UnwriteoffRow> rows = new List<UnwriteoffRow>(raw.Count);
            foreach (Dictionary<string, object> r in raw)
            {
                rows.Add(UnwriteoffSql.RowOf(r));
            }
            WriteoffBatchView view = new WriteoffBatchView(scope, data, cancelNo, rows);
            Dictionary<string, object> item = view.Describe(raw[0]);
            item["voucher"] = Voucher(raw);
            item["gl_voucher"] = item["voucher"] != null;
            string reason = Reason(scope, data, view);
            item["cancellable"] = reason == null;
            item["reason"] = reason;
            return item;
        }

        // 制单信息：第一条有 cPZid 的行的凭证（cPZid、cGLSign、iGLno_id、dPZDate，未经实测）；都没有返回 null。
        static Dictionary<string, object> Voucher(List<Dictionary<string, object>> raw)
        {
            foreach (Dictionary<string, object> r in raw)
            {
                string pz = CoRows.Col(r, "pz");
                if (pz.Length == 0)
                {
                    continue;
                }
                Dictionary<string, object> v = new Dictionary<string, object>();
                v["id"] = pz;
                v["sign"] = Reports.Text(r, "pzsign");
                int no = CoRows.AsId(CoRows.Col(r, "pzno"));
                v["no"] = no > 0 ? (object)no : null;
                v["date"] = Reports.Text(r, "pzdate");
                return v;
            }
            return null;
        }

        // 取消核销闸门的只读版：同样的规则函数、同样的顺序和消息（ArapUnwriteoffGate.Plan），只是读取不加锁，
        // 期间和「未审核收款单」按请求缓存，收付款单行余额、表头、「之后的处理」用整页预读（WriteoffPageData）。
        // 能取消返回 null，否则返回原因。
        static string Reason(WriteoffListScope scope, WriteoffPageData data, WriteoffBatchView view)
        {
            UnwriteoffAsk ask = new UnwriteoffAsk();
            ask.Flag = scope.Flag;
            ask.CancelNo = view.CancelNo;
            try
            {
                UnwriteoffPlan plan = ArapUnwriteoffGate.FromRows(ask, view.Rows, scope.Local());
                plan.ReceiptId = view.ReceiptId;
                plan.Head = view.ReceiptId > 0 ? new Dictionary<string, object>() : null;
                Dictionary<int, decimal> remain = view.ReceiptId > 0
                    ? data.Lines(view.ReceiptId) : new Dictionary<int, decimal>();
                ArapUnwriteoffGate.CheckReceipt(plan, remain);
                foreach (UnwriteoffTarget t in plan.Targets)
                {
                    t.Head = data.Head(t.Kind.Name, t.VType, t.Code);
                    ArapUnwriteoffGate.CheckTarget(t);
                }
                string closed = scope.PeriodReason(plan);
                if (closed != null)
                {
                    return closed;
                }
                if (data.AnyLater(plan))
                {
                    return ArapUnwriteoffGate.Later;
                }
                return scope.Flag == "AR" && scope.Unaudited() ? ArapUnwriteoffGate.Unaudited : null;
            }
            catch (BridgeException ex)
            {
                if (!Refusal(ex))
                {
                    throw;
                }
                return ex.Message;
            }
        }

        // 闸门的拒绝（409，含币种或汇率混用）当原因；别的错误照常抛出。
        internal static bool Refusal(BridgeException ex)
        {
            return ex.Status == 400 || ex.Status == 409;
        }

        internal static string Idx(int value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }
    }

    // 一次查询里共用的东西：连接、本位币、「账套里有未审核收款单」、会计期间（按登记日期）和结账判断的缓存。
    internal sealed class WriteoffListScope
    {
        public readonly object Conn;
        public readonly string Flag;
        readonly string _acc;
        string _local;
        int _unaudited = -1;
        readonly Dictionary<string, int[]> _periods = new Dictionary<string, int[]>(StringComparer.Ordinal);
        readonly Dictionary<string, string> _closed = new Dictionary<string, string>(StringComparer.Ordinal);

        public WriteoffListScope(WorkContext ctx, string flag)
        {
            Conn = ctx.Conn;
            Flag = flag;
            _acc = ctx.Item.Acc;
        }

        public string Local()
        {
            if (_local == null)
            {
                _local = WriteoffSql.LocalCurrency(Conn);
            }
            return _local;
        }

        public bool Unaudited()
        {
            if (_unaudited < 0)
            {
                _unaudited = UnwriteoffSql.UnauditedCloseBill(Conn) ? 1 : 0;
            }
            return _unaudited == 1;
        }

        // 登记日期所在的 U8 会计期间（UA_Period）{ 年度, 期间 }；找不到为 null。
        public int[] PeriodOf(string date)
        {
            int[] found;
            if (!_periods.TryGetValue(date, out found))
            {
                found = WriteoffSql.PeriodOf(Conn, _acc, date);
                _periods[date] = found;
            }
            return found;
        }

        // 同 ArapUnwriteoffGate.OpenPeriod；未结账返回 null，否则返回原因。按（登记日期、期间）缓存。
        public string PeriodReason(UnwriteoffPlan plan)
        {
            string key = plan.RegDate + "|" + ReportsArapWriteoff.Idx(plan.Period);
            string reason;
            if (_closed.TryGetValue(key, out reason))
            {
                return reason;
            }
            try
            {
                ArapUnwriteoffGate.OpenPeriod(Conn, plan, _acc);
                reason = null;
            }
            catch (BridgeException ex)
            {
                if (!ReportsArapWriteoff.Refusal(ex))
                {
                    throw;
                }
                reason = ex.Message;
            }
            _closed[key] = reason;
            return reason;
        }
    }
}
