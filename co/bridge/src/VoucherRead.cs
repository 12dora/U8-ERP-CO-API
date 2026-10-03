using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    internal static class VoucherRead
    {
        const int LineCap = 500;

        public static ApiResult Load(WorkContext ctx)
        {
            if (ctx == null || ctx.Item == null || ctx.Item.Type == null)
            {
                throw new BridgeException(400, "bad_request", "缺少单据类型");
            }
            return LoadOne(ctx, ctx.Item.Type, ctx.Item.Id);
        }

        // 读一张单据（vouchers/load 与 vouchers/load_many 共用）：不登录、不改 ctx.Item，可在同一请求里逐张调用。
        internal static ApiResult LoadOne(WorkContext ctx, VoucherKind kind, int id)
        {
            ApiResult result = Guarded(ctx, kind, id);
            if (result == null || result.Body == null || result.Status != 200)
            {
                return result;
            }
            Strip(result.Body);
            Cap(result.Body);
            if (kind.Workflow && !result.Body.ContainsKey("wf"))
            {
                result.Body["wf"] = WfState.Describe(ctx, kind, id);
            }
            return result;
        }

        // 数据权限：先在表头表上带类型条件探一次，越权的单据不走 CO 读取，直接 403。
        // 事先没探到（类型条件与读取口径不一时）：单据读到后再按列表同一套条件核对，越权 403。
        static ApiResult Guarded(WorkContext ctx, VoucherKind kind, int id)
        {
            bool permitted = PermHook.VoucherBefore(ctx, kind, id);
            ApiResult result = Pick(ctx, kind, id);
            if (!permitted && result != null && result.Body != null && result.Status == 200)
            {
                PermHook.Voucher(ctx, kind, id);
            }
            return result;
        }

        // 请购单走 PuApp.Load，物料清单走 BomRead.Load（纯 SQL）；其余照旧。
        // 放在 Guarded 里而不是 Call 里，读取后的过滤对它们同样生效。
        static ApiResult Pick(WorkContext ctx, VoucherKind kind, int id)
        {
            if (kind.Name == PuAppRoutes.KindName)
            {
                return PuApp.Load(ctx, kind, id);
            }
            if (kind.Name == BomRoutes.KindName)
            {
                return BomRead.Load(ctx, kind, id);
            }
            // 采购结算单：纯 SQL（PuSettleRead）。
            if (kind.Name == PuSettleRead.KindName)
            {
                return PuSettleRead.Load(ctx, kind, id);
            }
            // 出入库调整单、存货调价单：纯 SQL（IaAdjustRead、InvPriceAdjustRead）。
            if (kind.Name == IaAdjustRead.KindName)
            {
                return IaAdjustRead.Load(ctx, kind, id);
            }
            if (kind.Name == InvPriceAdjustRead.KindName)
            {
                return InvPriceAdjustRead.Load(ctx, kind, id);
            }
            // 退货申请单：纯 SQL（ReturnsApplyRead）。
            if (kind.Name == ReturnsApplyRead.KindName)
            {
                return ReturnsApplyRead.Load(ctx, kind, id);
            }
            // 货位调整单：纯 SQL，另带货位台账（PositionAdjust.Load）。
            if (kind.Name == PositionAdjust.KindName)
            {
                return PositionAdjust.Load(ctx, kind, id);
            }
            return Call(ctx, kind, id);
        }

        static ApiResult Call(WorkContext ctx, VoucherKind kind, int id)
        {
            // 采购发票、销售发票的 state 另加应付 / 应收审核状态（ArapAudit.WithState）。
            if (kind.Name == "sale_invoice")
            {
                return ArapAudit.WithState(ctx.Conn, kind, id, SaleInvoice.Load(ctx, kind, id));
            }
            if (kind.Name == "purchase_invoice" || kind.Name == "production_order")
            {
                return ArapAudit.WithState(ctx.Conn, kind, id, SqlRead.Load(ctx, kind, id));
            }
            // 退货单先核对红字，再同发货单走 CO（VT 10）。
            if (kind.Name == SaleReturn.KindName)
            {
                return SaleReturn.Load(ctx, kind, id);
            }
            if (kind.Family == "sa")
            {
                return SaleOrderCo.Load(ctx, kind, id);
            }
            if (kind.Family == "pu")
            {
                return PurchaseCo.Load(ctx, kind, id);
            }
            if (kind.Family == "st")
            {
                return StockCo.Load(ctx, kind, id);
            }
            if (kind.Family == "qm")
            {
                return QmRead.Load(ctx, kind, id);
            }
            if (kind.Family == "ar")
            {
                return ArapCo.Load(ctx, kind, id);
            }
            throw new BridgeException(400, "bad_request", "该单据类型不支持读取");
        }

        static void Strip(Dictionary<string, object> body)
        {
            object head;
            if (body.TryGetValue("head", out head))
            {
                DropHidden(head as Dictionary<string, object>);
            }
            object lines;
            if (!body.TryGetValue("lines", out lines))
            {
                return;
            }
            IList list = lines as IList;
            if (list == null)
            {
                return;
            }
            for (int i = 0; i < list.Count; i++)
            {
                DropHidden(list[i] as Dictionary<string, object>);
            }
        }

        static void DropHidden(Dictionary<string, object> map)
        {
            if (map == null)
            {
                return;
            }
            List<string> drop = new List<string>();
            foreach (string key in map.Keys)
            {
                if (Hidden(key))
                {
                    drop.Add(key);
                }
            }
            for (int i = 0; i < drop.Count; i++)
            {
                map.Remove(drop[i]);
            }
        }

        static void Cap(Dictionary<string, object> body)
        {
            object raw;
            if (!body.TryGetValue("lines", out raw) || raw == null)
            {
                return;
            }
            IList list = raw as IList;
            if (list == null || list.Count <= LineCap)
            {
                return;
            }
            object[] cut = new object[LineCap];
            for (int i = 0; i < LineCap; i++)
            {
                cut[i] = list[i];
            }
            body["lines"] = cut;
            body["lines_truncated"] = true;
        }

        static bool Hidden(string name)
        {
            if (name == null)
            {
                return false;
            }
            string lower = name.ToLowerInvariant();
            if (lower.IndexOf("password", StringComparison.Ordinal) >= 0)
            {
                return true;
            }
            return lower.IndexOf("pwd", StringComparison.Ordinal) >= 0;
        }
    }
}
