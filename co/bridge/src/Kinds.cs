using System.Collections.Generic;

namespace U8Co
{
    internal sealed class VoucherKind
    {
        public string Name;
        public string Title;
        public string Family;
        public string SubId;
        public string HeadTable;
        public string IdColumn;
        public string CodeColumn;
        public string BodyTable;
        public string BodyFk;
        public string VerifierColumn;
        public string VerifyDateColumn;
        public string StType;
        public string BizObjectId;
        public int SaVt;
        public string SaCard;
        public bool Creatable;
        public bool Deletable;
        public bool Workflow;
        public string LineIdColumn;
        public bool Updatable;
        public bool Closable;
        public string GenerateFrom;
        // vouchers/generate 可用的来源类型；第一个是缺省（等于 GenerateFrom）。
        public string[] Sources;
        // 能否走 vouchers/verify。采购发票的审核是采购复核。
        public bool Verifiable;
        // 审核另用的登录子系统，空串表示用 SubId。生产订单审核要 MO。
        public string VerifySub;
    }

    // 表名、列名按 U8 数据库里的写法。rdrecord10/11/32 与到货审核人 cverifier 的大小写照数据库。
    internal static class Kinds
    {
        static readonly VoucherKind[] Table = Build();

        public static VoucherKind Find(string name)
        {
            if (name == null)
            {
                return null;
            }
            for (int i = 0; i < Table.Length; i++)
            {
                if (Table[i].Name == name)
                {
                    return Table[i];
                }
            }
            return null;
        }

        public static IEnumerable<VoucherKind> All()
        {
            return Table;
        }

        static VoucherKind[] Build()
        {
            VoucherKind[] sa = SaKinds();
            VoucherKind[] pu = PuKinds();
            VoucherKind[] st = StKinds();
            VoucherKind[] mo = MoKinds();
            VoucherKind[] qm = QmKinds();
            VoucherKind[] ar = ArKinds();
            // 出入库调整单（存货核算，IaAdjustRead）只读。
            VoucherKind[] ia = new VoucherKind[] { IaAdjustRead.Kind() };
            VoucherKind[] all = new VoucherKind[
                sa.Length + pu.Length + st.Length + ia.Length + mo.Length + qm.Length + ar.Length];
            int n = Copy(all, 0, sa);
            n = Copy(all, n, pu);
            n = Copy(all, n, st);
            n = Copy(all, n, ia);
            n = Copy(all, n, mo);
            n = Copy(all, n, qm);
            Copy(all, n, ar);
            return all;
        }

        static int Copy(VoucherKind[] dest, int at, VoucherKind[] src)
        {
            for (int i = 0; i < src.Length; i++)
            {
                dest[at + i] = src[i];
            }
            return at + src.Length;
        }

        static VoucherKind[] SaKinds()
        {
            VoucherKind sale = Blank("sale_order", "销售订单", "sa", "SA");
            Fill(sale, "SO_SOMain", "ID", "cSOCode", "SO_SODetails", "ID");
            Mark(sale, "cVerifier", "dverifydate");
            sale.SaVt = 12;
            sale.SaCard = "17";
            sale.Creatable = true;
            sale.Deletable = true;
            sale.Updatable = true;
            sale.Closable = true;
            sale.LineIdColumn = "iSOsID";
            VoucherKind dispatch = Blank("dispatch", "发货单(蓝字)", "sa", "SA");
            Fill(dispatch, "DispatchList", "DLID", "cDLCode", "DispatchLists", "DLID");
            Mark(dispatch, "cVerifier", "dverifydate");
            dispatch.SaVt = 9;
            dispatch.SaCard = "01";
            dispatch.Deletable = true;
            // 生单来的单据可改（EditMore 分派，各类的范围见 docs/api-reference.md §8）。
            dispatch.Updatable = true;
            dispatch.LineIdColumn = "iDLsID";
            Source(dispatch, "sale_order");
            // 无来源新增（SrcLess；SA.bMustSO_ptxs 打开时 409）。
            dispatch.Creatable = true;
            // 存货调价单（InvPriceAdjustRead）只读；退货申请单（ReturnsApplyRead，K12 起可写，ReturnsApply）。
            return new VoucherKind[] { sale, dispatch, SaleInvoice(), SaleReturnKind(), InvPriceAdjustRead.Kind(),
                ReturnsApplyRead.Kind() };
        }

