using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 生单来的库存单据修改：销售出库（32，来源发货单；来源库存的转 StockEdit.Update，见 StockEditSrcSale）、产成品入库（10，产品检验单 / 产品不良品处理单）、
    // 材料出库（11，生产订单）、有来源的采购入库（01，采购订单 / 来料检验单；来源库存的仍走 StockEdit）。
    // 只收未审核单据，闸门照该类型的删除。Load 之后只改备注、自定义项和数量（只减不增），不新增行，
    // 再调 12 个参数的 Update（同 08/09/01）。事务里先带 UPDLOCK 读来源累计数和本单各行，
    // 保存后按本单各行的实际变化核对来源累计数（StockEditSrcCheck），不符回滚 409。
    internal static partial class StockEditSrc
    {
        const string StateSql = "select convert(varchar(5), isnull(bredvouch,0)) as red,"
            + " convert(varchar(5), isnull(bIsSTQc,0)) as qc from {0} where ID=?";

        internal static ApiResult Update(WorkContext ctx, VoucherKind kind, int id,
            Dictionary<string, object> head, object[] lines)
        {
            Require(kind);
            if (head == null)
            {
                head = new Dictionary<string, object>();
            }
            if (lines == null)
            {
                lines = new object[0];
            }
            Dictionary<string, object> before = StockCo.ReadHead(ctx.Conn, kind, id, true, true);
            if (before == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            string source = StockMsg.Col(before, "cSource");
            if (kind.Name == "purchase_in" && source != "采购订单" && source != "来料检验单")
            {
                // 来源库存（以及其他来源的拒绝口径）保持原有的修改路径。
                return StockEdit.Update(ctx, kind, id, head, lines);
            }
            if (kind.Name == "sale_out" && source != "发货单")
            {
                // 来源库存的销售出库单走 StockEdit（可增删行），其他来源 409（StockEditSrcSale）。
                return SaleOutStock(ctx, kind, id, before, head, lines);
            }
            Gate(ctx, kind, id, before, source);
            EditReq req = new EditReq();
            req.Kind = kind;
            req.Id = id;
            req.Source = source;
            req.Head = head;
            req.Lines = lines;
            req.QtyLocked = kind.Name == "product_in" && MfgGen.IsRejectSource(source);
            return Save(ctx, req);
        }

        // true：该字段在修改时可写。采购入库按单据来源分两套：meta 给来源库存那一套（更宽），
        // 有来源的单据在运行时只收下面这一套，名单外 400。
        internal static bool MetaAllowed(VoucherKind kind, bool head, string lowerField)
        {
            if (kind == null)
            {
                return false;
            }
            if (kind.Name == "purchase_in")
            {
                return StockDom.MetaAllowed(kind, head, lowerField);
            }
            if (kind.Name == "sale_out")
            {
                // 同采购入库：meta 给来源库存那一套（仓库不能改，除外）；来源发货单的在运行时只收 SrcField。
                return SaleOutMeta(kind, head, lowerField);
            }
            return SrcField(head, lowerField);
        }

        // 表头：备注、自定义项 1–16；表体：数量（只减）、行备注、自定义项 22–37。仓库、存货、批次、货位都不改。
        static bool SrcField(bool head, string name)
        {
            string low = name == null ? "" : name.Trim().ToLowerInvariant();
            if (head)
            {
                return low == "cmemo" || ArapReq.Span(low, "cdefine", 1, 16);
            }
            return low == "iquantity" || low == "cbmemo" || ArapReq.Span(low, "cdefine", 22, 37);
        }

        static void Require(VoucherKind kind)
        {
            string name = kind == null ? "" : kind.Name;
            if (name != "sale_out" && name != "product_in" && name != "material_out" && name != "purchase_in")
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持修改");
            }
        }

        // 闸门：未审核、非红字非期初、未记账，再按类型走删除的来源和下游闸门，最后查审批流。
        static void Gate(WorkContext ctx, VoucherKind kind, int id, Dictionary<string, object> before, string source)
        {
            object conn = ctx.Conn;
            if (StockMsg.Col(before, kind.VerifierColumn).Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核");
            }
            if (StockMsg.Col(before, "cbaccounter").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已记账，不能修改");
            }
            RefuseState(conn, kind, id);
            if (kind.Name == "product_in" || kind.Name == "material_out")
            {
                MfgGen.RefuseEdit(conn, kind, id, source);
            }
            else
            {
                StockCo.RefuseDelete(conn, kind, id, before);
            }
            if (kind.Name == "purchase_in")
            {
                StockPurIn.RefuseSourcedEdit(ctx, id);
            }
            StockCo.Gate(conn, kind, before);
        }

        static void RefuseState(object conn, VoucherKind kind, int id)
        {
            string sql = string.Format(CultureInfo.InvariantCulture, StateSql, kind.HeadTable);
            Dictionary<string, object> state = Rows.One(conn, sql, new object[] { id });
            if (state == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (CoRows.FlagOf(state, "red"))
            {
                throw new BridgeException(409, "state_mismatch", "红字单据不支持修改");
            }
            if (CoRows.FlagOf(state, "qc"))
            {
                throw new BridgeException(409, "state_mismatch", "期初单据不支持修改");
            }
        }

        static ApiResult Save(WorkContext ctx, EditReq req)
        {
            object co = null;
            object msg = null;
            StockLoaded loaded = new StockLoaded();
            try
            {
                co = StockCall.OpenCo(ctx);
                loaded.Head = Rows.NewDom();
                loaded.Body = Rows.NewDom();
                loaded.Pos = Rows.NewDom();
                StockCall.CallLoad(ctx, co, req.Kind, req.Id, loaded);
                if (loaded.Pos == null)
                {
                    loaded.Pos = Rows.NewDom();
                }
                msg = Rows.NewDom();
                req.Bins = StockPosGuard.BinnedLines(ctx.Conn, req.Kind, req.Id);
                Dictionary<string, decimal> plan = Apply(ctx.Conn, req, loaded);
                int[] refs;
                object[] args = StockCall.UpdateArgs(req.Kind,
                    StockCall.Forms(loaded.Head, loaded.Body, loaded.Pos, ctx.Conn, msg), out refs);
                StockAt at = StockCall.AtFor("Update", args, refs, null);
                SrcGuard guard = GuardOf(ctx, req, plan);
                at.Before = guard.Before;
                at.After = guard.After;
                StockCall.RunAt(ctx, co, at);
                return AfterSaved(ctx, req.Kind, req.Id);
            }
            finally
            {
                ComUtil.Final(msg);
                ComUtil.Final(loaded.Pos);
                ComUtil.Final(loaded.Body);
                ComUtil.Final(loaded.Head);
                ComUtil.Final(co);
            }
        }

        // 已提交。回读失败一律 504，不让调用方重投。
        static ApiResult AfterSaved(WorkContext ctx, VoucherKind kind, int id)
        {
            try
            {
                return EditMsg.Saved(ctx, kind, id, null, 0);
            }
            catch (Exception ex)
            {
                BridgeException bridge = ex as BridgeException;
                if (bridge != null && bridge.Code == "outcome_unknown")
                {
                    throw;
                }
                CoRows.Note(ctx.Item, "Saved " + ex.Message);
                throw new BridgeException(504, "outcome_unknown",
                    "已保存但未能回读单据，标识 " + id.ToString(CultureInfo.InvariantCulture));
            }
        }

        sealed class EditReq
        {
            public VoucherKind Kind;
            public int Id;
            public string Source;
            public Dictionary<string, object> Head;
            public object[] Lines;
            // 产品不良品处理单来源的产成品入库：U8 只收一次入库全部处理后数量，数量不能改。
            public bool QtyLocked;
            public HashSet<int> Bins;
        }
    }
}
