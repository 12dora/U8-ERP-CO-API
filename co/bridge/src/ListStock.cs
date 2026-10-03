using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // stock/current：CurrentStock 按 AutoID 分页，带仓库名和存货名。
    // U8 通常不填 fAvaQuantity，原值放 qty_available_raw。qty_available 照 U8 的 ST_GetStockFormula：
    // 整行冻结（bStopFlag / bGSPStop）为 0，否则现存量 - 冻结量；待入、调拨待入、待出、调拨待出只在库存选项
    // bNormalOn* / bBatchOn*（按是否批次管理）打开时计入。已在测试账套上与 prc_SCM_GetCurrentStockForVouch 的 iavaqty 对过。
    // qty_forecast_available 是桥自己的口径：现存 - 冻结 + 预计入库合计 - 预计出库合计，U8 没有同名数值。
    internal static class ListStock
    {
        const string Base = "(CASE WHEN ISNULL(cs.bStopFlag, 0) = 1 OR ISNULL(cs.bGSPStop, 0) = 1 THEN 0 "
            + "ELSE ISNULL(cs.iQuantity, 0) - ISNULL(cs.fStopQuantity, 0) END)";

        const string Flags = "(SELECT "
            + "MAX(CASE WHEN cName = 'bNormalOnCheck' AND LOWER(cValue) = 'true' THEN 1 ELSE 0 END) AS nc, "
            + "MAX(CASE WHEN cName = 'bNormalOnTransWay' AND LOWER(cValue) = 'true' THEN 1 ELSE 0 END) AS nw, "
            + "MAX(CASE WHEN cName = 'bNormalOnDispatch' AND LOWER(cValue) = 'true' THEN 1 ELSE 0 END) AS nd, "
            + "MAX(CASE WHEN cName = 'bNormalOnTransOut' AND LOWER(cValue) = 'true' THEN 1 ELSE 0 END) AS no, "
            + "MAX(CASE WHEN cName = 'bBatchOnCheck' AND LOWER(cValue) = 'true' THEN 1 ELSE 0 END) AS bc, "
            + "MAX(CASE WHEN cName = 'bBatchOnTransWay' AND LOWER(cValue) = 'true' THEN 1 ELSE 0 END) AS bw, "
            + "MAX(CASE WHEN cName = 'bBatchOnDispatch' AND LOWER(cValue) = 'true' THEN 1 ELSE 0 END) AS bd, "
            + "MAX(CASE WHEN cName = 'bBatchOnTransOut' AND LOWER(cValue) = 'true' THEN 1 ELSE 0 END) AS bo "
            + "FROM AccInformation WHERE cSysID = 'ST') f";

        const string Select = "SELECT TOP (?) cs.AutoID AS id, cs.cWhCode AS wh_code, w.cWhName AS wh_name, "
            + "cs.cInvCode AS inv_code, i.cInvName AS inv_name, i.cInvStd AS inv_std, "
            + "i.cInvCCode AS inv_class_code, cs.cBatch AS batch, cs.cFree1 AS free1, cs.cFree2 AS free2, "
            + "cs.cFree3 AS free3, cs.cFree4 AS free4, cs.cFree5 AS free5, cs.cFree6 AS free6, "
            + "cs.cFree7 AS free7, cs.cFree8 AS free8, cs.cFree9 AS free9, cs.cFree10 AS free10, "
            + "cs.ItemId AS item_id, cs.iQuantity AS qty, cs.iNum AS qty_aux, cs.fStopQuantity AS qty_frozen, "
            + "cs.fInQuantity AS qty_pending_in, cs.fTransInQuantity AS qty_trans_in, "
            + "cs.fOutQuantity AS qty_pending_out, cs.fTransOutQuantity AS qty_trans_out, "
            + "cs.fDisableQuantity AS qty_disabled, cs.fAvaQuantity AS qty_available_raw, "
            + Base + " + CASE WHEN ISNULL(i.bInvBatch, 0) = 1 THEN "
            + "f.bc * ISNULL(cs.fInQuantity, 0) + f.bw * ISNULL(cs.fTransInQuantity, 0) "
            + "- f.bd * ISNULL(cs.fOutQuantity, 0) - f.bo * ISNULL(cs.fTransOutQuantity, 0) ELSE "
            + "f.nc * ISNULL(cs.fInQuantity, 0) + f.nw * ISNULL(cs.fTransInQuantity, 0) "
            + "- f.nd * ISNULL(cs.fOutQuantity, 0) - f.no * ISNULL(cs.fTransOutQuantity, 0) END "
            + "AS qty_available, " + Base + " + ISNULL(cs.fInQuantity, 0) + ISNULL(cs.fTransInQuantity, 0) "
            + "- ISNULL(cs.fOutQuantity, 0) - ISNULL(cs.fTransOutQuantity, 0) AS qty_forecast_available, "
            + "cs.bStopFlag AS stopped, cs.dMdate AS made_date, cs.dVDate AS valid_until, "
            + "cs.iMassDate AS mass_days, cs.cMassUnit AS mass_unit, cs.dExpirationdate AS expires, "
            + "cs.cVMIVenCode AS vmi_ven_code, cs.iSoType AS so_type, cs.iSodid AS so_line, "
            + "CONVERT(varchar(20), CONVERT(bigint, cs.ufts)) AS ufts "
            + "FROM CurrentStock cs LEFT JOIN Warehouse w ON w.cWhCode = cs.cWhCode "
            + "LEFT JOIN Inventory i ON i.cInvCode = cs.cInvCode CROSS JOIN " + Flags + " WHERE cs.AutoID > ?";

        static readonly string[] Keys = new string[]
        {
            "id", "wh_code", "wh_name", "inv_code", "inv_name", "inv_std", "inv_class_code", "batch",
            "free1", "free2", "free3", "free4", "free5", "free6", "free7", "free8", "free9", "free10",
            "item_id", "qty", "qty_aux", "qty_frozen", "qty_pending_in", "qty_trans_in", "qty_pending_out",
            "qty_trans_out", "qty_disabled", "qty_available_raw", "qty_available", "qty_forecast_available", "stopped", "made_date",
            "valid_until", "mass_days", "mass_unit", "expires", "vmi_ven_code", "so_type", "so_line", "ufts"
        };

        public static ApiResult Run(WorkContext ctx, ListArgs args)
        {
            object conn = ctx.Conn;
            string watermark = ListRoutes.Watermark(conn);
            List<object> ps = new List<object>();
            string sql = Build(args, ps, ctx);
            List<Dictionary<string, object>> rows = Rows.Query(conn, sql, ps.ToArray(), args.Limit + 1);
            Dictionary<string, object> result = new Dictionary<string, object>();
            result["ok"] = true;
            return ListRoutes.Page(result, rows, Keys, args.Limit, watermark);
        }

        static string Build(ListArgs args, List<object> ps, WorkContext ctx)
        {
            ps.Add(args.Limit + 1);
            ps.Add(args.After);
            StringBuilder sb = new StringBuilder(Select);
            Equal(sb, "cs.cWhCode", args.Wh, ps);
            Equal(sb, "cs.cInvCode", args.Inv, ps);
            Equal(sb, "cs.cBatch", args.Batch, ps);
            if (args.NonZero)
            {
                sb.Append(" AND cs.iQuantity <> 0");
            }
            if (args.Since != null)
            {
                sb.Append(" AND cs.ufts > CONVERT(binary(8), CONVERT(bigint, ?))");
                ps.Add(args.Since);
            }
            // 数据权限：仓库、存货。
            PermHook.Where(sb, ps, ctx, "cs");
            sb.Append(" ORDER BY cs.AutoID");
            return sb.ToString();
        }

        static void Equal(StringBuilder sb, string column, string value, List<object> ps)
        {
            if (value == null)
            {
                return;
            }
            sb.Append(" AND ").Append(column).Append(" = ?");
            ps.Add(value);
        }
    }
}
