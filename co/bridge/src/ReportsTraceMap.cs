using System;
using System.Collections.Generic;

namespace U8Co
{
    // 单据追溯的一种节点：表头表（别名 h）、主键、单号、日期、状态表达式、类型条件。表名、列名都是常量。
    internal sealed class TraceNode
    {
        public string Kind;
        public string Head;
        public string Id;
        public string Code;
        public string Date;
        public string State;
        public string Cond;
    }

    // 单据追溯的一条边：上游 From → 下游 To。Source 是 FROM 子句，Up / Down 是两端表头 id 的表达式，Cond 以 " AND " 开头。
    // 下游方向按 Up IN (…) 查，上游方向按 Down IN (…) 查；每对（上游、下游）的行数是 COUNT(*)（关联的明细行数）。
    internal sealed class TraceEdge
    {
        public string From;
        public string To;
        public string Up;
        public string Down;
        public string Source;
        public string Cond;
    }

    // 单据追溯的节点表和边表（唯一的一份）。关联列都已在测试账套核对：发货、出库、开票、到货、入库、发票的来源行 id，
    // 退货单 iCorID / iCorId 指原发货（到货）行，质检单 SOURCEAUTOID / INSPECTAUTOID / CHECKID，入库 iArrsId /
    // iCheckIdBaks / iRejectIds / iMPoIds，收付款按核销明细 Ar_Detail / Ap_Detail（cProcStyle=9P）的单号关联发票。
    internal static class ReportsTraceMap
    {
        const string Dl = "DispatchLists u JOIN DispatchList y ON y.DLID=u.DLID";
        const string Arr = "PU_ArrivalVouchs u JOIN PU_ArrivalVouch y ON y.ID=u.ID";
        const string Blue = " AND ISNULL(y.bReturnFlag,0)=0";
        const string Red = " AND y.bReturnFlag=1";
        const string ArrBlue = " AND ISNULL(y.iBillType,0)=0";
        const string DlKind = " AND h.cVouchType=N'05'";

        const string PoState = "CASE WHEN ISNULL(h.cState,0)=2 OR NULLIF(LTRIM(RTRIM(h.cCloser)),N'') IS NOT NULL"
            + " THEN N'closed' WHEN NULLIF(LTRIM(RTRIM(h.cVerifier)),N'') IS NOT NULL THEN N'verified'"
            + " ELSE N'unverified' END";
        // 生产订单的审核、关闭在行上（同 ListKinds 的 MoApply）：全部行关闭为 closed，全部行已下达为 verified。
        const string MoState = "CASE WHEN NOT EXISTS (SELECT 1 FROM mom_orderdetail z WHERE z.MoId=h.MoId)"
            + " THEN N'unverified' WHEN NOT EXISTS (SELECT 1 FROM mom_orderdetail z WHERE z.MoId=h.MoId AND z.Status<>4)"
            + " THEN N'closed' WHEN NOT EXISTS (SELECT 1 FROM mom_orderdetail z WHERE z.MoId=h.MoId"
            + " AND (z.Status NOT IN (3,4) OR NULLIF(LTRIM(RTRIM(z.RelsUser)),N'') IS NULL))"
            + " THEN N'verified' ELSE N'unverified' END";
        const string BomState = "CASE h.Status WHEN 4 THEN N'closed' WHEN 3 THEN N'verified' ELSE N'unverified' END";
        const string BomCode = "(SELECT TOP 1 bp.InvCode FROM bom_parent p JOIN bas_part bp ON bp.PartId=p.ParentId"
            + " WHERE p.BomId=h.BomId)";

        static readonly TraceNode[] Nodes = BuildNodes();
        static readonly TraceEdge[] Edges = BuildEdges();

        public static TraceNode Node(string kind)
        {
            for (int i = 0; i < Nodes.Length; i++)
            {
                if (string.Equals(Nodes[i].Kind, kind, StringComparison.Ordinal))
                {
                    return Nodes[i];
                }
            }
            return null;
        }

        public static string[] KindNames()
        {
            string[] names = new string[Nodes.Length];
            for (int i = 0; i < Nodes.Length; i++)
            {
                names[i] = Nodes[i].Kind;
            }
            return names;
        }

        public static TraceEdge[] All()
        {
            return Edges;
        }

