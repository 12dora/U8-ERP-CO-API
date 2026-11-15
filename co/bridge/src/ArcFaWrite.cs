namespace U8Co
{
    // 固定资产卡片（fa_card）写入：新增、撤销本期新增，走 U8 官方 EAI（roottag capitalasserts，经 EaiDistribute 分发，
    // 不用 U8SrvTrans.IClsCommon）。导入自行提交、不在请求连接的事务里：调用前查完（功能权限、固定资产当前期间、档案、部门数据权限），
    // 预演停在 ProcessEx 之前（validate），调用后在新连接上回读（ArcPartnerRun.Eai：U8 拒绝 409，回读不符或失败 504）。
    // 新增：code 是资产编号；EAI 导入的是原始卡片，开始使用日期必须早于固定资产当前期间（模板「小于等于登录期间 - 1」），
    // 只收单个使用部门、本位币；响应的 code 是 U8 编的卡片编号，另给 asset_num、card_id。
    // 删除：code 是卡片编号，只撤销本期新增录入、没有变动单、减少和制单、本期未计提折旧且资产编号唯一的卡片（FaCardSql.CheckUndo），
    // 按资产编号发 proc=delete。
    // 资产减少不在这里：EAI 没有对应的根标签，请在 U8 客户端处理。修改 400（ArcReq.KindOf，FaCardReq.UpdateText）。
    internal static class ArcFaWrite
    {
        public static ApiResult Write(WorkContext ctx, ArcReq req)
        {
            ArcGuard.Permit(ctx, req);
            FaPeriod period = FaPeriod.Require(ctx);
            if (req.Op == "create")
            {
                return Create(ctx, req, period);
            }
            return Delete(ctx, req, period);
        }

        static ApiResult Create(WorkContext ctx, ArcReq req, FaPeriod period)
        {
            FaCardPlan plan = FaCardReq.Plan(req);
            if (string.CompareOrdinal(plan.Tags["startusedate"], period.First) >= 0)
            {
                throw ArcReq.Bad("开始使用日期 start_date 必须早于固定资产当前期间（" + period.Label() + "）：U8 导入的是原始卡片",
                    "fields.start_date");
            }
            string currency = FaCardSql.CheckAdd(ctx.Conn, plan);
            if (!PermCheck.Allow(PermCheck.Of(ctx), PermObj.Department, plan.Dept))
            {
                throw new BridgeException(403, "no_permission", PermCheck.DeniedRow);
            }
            string xml = FaCardEai.Add(plan, currency);
            DryRun.Set("fa_card", FaCardSql.Preview(plan, currency));
            FaCardFound found = new FaCardFound();
            ApiResult result = ArcPartnerRun.Eai(ctx, req, "add", EaiDistribute.What, delegate { return EaiDistribute.Call(ctx, xml); },
                delegate(object fresh) { return FaCardSql.Created(fresh, plan, found); });
            result.Body["code"] = found.CardNum;
            result.Body["asset_num"] = plan.AssetNum;
            result.Body["card_id"] = found.CardId;
            CoRows.Note(ctx.Item, "fa_card add card=" + found.CardNum);
            return result;
        }

        static ApiResult Delete(WorkContext ctx, ArcReq req, FaPeriod period)
        {
            string asset = FaCardSql.CheckUndo(ctx, req.Code, period);
            if (asset.Length == 0)
            {
                throw ArcGuard.State("卡片 " + req.Code + " 没有资产编号，U8 导入无法按资产编号删除，请在 U8 客户端处理");
            }
            string xml = FaCardEai.Delete(asset);
            return ArcPartnerRun.Eai(ctx, req, "delete", EaiDistribute.What, delegate { return EaiDistribute.Call(ctx, xml); },
                delegate(object fresh) { return FaCardSql.Gone(fresh, req.Code); });
        }
    }

    // ArcRoutes 的分派入口：固定资产卡片、设备台账的登录前校验和写入；别的档案返回 null / 什么也不做。
    internal static class FaWrites
    {
        internal static void Check(ArcReq req)
        {
            if (ArcFa.Is(req.Kind))
            {
                FaCardReq.Check(req);
            }
            else if (ArcEq.Is(req.Kind))
            {
                ArcEq.Check(req);
            }
        }

        internal static ApiResult Write(WorkContext ctx, ArcReq req)
        {
            if (ArcFa.Is(req.Kind))
            {
                return ArcFaWrite.Write(ctx, req);
            }
            return ArcEq.Is(req.Kind) ? ArcEq.Write(ctx, req) : null;
        }
    }
}