        // 退货单（红字发货单）：同发货单表，bReturnFlag=1；VT 10、卡片 03。参照蓝字发货单生成（SaleGen.Return）。
        static VoucherKind SaleReturnKind()
        {
            VoucherKind kind = Blank("sale_return", "退货单(红字发货单)", "sa", "SA");
            Fill(kind, "DispatchList", "DLID", "cDLCode", "DispatchLists", "DLID");
            Mark(kind, "cVerifier", "dverifydate");
            kind.SaVt = 10;
            kind.SaCard = "03";
            kind.Deletable = true;
            kind.Updatable = true;
            kind.LineIdColumn = "iDLsID";
            // K12：另可参照已审核的退货申请单（SaleGenApplyRet，source_line_id 是申请单行 AutoID）。
            Source(kind, "dispatch", ReturnsApplyRead.KindName);
            return kind;
        }

        // 专票默认 VT 0 / 卡片 07。运行时按表头 cVouchType：26 用这组，27 用 VT 2 / 卡片 13。
        static VoucherKind SaleInvoice()
        {
            VoucherKind kind = Blank("sale_invoice", "销售发票", "sa", "SA");
            Fill(kind, "SaleBillVouch", "SBVID", "cSBVCode", "SaleBillVouchs", "SBVID");
            Mark(kind, "cChecker", "dverifydate");
            kind.SaVt = 0;
            kind.SaCard = "07";
            kind.LineIdColumn = "AutoID";
            kind.Deletable = true;
            kind.Updatable = true;
            // 参照退货单生成红字发票（SaleGenRed）；参照已复核的蓝字发票红冲（SaleGenBlueRed）。
            Source(kind, "dispatch", "sale_return", "sale_invoice");
            // 先开票新增（SrcLess，idisp=0，U8 生成发货单；SA.bMustSO_ptxs 打开时 409）。
            kind.Creatable = true;
            return kind;
        }

        static VoucherKind[] PuKinds()
        {
            VoucherKind order = Blank("purchase_order", "采购订单", "pu", "PU");
            Fill(order, "PO_Pomain", "POID", "cPOID", "PO_Podetails", "POID");
            Mark(order, "cVerifier", "cAuditDate");
            order.Creatable = true;
            order.Deletable = true;
            order.Updatable = true;
            order.Closable = true;
            order.LineIdColumn = "ID";
            VoucherKind arrival = Blank("arrival", "到货单", "pu", "PU");
            Fill(arrival, "PU_ArrivalVouch", "ID", "cCode", "PU_ArrivalVouchs", "ID");
            Mark(arrival, "cverifier", "cAuditDate");
            arrival.Deletable = true;
            arrival.Updatable = true;
            // 关闭 / 打开（PuArrClose）。
            arrival.Closable = true;
            arrival.LineIdColumn = "Autoid";
            Source(arrival, "purchase_order");
            // 无来源新增（SrcLess；PU.bPTHavePO 打开时 409）。
            arrival.Creatable = true;
            // 采购结算单（PuSettleRead）：读取，参照采购发票自动结算（PuSettleGen），删除（PuSettleDel）；手工结算（PuSettleMan）。
            return new VoucherKind[]
            {
                order, arrival, PurchaseInvoice(), PurchaseReturn(), PurchaseRequisition(), PuSettleRead.Kind()
            };
        }

        // 请购单（PuApp*.cs）：CO 读取、新增、修改、删除、审核、整单关闭。表体主键 AutoID，外键 ID。
        static VoucherKind PurchaseRequisition()
        {
            VoucherKind kind = Blank("purchase_requisition", "请购单", "pu", "PU");
            Fill(kind, "PU_AppVouch", "ID", "cCode", "PU_AppVouchs", "ID");
            Mark(kind, "cVerifier", "cAuditDate");
            kind.LineIdColumn = "AutoID";
            kind.Creatable = true;
            kind.Deletable = true;
            kind.Updatable = true;
            kind.Closable = true;
            return kind;
        }

        // 采购退货单（红字到货单）：同到货单的表，iBillType=1。参照原蓝字到货单行（缺省）或采购订单行生成（PuRet）。
        static VoucherKind PurchaseReturn()
        {
            VoucherKind kind = Blank("purchase_return", "采购退货单", "pu", "PU");
            Fill(kind, "PU_ArrivalVouch", "ID", "cCode", "PU_ArrivalVouchs", "ID");
            Mark(kind, "cverifier", "cAuditDate");
            kind.Deletable = true;
            kind.Updatable = true;
            kind.LineIdColumn = "Autoid";
            Source(kind, "arrival", "purchase_order");
            return kind;
        }

