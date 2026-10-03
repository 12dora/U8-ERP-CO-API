using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 签名自检的一行：桥对某个 ProgID 的某个成员怎么调用（实参个数、按引用的下标）。
    // Member 为空表示只要求 ProgID 已注册（ADO、MSXML 这类系统组件）。
    // OptionalTail：类型库里 ArgCount 之后还有几个可选参数没传；-1 表示该组件没有注册类型库、不核对（只作提示，运行时以 LoadTypeLibEx 读到的为准）。
    // PreferMeasured：按引用下标以实测为准，类型库不一致只记提示，不算不匹配（见 docs/u8-notes.md §3）。
    internal sealed class SigNeed
    {
        public string Feature;
        public string Routes;
        public string ProgId;
        public string Member;
        public string Invoke;
        public int ArgCount;
        public int[] ByRef;
        public int OptionalTail;
        public bool PreferMeasured;
    }

    // 期望签名表。与各 CO 调用点同步维护：改了实参个数或 by-ref 下标，这里一起改。
    // 数字来自代码里的调用点，以及运行时读到的注册类型库（PU CO、USERPCO、UFAPBO、TransSrv）。
    internal static class SigTable
    {
        const string V = "/u8co/v1/vouchers/";
        const string All = "*";
        const string SaRoutes = "/u8co/v1/sale-orders/verify,/u8co/v1/dispatches/verify," + V + "load," + V + "verify,"
            + V + "create," + V + "update," + V + "delete," + V + "close," + V + "generate," + V + "lock";
        const string PuRoutes = V + "load," + V + "verify," + V + "create," + V + "update," + V + "delete,"
            + V + "close," + V + "generate";
        const string StRoutes = V + "load," + V + "verify," + V + "create," + V + "update," + V + "delete," + V + "generate";
        const string ArRoutes = V + "load," + V + "verify," + V + "create," + V + "update," + V + "delete";
        // 收款单审核组件另被坏账收回（审核）和取消坏账收回（弃审）用（ArapBadRecover / ArapProcCancelBad）。
        const string ArSignRoutes = ArRoutes + ",/u8co/v1/arap/bad_debt,/u8co/v1/arap/process/cancel";
        const string SaleVerify = "/u8co/v1/sale-orders/verify,/u8co/v1/dispatches/verify," + V + "verify";
        const string SaleEdit = V + "create," + V + "update," + V + "generate";
        const string StNumber = V + "create," + V + "generate";
        // 期间损益结转、自定义转账（GlTransfer）同样经 U8PzInsert.clsPZInsert 保存凭证。
        const string GlWrite = "/u8co/v1/gl/vouchers/create,/u8co/v1/gl/vouchers/update,/u8co/v1/gl/transfer/pnl,"
            + "/u8co/v1/gl/transfer/custom";
        const string ArcWrite = "/u8co/v1/archives/create,/u8co/v1/archives/update,/u8co/v1/archives/delete";
        const string WfAll = "/u8co/v1/workflow/submit,/u8co/v1/workflow/withdraw,/u8co/v1/workflow/approve,"
            + "/u8co/v1/workflow/disagree,/u8co/v1/workflow/return,/u8co/v1/workflow/abandon,/u8co/v1/workflow/resubmit";
        const string WfAudit = "/u8co/v1/workflow/approve,/u8co/v1/workflow/disagree,/u8co/v1/workflow/return";
        const string WfTxn = "/u8co/v1/workflow/submit,/u8co/v1/workflow/withdraw";
        const string Proxy = "UFIDA.U8.Audit.ServiceProxy.AuditServiceProxy";
        const string Broker = "UFIDA.U8.U8APIFramework.U8ApiComBroker";
        const string SaCo = "VoucherCO_Sa.ClsVoucherCO_SA";
        const string SaSys = "USSAServer.clsSystem";
        const string PuCo = "VoucherCO_PU.clsVoucherCO_PU";
        const string StCo = "USERPCO.VoucherCO";
        const string ApAcc = "UFAPBO.clsAccount_AP";
        const string ApClose = "UFAPBO.clsCloseBill";
        const string ApVouch = "UFAPBO.clsAPVouch";
        const string Login = "U8Login.clsLogin";

        public static SigNeed[] Build()
        {
            List<SigNeed> all = new List<SigNeed>();
            all.AddRange(Base());
            all.AddRange(Sales());
            all.AddRange(Purchase());
            all.AddRange(Stock());
            all.AddRange(Arap());
            all.AddRange(Writes());
            all.AddRange(Flow());
            all.AddRange(ProductionOrder());
            all.AddRange(BomApi());
            // 质量单据新增、删除（QmCo；报检单删除前先弃审）。
            all.AddRange(Quality());
            // 不良品处理单生单、审核、弃审、删除（QmRejCo）。
            all.AddRange(QualityReject());
            // 其他报检单新增、删除，其他检验单生单、审核、弃审、删除（QmOthSpec，同族 VO 接口）。
            all.AddRange(QualityOther());
            // 采购发票应付审核、销售发票应收审核及弃审（ArapAudit）。
            all.AddRange(ArapAuditSigs());
            // 应收 / 应付核销（ArapWriteoff）。
            all.AddRange(WriteoffSigs());
            // 应收 / 应付制单（ArapVoucher）：凭证导入同总账新增。
            all.AddRange(ArapVoucherSigs());
            // 票据登记、删除（NotesReg）不再调用 NoteManageAR：NoteSave 无界面时在桥里挂起（测试账套实测），
            // 票据行由桥照 U8 写 AP_Note，收款单仍走上面的 UFAPBO clsCloseBill，没有另外的签名要核对。
            return all.ToArray();
        }

        // 所有需要登录或读库的路由
        static SigNeed[] Base()
        {
            return new SigNeed[]
            {
                Need("ado", All, "ADODB.Connection", "", "create 0"),
                Need("ado", All, "ADODB.Command", "", "create 0"),
                Need("ado", All, "ADODB.Recordset", "", "create 0"),
                Need("msxml", All, "MSXML2.DOMDocument", "", "create 0"),
                Need("login", All, Login, "Login", "call 8"),
                Need("login", All, Login, "ShutDown", "call 0"),
                Need("login", All, Login, "LogState", "get 0"),
                Need("login", All, Login, "cUserName", "get 0"),
                Need("login", All, Login, "ShareString", "get 0"),
                Need("login", All, Login, "UfDbName", "get 0"),
                Need("login", All, Login, "userToken", "get 0"),
            };
        }

        // 销售
        static SigNeed[] Sales()
        {
            return new SigNeed[]
            {
                Need("sa", SaRoutes, SaSys, "Init", "call 1"),
                Need("sa", SaRoutes, SaSys, "INIMySAInfor", "call 0"),
                Need("sa", SaRoutes, SaSys, "bManualTrans", "set 1"),
                Need("sa", SaRoutes, SaSys, "CloseSys", "call 0"),
                Need("sa", SaRoutes, SaSys, "getDefaltVTID", "callref 5 ref=0,1,2,3,4"),
                Need("sa", SaRoutes, SaCo, "Init", "call 5"),
                Need("sa", SaRoutes, SaCo, "GetVoucherData", "callref 6 ref=0,1,2,3,4,5"),
                Need("sa", SaRoutes, SaCo, "GetDefaultVoucherDom", "callref 4 ref=0,1,2,3"),
                Need("sa", SaRoutes, SaCo, "Save", "callref 4 ref=3"),
                Need("sa", SaRoutes, SaCo, "GetVoucherNO", "callref 3 ref=0,1,2"),
                Need("sa", SaRoutes, SaCo, "GetVoucherTrueNO", "callref 3 ref=0,1,2"),
                Need("sa", SaRoutes, SaCo, "Delete", "callref 2 ref=0,1"),
                // 退货单参照蓝字发货单（SaleReturnLine.NegaDoms）。
                Need("sa", V + "generate", SaCo, "GetNegaVouchData", "callref 6 ref=0,1,2,3,4,5"),
                Need("sa", SaleVerify, SaCo, "VerifyVouch", "call 2 measured"),
                Need("sa-invoice", V + "verify", SaCo, "VerifyVouch", "callref 2 ref=0,1 measured"),
                Need("sa", V + "close", SaCo, "OrderClose", "callref 2 ref=0,1"),
                Need("sa", V + "close", SaCo, "OrderClose", "callref 3 ref=0,1,2"),
                Need("sa", SaleEdit, SaCo, "BodyCheck", "call 4 measured"),
                // 销售订单整单锁定 / 解锁（VoucherLock）：实测只有 DOM 按引用；第三个可选参数 iSID（按行）不传。
                Need("sa", V + "lock", SaCo, "LockVouch", "callref 2 ref=0 measured"),
            };
        }

        // 采购
        static SigNeed[] Purchase()
        {
            return new SigNeed[]
            {
                Need("pu", PuRoutes, "Info_PU.ClsS_Infor", "Init", "callref 3 ref=0"),
                Need("pu", PuRoutes, PuCo, "Init", "callref 10 ref=1,2,3,4,5,6,7 tail=0"),
                Need("pu", PuRoutes, PuCo, "InitBySysInfo", "callref 8 ref=1,2,3,4,5,6,7 tail=0"),
                Need("pu", PuRoutes, PuCo, "bOutTrans", "set 1 tail=0"),
                Need("pu", PuRoutes, PuCo, "SetVerifyMode", "call 1 tail=0"),
                Need("pu", PuRoutes, PuCo, "GetVoucherDataById", "callref 5 ref=0,1,4 tail=0"),
                Need("pu", PuRoutes, PuCo, "GetVoucherNO", "callref 4 ref=0,1,2,3 tail=5"),
                Need("pu", PuRoutes, PuCo, "VoucherSave2", "callref 4 ref=3 tail=2"),
                Need("pu", PuRoutes, PuCo, "Delete", "callref 2 ref=0,1 tail=1 measured"),
                Need("pu", V + "verify", PuCo, "ConfirmPO", "callref 3 ref=1 tail=0"),
                Need("pu", V + "verify", PuCo, "CancelconfirmPO", "call 2 tail=0"),
                Need("pu", V + "verify", PuCo, "ConfirmArr", "call 1 tail=0"),
                Need("pu", V + "verify", PuCo, "CancelconfirmArr", "call 1 tail=0"),
                // 采购发票复核 / 取消复核（PuInvReview）。
                Need("pu", V + "verify", PuCo, "ConfirmBill", "callref 2 ref=1 tail=0"),
                Need("pu", V + "verify", PuCo, "CancelconfirmBill", "call 1 tail=0"),
                Need("pu", V + "close", PuCo, "ClosePOItems", "callref 4 ref=2,3 tail=0"),
                Need("pu", V + "close", PuCo, "OpenPOItems", "callref 4 ref=2,3 tail=0"),
                // 到货单关闭 / 打开（PuArrClose）：strErr 按引用，bleCloseAll 按值（类型库）。
                Need("pu", V + "close", PuCo, "CloseArrItems", "callref 4 ref=2 tail=0"),
                Need("pu", V + "close", PuCo, "OpenArrItems", "callref 4 ref=2 tail=0"),
                Need("pu", V + "create", PuCo, "GetDefaultVTID", "call 2 tail=0 measured"),
                // 请购单审核 / 弃审、整单关闭 / 打开（PuApp）。
                Need("pu", V + "verify", PuCo, "ConfirmApp", "callref 2 ref=1 tail=0"),
                Need("pu", V + "verify", PuCo, "CancelconfirmApp", "call 2 tail=0"),
                Need("pu", V + "close", PuCo, "CloseApp", "call 1 tail=0"),
                Need("pu", V + "close", PuCo, "OpenApp", "call 1 tail=0"),
                // 采购结算（PuSettleGen）：CheckSettle 只传 sErr；bRdBVAutoSettle 末尾两个 OUT 串按值传空串（实测）。
                // 手工结算（PuSettleMan）同样先调 CheckSettle。
                Need("pu", V + "generate," + V + "create", PuCo, "CheckSettle", "callref 1 ref=0 tail=1 measured"),
                Need("pu", V + "generate", PuCo, "bRdBVAutoSettle", "call 4 tail=0 measured"),
            };
        }

        // 库存与生产领料入库
        static SigNeed[] Stock()
        {
            return new SigNeed[]
            {
                Need("st", StRoutes, StCo, "IniLogin", "callref 2 ref=1 tail=0"),
                Need("st", StRoutes, StCo, "Load", "callref 8 ref=2,3,4,5 tail=0"),
                Need("st", StRoutes, StCo, "Verify", "callref 9 ref=2,3,4,5,6,7,8 tail=3"),
                Need("st", StRoutes, StCo, "UnVerify", "callref 9 ref=2,3,4,5,6,7,8 tail=0"),
                Need("st", StRoutes, StCo, "Delete", "callref 9 ref=2,3,4,5,6,7,8 tail=0"),
                Need("st", V + "delete", StCo, "ClearPosition", "callref 4 ref=1,2,3 measured"),
                Need("st", StRoutes, StCo, "Insert", "callref 13 ref=4,5,6,7,8,9 tail=0"),
                Need("st", StRoutes, StCo, "Update", "callref 12 ref=4,5,6,7,8 tail=0"),
                Need("st", V + "verify", StCo, "Verify", "callref 12 ref=2,3,4,5,6,7,8,10,11 tail=0 measured"),
                Need("st", V + "generate", StCo, "MakeOutVouch", "callref 6 ref=1,2,3,4,5 tail=1"),
                Need("st", V + "generate", StCo, "ClearPosition", "callref 4 ref=1,2,3 measured"),
                Need("st", StNumber, "USCOMMON.PublicSub", "GetBillRuleXml", "call 2"),
                Need("st", StNumber, "UFBillComponent.clsBillComponent", "InitBill", "call 2"),
                Need("st", StNumber, "UFBillComponent.clsBillComponent", "GetNumber", "call 2"),
                Need("st", V + "verify", "Scripting.Dictionary", "", "create 0"),
            };
        }

        // 应收应付
        static SigNeed[] Arap()
        {
            return new SigNeed[]
            {
                Need("arap", ArRoutes, ApAcc, "Init", "call 2 tail=0"),
                Need("arap", ArRoutes, ApClose, "Init", "callref 4 ref=0,1,2,3 tail=0"),
                Need("arap", ArRoutes, ApVouch, "Init", "callref 3 ref=0,1,2 tail=0"),
                Need("arap", ArRoutes, ApClose, "GetVouchData", "callref 5 ref=1,2,3,4 tail=0"),
                Need("arap", ArRoutes, ApVouch, "GetVouchData", "callref 5 ref=1,2,3,4 tail=0"),
                Need("arap", ArRoutes, ApClose, "SaveVouch", "callref 4 ref=0,1,2 tail=0"),
                Need("arap", ArRoutes, ApVouch, "SaveVouch", "callref 4 ref=2 tail=0"),
                Need("arap", ArSignRoutes, ApClose, "Sign", "callref 2 ref=1 tail=0"),
                Need("arap", ArRoutes, ApVouch, "Sign", "callref 2 ref=1 tail=0"),
                Need("arap", ArSignRoutes, ApClose, "CancelSign", "callref 2 ref=1 tail=0"),
                Need("arap", ArRoutes, ApVouch, "CancelSign", "callref 2 ref=1 tail=0"),
                Need("arap", ArRoutes, ApClose, "DeleteVouch", "callref 2 ref=1 tail=1"),
                Need("arap", ArRoutes, ApVouch, "DeleteVouch", "callref 2 ref=1 tail=1"),
            };
        }

        // 总账写入、档案写入（读、列表、审核记账类操作只走 SQL）
        static SigNeed[] Writes()
        {
            return new SigNeed[]
            {
                Need("gl-write", GlWrite, "U8PzInsert.clsPZInsert", "Transact", "callref 2 ref=1"),
                Need("gl-write", GlWrite, "U8PzInsert.clsPZInsert", "ToEAICon", "set 1"),
                Need("arc-write", ArcWrite, "U8SrvTrans.IClsCommon", "Transact", "callref 2 ref=1 tail=0"),
                // 币种、凭证类别的新增（ArcGl）：U8PZInsert.dll 里的 EAI 组件，不设 ToEAICon，只调 Transact。
                Need("arc-gl-write", "/u8co/v1/archives/create", "U8PzInsert.ICurrency", "Transact", "callref 2 ref=1 measured"),
                Need("arc-gl-write", "/u8co/v1/archives/create", "U8PzInsert.IDsign", "Transact", "callref 2 ref=1 measured"),
                // 客户联系人（ArcPartnerContact）：EAI 分发表里的 clsCRMEAI，照 IClsCommon 的 (xml, login)。
                Need("arc-partner-write", ArcWrite, ArcPartnerContact.CustomerProgId, "Transact", "callref 2 ref=1 measured"),
                // 库存期初结存新增（StockOpeningAdd）：U8 官方 EAI 分发 ProcessEx(xml, login)，login 按引用 {1}。
                Need("stock-opening", "/u8co/v1/vouchers/create", StockOpeningEai.ProgId, StockOpeningEai.Method,
                    "callref 2 ref=1 measured"),
                // 汇率、供应商联系人新增（ArcExchWrite、ArcVenContact）：同一分发器，根标签 currencyrate / vendorcontact。
                // 固定资产卡片新增与撤销（capitalasserts，ArcFaWrite）、设备台账新增（eqdata，ArcEq）也走它。
                Need("arc-eai-dist", "/u8co/v1/archives/create,/u8co/v1/archives/delete", EaiDistribute.ProgId, EaiDistribute.Method,
                    "callref 2 ref=1 measured"),
            };
        }

        // 审批流
        static SigNeed[] Flow()
        {
            return new SigNeed[]
            {
                Need("wf", WfAll, Proxy, "IsFlowEnabled2", "callref 4 ref=3"),
                Need("wf", WfAudit, Proxy, "Audit2", "callref 6 ref=5"),
                Need("wf", "/u8co/v1/workflow/abandon", Proxy, "Abandon2", "callref 5 ref=4"),
                Need("wf", "/u8co/v1/workflow/resubmit", Proxy, "SubmitResubmitMessage2", "callref 3 ref=2"),
                Need("wf", WfTxn, "UFLTMService.clsService", "Start", "call 1"),
                Need("wf", WfTxn, "UFLTMService.clsService", "BeginTransaction", "call 0"),
                Need("wf", WfTxn, "UFLTMService.clsService", "Commit", "call 0"),
                Need("wf", WfTxn, "UFLTMService.clsService", "Rollback", "call 0"),
                Need("wf", WfTxn, "UFLTMService.clsService", "Finish", "call 0"),
                Need("wf", WfTxn, "QMWorkFlowSrv.clsQMFinalVerifyPI", "DoSubmit", "callref 6 ref=0,1,2,3,4,5"),
                Need("wf", "/u8co/v1/workflow/withdraw", "QMWorkFlowSrv.clsQMFinalVerifyPI", "UndoSubmit", "callref 6 ref=0,1,2,3,4,5"),
            };
        }

        // 生产订单审核、新增、修改、删除（U8 API 框架，.NET 组件，通常没有类型库，只核对注册）。
        // 新增另用 GetExtBoEntity 取扩展实体；实体上的 NewItem / GetSubEntity / SetValue / Serialize 不是按 ProgID 创建的，不在表里。
        // 修改（MoUpdate）：MOrderLoad 用 AssignNormalValue + GetExtBoEntity 取加载的实体，MOrderUpdate 用 SetExtBoEntity 交回。
        static SigNeed[] ProductionOrder()
        {
            string mo = V + "verify," + V + "create," + V + "delete," + V + "update";
            return new SigNeed[]
            {
                Need("mo", mo, "UFIDA.U8.U8APIFramework.U8EnvContext", "U8Login", "set 1"),
                Need("mo", mo, Broker, "Connect", "call 2"),
                Need("mo", V + "verify," + V + "delete," + V + "update", Broker, "AssignNormalValue", "call 2"),
                Need("mo", V + "create," + V + "update", Broker, "GetExtBoEntity", "call 1"),
                Need("mo", V + "update", Broker, "SetExtBoEntity", "call 2"),
                Need("mo", V + "update", Broker, "GetResult", "call 1"),
                Need("mo", mo, Broker, "InvokeApi", "call 0"),
                Need("mo", mo, Broker, "GetLastError", "call 0"),
                Need("mo", mo, Broker, "Disconnect", "call 0"),
            };
        }

        // 物料清单新增、修改、删除、审核（同一个 U8ApiComBroker，AppType BOM）。审核、弃审、删除用 AssignNormalValue 传三个参数，
        // 新增、修改用 GetExtBoEntity 取扩展实体。
        static SigNeed[] BomApi()
        {
            string all = V + "verify," + V + "create," + V + "update," + V + "delete";
            return new SigNeed[]
            {
                Need("bom", all, "UFIDA.U8.U8APIFramework.U8EnvContext", "U8Login", "set 1"),
                Need("bom", all, Broker, "Connect", "call 2"),
                Need("bom", V + "verify," + V + "delete", Broker, "AssignNormalValue", "call 2"),
                Need("bom", V + "create," + V + "update", Broker, "GetExtBoEntity", "call 1"),
                Need("bom", all, Broker, "InvokeApi", "call 0"),
                Need("bom", all, Broker, "GetLastError", "call 0"),
                Need("bom", all, Broker, "Disconnect", "call 0"),
            };
        }

        // 质量单据（UFQMCo，登录子系统 QM）：Init(login, conn, bOutTrans) 引用 {0,1,2}；
        // VoucherOperate(DomHead, domBody, actionType, error, voucherid) 引用 {0,1,3}，voucherid 在类型库里是带缺省的可选参数，
        // 桥总是传（新增传空串），by-ref 以实测为准。检验单取号另走 USERPCO / USCOMMON / UFBillComponent（见 Stock 组）。
        static SigNeed[] Quality()
        {
            // 报检单不开放单独审核（vouchers/verify 在登录前 400），只有生单和删除（删除前的弃审）用到这两个组件。
            string inspect = V + "generate," + V + "delete";
            // 检验单修改：同一 VoucherOperate，actionType 用 update。
            string gen = inspect + "," + V + "update";
            // 产品报检单另有单独弃审（unconfirm，vouchers/verify），同一 VoucherOperate。
            string proInspect = inspect + "," + V + "verify";
            return new SigNeed[]
            {
                Need("qm", inspect, "UFQMCo.clsArrInspectCO", "Init", "callref 3 ref=0,1,2 tail=0 measured"),
                Need("qm", inspect, "UFQMCo.clsArrInspectCO", "VoucherOperate", "callref 5 ref=0,1,3 tail=0 measured"),
                Need("qm", proInspect, "UFQMCo.clsProInspectCO", "Init", "callref 3 ref=0,1,2 tail=0 measured"),
                Need("qm", proInspect, "UFQMCo.clsProInspectCO", "VoucherOperate", "callref 5 ref=0,1,3 tail=0 measured"),
                Need("qm", gen, "UFQMCo.clsArrCheckCO", "Init", "callref 3 ref=0,1,2 tail=0 measured"),
                Need("qm", gen, "UFQMCo.clsArrCheckCO", "VoucherOperate", "callref 5 ref=0,1,3 tail=0 measured"),
                Need("qm", gen, "UFQMCo.clsProCheckCO", "Init", "callref 3 ref=0,1,2 tail=0 measured"),
                Need("qm", gen, "UFQMCo.clsProCheckCO", "VoucherOperate", "callref 5 ref=0,1,3 tail=0 measured"),
            };
        }

        // 不良品处理单（UFQMCo.clsArrRejectCO / clsProRejectCO，登录子系统 QM，测试账套实测，没有注册类型库）：
        // Init() 无参；GetVtidList(login, False) {0,1}；GetVTID(login, VT, 0, vo, False) {0,2,3}；AddNewVoucher(login, vo, "") {0,1}；
        // AddVoucherByRef(conn, vo, rs) {0,1,2}；AddVoucher / GetTheVoucher(login, vo[, id]) / AuditVoucher / UnAuditVoucher / DelVoucher {0,1}。
        static SigNeed[] QualityReject()
        {
            string gen = V + "generate";
            string all = V + "generate," + V + "verify," + V + "delete";
            List<SigNeed> list = new List<SigNeed>();
            string[] cos = new string[] { "UFQMCo.clsArrRejectCO", "UFQMCo.clsProRejectCO" };
            for (int i = 0; i < cos.Length; i++)
            {
                list.Add(Need("qm-reject", all, cos[i], "Init", "call 0 measured"));
                list.Add(Need("qm-reject", all, cos[i], "bOutAuth", "set 1 measured"));
                list.Add(Need("qm-reject", all, cos[i], "bOutTrans", "set 1 measured"));
                list.Add(Need("qm-reject", all, cos[i], "LoadTemp", "set 1 measured"));
                list.Add(Need("qm-reject", all, cos[i], "GetVtidList", "callref 2 ref=0,1 measured"));
                list.Add(Need("qm-reject", all, cos[i], "GetVTID", "callref 5 ref=0,2,3 measured"));
                list.Add(Need("qm-reject", gen, cos[i], "AddNewVoucher", "callref 3 ref=0,1 measured"));
                list.Add(Need("qm-reject", gen, cos[i], "AddVoucherByRef", "callref 3 ref=0,1,2 measured"));
                list.Add(Need("qm-reject", gen, cos[i], "AddVoucher", "callref 2 ref=0,1 measured"));
                list.Add(Need("qm-reject", V + "verify," + V + "delete", cos[i], "GetTheVoucher", "callref 3 ref=0,1 measured"));
                list.Add(Need("qm-reject", V + "verify", cos[i], "AuditVoucher", "callref 2 ref=0,1 measured"));
                list.Add(Need("qm-reject", V + "verify", cos[i], "UnAuditVoucher", "callref 2 ref=0,1 measured"));
                list.Add(Need("qm-reject", V + "delete", cos[i], "DelVoucher", "callref 2 ref=0,1 measured"));
            }
            list.Add(Need("qm-reject", all, "UFQMVOCom.clsVoucherVO", "InitByXml", "call 2 measured"));
            return list.ToArray();
        }

        // 类型库（UFQMCo.dll）：两个组件都没有 Init；其他报检单组件没有 bOutAuth / bOutTrans。尾部可选参数不传（tail）。
        static SigNeed[] QualityOther()
        {
            const string Ins = "UFQMCo.clsOtherInspectVoucherCO";
            const string Chk = "UFQMCo.clsOtherCheckVoucherCO";
            string insAll = V + "create," + V + "verify," + V + "delete," + V + "update";
            string chkAll = V + "generate," + V + "verify," + V + "delete," + V + "update";
            string chkLoad = V + "verify," + V + "delete," + V + "update";
            return new SigNeed[]
            {
                Need("qm-other", insAll, Ins, "LoadTemp", "set 1"),
                Need("qm-other", insAll, Ins, "GetVtidList", "callref 2 ref=0,1 tail=0"),
                Need("qm-other", insAll, Ins, "GetVTID", "callref 5 ref=0,2,3 tail=0"),
                Need("qm-other", V + "create", Ins, "AddNewVoucher", "callref 3 ref=0,1 tail=0"),
                Need("qm-other", V + "create", Ins, "AddVoucher", "callref 2 ref=0,1 tail=0"),
                // 新增后的补审核（QmOthIns.AutoVerify）与 vouchers/verify 都载入后 AuditVoucher；弃审另用于删除已审核的单据。
                Need("qm-other", insAll, Ins, "GetTheVoucher", "callref 3 ref=0,1 tail=1"),
                Need("qm-other", V + "create," + V + "verify", Ins, "AuditVoucher", "callref 2 ref=0,1 tail=0"),
                Need("qm-other", V + "verify," + V + "delete", Ins, "UnAuditVoucher", "callref 2 ref=0,1 tail=0"),
                Need("qm-other", V + "delete", Ins, "DelVoucher", "callref 2 ref=0,1 tail=1"),
                // 修改：载入后 UpdateVoucher(login, vo) {0,1}（SaveVoucher / EditVoucher / ModVoucher 不存在）。
                Need("qm-other", V + "update", Ins, "UpdateVoucher", "callref 2 ref=0,1 measured"),
                Need("qm-other", chkAll, Chk, "LoadTemp", "set 1"),
                Need("qm-other", chkAll, Chk, "bOutAuth", "set 1"),
                Need("qm-other", chkAll, Chk, "bOutTrans", "set 1"),
                Need("qm-other", chkAll, Chk, "GetVtidList", "callref 2 ref=0,1 tail=0"),
                Need("qm-other", chkAll, Chk, "GetVTID", "callref 5 ref=0,2,3 tail=0"),
                Need("qm-other", V + "generate", Chk, "AddNewVoucher", "callref 3 ref=0,1 tail=0"),
                Need("qm-other", V + "generate", Chk, "AddVoucherByRef", "callref 3 ref=0,1,2 tail=1"),
                Need("qm-other", V + "generate", Chk, "AddVoucher", "callref 2 ref=0,1 tail=1"),
                Need("qm-other", chkLoad, Chk, "GetTheVoucher", "callref 3 ref=0,1 tail=1"),
                Need("qm-other", V + "verify", Chk, "AuditVoucher", "callref 2 ref=0,1 tail=1"),
                Need("qm-other", V + "verify", Chk, "UnAuditVoucher", "callref 2 ref=0,1 tail=1"),
                Need("qm-other", V + "delete", Chk, "DelVoucher", "callref 2 ref=0,1 tail=1"),
                Need("qm-other", V + "update", Chk, "UpdateVoucher", "callref 2 ref=0,1 measured"),
                Need("qm-other", insAll + "," + V + "generate", "UFQMVOCom.clsVoucherVO", "InitByXml", "call 2 measured"),
            };
        }

        // clsPub_AP：Init 与 clsCloseBill 同形 {0,1,2,3}；CanSign(类型, 单号, bCancel, msg) {3}；Sign / CancelSign(cond, msg) {1}。
        static SigNeed[] ArapAuditSigs()
        {
            string verify = V + "verify";
            const string Pub = "UFAPBO.clsPub_AP";
            return new SigNeed[]
            {
                Need("arap-audit", verify, ApAcc, "Init", "call 2 tail=0"),
                Need("arap-audit", verify, Pub, "Init", "callref 4 ref=0,1,2,3 tail=0"),
                Need("arap-audit", verify, Pub, "PUVouchCanSign", "callref 4 ref=3 tail=0"),
                Need("arap-audit", verify, Pub, "SAVouchCanSign", "callref 4 ref=3 tail=0"),
                Need("arap-audit", verify, Pub, "Sign_PurBill", "callref 2 ref=1 tail=0"),
                Need("arap-audit", verify, Pub, "CancelSign_PurBill", "callref 2 ref=1 tail=0"),
                Need("arap-audit", verify, Pub, "Sign_SaleBill", "callref 2 ref=1 tail=0"),
                Need("arap-audit", verify, Pub, "CancelSign_SaleBill", "callref 2 ref=1 tail=0"),
                // 审核后补往来科目（ArapAuditKm）：CusVenToCtrlKMForSAPU(往来单位, 币种, 类型, 存货, "AR"|"AP") 按值 5 个（测试账套实测）。
                Need("arap-audit", verify, Pub, "CusVenToCtrlKMForSAPU", "call 5 measured"),
            };
        }

        // U8ApCancel.cLsCancel：Init(login, "AR"|"AP", conn) 引用 {0,2}；Save(xml) 引用 {0}（测试账套实测）。没有注册类型库。
        static SigNeed[] WriteoffSigs()
        {
            const string Hx = "U8ApCancel.cLsCancel";
            const string W2b = "Ussaupdispatch.clsWrite2Bill";
            return new SigNeed[]
            {
                Need("arap-writeoff", Requests.WriteoffPath + "," + Requests.WriteoffAutoPath, Hx, "Init", "callref 3 ref=0,2 measured"),
                Need("arap-writeoff", Requests.WriteoffPath + "," + Requests.WriteoffAutoPath, Hx, "Save", "callref 1 ref=0 measured"),
                // 红票对冲：同一组件 Init 后 AP_JZ_Red(xmlData, ByRef cErrMsg) As String（返回处理号），引用 {1}（测试账套实测）。
                Need("arap-red-offset", ArapRedReq.Path, Hx, "Init", "callref 3 ref=0,2 measured"),
                Need("arap-red-offset", ArapRedReq.Path, Hx, "AP_JZ_Red", "callref 2 ref=1 measured"),
                // 取消核销：销售发票累计核销的回写，类型库 UpdateBillForAR(ByRef CN, ByRef strTblName) As String。
                Need("arap-writeoff-cancel", Requests.WriteoffCancelPath, W2b, "UpdateBillForAR", "callref 2 ref=0,1"),
                // 取消应收冲应付 / 应付冲应收：同一段销售发票回写（ArapUnwriteoffBill）。
                Need("arap-process-cancel", ArapProcCancelReq.Path, W2b, "UpdateBillForAR", "callref 2 ref=0,1"),
                // 应收冲应付 / 应付冲应收：销售发票累计核销加本次（ArapTransferWrite 经 ArapUnwriteoffBill.Write2）。
                Need("arap-transfer", Requests.TransferPath, W2b, "UpdateBillForAR", "callref 2 ref=0,1"),
                // 汇兑损益、取消汇兑损益（ArapExGainBill）：销售发票 UpdateBillForAR；取消另调采购侧组件的 UpdateBillForAP（更正 ProgID）。
                Need("arap-exchange-gain", ArapExGainReq.Path + "," + ArapExGainReq.CancelPath, W2b, "UpdateBillForAR",
                    "callref 2 ref=0,1"),
                Need("arap-exchange-gain", ArapExGainReq.CancelPath, "Pu_Productinf.cls_ForAPsrv", "UpdateBillForAP", "callref 2 ref=0"),
                // 坏账发生：销售发票累计核销加本次（ArapBadOccurSql.SaleBill 经 ArapUnwriteoffBill.Write2）。
                Need("arap-bad-debt", ArapBadReq.Path, W2b, "UpdateBillForAR", "callref 2 ref=0,1"),
            };
        }

        // 制单的凭证保存走 U8PzInsert.clsPZInsert（同 gl-write：ToEAICon 设连接，Transact(xml, login) 引用 {1}）。
        static SigNeed[] ArapVoucherSigs()
        {
            return new SigNeed[]
            {
                Need("arap-voucher", Requests.ArapVoucherPath, "U8PzInsert.clsPZInsert", "Transact", "callref 2 ref=1"),
                Need("arap-voucher", Requests.ArapVoucherPath, "U8PzInsert.clsPZInsert", "ToEAICon", "set 1"),
            };
        }

        // spec 形如 "callref 5 ref=0,1,4 tail=1 measured"：调用方式、实参个数、按引用下标、可选尾参数个数、以实测为准。
        static SigNeed Need(string feature, string routes, string progId, string member, string spec)
        {
            SigNeed n = new SigNeed();
            n.Feature = feature;
            n.Routes = routes;
            n.ProgId = progId;
            n.Member = member;
            n.ByRef = new int[0];
            n.OptionalTail = -1;
            string[] parts = spec.Split(' ');
            n.Invoke = parts[0];
            n.ArgCount = int.Parse(parts[1], CultureInfo.InvariantCulture);
            for (int i = 2; i < parts.Length; i++)
            {
                ApplyOption(n, parts[i]);
            }
            return n;
        }

        static void ApplyOption(SigNeed n, string part)
        {
            if (part == "measured")
            {
                n.PreferMeasured = true;
            }
            else if (part.StartsWith("tail=", StringComparison.Ordinal))
            {
                n.OptionalTail = int.Parse(part.Substring(5), CultureInfo.InvariantCulture);
            }
            else if (part.StartsWith("ref=", StringComparison.Ordinal))
            {
                string[] items = part.Substring(4).Split(',');
                n.ByRef = new int[items.Length];
                for (int i = 0; i < items.Length; i++)
                {
                    n.ByRef[i] = int.Parse(items[i], CultureInfo.InvariantCulture);
                }
            }
        }
    }
}
