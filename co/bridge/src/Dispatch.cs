using System;
using System.Collections.Generic;

namespace U8Co
{
    internal delegate ApiResult OpHandler(WorkContext ctx, string op);

    // 登录检查在 StaWorker 里直接返回，不进这张表。
    internal static class Dispatch
    {
        static readonly string[] GlOps = new string[]
        {
            "load", "list", "create", "update", "void", "unvoid", "verify", "unverify", "sign", "unsign", "delete",
            // 记账（GlPost）。
            "post",
            // 红字冲销（GlReverse）。
            GlReverseReq.Op,
            // 取消记账（GlUnpost，测试账套）。
            "unpost",
            // 凭证摘要（GlDigest，事件源）。
            GlDigest.Op
        };
        static readonly string[] ArcOps = new string[] { "get", "list", "create", "update", "delete" };
        static readonly Dictionary<string, Handler> Routes = BuildMap();
        static readonly HashSet<string> DeletePu = NameSet(new string[] { "purchase_order", "arrival" });
        static readonly HashSet<string> DeleteSt = NameSet(new string[]
        {
            "other_in", "other_out", "transfer", "purchase_in", "sale_out"
        });
        static readonly HashSet<string> Mfg = NameSet(new string[] { "material_out", "product_in" });
        // 发货单、销售发票、退货单（SaleEdit.Delete 再分到 SaleReturn.Delete）。
        static readonly HashSet<string> DeleteSa = NameSet(new string[] { "dispatch", "sale_invoice", "sale_return" });

        public static ApiResult Handle(WorkContext ctx)
        {
            string path = ctx == null || ctx.Item == null || ctx.Item.Path == null ? "" : ctx.Item.Path;
            Handler handler;
            if (!Routes.TryGetValue(path, out handler))
            {
                throw new BridgeException(404, "not_found", "未知路径");
            }
            // 请购单：vouchers/verify|create|update|delete|close 走 PuAppRoutes（读取在 VoucherRead）。
            ApiResult requisition = PuAppRoutes.Try(ctx, path);
            if (requisition != null)
            {
                return requisition;
            }
            // 物料清单（type=bom）：vouchers/verify|create|update|delete 走 BomRoutes（读取在 VoucherRead）。
            ApiResult bom = BomRoutes.Try(ctx, path);
            if (bom != null)
            {
                return bom;
            }
            // 形态转换单、调拨申请单、盘点单：vouchers/verify|create|update|delete 走 StockMisc（读取仍在 VoucherRead）。
            ApiResult misc = StockMisc.Try(ctx, path);
            if (misc != null)
            {
                return misc;
            }
            // 货位调整单：vouchers/verify|create|delete 走 PositionAdjust（读取在 VoucherRead）。
            ApiResult adjust = PositionAdjust.Try(ctx, path);
            if (adjust != null)
            {
                return adjust;
            }
            // 库存期初结存单：测试账套、存货核算闸门、固定单据日期、换登录日期之后，新增走 EAI 导入（StockOpeningAdd），
            // 删除、审核、弃审交给 StockCo；修改 400（StockOpening）。K12 退货申请单的写入与退货单参照退货申请单生成同在 DispatchMore。
            ApiResult opening = DispatchMore.Try(ctx, path);
            if (opening != null)
            {
                return opening;
            }
            return handler(ctx);
        }