        // 读取走 SQL；参照采购入库生成、删除、复核走 PU 模板 vt 4，sBillType 按 cPBVBillType 用 purbill / ppurbill（PuInv）。
        static VoucherKind PurchaseInvoice()
        {
            VoucherKind kind = Blank("purchase_invoice", "采购发票", "pu", "PU");
            Fill(kind, "PurBillVouch", "PBVID", "cPBVCode", "PurBillVouchs", "PBVID");
            Mark(kind, "cVerifier", "cAuditDate");
            kind.LineIdColumn = "ID";
            // 审核 = 采购复核（cVerifier），见 PuInvReview；应付审核（cPBVVerifier）是 arap_verify，见 ArapAudit。
            kind.Verifiable = true;
            kind.Deletable = true;
            kind.Updatable = true;
            Source(kind, "purchase_in");
            return kind;
        }

        static VoucherKind[] StKinds()
        {
            return new VoucherKind[]
            {
                St("purchase_in", "采购入库单", "RdRecord01", "rdrecords01", "01", false),
                St("other_in", "其他入库单", "RdRecord08", "rdrecords08", "08", true),
                St("other_out", "其他出库单", "RdRecord09", "rdrecords09", "09", true),
                // 库存期初结存单（StockOpening：新增走 EAI storeqc、不能修改，单据日期固定为库存启用日前一天，
                // 存货核算期初记账后只能审核）。
                St(StockOpening.KindName, "期初结存", "rdrecord34", "rdrecords34", StockOpening.StType, true),
                St("product_in", "产成品入库单", "rdrecord10", "rdrecords10", "10", false),
                St("material_out", "材料出库单", "rdrecord11", "rdrecords11", "11", false),
                St("sale_out", "销售出库单", "rdrecord32", "rdrecords32", "32", false),
                TransferKind(),
                // 形态转换单、调拨申请单、盘点单（StockMisc*.cs、StockCheck*.cs）。
                StockMiscKind("shape_change", "形态转换单", "15"),
                StockMiscKind("transfer_request", "调拨申请单", "62"),
                StockMiscKind("stock_check", "盘点单", "18"),
                // 货位调整单（PositionAdjust*.cs、StockDomAdjust.cs）。
                PositionAdjustKind()
            };
        }

        // 货位调整单（短码 19，卡片 0313）：同一仓库内货位之间移库，审核时 U8 写货位台账 InvPosition（每行一出一入）。
        // 主键 Id、表体 autoID 都不是自增；审核人 chandler / dVeriDate。不开修改（PositionAdjust.PreLogin）。
        static VoucherKind PositionAdjustKind()
        {
            VoucherKind kind = Blank(PositionAdjust.KindName, "货位调整单", "st", "ST");
            Fill(kind, "AdjustPVouch", "Id", "cVouchCode", "AdjustPVouchs", "ID");
            Mark(kind, "chandler", "dVeriDate");
            kind.StType = PositionAdjust.StType;
            kind.LineIdColumn = "autoID";
            kind.Creatable = true;
            kind.Deletable = true;
            return kind;
        }

        // 同调拨单走 USERPCO.VoucherCO。形态转换单与组装、拆卸同表（AssemVouch.cVouchType='15'）；
        // 盘点单的审核人是 cAccounter / dveridate，不开修改。
        static VoucherKind StockMiscKind(string name, string title, string stType)
        {
            VoucherKind kind = Blank(name, title, "st", "ST");
            if (stType == "15")
            {
                Fill(kind, "AssemVouch", "ID", "cAVCode", "AssemVouchs", "ID");
            }
            else if (stType == "62")
            {
                Fill(kind, "ST_AppTransVouch", "ID", "cTVCode", "ST_AppTransVouchs", "ID");
            }
            else
            {
                Fill(kind, "CheckVouch", "ID", "cCVCode", "CheckVouchs", "ID");
            }
            if (stType == "18")
            {
                Mark(kind, "cAccounter", "dveridate");
            }
            else
            {
                Mark(kind, "cVerifyPerson", "dVerifyDate");
            }
            kind.StType = stType;
            kind.LineIdColumn = "autoID";
            kind.Creatable = true;
            kind.Deletable = true;
            kind.Updatable = stType != "18";
            // 盘点单审核暂不支持：实测 USERPCO.Verify("18") 9 参、12 参都回「类型不匹配生单时出错」（StockMisc.NoCheckVerify）。
            kind.Verifiable = stType != "18";
            return kind;
        }