        // 已关闭 > 已审核 > 未审核。closer 为 null 表示该单据没有关闭。
        static string State(string closer, string verifier)
        {
            string head = closer == null ? "" : " WHEN NULLIF(LTRIM(RTRIM(" + closer + ")),N'') IS NOT NULL THEN N'closed'";
            return "CASE" + head + " WHEN NULLIF(LTRIM(RTRIM(" + verifier + ")),N'') IS NOT NULL THEN N'verified'"
                + " ELSE N'unverified' END";
        }

        // table 写成「表头表.主键列」。
        static TraceNode N(string kind, string table, string code, string date, string state, string cond)
        {
            int dot = table.IndexOf('.');
            TraceNode node = new TraceNode();
            node.Kind = kind;
            node.Head = table.Substring(0, dot);
            node.Id = table.Substring(dot + 1);
            node.Code = code;
            node.Date = date;
            node.State = state;
            node.Cond = cond;
            return node;
        }

        static TraceNode[] BuildNodes()
        {
            List<TraceNode> all = new List<TraceNode>();
            all.Add(N("sale_order", "SO_SOMain.ID", "h.cSOCode", "h.dDate", State("h.cCloser", "h.cVerifier"), ""));
            all.Add(N("dispatch", "DispatchList.DLID", "h.cDLCode", "h.dDate", State("h.cCloser", "h.cVerifier"),
                DlKind + " AND ISNULL(h.bReturnFlag,0)=0"));
            all.Add(N("sale_return", "DispatchList.DLID", "h.cDLCode", "h.dDate", State("h.cCloser", "h.cVerifier"),
                DlKind + " AND h.bReturnFlag=1"));
            // 退货申请单（只读）。
            all.Add(N("sale_return_apply", "SA_ReturnsApplyMain.ID", "h.cCode", "h.dDate",
                State("h.cCloser", "h.cVerifier"), ""));
            all.Add(N("sale_out", "rdrecord32.ID", "h.cCode", "h.dDate", State(null, "h.cHandler"), ""));
            all.Add(N("sale_invoice", "SaleBillVouch.SBVID", "h.cSBVCode", "h.dDate", State(null, "h.cChecker"), ""));
            all.Add(N("ar_receipt", "Ap_CloseBill.iID", "h.cVouchID", "h.dVouchDate", State(null, "h.cCheckMan"),
                " AND h.cFlag=N'AR' AND h.cVouchType=N'48'"));
            all.Add(N("purchase_requisition", "PU_AppVouch.ID", "h.cCode", "h.dDate",
                State("h.cCloser", "h.cVerifier"), ""));
            all.Add(N("purchase_order", "PO_Pomain.POID", "h.cPOID", "h.dPODate", PoState, ""));
            all.Add(N("arrival", "PU_ArrivalVouch.ID", "h.cCode", "h.dDate", State("h.ccloser", "h.cverifier"),
                " AND ISNULL(h.iBillType,0)=0"));
            all.Add(N("purchase_return", "PU_ArrivalVouch.ID", "h.cCode", "h.dDate",
                State("h.ccloser", "h.cverifier"), " AND h.iBillType=1"));
            all.Add(N("purchase_in", "RdRecord01.ID", "h.cCode", "h.dDate", State(null, "h.cHandler"), ""));
            all.Add(N("purchase_invoice", "PurBillVouch.PBVID", "h.cPBVCode", "h.dPBVDate",
                State(null, "h.cVerifier"), ""));
            all.Add(N("ap_payment", "Ap_CloseBill.iID", "h.cVouchID", "h.dVouchDate", State(null, "h.cCheckMan"),
                " AND h.cFlag=N'AP' AND h.cVouchType=N'49'"));
            // 退款：供应商退款是应付的收款单（AP48），客户退款是应收的付款单（AR49）。只作节点（可从它们查起），不挂边。
            all.Add(N("ap_refund", "Ap_CloseBill.iID", "h.cVouchID", "h.dVouchDate", State(null, "h.cCheckMan"),
                " AND h.cFlag=N'AP' AND h.cVouchType=N'48'"));
            all.Add(N("ar_refund", "Ap_CloseBill.iID", "h.cVouchID", "h.dVouchDate", State(null, "h.cCheckMan"),
                " AND h.cFlag=N'AR' AND h.cVouchType=N'49'"));
            AddQm(all);
            all.Add(N("production_order", "mom_order.MoId", "h.MoCode", "h.CreateDate", MoState, ""));
            all.Add(N("material_out", "rdrecord11.ID", "h.cCode", "h.dDate", State(null, "h.cHandler"), ""));
            all.Add(N("product_in", "rdrecord10.ID", "h.cCode", "h.dDate", State(null, "h.cHandler"), ""));
            all.Add(N("bom", "bom_bom.BomId", BomCode, "h.VersionEffDate", BomState, " AND h.BomType=1"));
            return all.ToArray();
        }