        static Dictionary<string, Handler> BuildMap()
        {
            Dictionary<string, Handler> map = new Dictionary<string, Handler>();
            map.Add("/u8co/v1/sale-orders/verify", LegacyVerify);
            map.Add("/u8co/v1/dispatches/verify", LegacyVerify);
            map.Add("/u8co/v1/vouchers/load", VoucherRead.Load);
            map.Add("/u8co/v1/vouchers/verify", Verify);
            map.Add("/u8co/v1/vouchers/create", Create);
            map.Add("/u8co/v1/vouchers/update", Update);
            map.Add("/u8co/v1/vouchers/delete", Delete);
            map.Add("/u8co/v1/vouchers/close", Close);
            map.Add("/u8co/v1/vouchers/generate", Generate);
            // 销售订单 / 采购订单锁定、解锁（VoucherLock）。
            map.Add(Requests.LockPath, VoucherLock.Run);
            map.Add("/u8co/v1/workflow/state", Workflow.State);
            map.Add("/u8co/v1/workflow/history", Workflow.History);
            map.Add("/u8co/v1/workflow/tasks", Workflow.Tasks);
            map.Add("/u8co/v1/workflow/submit", delegate(WorkContext ctx) { return Workflow.Run(ctx, "submit"); });
            map.Add("/u8co/v1/workflow/withdraw", delegate(WorkContext ctx) { return Workflow.Run(ctx, "withdraw"); });
            map.Add("/u8co/v1/workflow/approve", delegate(WorkContext ctx) { return Workflow.Run(ctx, "approve"); });
            map.Add("/u8co/v1/workflow/disagree", delegate(WorkContext ctx) { return Workflow.Run(ctx, "disagree"); });
            map.Add("/u8co/v1/workflow/return", delegate(WorkContext ctx) { return Workflow.Run(ctx, "return"); });
            map.Add("/u8co/v1/workflow/abandon", delegate(WorkContext ctx) { return Workflow.Run(ctx, "abandon"); });
            map.Add("/u8co/v1/workflow/resubmit", delegate(WorkContext ctx) { return Workflow.Run(ctx, "resubmit"); });
            AddOps(map, Requests.GlRoot, GlOps, GlRoutes.Handle);
            AddOps(map, Requests.ArcRoot, ArcOps, ArcRoutes.Handle);
            // 名称解析（ArcResolve）、幂等结果查询（IdemGet），都是读路由。
            map.Add(ArcResolve.Path, ArcResolve.Run);
            map.Add(IdemGet.Path, IdemGet.Run);
            // 字段说明（MetaFieldsRoute），读路由，除有效登录外不要求功能权限。
            map.Add(MetaFieldsReq.Path, MetaFieldsRoute.Run);
            // 单据搜索（VoucherSearch）、单据批量读取（LoadMany）、档案批量读取（ArcGetMany），都是读路由。
            map.Add(VoucherSearch.Path, VoucherSearch.Run);
            map.Add(LoadMany.Path, LoadMany.Run);
            map.Add(ArcGetMany.Path, ArcGetMany.Run);
            // 单张票据读取（NotesRead），读路由。
            map.Add(NotesReadReq.Path, NotesRead.Run);
            // 权限快照（本人）、权限评估（subject，限 permEvaluateOperators），读路由。
            map.Add(PermSnapshot.Path, PermSnapshot.Run);
            map.Add(PermEvaluate.Path, PermEvaluate.Run);
            map.Add(Requests.ListPath, Bind(ListRoutes.Handle, Requests.OpOf(Requests.ListPath)));
            map.Add(Requests.StockPath, Bind(ListRoutes.Handle, Requests.OpOf(Requests.StockPath)));
            // 只读报表：reports/<name>。
            AddOps(map, Requests.ReportRoot, Reports.Names, Reports.Handle);
            // 凭证附件、单据附件列表（只读，GlAttach、VoucherAttach）。
            map.Add(GlAttach.Path, GlAttach.Handle);
            map.Add(VoucherAttach.Path, VoucherAttach.Handle);
            // 应收 / 应付核销（ArapWriteoff）。
            map.Add(Requests.WriteoffPath, ArapWriteoff.Run);
            // 取消核销（ArapUnwriteoff）。
            map.Add(Requests.WriteoffCancelPath, ArapUnwriteoff.Run);
            // 取消应收冲应付 / 应付冲应收 / 并账（ArapProcCancel）。
            map.Add(ArapProcCancelReq.Path, ArapProcCancel.Run);
            // 应收 / 应付处理记录与期间摘要（ArapProcList，读路由，事件源）。
            map.Add(ArapProcListReq.Path, ArapProcList.Run);
            // 自动核销（ArapAutoWriteoff）。
            map.Add(Requests.WriteoffAutoPath, ArapAutoWriteoff.Run);
            // 并账（ArapMerge）。
            map.Add(ArapMergeReq.Path, ArapMerge.Run);
            // 应收冲应付 / 应付冲应收（ArapTransfer）。
            map.Add(Requests.TransferPath, ArapTransfer.Run);
            // 红票对冲（ArapRed，U8ApCancel.cLsCancel.AP_JZ_Red）。
            map.Add(ArapRedReq.Path, ArapRed.Run);
            // 票据处理：托收 / 结算、贴现、背书（NotesProc）。
            map.Add(NotesProcReq.Path, NotesProc.Run);
            // 票据登记、删除（NotesReg、NotesRegDel：票据行和收款单在同一个请求事务里）。
            map.Add(NotesRegReq.CreatePath, NotesReg.Create);
            map.Add(NotesRegReq.DeletePath, NotesRegDel.Delete);
            // 应收 / 应付制单、取消制单（ArapVoucher、ArapVoucherDrop）。
            map.Add(Requests.ArapVoucherPath, ArapVoucher.Run);
            map.Add(Requests.ArapVoucherDropPath, ArapVoucherDrop.Run);
            // 处理制单（ArapProcVoucher：应收冲应付、应付冲应收、并账、汇兑损益）。
            map.Add(ArapProcVoucherReq.Path, ArapProcVoucher.Run);
            // 汇兑损益、取消汇兑损益（ArapExGain、ArapExGainCancel，只对测试账套开放）。
            map.Add(ArapExGainReq.Path, ArapExGain.Run);
            map.Add(ArapExGainReq.CancelPath, ArapExGainCancel.Run);
            AddLedger(map);
            return map;
        }