        static VoucherKind[] MoKinds()
        {
            return new VoucherKind[] { ProductionOrder(), Bom() };
        }

        // 读取走 SQL，登录用 SA；审核走 U8API（MoApi），登录用 MO。审核人在行上（RelsUser / RelsDate），不走 HeadRow。
        static VoucherKind ProductionOrder()
        {
            VoucherKind kind = Blank("production_order", "生产订单", "mo", "SA");
            Fill(kind, "mom_order", "MoId", "MoCode", "mom_orderdetail", "MoId");
            Mark(kind, "RelsUser", "RelsDate");
            kind.LineIdColumn = "MoDId";
            kind.VerifySub = "MO";
            // 关闭 / 打开走 Usp_MO_Close / Usp_MO_UnClose（MoClose），纯 SQL，登录子系统仍是 SA。
            kind.Closable = true;
            // 新增 / 删除走 U8API MOrderAdd / MOrderDelete（MoCreate / MoDelete），登录子系统同审核用 MO（RequestsP4.CheckKind）。
            // 修改走 MOrderLoad + MOrderUpdate（MoUpdate）：MOrderUpdate 会清空并重写全部子件，桥把加载出的子件全部重送并回读核对。
            kind.Creatable = true;
            kind.Deletable = true;
            kind.Updatable = true;
            return kind;
        }

        // 物料清单（只收标准 BOM，BomType=1）：读取、列表走 SQL，登录用 SA；新增、修改、删除、审核走 U8API（BomRoutes），
        // 登录用 BO。表头没有单号列，响应的 code 是母件存货编码（bom_parent → bas_part），另给 version。
        static VoucherKind Bom()
        {
            VoucherKind kind = Blank(BomRoutes.KindName, "物料清单", "mo", "SA");
            Fill(kind, "bom_bom", "BomId", "", "bom_opcomponent", "BomId");
            Mark(kind, "RelsUser", "RelsDate");
            kind.LineIdColumn = "OpComponentId";
            kind.VerifySub = "BO";
            kind.Creatable = true;
            kind.Deletable = true;
            kind.Updatable = true;
            return kind;
        }

        // 视图 transm / TransD，卡片 0304。审核人是 cVerifyPerson，不是 cHandler。
        static VoucherKind TransferKind()
        {
            VoucherKind kind = Blank("transfer", "调拨单", "st", "ST");
            Fill(kind, "TransVouch", "ID", "cTVCode", "TransVouchs", "ID");
            Mark(kind, "cVerifyPerson", "dVerifyDate");
            kind.StType = "12";
            kind.LineIdColumn = "autoID";
            kind.Creatable = true;
            kind.Deletable = true;
            kind.Updatable = true;
            // 参照调拨申请单生成（StockGenTr，StockMisc.Try 分派）。
            Source(kind, "transfer_request");
            return kind;
        }

