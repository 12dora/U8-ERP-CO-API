using System;
using System.Collections.Generic;

namespace U8Co
{
    // USERPCO.VoucherCO。审核列用单据类型上的审核人/审核日期；调拨是 cVerifyPerson / dVerifyDate。
    internal static partial class StockCo
    {
        public static ApiResult Load(WorkContext ctx, VoucherKind kind, int id)
        {
            RequireSt(kind);
            if (ReadHead(ctx.Conn, kind, id, false, false) == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            object co = null;
            StockLoaded loaded = new StockLoaded();
            try
            {
                co = StockCall.OpenCo(ctx);
                loaded.Head = Rows.NewDom();
                loaded.Body = Rows.NewDom();
                loaded.Pos = Rows.NewDom();
                StockCall.CallLoad(ctx, co, kind, id, loaded);
                List<Dictionary<string, object>> heads = Rows.FromDom(loaded.Head, 1);
                if (heads.Count == 0)
                {
                    throw new BridgeException(404, "not_found", "单据不存在");
                }
                return StockMsg.Loaded(kind, id, heads[0], Rows.FromDom(loaded.Body, 501));
            }
            finally
            {
                ComUtil.Final(loaded.Pos);
                ComUtil.Final(loaded.Body);
                ComUtil.Final(loaded.Head);
                ComUtil.Final(co);
            }
        }

        public static ApiResult Verify(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            RequireSt(kind);
            if (action != "verify" && action != "unverify")
            {
                throw new BridgeException(400, "bad_request", "action 只能是 verify 或 unverify");
            }
            bool undo = action == "unverify";
            Dictionary<string, object> before = ReadHead(ctx.Conn, kind, id, false, undo);
            if (before == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            RefuseBefore(before, action, kind);
            Gate(ctx.Conn, kind, before);
            string ufts = StockCall.Ufts(ctx.Conn, kind, id);
            object co = null;
            object msg = null;
            object dict = null;
            object made = null;
            object wheres = null;
            try
            {
                co = StockCall.OpenCo(ctx);
                msg = Rows.NewDom();
                int[] refs;
                List<Dictionary<string, object>> generated = null;
                if (kind.StType == "12" && !undo)
                {
                    object[] args = StockCall.TransferVerifyArgs(kind, id, ctx.Conn, ufts, msg, out refs);
                    dict = args[11];
                    wheres = args[9];
                    StockCall.RunCo(ctx, co, "Verify", args, refs, ufts);
                    made = args[11];
                    wheres = args[9];
                    generated = ReadGenerated(ctx, made == null ? dict : made);
                }
                else
                {
                    string method = action == "verify" ? "Verify" : "UnVerify";
                    object[] args = StockCall.VerifyArgs(kind, id, ctx.Conn, ufts, msg, out refs);
                    // 期初结存审核：提交前把 dVeriDate 改成单据日期（StockOpening.WithCheck，其余类型原样）。
                    StockCall.RunAt(ctx, co, StockOpening.WithCheck(StockCall.AtFor(method, args, refs, ufts), kind, id, undo));
                }
                Dictionary<string, object> after = Reread(ctx, kind, id);
                RefuseAfter(after, action, ctx.Session.OperatorName, kind);
                return StockMsg.Verified(ctx.Item, kind, id, action, after, generated);
            }
            finally
            {
                ReleaseSlot(wheres, ctx.Conn);
                if (made != null && !object.ReferenceEquals(made, dict))
                {
                    ComUtil.Final(made);
                }
                ComUtil.Final(dict);
                ComUtil.Final(msg);
                ComUtil.Final(co);
            }
        }

        public static ApiResult Create(WorkContext ctx, VoucherKind kind,
            Dictionary<string, object> head, object[] lines)
        {
            RequireSt(kind);
            if (kind.StType != "12" && (!kind.Creatable || !Insertable(kind)))
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持新增");
            }
            object co = null;
            object domH = null;
            object domB = null;
            object pos = null;
            object msg = null;
            try
            {
                string maker = ctx.Session.OperatorName == null ? "" : ctx.Session.OperatorName.Trim();
                string billDate = ctx.Item == null || ctx.Item.Date == null ? "" : ctx.Item.Date.Trim();
                domH = StockDom.BuildHead(ctx, kind, head, maker, billDate);
                domB = StockDom.BuildBody(ctx.Conn, kind, lines);
                pos = Rows.NewDom();
                msg = Rows.NewDom();
                co = StockCall.OpenCo(ctx);
                string code = StockCall.NewCode(ctx, co, kind, HeadSeeds(domH));
                string codeAttr = kind.StType == "12" ? "cTVCode" : "cCode";
                StockDom.SetHeadValue(domH, codeAttr, code);
                int[] refs;
                object[] args = StockCall.InsertArgs(kind, StockCall.Forms(domH, domB, pos, ctx.Conn, msg), out refs);
                StockCall.RunCo(ctx, co, "Insert", args, refs, null);
                return Inserted(ctx, kind, code, args[6]);
            }
            finally
            {
                ComUtil.Final(msg);
                ComUtil.Final(pos);
                ComUtil.Final(domB);
                ComUtil.Final(domH);
                ComUtil.Final(co);
            }
        }

        public static ApiResult Delete(WorkContext ctx, VoucherKind kind, int id)
        {
            RequireSt(kind);
            if (!CanDelete(kind))
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持删除");
            }
            // 采购入库单要带出记账人，删除前拒绝已记账（RefuseBuy）。
            Dictionary<string, object> before = ReadHead(ctx.Conn, kind, id, true, kind.StType == "01");
            if (before == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (StockMsg.Col(before, kind.VerifierColumn).Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核");
            }
            RefuseDelete(ctx.Conn, kind, id, before);
            string ufts = StockCall.Ufts(ctx.Conn, kind, id);
            object co = null;
            object msg = null;
            try
            {
                co = StockCall.OpenCo(ctx);
                msg = Rows.NewDom();
                int[] refs;
                object[] args = StockCall.VerifyArgs(kind, id, ctx.Conn, ufts, msg, out refs);
                StockAt at = StockCall.AtFor("Delete", args, refs, ufts);
                if (kind.StType == "01" && StockMsg.Col(before, "cSource") == "来料检验单")
                {
                    StockGen.GuardQmUndo(at, id);
                }
                else if (kind.StType == "01" && StockMsg.Col(before, "cSource") == "采购到货单")
                {
                    StockGen.GuardArrUndo(at, id);
                }
                else if (kind.StType == "12")
                {
                    // 参照调拨申请单生成的调拨单：核对申请单行累计调拨数量退回（没有 iTRIds 的行不查）。
                    StockGen.GuardTrUndo(at, id);
                }
                // 有货位记录时事务里先 ClearPosition 再以 bList=true 删除（u8-notes §8）。
                StockPosGuard.ClearOnDelete(ctx, co, at, kind, id);
                StockCall.RunAt(ctx, co, at);
                if (Reread(ctx, kind, id) != null)
                {
                    throw new BridgeException(409, "state_mismatch", "U8 返回成功但回读状态不符");
                }
                return StockMsg.Gone(kind, id);
            }
            finally
            {
                ComUtil.Final(msg);
                ComUtil.Final(co);
            }
        }

        internal static void Gate(object conn, VoucherKind kind, Dictionary<string, object> head)
        {
            // 期初结存单（34）没有审批流，AuditBizObjects 里也没有 rdrecord34（已在测试账套核对），只看单据上的标志。
            bool flow = !StockOpening.Handles(kind);
            if (flow && !AdoXml.HasBizObject(conn, kind.HeadTable))
            {
                throw new BridgeException(409, "workflow_unknown", "AuditBizObjects 没有该单据表");
            }
            if (Values.Flag(StockMsg.Col(head, "iswfcontrolled")) || (flow && AdoXml.WorkflowReleased(conn, kind.HeadTable)))
            {
                throw new BridgeException(409, "workflow_enabled", "单据已启用审批流，本期不支持");
            }
        }

        static void RefuseBefore(Dictionary<string, object> head, string action, VoucherKind kind)
        {
            bool verified = StockMsg.Col(head, kind.VerifierColumn).Length > 0;
            if (action == "verify" && verified)
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核");
            }
            if (action != "unverify")
            {
                return;
            }
            if (!verified)
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            if (StockMsg.Col(head, "cbaccounter").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已记账，不能弃审");
            }
            // 调拨单、形态转换单、盘点单审核生成的 08/09 随来源单弃审删除，不能单独弃审（StockMisc.MadeBy）。
            string maker = StockMisc.MadeBy(kind, head);
            if (maker.Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "该单据由" + maker + "生成，不能弃审");
            }
        }

