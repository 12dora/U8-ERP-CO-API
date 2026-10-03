using System;
using System.Collections.Generic;

namespace U8Co
{
    // K12 退货申请单新增（vouchers/create，type=sale_return_apply）：每行参照一条已审核、未关闭的蓝字发货单行。
    // 销售 CO VT 34、卡片 SA31：GetDefaultVoucherDom 取模板，表头照第一条发货行所在发货单的参照视图（Sales_FHD_T）抄，
    // 表体照发货行参照视图（Sales_FHD_W）抄，数量写负数、金额按比例缩放（负）后单行 BodyCheck，挂原发货行（iDLsID、cDLCode、
    // iCorID，同 U8 申请单 iCorID = iDLsID），订单行由视图带过来。Save 由 U8 自行提交（ReturnsApplyTran）：调用前重查来源与
    // 可申请数量，GetVoucherNO 取号、Save(头, 体, (short)0, "")，之后核对新单行数与负数量合计（不符 504）。所有行须同一客户、同一币种。
    internal static partial class SaleGen
    {
        const string AppSrcSql = "select h.DLID, h.cDLCode, h.cCusCode, h.cexch_name, h.cVerifier, h.cCloser, h.cVouchType, "
            + "h.bReturnFlag, h.bFirst, d.cSCloser from DispatchLists d inner join DispatchList h on h.DLID=d.DLID where d.iDLsID=?";
        const string AppReasonSql = "select top 1 cReasonCode from Reason where cReasonCode=?";
        const string AppCodeSql = "select top 1 ID from SA_ReturnsApplyMain where cCode=? order by ID desc";
        const string AppNoSql = "select cCode from SA_ReturnsApplyMain where ID=?";
        const string AppSavedSql = "select count(*) as n, sum(case when iQuantity >= 0 then 1 else 0 end) as pos, "
            + "convert(varchar(40), isnull(sum(-iQuantity),0)) as back from SA_ReturnsApplyDetail where ID=?";
        const string AppHeadSkip = "id,dlid,ccode,cdlcode,ufts,cmaker,cverifier,dverifydate,ivtid,ddate,editprop,cmemo,ccloser,"
            + "iswfcontrolled,iverifystate,ireturncount,icreditstate,cmodifier,dmoddate,dcreatesystime,dverifysystime,"
            + "dmodifysystime,iprintcount,csysbarcode,ccurrentauditor,iflowid,cvouchtype,breturnflag,bfirst,sbvid,csbvcode,csocode";
        const string AppLineSkip = "autoid,id,dlid,editprop,ufts,corufts,cbsysbarcode,cscloser,fretqty,fretsum,isaleoutid,"
            + "isaleoutrowno,csaleoutcode,cverifymemo,icorid,irowno,ccode,cdlcode,idlsid,creasoncode,cmemo,cbodymemo";

        internal static ApiResult CreateApply(WorkContext ctx, VoucherKind kind, Dictionary<string, object> head, object[] lines)
        {
            Dictionary<string, object> fields = head ?? new Dictionary<string, object>();
            List<ApplyPlanLine> plan = PlanApply(ctx.Conn, lines ?? new object[0]);
            object srcHead = null;
            object sys = null;
            object co = null;
            object[] doms = new object[2];
            string code;
            int id;
            try
            {
                srcHead = LoadRef(ctx.Conn, InvRefHead, plan[0].DlId, "参照视图没有该发货单");
                SaSession.OpenSa(ctx.Conn, ctx.Session.Login, ReturnsApplyRead.SaVt, out sys, out co);
                SoDom.Templates(co, ctx.Conn, ReturnsApplyRead.Card, ctx.Item, doms);
                FillAppHead(ctx, sys, doms, srcHead, fields);
                DropAll(doms[1]);
                for (int i = 0; i < plan.Count; i++)
                {
                    FillAppLine(ctx, co, doms, plan[i], i);
                }
                id = SaveApply(ctx, co, doms, plan, out code);
            }
            finally
            {
                ComUtil.Final(srcHead);
                SaSession.Release(sys, co, doms);
            }
            return ReturnsApply.Saved(ctx, kind, id, code);
        }