        static VoucherKind[] QmKinds()
        {
            VoucherKind[] qm = new VoucherKind[]
            {
                Inspect("qm_incoming_inspect", "来料报检单", "QM01"),
                Inspect("qm_product_inspect", "产品报检单", "QM02"),
                Qm("qm_incoming_check", "来料检验单", "QMCHECKVOUCHER", "CCHECKCODE", "QMCHECKVOUCHERS", "QM03"),
                Qm("qm_product_check", "产品检验单", "QMCHECKVOUCHER", "CCHECKCODE", "QMCHECKVOUCHERS", "QM04"),
                Qm("qm_incoming_reject", "来料不良品处理单", "QMREJECTVOUCHER", "CREJECTCODE", "QMREJECTVOUCHERS", "QM05"),
                Qm("qm_product_reject", "产品不良品处理单", "QMREJECTVOUCHER", "CREJECTCODE", "QMREJECTVOUCHERS", "QM06"),
                // 其他报检单（QM11）、其他检验单（QM15），无来源、没有审批流（写入见下）。
                Inspect("qm_other_inspect", "其他报检单", "QM11"),
                OtherCheck()
            };
            // 报检单、检验单可生单、删除（QmGen / QmDelete，登录子系统 QM）。报检单不开放单独审核、弃审：U8 的 confirm
            // 对报检单总是失败（QMINSPECTVOUCHER 没有 IVERIFYSTATE 列），只在保存时自动审核；删除时 QmDelete 先弃审。
            QmWrite(qm[0], false, "arrival");
            QmWrite(qm[1], false, "production_order");
            QmWrite(qm[2], false, "qm_incoming_inspect");
            QmWrite(qm[3], false, "qm_product_inspect");
            // 不良品处理单参照检验单生单、直接审核 / 弃审（受审批流控制的单据仍走 workflow/*）、删除（QmRejOps，登录子系统 QM）。
            QmWrite(qm[4], true, "qm_incoming_check");
            QmWrite(qm[5], true, "qm_product_check");
            // 其他报检单无来源新增（vouchers/create）、审核 / 弃审（VO 的保存不自动审核，桥补审核，失败时可经接口补审）、删除；
            // 其他检验单参照其他报检单生单、直接审核 / 弃审、删除（QmOthOps，VO 接口，登录子系统 QM）。
            qm[6].Creatable = true;
            qm[6].Deletable = true;
            qm[6].Verifiable = true;
            qm[6].VerifySub = QmCo.LoginSub;
            QmWrite(qm[7], true, "qm_other_inspect");
            // 来料 / 产品检验单、其他报检单、其他检验单可修改（vouchers/update，QmEdit，只收表头）。
            qm[2].Updatable = true;
            qm[3].Updatable = true;
            qm[6].Updatable = true;
            qm[7].Updatable = true;
            // 产品报检单可单独弃审（只收 unverify，QmInsUnverify；审核仍 400）。不开放修改：
            // VoucherOperate("update") 报「表体行数必须大于0行！」或「更新发退货报检单失败！」。
            qm[1].Verifiable = true;
            qm[1].VerifySub = QmCo.LoginSub;
            return qm;
        }

        // 生单不是 vouchers/create：Creatable 仍为假（meta 的 generate 按 Sources 给出）。检验单照旧不直接审核（走 workflow/*）。
        static void QmWrite(VoucherKind kind, bool verifiable, string source)
        {
            kind.Deletable = true;
            kind.Verifiable = verifiable;
            kind.VerifySub = verifiable ? QmCo.LoginSub : "";
            Source(kind, source);
        }

        static VoucherKind St(string name, string title, string head, string body, string stType, bool write)
        {
            VoucherKind kind = Blank(name, title, "st", "ST");
            Fill(kind, head, "ID", "cCode", body, "ID");
            Mark(kind, "cHandler", "dVeriDate");
            kind.StType = stType;
            kind.LineIdColumn = "AutoID";
            kind.Creatable = write;
            kind.Deletable = write;
            if (name == "purchase_in")
            {
                kind.Deletable = true;
                // 无来源新增、修改（cSource=库存，StockDomPurIn / StockPurIn）。生单、删除、审核不变。
                kind.Creatable = true;
                kind.Updatable = true;
                // 采购退货单（红字到货单）→ 红字采购入库（StockGenRed）；蓝字到货单 → 采购入库（StockGenArr）。
                Source(kind, "purchase_order", "qm_incoming_check", "purchase_return", StockGen.ArrSourceName);
            }
            else if (name == "sale_out")
            {
                kind.Deletable = true;
                kind.Updatable = true;
                // 无来源新增（cSource=库存，StockSaleOut；销售管理已启用的账套 409）。生单仍参照发货单。
                kind.Creatable = true;
                Source(kind, "dispatch");
            }
            else if (name == "other_in" || name == "other_out")
            {
                kind.Updatable = true;
            }
            else
            {
                MfgSource(kind);
            }
            return kind;
        }

        // 材料出库参照生产订单、产成品入库参照产品检验单或产品不良品处理单生成，可删（MfgGen、MfgGenRej）。
        static void MfgSource(VoucherKind kind)
        {
            if (kind.Name == "material_out")
            {
                kind.Deletable = true;
                kind.Updatable = true;
                // 无来源新增（SrcLess，csource=库存；ST.ballowAddnewVouch「领料必有订单」为 true 时 409）。
                kind.Creatable = true;
                Source(kind, "production_order");
            }
            else if (kind.Name == "product_in")
            {
                kind.Deletable = true;
                kind.Updatable = true;
                // 另可参照生产订单（MfgGenMo，id 是 MoId，source_line_id 是 MoDId）。
                Source(kind, "qm_product_check", "qm_product_reject", "production_order");
            }
        }