        // 第二级写入（只对测试账套开放）的账务处理路由：坏账处理、期初记账、期初单据、月末结账、存货核算。
        static void AddLedger(Dictionary<string, Handler> map)
        {
            // 坏账处理：坏账发生、坏账收回、计提坏账准备（ArapBad 按 action 分派，只对测试账套开放）。
            map.Add(ArapBadReq.Path, ArapBad.Run);
            // 期初记账 / 取消记账（OpeningPost：采购管理；存货核算交给 OpeningIa）。
            map.Add(OpeningPostReq.Path, OpeningPost.Run);
            // 应收 / 应付期初单据（OpeningsArap）。
            map.Add(OpeningsArapReq.Path, OpeningsArap.Run);
            // 月末结账 / 取消结账（PeriodClose，只对测试账套开放）。
            map.Add(PeriodCloseReq.Path, PeriodClose.Run);
            // 存货核算记账 / 恢复记账、期末处理 / 取消期末处理（IaRun，只对测试账套开放）。
            map.Add(IaReq.PostPath, IaRun.Run);
            map.Add(IaReq.PeriodEndPath, IaRun.Run);
            // 期间损益结转、自定义转账（GlTransfer，只对测试账套开放）。
            map.Add(GlTransferReq.PnlPath, GlTransfer.Run);
            map.Add(GlTransferReq.CustomPath, GlTransfer.Run);
        }

        static void AddOps(Dictionary<string, Handler> map, string root, string[] ops, OpHandler handler)
        {
            for (int i = 0; i < ops.Length; i++)
            {
                map.Add(root + ops[i], Bind(handler, ops[i]));
            }
        }

        static Handler Bind(OpHandler handler, string op)
        {
            return delegate(WorkContext ctx) { return handler(ctx, op); };
        }

        static ApiResult LegacyVerify(WorkContext ctx)
        {
            return SalesVerify.Verify(ctx.Conn, ctx.Session, ctx.Item);
        }

        static ApiResult Verify(WorkContext ctx)
        {
            VoucherKind kind = NeedKind(ctx);
            int id = ctx.Item.Id;
            string action = ctx.Item.Action;
            if (!kind.Verifiable)
            {
                throw new BridgeException(400, "bad_request", QmVerify.RefuseText(kind));
            }
            // 应付审核 / 应收审核及弃审（ArapAudit），只有采购发票、销售发票。
            if (ArapAudit.IsAction(action))
            {
                return ArapAudit.Run(ctx, kind, id, action);
            }
            if (kind.Name == "sale_invoice")
            {
                return SaleInvoice.Verify(ctx, kind, id, action);
            }
            // 采购发票的审核是采购复核（PuInvReview）。
            if (kind.Name == "purchase_invoice")
            {
                return PuInv.Review(ctx, kind, id, action);
            }
            switch (kind.Family)
            {
                case "sa":
                    return SalesVerify.VerifyKind(ctx, kind, id, action);
                case "pu":
                    return PurchaseCo.Verify(ctx, kind, id, action);
                case "st":
                    return StockCo.Verify(ctx, kind, id, action);
                case "mo":
                    return MoApi.Verify(ctx, kind, id, action);
                case "ar":
                    return ArapCo.Verify(ctx, kind, id, action);
            }
            return VerifyMore(ctx, kind, id, action);
        }

