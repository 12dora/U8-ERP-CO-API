using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // vouchers/search 的 SQL：在 vouchers/list 的查询（ListSql 的选择列、APPLY、筛选）上加单号包含、往来单位、存货三个条件，
    // 并 LEFT JOIN 客户 / 供应商档案给出 partner_name。标识符只来自 ListKind 和这里的常量表，调用方的值只进 ? 参数。
    // 往来单位列就是 ListKind 的 cus / ven 列（收付款单、应收应付单的 cDwCode 按 cFlag 放在其中之一）；两列都有的类型（出入库单、质检单）任一相等即可。
    internal static class VoucherSearchSql
    {
        // 存货条件：{ 类型, FROM 片段, 外键表达式, 存货表达式 }。FROM 为 null 表示表头列（检验单、不良品处理单一张一个存货）。
        // 列名按 U8 表结构（sys.columns）；收付款单、应收应付单的表体没有存货列，不支持按存货搜索。
        static readonly string[][] InvRows = new string[][]
        {
            new string[] { "sale_order", "SO_SODetails d", "d.ID", "d.cInvCode" },
            new string[] { "dispatch", "DispatchLists d", "d.DLID", "d.cInvCode" },
            new string[] { "sale_return", "DispatchLists d", "d.DLID", "d.cInvCode" },
            new string[] { "sale_invoice", "SaleBillVouchs d", "d.SBVID", "d.cInvCode" },
            new string[] { "purchase_order", "PO_Podetails d", "d.POID", "d.cInvCode" },
            new string[] { "arrival", "PU_ArrivalVouchs d", "d.ID", "d.cInvCode" },
            new string[] { "purchase_return", "PU_ArrivalVouchs d", "d.ID", "d.cInvCode" },
            new string[] { "purchase_invoice", "PurBillVouchs d", "d.PBVID", "d.cInvCode" },
            new string[] { "purchase_requisition", "PU_AppVouchs d", "d.ID", "d.cInvCode" },
            new string[] { "purchase_in", "rdrecords01 d", "d.ID", "d.cInvCode" },
            new string[] { "other_in", "rdrecords08 d", "d.ID", "d.cInvCode" },
            new string[] { "other_out", "rdrecords09 d", "d.ID", "d.cInvCode" },
            new string[] { "product_in", "rdrecords10 d", "d.ID", "d.cInvCode" },
            new string[] { "material_out", "rdrecords11 d", "d.ID", "d.cInvCode" },
            new string[] { "sale_out", "rdrecords32 d", "d.ID", "d.cInvCode" },
            new string[] { "transfer", "TransVouchs d", "d.ID", "d.cInvCode" },
            new string[] { "transfer_request", "ST_AppTransVouchs d", "d.ID", "d.cInvCode" },
            new string[] { "shape_change", "AssemVouchs d", "d.ID", "d.cInvCode" },
            new string[] { "stock_check", "CheckVouchs d", "d.ID", "d.cInvCode" },
            // 货位调整单：按调整行的存货。
            new string[] { "position_adjust", "AdjustPVouchs d", "d.ID", "d.cInvCode" },
            new string[] { "production_order", "mom_orderdetail d", "d.MoId", "d.InvCode" },
            new string[] { "qm_incoming_inspect", "QMINSPECTVOUCHERS d", "d.ID", "d.CINVCODE" },
            new string[] { "qm_product_inspect", "QMINSPECTVOUCHERS d", "d.ID", "d.CINVCODE" },
            new string[] { "qm_incoming_check", null, null, "h.CINVCODE" },
            new string[] { "qm_product_check", null, null, "h.CINVCODE" },
            new string[] { "qm_incoming_reject", null, null, "h.CINVCODE" },
            new string[] { "qm_product_reject", null, null, "h.CINVCODE" },
            // 其他报检单按表体、其他检验单按表头（一张一个存货）。
            new string[] { "qm_other_inspect", "QMINSPECTVOUCHERS d", "d.ID", "d.CINVCODE" },
            new string[] { "qm_other_check", null, null, "h.CINVCODE" },
            // 物料清单按子件：子件行的 ComponentId 是物料 PartId，经 bas_part 取存货编码（母件编码就是列表的 code）。
            new string[] { "bom", "bom_opcomponent d JOIN bas_part dp ON dp.PartId = d.ComponentId", "d.BomId", "dp.InvCode" },
            // 采购结算单：按结算行的存货。
            new string[] { PuSettleRead.KindName, "PurSettleVouchs d", "d.PSVID", "d.cInvCode" },
            // 出入库调整单（表体按单号 cJVCode 挂，经表头换成 id）、存货调价单：按表体存货。
            new string[] { IaAdjustRead.KindName, "JustInVouchs d JOIN JustInVouch dh ON dh.cJVCode = d.cJVCode", "dh.id",
                "d.cInvCode" },
            new string[] { InvPriceAdjustRead.KindName, "SA_InvPriceJustDetail d", "d.id", "d.cinvcode" },
            // 退货申请单：按表体存货。
            new string[] { ReturnsApplyRead.KindName, "SA_ReturnsApplyDetail d", "d.ID", "d.cInvCode" }
        };

        static readonly Dictionary<string, string[]> Inv = BuildInv();

        public const string PartnerName = "partner_name";

        static Dictionary<string, string[]> BuildInv()
        {
            Dictionary<string, string[]> map = new Dictionary<string, string[]>(StringComparer.Ordinal);
            for (int i = 0; i < InvRows.Length; i++)
            {
                map.Add(InvRows[i][0], InvRows[i]);
            }
            return map;
        }

        public static bool HasInventory(ListKind kind)
        {
            return kind != null && Inv.ContainsKey(kind[ListKind.Name]);
        }

        public static bool HasPartner(ListKind kind)
        {
            return kind.Has(ListKind.Cus) || kind.Has(ListKind.Ven);
        }

        // 响应键：列表的全部键，有往来单位列的类型另加 partner_name。
        public static string[] Keys(ListKind kind)
        {
            List<string> keys = new List<string>(ListSql.FullKeys(kind));
            if (HasPartner(kind))
            {
                keys.Add(PartnerName);
            }
            return keys.ToArray();
        }

        // SELECT TOP (?) … FROM head h [APPLY] [LEFT JOIN 客户 / 供应商] WHERE [类型条件 AND] h.id > ? [列表筛选]
        // [单号包含] [往来单位] [存货] [表头自定义项] [数据权限] ORDER BY h.id。选择列另加 defines 的各列（VoucherSearchDefines）。? 的顺序就是在文本里出现的顺序。
        public static string Build(VoucherSearchArgs args, List<object> ps, WorkContext ctx)
        {
            ListKind kind = args.Kind;
            string id = "h." + kind[ListKind.Id];
            ps.Add(args.Limit + 1);
            StringBuilder sb = new StringBuilder("SELECT TOP (?) ");
            sb.Append(ListSql.FullSelect(kind));
            AppendPartnerName(sb, kind);
            VoucherSearchDefines.AppendSelect(sb, args.Defines);
            sb.Append(" FROM ").Append(kind[ListKind.Head]).Append(" h").Append(ListSql.ApplyOf(kind));
            AppendJoins(sb, kind);
            sb.Append(" WHERE ");
            if (kind.Has(ListKind.Cond))
            {
                sb.Append(kind[ListKind.Cond]).Append(" AND ");
            }
            sb.Append(id).Append(" > ?");
            ps.Add(args.After);
            ListSql.AppendFilters(sb, kind, args.Filters, ps);
            AppendExtra(sb, kind, args, ps);
            // 数据权限：与 vouchers/list 同一套条件，放在 WHERE 最后。
            PermHook.Where(sb, ps, ctx, "h");
            sb.Append(" ORDER BY ").Append(id);
            return sb.ToString();
        }

        static void AppendPartnerName(StringBuilder sb, ListKind kind)
        {
            bool cus = kind.Has(ListKind.Cus);
            bool ven = kind.Has(ListKind.Ven);
            if (cus && ven)
            {
                sb.Append(", COALESCE(pc.cCusName, pv.cVenName) AS ").Append(PartnerName);
            }
            else if (cus)
            {
                sb.Append(", pc.cCusName AS ").Append(PartnerName);
            }
            else if (ven)
            {
                sb.Append(", pv.cVenName AS ").Append(PartnerName);
            }
        }

        // 客户、供应商编码是档案主键，LEFT JOIN 不会让单据重复。
        static void AppendJoins(StringBuilder sb, ListKind kind)
        {
            if (kind.Has(ListKind.Cus))
            {
                sb.Append(" LEFT JOIN Customer pc ON pc.cCusCode = ").Append(kind[ListKind.Cus]);
            }
            if (kind.Has(ListKind.Ven))
            {
                sb.Append(" LEFT JOIN Vendor pv ON pv.cVenCode = ").Append(kind[ListKind.Ven]);
            }
        }

        static void AppendExtra(StringBuilder sb, ListKind kind, VoucherSearchArgs args, List<object> ps)
        {
            if (args.CodeLike != null)
            {
                sb.Append(" AND ").Append(kind[ListKind.Code]).Append(" LIKE ? ESCAPE '\\'");
                ps.Add(args.CodeLike);
            }
            if (args.Partner != null)
            {
                AppendPartner(sb, kind, args.Partner, ps);
            }
            if (args.Inventory != null)
            {
                AppendInventory(sb, kind, args.Inventory, ps);
            }
            VoucherSearchDefines.AppendWhere(sb, args.Defines, ps);
        }

        static void AppendPartner(StringBuilder sb, ListKind kind, string partner, List<object> ps)
        {
            bool cus = kind.Has(ListKind.Cus);
            bool ven = kind.Has(ListKind.Ven);
            if (cus && ven)
            {
                sb.Append(" AND (").Append(kind[ListKind.Cus]).Append(" = ? OR ").Append(kind[ListKind.Ven]).Append(" = ?)");
                ps.Add(partner);
                ps.Add(partner);
                return;
            }
            sb.Append(" AND ").Append(kind[cus ? ListKind.Cus : ListKind.Ven]).Append(" = ?");
            ps.Add(partner);
        }

        static void AppendInventory(StringBuilder sb, ListKind kind, string inventory, List<object> ps)
        {
            string[] row = Inv[kind[ListKind.Name]];
            if (row[1] == null)
            {
                sb.Append(" AND ").Append(row[3]).Append(" = ?");
            }
            else
            {
                sb.Append(" AND EXISTS (SELECT 1 FROM ").Append(row[1]).Append(" WHERE ").Append(row[2]).Append(" = h.")
                    .Append(kind[ListKind.Id]).Append(" AND ").Append(row[3]).Append(" = ?)");
            }
            ps.Add(inventory);
        }
    }
}