        static void AddQm(List<TraceNode> all)
        {
            string[][] rows = new string[][]
            {
                new string[] { "qm_incoming_inspect", "QMINSPECTVOUCHER", "h.CINSPECTCODE", "QM01" },
                new string[] { "qm_product_inspect", "QMINSPECTVOUCHER", "h.CINSPECTCODE", "QM02" },
                new string[] { "qm_incoming_check", "QMCHECKVOUCHER", "h.CCHECKCODE", "QM03" },
                new string[] { "qm_product_check", "QMCHECKVOUCHER", "h.CCHECKCODE", "QM04" },
                new string[] { "qm_incoming_reject", "QMREJECTVOUCHER", "h.CREJECTCODE", "QM05" },
                new string[] { "qm_product_reject", "QMREJECTVOUCHER", "h.CREJECTCODE", "QM06" },
                new string[] { "qm_other_inspect", "QMINSPECTVOUCHER", "h.CINSPECTCODE", "QM11" },
                new string[] { "qm_other_check", "QMCHECKVOUCHER", "h.CCHECKCODE", "QM15" }
            };
            for (int i = 0; i < rows.Length; i++)
            {
                string[] r = rows[i];
                all.Add(N(r[0], r[1] + ".ID", r[2], "h.DDATE", State(null, "h.CVERIFIER"),
                    " AND h.CVOUCHTYPE=N'" + r[3] + "'"));
            }
        }

        static TraceEdge E(string from, string to, string up, string down, string source, string cond)
        {
            TraceEdge edge = new TraceEdge();
            edge.From = from;
            edge.To = to;
            edge.Up = up;
            edge.Down = down;
            edge.Source = source;
            edge.Cond = cond;
            return edge;
        }

        static TraceEdge[] BuildEdges()
        {
            List<TraceEdge> all = new List<TraceEdge>();
            AddSale(all);
            AddPurchase(all);
            AddMfg(all);
            AddOtherQm(all);
            return all.ToArray();
        }

        // 其他报检 → 其他检验：检验单按 INSPECTAUTOID 挂报检行（不是 SOURCEAUTOID，实测为空）；两者都没有出入库来源。
        static void AddOtherQm(List<TraceEdge> all)
        {
            all.Add(E("qm_other_inspect", "qm_other_check", "u.ID", "d.ID",
                "QMINSPECTVOUCHERS u JOIN QMCHECKVOUCHER d ON d.INSPECTAUTOID=u.AUTOID", " AND d.CVOUCHTYPE=N'QM15'"));
        }