        // 不良品处理单 AuditVoucher / UnAuditVoucher（QmRejOps，U8 自己提交）；其余质量单据不可直接审核。
        static ApiResult VerifyMore(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            if (QmRejOps.Handles(kind))
            {
                return QmRejOps.Verify(ctx, kind, id, action);
            }
            // 产品报检单只开放弃审（VoucherOperate unconfirm，U8 自己提交，QmInsUnverify）。
            if (QmInsUnverify.Handles(kind))
            {
                return QmInsUnverify.Run(ctx, kind, id, action);
            }
            // 其他报检单、其他检验单 AuditVoucher / UnAuditVoucher（QmOthOps，U8 自己提交）。
            if (QmOthOps.Handles(kind))
            {
                return QmOthOps.Verify(ctx, kind, id, action);
            }
            throw new BridgeException(400, "bad_request", "该单据类型不支持直接审核");
        }

        static ApiResult Create(WorkContext ctx)
        {
            VoucherKind kind = NeedKind(ctx);
            ApiResult result = CreateOf(ctx, kind);
            return StampNewId(ctx, result);
        }

        static ApiResult CreateOf(WorkContext ctx, VoucherKind kind)
        {
            Dictionary<string, object> head = ctx.Item.Head;
            object[] lines = ctx.Item.Lines;
            if (kind.Name == "sale_order")
            {
                return SaleOrderCo.Create(ctx, kind, head, lines);
            }
            if (kind.Name == "purchase_order")
            {
                return PurchaseEdit.Create(ctx, kind, head, lines);
            }
            if (kind.Name == "other_in" || kind.Name == "other_out" || kind.Name == "transfer")
            {
                return StockCo.Create(ctx, kind, head, lines);
            }
            // 采购入库单无来源新增（StockPurIn 预检，StockDomPurIn 建 DOM）。
            if (kind.Name == "purchase_in")
            {
                return StockPurIn.Create(ctx, kind, head, lines);
            }
            if (kind.Family == "ar")
            {
                return ArapCo.Create(ctx, kind, head, lines);
            }
            // 生产订单：U8API MOrderAdd（MoCreate），不包 CoTrans。
            if (kind.Name == "production_order")
            {
                return MoCreate.Run(ctx, kind, head, lines);
            }
            // 无来源发货单、先开票销售发票、无来源到货单、无来源材料出库单（SrcLess）。
            ApiResult free = SrcLess.TryCreate(ctx, kind);
            if (free != null)
            {
                return free;
            }
            // 其他报检单、采购手工结算（DispatchCreate，从这里移出：文件行数上限）。
            return DispatchCreate.More(ctx, kind, head, lines);
        }

        static ApiResult Update(WorkContext ctx)
        {
            VoucherKind kind = NeedKind(ctx);
            Dictionary<string, object> head = ctx.Item.Head ?? new Dictionary<string, object>();
            object[] lines = ctx.Item.Lines ?? new object[0];
            int id = ctx.Item.Id;
            // 生单来的单据和应收应付（EditMore）；采购入库在 StockEditSrc 里按来源分流。
            ApiResult more = EditMore.Update(ctx, kind, id, head, lines);
            if (more != null)
            {
                return more;
            }
            if (kind.Name == "sale_order")
            {
                return SaleEdit.Update(ctx, kind, id, head, lines);
            }
            if (kind.Name == "purchase_order")
            {
                return PurchaseEdit.Update(ctx, kind, id, head, lines);
            }
            if (kind.Name == "other_in" || kind.Name == "other_out" || kind.Name == "transfer")
            {
                return StockEdit.Update(ctx, kind, id, head, lines);
            }
            // 无来源采购入库单修改（StockPurIn 预检）。
            if (kind.Name == "purchase_in")
            {
                return StockEdit.Update(ctx, kind, id, head, lines);
            }
            return UpdateMore(ctx, kind, id, head, lines);
        }

        static ApiResult UpdateMore(WorkContext ctx, VoucherKind kind, int id, Dictionary<string, object> head, object[] lines)
        {
            // 生产订单：U8API MOrderLoad + MOrderUpdate（MoUpdate），不包 CoTrans。
            if (kind.Name == "production_order")
            {
                return MoUpdate.Run(ctx, kind, id, head, lines);
            }
            // 检验单、其他报检单、其他检验单：VoucherOperate(update) / VO 的 UpdateVoucher，不包 CoTrans（QmEdit）。
            if (QmEdit.Handles(kind))
            {
                return QmEdit.Run(ctx, kind, id, head, lines);
            }
            throw new BridgeException(400, "bad_request", "该单据类型不支持修改");
        }