        // 08/09 上 cSource=调拨 或 cBusType=调拨入库/调拨出库，成对来自调拨单。
        internal static bool FromTransfer(Dictionary<string, object> head)
        {
            string source = StockMsg.Col(head, "cSource");
            string bus = StockMsg.Col(head, "cBusType");
            if (source.IndexOf("调拨", StringComparison.Ordinal) >= 0)
            {
                return true;
            }
            return bus.IndexOf("调拨", StringComparison.Ordinal) >= 0;
        }

        static void RefuseAfter(Dictionary<string, object> head, string action, string operatorName, VoucherKind kind)
        {
            if (head == null)
            {
                throw new BridgeException(409, "state_mismatch", "审核状态与操作不一致");
            }
            string verifier = StockMsg.Col(head, kind.VerifierColumn);
            string date = StockMsg.Col(head, kind.VerifyDateColumn);
            if (action != "verify")
            {
                if (verifier.Length > 0 || date.Length > 0)
                {
                    throw new BridgeException(409, "state_mismatch", "弃审后审核人仍在");
                }
                return;
            }
            if (verifier.Length == 0 || date.Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "审核后审核人或审核日期为空");
            }
            string name = operatorName == null ? "" : operatorName.Trim();
            if (!string.Equals(verifier, name, StringComparison.Ordinal))
            {
                throw new BridgeException(409, "state_mismatch", "审核人与登录操作员姓名不一致");
            }
        }

        // 走 StockDom 建 DOM 的新增：其他入出库、无来源采购入库，加无来源销售出库（StockSaleOut 先做预检）。
        static bool Insertable(VoucherKind kind)
        {
            string st = kind.StType;
            return st == "08" || st == "09" || st == "32" || StockDom.PurType(kind);
        }

        static ApiResult Inserted(WorkContext ctx, VoucherKind kind, string code, object rawId)
        {
            int newId;
            Dictionary<string, object> after;
            try
            {
                newId = StockCall.NewId(ctx, rawId);
                after = newId > 0 ? Reread(ctx, kind, newId) : null;
                if (after == null)
                {
                    newId = IdByCode(ctx, kind, code);
                    after = newId > 0 ? Reread(ctx, kind, newId) : null;
                }
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                // 单据已经提交；回读失败时结果未知，不能报成普通错误让调用方重试。
                CoRows.Note(ctx.Item, "Inserted " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", Unknown(code));
            }
            if (after == null)
            {
                throw new BridgeException(504, "outcome_unknown", Unknown(code));
            }
            return StockMsg.Created(kind, newId, after);
        }

        // 编号种子取模板落定后的表头，含 cBusType、cSource、cVouchType。
        static Dictionary<string, object> HeadSeeds(object dom)
        {
            List<Dictionary<string, object>> rows = Rows.FromDom(dom, 1);
            if (rows == null || rows.Count == 0)
            {
                throw new BridgeException(500, "internal", "表头模板没有行");
            }
            return rows[0];
        }

        static int IdByCode(WorkContext ctx, VoucherKind kind, string code)
        {
            string no = code == null ? "" : code.Trim();
            if (no.Length == 0)
            {
                return 0;
            }
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                // 形态转换单与组装、拆卸同表，按单号找时只认 cVouchType='15'。
                string sql = "select " + kind.IdColumn + " from " + kind.HeadTable
                    + " where " + kind.CodeColumn + "=?" + (kind.StType == "15" ? " and cVouchType=N'15'" : "");
                List<Dictionary<string, object>> rows = Rows.Query(conn, sql, new object[] { no }, 2);
                if (rows == null || rows.Count != 1)
                {
                    return 0;
                }
                return CoRows.AsId(CoRows.Col(rows[0], kind.IdColumn));
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static string Unknown(string code)
        {
            string no = code == null ? "" : code.Trim();
            if (no.Length == 0)
            {
                return "已保存但未能确定单据标识";
            }
            return "已保存但未能确定单据标识，单号 " + no;
        }

        internal static Dictionary<string, object> ReadHead(object conn, VoucherKind kind, int id, bool source, bool booked)
        {
            return Rows.One(conn, HeadSql(kind, source, booked), new object[] { id });
        }

        static Dictionary<string, object> Reread(WorkContext ctx, VoucherKind kind, int id)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                return Rows.One(conn, HeadSql(kind, false, false), new object[] { id });
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static string HeadSql(VoucherKind kind, bool source, bool booked)
        {
            string sql = "select " + kind.CodeColumn + ", " + kind.VerifierColumn + ", "
                + kind.VerifyDateColumn + ", iswfcontrolled";
            if (source || booked)
            {
                sql = sql + ", cSource";
            }
            if (booked && kind.StType == "12")
            {
                sql = sql + ", cAccounter as cbaccounter";
            }
            else if (booked)
            {
                sql = sql + ", cBusType, " + BookedSql(kind);
            }
            return sql + " from " + kind.HeadTable + " where " + kind.IdColumn + "=?";
        }

        static bool CanDelete(VoucherKind kind)
        {
            if (kind == null)
            {
                return false;
            }
            string st = kind.StType;
            if (st == "01" || st == "12" || st == "32")
            {
                return true;
            }
            return kind.Deletable && Own(st);
        }

        // 只能删改来源为库存的类型：其他入库、其他出库、期初结存（34，只删不改，StockOpening）。
        static bool Own(string st)
        {
            return st == "08" || st == "09" || st == StockOpening.StType;
        }

        // 修改生单来的单据（StockEditSrc）也走这道闸门。
        internal static void RefuseDelete(object conn, VoucherKind kind, int id, Dictionary<string, object> before)
        {
            string st = kind.StType;
            if (Own(st))
            {
                RefuseOwn(before);
                return;
            }
            if (st == "12")
            {
                return;
            }
            if (st == "01")
            {
                RefuseBuy(conn, kind, id, before);
                return;
            }
            RefuseShip(conn, kind, id, before);
        }

        static void RefuseOwn(Dictionary<string, object> before)
        {
            string source = StockMsg.Col(before, "cSource");
            if (source.Length > 0 && source != "库存")
            {
                throw new BridgeException(409, "state_mismatch", "只能删除来源为库存的单据");
            }
        }

        static void RefuseBuy(object conn, VoucherKind kind, int id, Dictionary<string, object> before)
        {
            // 来源采购到货单的（参照到货单生成的蓝字、参照采购退货单生成的红字）删除时核对到货行 fValidInQuan 退回（GuardArrUndo）。
            ExpectSource(before, "只能删除来源为采购订单、来料检验单、采购到货单或库存的单据",
                "采购订单", "来料检验单", "采购到货单", "库存");
            StockPurIn.RefuseDeleteState(conn, id, before);
            if (Down(conn, InvoiceSql(kind), id) || Down(conn, SettleSql(kind), id))
            {
                throw new BridgeException(409, "state_mismatch", "单据已有下游单据");
            }
        }

        static void RefuseShip(object conn, VoucherKind kind, int id, Dictionary<string, object> before)
        {
            if (kind.StType != "32")
            {
                return;
            }
            // 无来源（库存）的也能删，没有发货单回写；两种都挡已开票的。
            ExpectSource(before, "只能删除来源为发货单或库存的单据", "发货单", "库存");
            if (Down(conn, SaleSql(kind), id))
            {
                throw new BridgeException(409, "state_mismatch", "单据已有下游单据");
            }
        }

        static void ExpectSource(Dictionary<string, object> before, string message, params string[] ok)
        {
            string source = StockMsg.Col(before, "cSource");
            for (int i = 0; i < ok.Length; i++)
            {
                if (source == ok[i])
                {
                    return;
                }
            }
            throw new BridgeException(409, "state_mismatch", message);
        }

        static List<Dictionary<string, object>> ReadGenerated(WorkContext ctx, object dict)
        {
            try
            {
                return StockCall.Generated(ctx, dict);
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "generated " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "审核已完成但未能读出调拨生成的单据");
            }
        }

        static void ReleaseSlot(object slot, object conn)
        {
            if (slot == null || object.ReferenceEquals(slot, conn))
            {
                return;
            }
            ComUtil.Final(slot);
        }

        // 记账人在表体。任一行非空，就在预检结果里带出 cbaccounter。
        static string BookedSql(VoucherKind kind)
        {
            return "(select top 1 b.cbaccounter from " + kind.BodyTable + " b where b."
                + kind.BodyFk + "=" + kind.HeadTable + "." + kind.IdColumn
                + " and nullif(ltrim(rtrim(b.cbaccounter)), N'') is not null) as cbaccounter";
        }

        static void RequireSt(VoucherKind kind)
        {
            if (kind == null || StockDom.HeadView(kind) == null)
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持");
            }
        }
    }
}