        static List<ApplyPlanLine> PlanApply(object conn, object[] lines)
        {
            List<ApplyPlanLine> plan = new List<ApplyPlanLine>();
            string party = null;
            for (int i = 0; i < lines.Length; i++)
            {
                Dictionary<string, object> map = (Dictionary<string, object>)lines[i];
                int lineId = ReturnsApplyReq.IdOf(map, "source_line_id", i);
                Dictionary<string, object> src = Rows.One(conn, AppSrcSql, new object[] { lineId });
                if (src == null)
                {
                    throw BridgeException.BadField("lines." + i + ".source_line_id", "明细行不存在");
                }
                RequireAppSource(src);
                string key = CoRows.Col(src, "cCusCode") + "|" + CoRows.Col(src, "cexch_name");
                if (party != null && party != key)
                {
                    throw new BridgeException(400, "bad_request", "参照的发货单行必须是同一客户、同一币种");
                }
                party = key;
                ApplyPlanLine line = new ApplyPlanLine();
                line.LineId = lineId;
                line.DlId = CoRows.AsId(CoRows.Col(src, "DLID"));
                line.DlCode = CoRows.Col(src, "cDLCode");
                line.Qty = ReturnsApplyReq.Qty(map, "quantity", i);
                line.Fields = map;
                ReturnsApplyDom.RequireRoom(conn, lineId, null, line.Qty);
                RequireReason(conn, TextOf(map, "creasoncode"), i);
                plan.Add(line);
            }
            return plan;
        }