        static ApiResult Delete(WorkContext ctx)
        {
            VoucherKind kind = NeedKind(ctx);
            int id = ctx.Item.Id;
            string name = kind.Name;
            if (name == "sale_order")
            {
                return SaleOrderCo.Delete(ctx, kind, id);
            }
            if (name == "purchase_invoice")
            {
                return PuInv.Delete(ctx, kind, id);
            }
            if (DeleteSa.Contains(name))
            {
                return SaleEdit.Delete(ctx, kind, id);
            }
            // 采购退货单（红字到货单）：红字 Init + 回写核对（PuRet）。
            if (name == "purchase_return")
            {
                return PuRet.Delete(ctx, kind, id);
            }
            if (DeletePu.Contains(name))
            {
                return PurchaseEdit.Delete(ctx, kind, id);
            }
            if (DeleteSt.Contains(name))
            {
                return StockCo.Delete(ctx, kind, id);
            }
            if (Mfg.Contains(name))
            {
                return MfgGen.Delete(ctx, kind, id);
            }
            if (kind.Family == "ar")
            {
                return ArapCo.Delete(ctx, kind, id);
            }
            return DeleteMore(ctx, kind, id);
        }

        static ApiResult DeleteMore(WorkContext ctx, VoucherKind kind, int id)
        {
            // 生产订单：U8API MOrderDelete（MoDelete），不包 CoTrans。
            if (kind.Name == "production_order")
            {
                return MoDelete.Run(ctx, kind, id);
            }
            // 报检单、检验单（QmDelete，UFQMCo 自己提交，不包 CoTrans）。
            if (QmGen.Handles(kind))
            {
                return QmGen.Delete(ctx, kind, id);
            }
            // 不良品处理单 DelVoucher（QmRejOps）。
            if (QmRejOps.Handles(kind))
            {
                return QmRejOps.Delete(ctx, kind, id);
            }
            // 其他报检单、其他检验单 DelVoucher（QmOthDel）。
            if (QmOthOps.Handles(kind))
            {
                return QmOthOps.Delete(ctx, kind, id);
            }
            // 采购结算单 VoucherCO_PU.Delete（PuSettleDel，在 CoTrans 里）。
            if (PuSettleReq.Handles(kind))
            {
                return PuSettleDel.Delete(ctx, kind, id);
            }
            throw new BridgeException(400, "bad_request", "该单据类型不支持删除");
        }

        static ApiResult Close(WorkContext ctx)
        {
            VoucherKind kind = NeedKind(ctx);
            int id = ctx.Item.Id;
            string action = ctx.Item.Action;
            int[] lineIds = ctx.Item.LineIds;
            if (kind.Name == "sale_order")
            {
                return SaleEdit.Close(ctx, kind, id, action, lineIds);
            }
            if (kind.Name == "purchase_order")
            {
                return PurchaseEdit.Close(ctx, kind, id, action, lineIds);
            }
            // 到货单：CloseArrItems / OpenArrItems，在请求连接的 CoTrans 里（PuArrClose）。
            if (kind.Name == "arrival")
            {
                return PuArrClose.Run(ctx, kind, id, action, lineIds);
            }
            // 生产订单：Usp_MO_Close / Usp_MO_UnClose，在请求连接的 CoTrans 里（MoClose）。
            if (kind.Name == "production_order")
            {
                return MoClose.Run(ctx, kind, id, action, lineIds);
            }
            throw new BridgeException(400, "bad_request", "该单据类型不支持关闭");
        }

        static ApiResult Generate(WorkContext ctx)
        {
            VoucherKind kind = NeedKind(ctx);
            ApiResult result = GenerateOf(ctx, kind);
            return StampNewId(ctx, result);
        }

        static ApiResult GenerateOf(WorkContext ctx, VoucherKind kind)
        {
            Dictionary<string, object> head = ctx.Item.Head ?? new Dictionary<string, object>();
            object[] lines = ctx.Item.Lines ?? new object[0];
            int sourceId = ctx.Item.Id;
            if (kind.Family == "sa")
            {
                return GenerateSa(ctx, kind, sourceId, head, lines);
            }
            if (kind.Name == "sale_out")
            {
                return StockGen.SaleOutLines(ctx, kind, sourceId, head, lines);
            }
            if (kind.Family == "pu" || kind.Name == "purchase_in")
            {
                return GeneratePu(ctx, kind, sourceId, head, lines);
            }
            if (kind.Name == "material_out")
            {
                return MfgGen.MaterialOut(ctx, sourceId, head, lines);
            }
            if (kind.Name == "product_in")
            {
                return MfgGen.ProductIn(ctx, sourceId, head, lines);
            }
            // 报检单参照到货单 / 生产订单，检验单参照报检单（QmGen）；不良品处理单参照检验单（QmRejOps）。
            if (kind.Family == "qm")
            {
                return GenerateQm(ctx, kind, sourceId, head, lines);
            }
            throw new BridgeException(400, "bad_request", "该单据类型不支持生单");
        }

