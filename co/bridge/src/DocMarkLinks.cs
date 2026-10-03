using System;
using System.Collections.Generic;

namespace U8Co
{
    // 预演：一张销售 / 采购单据的上游单据（生单来源、回写累计数量的单据）。按本单主键在请求连接上查，
    // 生单在新单保存之后、提交之前查；修改、删除在调用 U8 之前查（删除之后本单行已不在）。
    // 发货单与退货单同表（DispatchList），按 bReturnFlag 分成 dispatch / sale_return。
    internal static class DocMarkLinks
    {
        const int MaxDocs = 10;

        const string SoOfDispatch = "select distinct top 10 convert(varchar(20), s.ID) as id from SO_SODetails s "
            + "inner join DispatchLists d on d.iSOsID=s.iSOsID where d.DLID=?";
        const string DispatchOfReturn = "select distinct top 10 convert(varchar(20), o.DLID) as id from DispatchLists o "
            + "inner join DispatchLists d on d.iCorID=o.iDLsID where d.DLID=?";
        const string SoOfInvoice = "select distinct top 10 convert(varchar(20), s.ID) as id from SO_SODetails s "
            + "inner join SaleBillVouchs b on b.iSOsID=s.iSOsID where b.SBVID=?";
        const string DlOfInvoice = "select distinct top 10 convert(varchar(20), h.DLID) as id from DispatchList h "
            + "inner join DispatchLists d on d.DLID=h.DLID inner join SaleBillVouchs b on b.iDLsID=d.iDLsID "
            + "where b.SBVID=? and isnull(h.bReturnFlag,0)=0";
        const string RetOfInvoice = "select distinct top 10 convert(varchar(20), h.DLID) as id from DispatchList h "
            + "inner join DispatchLists d on d.DLID=h.DLID inner join SaleBillVouchs b on b.iDLsID=d.iDLsID "
            + "where b.SBVID=? and isnull(h.bReturnFlag,0)=1";
        // 先开票发票：U8 按发票生成的发货单指回发票（DispatchList.SBVID）。
        const string AdvanceDlOfInvoice = "select distinct top 10 convert(varchar(20), DLID) as id from DispatchList "
            + "where SBVID=?";
        const string PoOfArrival = "select distinct top 10 convert(varchar(20), p.POID) as id from PO_Podetails p "
            + "inner join PU_ArrivalVouchs s on s.iPOsID=p.ID where s.ID=?";
        const string ArrOfReturn = "select distinct top 10 convert(varchar(20), a.ID) as id from PU_ArrivalVouchs a "
            + "inner join PU_ArrivalVouchs s on s.iCorId=a.Autoid where s.ID=?";
        const string InOfPuInvoice = "select distinct top 10 convert(varchar(20), r.ID) as id from rdrecords01 r "
            + "inner join PurBillVouchs b on b.RdsId=r.AutoID where b.PBVID=?";

        static readonly Dictionary<string, string[]> Map = Build();

        // 值成对：上游类型、查询。
        static Dictionary<string, string[]> Build()
        {
            Dictionary<string, string[]> map = new Dictionary<string, string[]>(StringComparer.Ordinal);
            map["dispatch"] = new string[] { "sale_order", SoOfDispatch };
            map["sale_return"] = new string[] { "dispatch", DispatchOfReturn, "sale_order", SoOfDispatch };
            map["sale_invoice"] = new string[] { "dispatch", DlOfInvoice, "sale_return", RetOfInvoice,
                "dispatch", AdvanceDlOfInvoice, "sale_order", SoOfInvoice };
            map["arrival"] = new string[] { "purchase_order", PoOfArrival };
            map["purchase_return"] = new string[] { "arrival", ArrOfReturn, "purchase_order", PoOfArrival };
            map["purchase_invoice"] = new string[] { "purchase_in", InOfPuInvoice };
            return map;
        }

        internal static void Touch(object conn, VoucherKind kind, int id)
        {
            string[] links;
            if (kind == null || id <= 0 || !Map.TryGetValue(kind.Name, out links))
            {
                return;
            }
            HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
            for (int i = 0; i + 1 < links.Length && seen.Count < MaxDocs; i += 2)
            {
                TouchAll(conn, links[i], links[i + 1], id, seen);
            }
        }

        static void TouchAll(object conn, string kindName, string sql, int id, HashSet<string> seen)
        {
            List<Dictionary<string, object>> rows;
            try
            {
                rows = Rows.Query(conn, sql, new object[] { id }, MaxDocs);
            }
            catch (Exception)
            {
                // 只影响预演回读哪几张单，不影响写入本身。
                return;
            }
            for (int i = 0; rows != null && i < rows.Count && seen.Count < MaxDocs; i++)
            {
                int found = CoRows.AsId(CoRows.Col(rows[i], "id"));
                if (found > 0 && seen.Add(kindName + ":" + found.ToString()))
                {
                    DocMark.Touched(kindName, found);
                }
            }
        }
    }
}