        // 销售：订单 → 发货 → 出库 / 发票 → 收款；退货单挂原发货行（iCorID），没有原发货行时挂订单行。
        // 订单 → 发票只收没有发货行的发票行（先开票）。收款按核销明细：收款单 48 核销专用 / 普通发票 26 / 27。
        static void AddSale(List<TraceEdge> all)
        {
            const string so = "SO_SODetails u JOIN DispatchLists d ON d.iSOsID=u.iSOsID JOIN DispatchList x ON x.DLID=d.DLID";
            all.Add(E("sale_order", "dispatch", "u.ID", "d.DLID", so,
                " AND x.cVouchType=N'05' AND ISNULL(x.bReturnFlag,0)=0"));
            all.Add(E("sale_order", "sale_return", "u.ID", "d.DLID", so,
                " AND x.cVouchType=N'05' AND x.bReturnFlag=1 AND ISNULL(d.iCorID,0)=0"));
            all.Add(E("dispatch", "sale_return", "u.DLID", "d.DLID",
                Dl + " JOIN DispatchLists d ON d.iCorID=u.iDLsID JOIN DispatchList x ON x.DLID=d.DLID",
                Blue + " AND x.cVouchType=N'05' AND x.bReturnFlag=1"));
            // 退货申请单：申请单行 iDLsID 指原蓝字发货单行；退货单行 irtnappid 指申请单行 AutoID，并核对单号 crtnappcode。
            all.Add(E("dispatch", "sale_return_apply", "u.DLID", "d.ID",
                Dl + " JOIN SA_ReturnsApplyDetail d ON d.iDLsID=u.iDLsID", Blue));
            all.Add(E("sale_return_apply", "sale_return", "u.ID", "d.DLID",
                "SA_ReturnsApplyDetail u JOIN SA_ReturnsApplyMain y ON y.ID=u.ID"
                + " JOIN DispatchLists d ON d.irtnappid=u.AutoID AND d.crtnappcode=y.cCode JOIN DispatchList x ON x.DLID=d.DLID",
                " AND x.cVouchType=N'05' AND x.bReturnFlag=1"));
            all.Add(E("dispatch", "sale_out", "u.DLID", "d.ID", Dl + " JOIN rdrecords32 d ON d.iDLsID=u.iDLsID", Blue));
            all.Add(E("sale_return", "sale_out", "u.DLID", "d.ID", Dl + " JOIN rdrecords32 d ON d.iDLsID=u.iDLsID", Red));
            all.Add(E("dispatch", "sale_invoice", "u.DLID", "d.SBVID",
                Dl + " JOIN SaleBillVouchs d ON d.iDLsID=u.iDLsID", Blue));
            all.Add(E("sale_return", "sale_invoice", "u.DLID", "d.SBVID",
                Dl + " JOIN SaleBillVouchs d ON d.iDLsID=u.iDLsID", Red));
            all.Add(E("sale_order", "sale_invoice", "u.ID", "d.SBVID",
                "SO_SODetails u JOIN SaleBillVouchs d ON d.iSOsID=u.iSOsID", " AND ISNULL(d.iDLsID,0)=0"));
            all.Add(E("sale_invoice", "ar_receipt", "u.SBVID", "d.iID",
                "Ar_Detail a JOIN SaleBillVouch u ON u.cSBVCode=a.cCoVouchID AND u.cVouchType=a.cCoVouchType"
                + " JOIN Ap_CloseBill d ON d.cVouchID=a.cVouchID",
                " AND a.cVouchType=N'48' AND a.cCoVouchType IN (N'26', N'27') AND a.cProcStyle=N'9P'"
                + " AND d.cFlag=N'AR' AND d.cVouchType=N'48'"));
        }

        // 采购：请购 → 订单 → 到货 → 来料报检 → 来料检验 → 入库 → 发票 → 付款。入库有检验单的挂检验单，
        // 否则挂到货单，都没有时挂订单行；发票有入库行的挂入库单，否则挂订单行。付款按核销明细：付款单 49 核销发票 01 / 02。
        static void AddPurchase(List<TraceEdge> all)
        {
            const string po = "PO_Podetails u JOIN PU_ArrivalVouchs d ON d.iPOsID=u.ID JOIN PU_ArrivalVouch x ON x.ID=d.ID";
            all.Add(E("purchase_requisition", "purchase_order", "u.ID", "d.POID",
                "PU_AppVouchs u JOIN PO_Podetails d ON d.iAppIds=u.AutoID", ""));
            all.Add(E("purchase_order", "arrival", "u.POID", "d.ID", po, " AND ISNULL(x.iBillType,0)=0"));
            all.Add(E("purchase_order", "purchase_return", "u.POID", "d.ID", po,
                " AND x.iBillType=1 AND ISNULL(d.iCorId,0)=0"));
            all.Add(E("arrival", "purchase_return", "u.ID", "d.ID",
                Arr + " JOIN PU_ArrivalVouchs d ON d.iCorId=u.Autoid JOIN PU_ArrivalVouch x ON x.ID=d.ID",
                ArrBlue + " AND x.iBillType=1"));
            all.Add(E("arrival", "qm_incoming_inspect", "u.ID", "d.ID",
                Arr + " JOIN QMINSPECTVOUCHERS d ON d.SOURCEAUTOID=u.Autoid JOIN QMINSPECTVOUCHER x ON x.ID=d.ID",
                ArrBlue + " AND x.CVOUCHTYPE=N'QM01' AND x.CSOURCE=N'到货单'"));
            all.Add(E("qm_incoming_inspect", "qm_incoming_check", "u.ID", "d.ID",
                "QMINSPECTVOUCHERS u JOIN QMCHECKVOUCHER d ON d.INSPECTAUTOID=u.AUTOID", " AND d.CVOUCHTYPE=N'QM03'"));
            all.Add(E("qm_incoming_check", "qm_incoming_reject", "u.ID", "d.ID",
                "QMCHECKVOUCHER u JOIN QMREJECTVOUCHER d ON d.CHECKID=u.ID",
                " AND u.CVOUCHTYPE=N'QM03' AND d.CVOUCHTYPE=N'QM05'"));
            all.Add(E("qm_incoming_check", "purchase_in", "u.ID", "d.ID",
                "QMCHECKVOUCHER u JOIN rdrecords01 d ON d.iCheckIdBaks=u.ID", " AND u.CVOUCHTYPE=N'QM03'"));
            all.Add(E("arrival", "purchase_in", "u.ID", "d.ID", Arr + " JOIN rdrecords01 d ON d.iArrsId=u.Autoid",
                ArrBlue + " AND ISNULL(d.iCheckIdBaks,0)=0"));
            all.Add(E("purchase_return", "purchase_in", "u.ID", "d.ID", Arr + " JOIN rdrecords01 d ON d.iArrsId=u.Autoid",
                " AND y.iBillType=1"));
            all.Add(E("purchase_order", "purchase_in", "u.POID", "d.ID", "PO_Podetails u JOIN rdrecords01 d ON d.iPOsID=u.ID",
                " AND ISNULL(d.iArrsId,0)=0"));
            all.Add(E("purchase_in", "purchase_invoice", "u.ID", "d.PBVID",
                "rdrecords01 u JOIN PurBillVouchs d ON d.RdsId=u.AutoID", ""));
            all.Add(E("purchase_order", "purchase_invoice", "u.POID", "d.PBVID",
                "PO_Podetails u JOIN PurBillVouchs d ON d.iPOsID=u.ID", " AND ISNULL(d.RdsId,0)=0"));
            all.Add(E("purchase_invoice", "ap_payment", "u.PBVID", "d.iID",
                "Ap_Detail a JOIN PurBillVouch u ON u.cPBVCode=a.cCoVouchID AND u.cPBVBillType=a.cCoVouchType"
                + " JOIN Ap_CloseBill d ON d.cVouchID=a.cVouchID",
                " AND a.cVouchType=N'49' AND a.cCoVouchType IN (N'01', N'02') AND a.cProcStyle=N'9P'"
                + " AND d.cFlag=N'AP' AND d.cVouchType=N'49'"));
        }