        static ApiResult GenerateQm(WorkContext ctx, VoucherKind kind, int sourceId,
            Dictionary<string, object> head, object[] lines)
        {
            // 不良品处理单：AddVoucher 自己提交（QmRejGen）。
            if (QmRejOps.Handles(kind))
            {
                return QmRejOps.Generate(ctx, kind, sourceId, head, lines);
            }
            // 其他检验单参照其他报检单（QmOthChk，AddVoucher 自己提交）。
            if (QmOthOps.Handles(kind))
            {
                return QmOthOps.Generate(ctx, kind, sourceId, head, lines);
            }
            return QmGen.Generate(ctx, kind, sourceId, head, lines);
        }

        // 发货单参照销售订单；销售发票参照发货单；退货单参照蓝字发货单。
        static ApiResult GenerateSa(WorkContext ctx, VoucherKind kind, int sourceId,
            Dictionary<string, object> head, object[] lines)
        {
            if (kind.Name == "dispatch")
            {
                return SaleGen.Dispatch(ctx, kind, sourceId, head, lines);
            }
            // source_type=sale_return 生成红字销售发票（SaleGenRed）；sale_invoice 红冲蓝字发票（SaleGenBlueRed）。
            if (kind.Name == "sale_invoice" && (SaleGen.FromReturn(ctx.Item) || SaleGen.FromBlue(ctx.Item)))
            {
                return SaleGen.RedOf(ctx, kind, sourceId, head, lines);
            }
            if (kind.Name == "sale_invoice")
            {
                return SaleGen.Invoice(ctx, kind, sourceId, head, lines);
            }
            if (kind.Name == "sale_return")
            {
                return SaleGen.Return(ctx, kind, sourceId, head, lines);
            }
            throw new BridgeException(400, "bad_request", "该单据类型不支持生单");
        }

        // 采购入库参照采购订单（缺省）或来料检验单；采购发票参照采购入库；到货单参照采购订单。
        // 来源类型在登录前已按 Kinds.Sources 核过。
        static ApiResult GeneratePu(WorkContext ctx, VoucherKind kind, int sourceId,
            Dictionary<string, object> head, object[] lines)
        {
            // 采购结算单参照采购发票自动结算（PuSettleGen；请求规则在登录前由 PuSettleReq 核过）。
            if (PuSettleReq.Handles(kind))
            {
                return PuSettleGen.Generate(ctx, kind, sourceId);
            }
            if (kind.Name == "purchase_invoice")
            {
                return PuInv.Generate(ctx, kind, sourceId, head, lines);
            }
            if (kind.Name == "arrival")
            {
                return PuArr.Generate(ctx, kind, sourceId, head, lines);
            }
            // 采购退货单参照原蓝字到货单（缺省）或采购订单（PuRet）。
            if (kind.Name == "purchase_return")
            {
                return PuRet.Generate(ctx, kind, sourceId, head, lines);
            }
            if (kind.Name != "purchase_in")
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持生单");
            }
            // 采购入库按来源分派（采购订单、来料检验单、采购退货单、到货单），见 StockGen.PurchaseInFrom。
            return StockGen.PurchaseInFrom(ctx, kind, sourceId, head, lines);
        }

        // 新增和生单成功后，审计 id 改成新单据。失败时保持请求里的 id（生单是来源 id）。
        static ApiResult StampNewId(WorkContext ctx, ApiResult result)
        {
            if (ctx == null || ctx.Item == null || result == null || result.Status != 200 || result.Body == null)
            {
                return result;
            }
            object raw;
            if (!result.Body.TryGetValue("id", out raw))
            {
                return result;
            }
            int id = CoRows.AsId(raw);
            if (id <= 0)
            {
                return result;
            }
            ctx.Item.HasId = true;
            ctx.Item.Id = id;
            return result;
        }

        static HashSet<string> NameSet(string[] names)
        {
            HashSet<string> set = new HashSet<string>();
            for (int i = 0; i < names.Length; i++)
            {
                set.Add(names[i]);
            }
            return set;
        }

        static VoucherKind NeedKind(WorkContext ctx)
        {
            if (ctx == null || ctx.Item == null || ctx.Item.Type == null)
            {
                throw new BridgeException(400, "bad_request", "缺少单据类型");
            }
            return ctx.Item.Type;
        }
    }
}