        // 来源：蓝字（bReturnFlag=0、cVouchType 05）、非期初、已审核、整单和行都未关闭。
        static void RequireAppSource(Dictionary<string, object> src)
        {
            if (CoRows.FlagOf(src, "bReturnFlag") || CoRows.Col(src, "cVouchType") != "05")
            {
                throw new BridgeException(400, "bad_request", "仅支持参照蓝字发货单");
            }
            if (CoRows.FlagOf(src, "bFirst"))
            {
                throw new BridgeException(400, "bad_request", "不支持期初发货单");
            }
            if (CoRows.Col(src, "cVerifier").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            if (CoRows.Col(src, "cCloser").Length > 0 || CoRows.Col(src, "cSCloser").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
        }

        static void RequireReason(object conn, string code, int index)
        {
            if (code.Length > 0 && Rows.Scalar(conn, AppReasonSql, new object[] { code }) == null)
            {
                throw BridgeException.BadField("lines." + index + ".creasoncode", "退货原因码不存在：" + code);
            }
        }

        static void FillAppHead(WorkContext ctx, object sys, object[] doms, object srcHead, Dictionary<string, object> head)
        {
            DropExtra(doms[0]);
            object row = OneRow(doms[0]);
            List<object> srcRows = DomRows.RowsOf(srcHead);
            if (srcRows.Count < 1)
            {
                throw new BridgeException(409, "state_mismatch", "参照视图没有该发货单");
            }
            DomRows.CopyInto(doms[0], row, srcRows[0], AppHeadSkip);
            List<string> schema = DomRows.Schema(doms[0]);
            // 表头主键属性必须在，值为空串。
            DomRows.Set(doms[0], row, "id", "", schema);
            DomRows.Set(doms[0], row, "ccode", "", schema);
            ApplyFields(doms[0], row, head, true);
            if (TextOf(head, "ddate").Length == 0)
            {
                DomRows.Set(doms[0], row, "ddate", LoginDate(ctx), schema);
            }
            DomRows.Set(doms[0], row, "editprop", "A", schema);
            DomRows.Set(doms[0], row, "cMaker", User(ctx), schema);
            SoSave.Stamp(ctx, sys, doms[0], ReturnsApplyRead.Card);
        }

        static void FillAppLine(WorkContext ctx, object co, object[] doms, ApplyPlanLine line, int index)
        {
            object src = null;
            try
            {
                src = LoadRef(ctx.Conn, InvRefBody, line.LineId, "参照视图没有该发货单行");
                List<object> srcRows = DomRows.RowsOf(src);
                if (srcRows.Count < 1)
                {
                    throw new BridgeException(409, "state_mismatch", "参照视图没有该发货单行");
                }
                DomRows.AddRow(doms[1]);
                DomRows.CopyInto(doms[1], At(doms[1], index), srcRows[0], AppLineSkip);
                decimal full = Dec(DomRows.Get(srcRows[0], "iquantity"));
                List<string> schema = DomRows.Schema(doms[1]);
                ReturnsApplyDom.SetQty(doms[1], At(doms[1], index), line.Qty, full == 0m ? 0m : -line.Qty / full, schema);
                ReturnsApplyDom.Recalc(ctx, co, doms, At(doms[1], index));
                PinAppLine(doms[1], At(doms[1], index), line, index, schema);
            }
            finally
            {
                ComUtil.Final(src);
            }
        }

        // 调用方字段（仓库、备注、原因码、自定义项）必须在模板 schema 里；再挂原发货行、行号、editprop=A。
        static void PinAppLine(object body, object row, ApplyPlanLine line, int index, List<string> schema)
        {
            foreach (string key in line.Fields.Keys)
            {
                if (!Meta(key) && !ReturnsApplyDom.Has(schema, key))
                {
                    throw BridgeException.BadField("lines." + index + "." + key, "未知字段 " + key);
                }
            }
            ApplyFields(body, row, line.Fields, false);
            string dl = Id(line.LineId);
            DomRows.Set(body, row, "idlsid", dl, schema);
            DomRows.Set(body, row, "icorid", dl, schema);
            DomRows.Set(body, row, "cdlcode", line.DlCode, schema);
            DomRows.Set(body, row, "irowno", Id(index + 1), schema);
            DomRows.Set(body, row, "editprop", "A", schema);
            if (DomRows.Get(row, "cwhcode").Trim().Length == 0)
            {
                throw BridgeException.BadField("lines." + index + ".cwhcode", "必须指定仓库");
            }
        }

        // Save 自行提交（ReturnsApplyTran）：调用前不加锁重查来源发货单（表头、行仍已审核、未关闭）和可申请数量（同一发货行的
        // 多行合计），GetVoucherNO 取号，Save；之后找新主键、核对行，失败一律 504（已提交，回滚不了）。
        static int SaveApply(WorkContext ctx, object co, object[] doms, List<ApplyPlanLine> plan, out string savedCode)
        {
            savedCode = "";
            RecheckApply(ctx.Conn, plan);
            // 预演停在取号之前：GetVoucherNO 会占用单据号。
            DryRun.Stop(ctx, ReturnsApplyTran.What + " Save");
            // GetVoucherNO 不给号时交给 Save（按 U8 的编号规则），保存后按主键回读单号。
            string no = SoSave.VoucherNo(co, doms, ctx.Item, "cCode");
            if (no.Length > 0)
            {
                SoDom.SetAttr(doms[0], "cCode", no);
            }
            object[] args = ReturnsApplyTran.Save(ctx, co, doms, (short)0, "新增退货申请单 " + no);
            int id = 0;
            StockCall.AfterCheck(ctx, delegate(object conn)
            {
                id = AppNewId(conn, args[3], doms[0], no);
                no = (Rows.Scalar(conn, AppNoSql, new object[] { id }) ?? no).Trim();
                RequireAppSaved(conn, ctx.Item, id, plan);
            }, "退货申请单 " + no);
            savedCode = no;
            DocMark.Created(ctx.Conn, ReturnsApplyRead.KindName, id, no);
            return id;
        }

        // 调用前的重查（不能带锁：Save 自己开事务）。同一发货行的多行合计后比可申请数量。
        static void RecheckApply(object conn, List<ApplyPlanLine> plan)
        {
            Dictionary<int, decimal> sum = new Dictionary<int, decimal>();
            for (int i = 0; i < plan.Count; i++)
            {
                Dictionary<string, object> src = Rows.One(conn, AppSrcSql, new object[] { plan[i].LineId });
                if (src == null)
                {
                    throw new BridgeException(409, "state_mismatch", "原发货单行不存在");
                }
                RequireAppSource(src);
                decimal had;
                sum.TryGetValue(plan[i].LineId, out had);
                sum[plan[i].LineId] = had + plan[i].Qty;
            }
            foreach (KeyValuePair<int, decimal> pair in sum)
            {
                ReturnsApplyDom.RequireRoom(conn, pair.Key, null, pair.Value);
            }
        }

        // 新主键：Save 的 vNewID，其次表头 ID，再按单号找（Save 已自行提交，找不到经 StockCall.AfterCheck 报 504）。
        static int AppNewId(object conn, object newId, object head, string no)
        {
            int id = CoRows.AsId(newId);
            if (id <= 0)
            {
                id = CoRows.AsId(SoDom.Attr(head, "ID"));
            }
            if (id <= 0 && no.Length > 0)
            {
                id = CoRows.AsId(Rows.Scalar(conn, AppCodeSql, new object[] { no }));
            }
            if (id <= 0)
            {
                throw new BridgeException(409, "u8_rejected", "U8 没有返回退货申请单主键");
            }
            return id;
        }

        static void RequireAppSaved(object conn, WorkItem item, int id, List<ApplyPlanLine> plan)
        {
            Dictionary<string, object> row = Rows.One(conn, AppSavedSql, new object[] { id });
            decimal want = 0m;
            for (int i = 0; i < plan.Count; i++)
            {
                want = want + plan[i].Qty;
            }
            decimal n = row == null ? 0m : PuInv.Num(CoRows.Col(row, "n"));
            decimal pos = row == null ? 0m : PuInv.Num(CoRows.Col(row, "pos"));
            decimal back = row == null ? 0m : PuInv.Num(CoRows.Col(row, "back"));
            if (n == plan.Count && pos == 0m && Near(back, want))
            {
                return;
            }
            NoteBack(item, "退货申请单 " + Id(id) + " 行数 " + Num(n) + " 非负行 " + Num(pos) + " 数量合计 " + Num(back) + "/" + Num(want));
            throw new BridgeException(409, "u8_rejected", "U8 保存的退货申请单行与请求不一致");
        }

        sealed class ApplyPlanLine
        {
            internal int LineId;
            internal int DlId;
            internal string DlCode;
            internal decimal Qty;
            internal Dictionary<string, object> Fields;
        }
    }
}