        // 生产：物料清单 → 生产订单 → 材料出库（子件 AllocateId）/ 产成品入库（行 MoDId）/ 产品报检 → 产品检验 →
        // 不良品处理 → 产成品入库。检验单 → 入库只收不是参照不良品处理单的入库行。
        static void AddMfg(List<TraceEdge> all)
        {
            all.Add(E("bom", "production_order", "u.BomId", "u.MoId", "mom_orderdetail u", ""));
            all.Add(E("production_order", "material_out", "u.MoId", "d.ID",
                "mom_orderdetail u JOIN mom_moallocate a ON a.MoDId=u.MoDId JOIN rdrecords11 d ON d.iMPoIds=a.AllocateId",
                ""));
            all.Add(E("production_order", "product_in", "u.MoId", "d.ID",
                "mom_orderdetail u JOIN rdrecords10 d ON d.iMPoIds=u.MoDId", ""));
            all.Add(E("production_order", "qm_product_inspect", "u.MoId", "d.ID",
                "mom_orderdetail u JOIN QMINSPECTVOUCHERS d ON d.SOURCEAUTOID=u.MoDId JOIN QMINSPECTVOUCHER x ON x.ID=d.ID",
                " AND x.CVOUCHTYPE=N'QM02' AND x.CSOURCE=N'生产订单'"));
            all.Add(E("qm_product_inspect", "qm_product_check", "u.ID", "d.ID",
                "QMINSPECTVOUCHERS u JOIN QMCHECKVOUCHER d ON d.INSPECTAUTOID=u.AUTOID", " AND d.CVOUCHTYPE=N'QM04'"));
            all.Add(E("qm_product_check", "qm_product_reject", "u.ID", "d.ID",
                "QMCHECKVOUCHER u JOIN QMREJECTVOUCHER d ON d.CHECKID=u.ID",
                " AND u.CVOUCHTYPE=N'QM04' AND d.CVOUCHTYPE=N'QM06'"));
            all.Add(E("qm_product_check", "product_in", "u.ID", "d.ID",
                "QMCHECKVOUCHER u JOIN rdrecords10 d ON d.iCheckIdBaks=u.ID",
                " AND u.CVOUCHTYPE=N'QM04' AND ISNULL(d.iRejectIds,0)=0"));
            all.Add(E("qm_product_reject", "product_in", "u.ID", "d.ID",
                "QMREJECTVOUCHERS u JOIN rdrecords10 d ON d.iRejectIds=u.AUTOID", ""));
        }
    }
}