        // 第一个来源是缺省（请求不带 source_type 时用它）。
        static void Source(VoucherKind kind, params string[] sources)
        {
            kind.GenerateFrom = sources[0];
            kind.Sources = sources;
        }

        // 收付款单：Ap_CloseBill 按 cFlag / cVouchType（48、49，另有 AP48、AR49 退款）分；应收应付单：Ap_Vouch（R0、P0），表体按 cLink 挂。
        static VoucherKind[] ArKinds()
        {
            return new VoucherKind[]
            {
                Ar("ar_receipt", "收款单", "AR", true),
                Ar("ap_payment", "付款单", "AP", true),
                Ar("ar_bill", "应收单", "AR", false),
                Ar("ap_bill", "应付单", "AP", false),
                // 供应商退款（AP48，应付的收款单，从供应商收回货款）、客户退款（AR49，应收的付款单，把货款退给客户）。
                Ar("ap_refund", "供应商退款", "AP", true),
                Ar("ar_refund", "客户退款", "AR", true)
            };
        }

        static VoucherKind Ar(string name, string title, string sub, bool closeBill)
        {
            VoucherKind kind = Blank(name, title, "ar", sub);
            if (closeBill)
            {
                Fill(kind, "Ap_CloseBill", "iID", "cVouchID", "Ap_CloseBills", "iID");
                kind.LineIdColumn = "ID";
            }
            else
            {
                Fill(kind, "Ap_Vouch", "Auto_ID", "cVouchID", "Ap_Vouchs", "cLink");
                kind.LineIdColumn = "Auto_ID";
            }
            Mark(kind, "cCheckMan", "dverifydate");
            kind.Creatable = true;
            kind.Deletable = true;
            kind.Updatable = true;
            return kind;
        }

        static VoucherKind Qm(string name, string title, string head, string code, string body, string biz)
        {
            VoucherKind kind = Blank(name, title, "qm", "SA");
            Fill(kind, head, "ID", code, body, "ID");
            Mark(kind, "CVERIFIER", "DVERIFYDATE");
            kind.BizObjectId = biz;
            kind.Workflow = true;
            // 检验单只走 workflow/*：vouchers/verify 400「该单据类型不支持直接审核」，meta 的 verify 为 false（不良品处理单见 QmKinds）。
            kind.Verifiable = false;
            return kind;
        }

        // 其他检验单（QM15）：同检验单表，IsWfControlled 恒为 0，不接审批流（workflow/* 400），读取不附 wf。
        static VoucherKind OtherCheck()
        {
            VoucherKind kind = Qm("qm_other_check", "其他检验单", "QMCHECKVOUCHER", "CCHECKCODE", "QMCHECKVOUCHERS", "QM15");
            kind.Workflow = false;
            return kind;
        }

        // 报检单（QM01 / QM02，QmInspect）：读取和列表走 SQL，没有审批流；生单、删除、审核见 QmKinds 的 QmWrite。
        // BizObjectId 同检验单，存表头 CVOUCHTYPE。行主键 AUTOID，外键 ID。
        static VoucherKind Inspect(string name, string title, string vouchType)
        {
            VoucherKind kind = Blank(name, title, "qm", "SA");
            Fill(kind, "QMINSPECTVOUCHER", "ID", "CINSPECTCODE", "QMINSPECTVOUCHERS", "ID");
            Mark(kind, "CVERIFIER", "DVERIFYDATE");
            kind.BizObjectId = vouchType;
            kind.LineIdColumn = "AUTOID";
            kind.Verifiable = false;
            return kind;
        }

        static VoucherKind Blank(string name, string title, string family, string subId)
        {
            VoucherKind kind = new VoucherKind();
            kind.Name = name;
            kind.Title = title;
            kind.Family = family;
            kind.SubId = subId;
            kind.StType = "";
            kind.BizObjectId = "";
            kind.SaCard = "";
            kind.LineIdColumn = "";
            kind.GenerateFrom = "";
            kind.Sources = new string[0];
            kind.Verifiable = true;
            kind.VerifySub = "";
            return kind;
        }

        static void Fill(VoucherKind kind, string head, string id, string code, string body, string fk)
        {
            kind.HeadTable = head;
            kind.IdColumn = id;
            kind.CodeColumn = code;
            kind.BodyTable = body;
            kind.BodyFk = fk;
        }

        static void Mark(VoucherKind kind, string verifier, string verifiedAt)
        {
            kind.VerifierColumn = verifier;
            kind.VerifyDateColumn = verifiedAt;
        }
    }
}
