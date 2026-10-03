using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 红冲蓝字销售发票的 DOM 与保存（K11）。
    // DOM：VT 1 / 3 的 CO 上 GetNegaVouchData(26|27, 头, 体, 蓝字 SBVID, err, true)，by-ref {0,1,2,3,4,5}；返回 false 或
    // 没有表头 409。表体按 autoid（= 蓝字行 AutoID）只留请求的行、顺序照 U8；随后同红字发票参照退货单：清主键、时间戳、
    // 审核与累计列，表头 sbvid=""、breturnflag=1、editprop=A；每行 iquantity=−qty、金额按比例、单行 BodyCheck、iSBVID=蓝字行、
    // editprop=A。idisp、dlid、idlsid、isosid 照 U8 给的（不自己挂发货单行）。
    // 保存：CoTrans 里 UPDLOCK 重读蓝字行剩余 → GetVoucherNO → Save(h, b, 0, newId) → 提交前（StockCall.AfterCheck）只核对
    // 红字发票行：行数、全是负数量、合计、每行 iSBVID 指向的蓝字行与数量。蓝字行所挂发货行的 iSettleQuantity / fVeriBillQty、
    // 订单行 iKPQuantity、U8 是否另生成发货单（DispatchList.SBVID）只记审计（「红冲回写」），暂不核对。
    internal static partial class SaleGen
    {
        // 红字行 iSBVID 写蓝字行 AutoID（剩余与「已全部红冲」靠它）。BlueLinkRequired：保存后没留住就 409 回滚。
        static readonly bool BluePinLink = true;
        static readonly bool BlueLinkRequired = true;
        const string BlueNewSql = "select b.AutoID, isnull(b.iSBVID,0) as link, convert(varchar(40), -isnull(b.iQuantity,0)) as qty "
            + "from SaleBillVouchs b inner join SaleBillVouch h on h.SBVID=b.SBVID "
            + "where h.cSBVCode=? and h.cVouchType=? and h.bReturnFlag=1";
        // 回写快照：蓝字行所挂发货行的累计开票 / 已复核开票、订单行累计开票（合计）。
        internal const string BlueSnapSql = "select "
            + "convert(varchar(40), isnull((select sum(l.iSettleQuantity) from DispatchLists l where l.iDLsID in "
            + "(select b.iDLsID from SaleBillVouchs b where b.SBVID=?)),0)) as st, "
            + "convert(varchar(40), isnull((select sum(l.fVeriBillQty) from DispatchLists l where l.iDLsID in "
            + "(select b.iDLsID from SaleBillVouchs b where b.SBVID=?)),0)) as vb, "
            + "convert(varchar(40), isnull((select sum(s.iKPQuantity) from SO_SODetails s where s.iSOsID in "
            + "(select b.iSOsID from SaleBillVouchs b where b.SBVID=?)),0)) as kp";
        // 新红字发票表头 iDisp、U8 是否生成了反指它的发货单。
        internal const string BlueNewHeadSql = "select isnull(h.iDisp,0) as disp, "
            + "(select count(*) from DispatchList d where d.SBVID=h.SBVID) as made from SaleBillVouch h "
            + "where h.cSBVCode=? and h.cVouchType=? and h.bReturnFlag=1";

        static ApiResult BuildBlueRed(BlueJob job)
        {
            WorkContext ctx = job.Ctx;
            object sys = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                SaSession.OpenSa(ctx.Conn, ctx.Session.Login, job.Want.Vt, out sys, out co);
                BlueNegaDoms(job, co, doms);
                BlueHeadFill(job, sys, doms);
                BlueFillLines(job, co, doms);
                string code;
                int id = SaveBlueRed(job, co, doms, out code);
                return AfterSave(ctx, job.Kind, id, code, Echo(InvIdSql, "SBVID", BlueSourceName, job.BlueId));
            }
            finally
            {
                SaSession.Release(sys, co, doms);
            }
        }

        static void BlueNegaDoms(BlueJob job, object co, object[] doms)
        {
            WorkItem item = job.Ctx.Item;
            doms[0] = Rows.NewDom();
            doms[1] = Rows.NewDom();
            object[] args = new object[] { job.Want.Vouch, doms[0], doms[1], job.BlueId, "", true };
            object ret = ComUtil.CallRef(co, "GetNegaVouchData", args, new int[] { 0, 1, 2, 3, 4, 5 });
            CoRows.Swap(doms, 0, args[1]);
            CoRows.Swap(doms, 1, args[2]);
            string err = Values.Text(args[4]).Trim();
            CoRows.Note(item, "GetNegaVouchData " + job.Want.Vouch + " " + Values.Text(ret) + " " + err);
            if (!Values.Flag(ret) || DomRows.RowsOf(doms[0]).Count < 1)
            {
                throw new BridgeException(409, "u8_rejected", err.Length > 0 ? err : "U8 没有生成红字发票");
            }
            List<object> rows = DomRows.RowsOf(doms[1]);
            if (rows.Count > 0)
            {
                CoRows.Note(item, "U8 红字行 isbvid=" + DomRows.Get(rows[0], "isbvid") + " isosid=" + DomRows.Get(rows[0], "isosid"));
            }
            string missing = BlueKeep(job, doms[1]);
            if (missing.Length > 0)
            {
                throw new BridgeException(409, "u8_rejected", "U8 生成的红字发票缺少蓝字发票行 " + missing);
            }
        }

        // 表体只留请求的蓝字行（按 autoid），计划按 DOM 顺序重排。缺行返回其 AutoID。
        static string BlueKeep(BlueJob job, object body)
        {
            Dictionary<int, BlueLine> want = new Dictionary<int, BlueLine>();
            for (int i = 0; i < job.Lines.Count; i++)
            {
                want[job.Lines[i].LineId] = job.Lines[i];
            }
            List<BlueLine> order = new List<BlueLine>();
            List<object> rows = DomRows.RowsOf(body);
            for (int i = 0; i < rows.Count; i++)
            {
                BlueLine line;
                int lineId = CoRows.AsId(DomRows.Get(rows[i], "autoid"));
                if (!want.TryGetValue(lineId, out line) || order.Contains(line))
                {
                    DomRows.RemoveRow(rows[i]);
                    continue;
                }
                order.Add(line);
            }
            for (int i = 0; i < job.Lines.Count; i++)
            {
                if (!order.Contains(job.Lines[i]))
                {
                    return Id(job.Lines[i].LineId);
                }
            }
            job.Lines = order;
            return "";
        }

        // 日期、制单人和模板号（Stamp 会释放表头行），然后写身份字段。idisp / dlid 记审计，不改。
        static void BlueHeadFill(BlueJob job, object sys, object[] doms)
        {
            DropExtra(doms[0]);
            object row = OneRow(doms[0]);
            ClearAttrs(doms[0], row, RInvHeadClear, DomRows.Schema(doms[0]));
            ApplyFields(doms[0], row, job.Head, true);
            DomRows.Set(doms[0], row, "ddate", job.Date);
            SoSave.Stamp(job.Ctx, sys, doms[0], job.Want.Card);
            row = OneRow(doms[0]);
            DomRows.Set(doms[0], row, "sbvid", "");
            DomRows.Set(doms[0], row, "cvouchtype", job.Want.Vouch);
            DomRows.Set(doms[0], row, "breturnflag", "1");
            DomRows.Set(doms[0], row, "editprop", "A");
            CoRows.Note(job.Ctx.Item, "红冲表头 idisp=" + DomRows.Get(row, "idisp") + " dlid=" + DomRows.Get(row, "dlid"));
        }

        static void BlueFillLines(BlueJob job, object co, object[] doms)
        {
            List<string> schema = DomRows.Schema(doms[1]);
            for (int i = 0; i < job.Lines.Count; i++)
            {
                BlueLine line = job.Lines[i];
                ClearAttrs(doms[1], At(doms[1], i), RInvLineClear, schema);
                RedScale(doms[1], At(doms[1], i), line.Qty);
                string msg = SaleCalc.Check(co, doms[0], doms[1], At(doms[1], i), "iquantity", false);
                if (msg.Length > 0)
                {
                    CoRows.Note(job.Ctx.Item, "BodyCheck " + msg);
                }
                object row = At(doms[1], i);
                ApplyFields(doms[1], row, line.Fields, false);
                if (BluePinLink)
                {
                    DomRows.Set(doms[1], row, "isbvid", Id(line.LineId), schema);
                }
                DomRows.Set(doms[1], row, "irowno", Id(i + 1), schema);
                DomRows.Set(doms[1], row, "editprop", "A", schema);
            }
        }

        static int SaveBlueRed(BlueJob job, object co, object[] doms, out string savedCode)
        {
            WorkContext ctx = job.Ctx;
            bool open = false;
            savedCode = "";
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                RefuseBlueRoom(job);
                string before = BlueSnap(ctx.Conn, job.BlueId);
                string no = SoSave.VoucherNo(co, doms, ctx.Item, "cSBVCode");
                if (no.Length == 0)
                {
                    throw new BridgeException(409, "u8_rejected", "没有生成发票号");
                }
                SoDom.SetAttr(doms[0], "cSBVCode", no);
                SoDom.SetAttr(doms[0], "sbvid", "");
                savedCode = no;
                job.Code = no;
                object[] args = new object[] { doms[0], doms[1], (short)0, "" };
                object ret = ComUtil.CallRef(co, "Save", args, new int[] { 3 });
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                string msg = ret == null ? "" : Convert.ToString(ret);
                CoRows.Note(ctx.Item, "Save " + msg);
                if (msg.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", msg);
                }
                StockCall.AfterCheck(ctx, delegate(object conn) { RequireBlueRedSaved(conn, job, before); }, "红字发票 " + no);
                int id = CoRows.AsId(args[3]);
                DocMark.Created(ctx.Conn, "sale_invoice", id, no);
                CoTrans.CommitSeen(ctx.Conn);
                open = false;
                return id;
            }
            catch (Exception)
            {
                CoRows.CatchTran(ctx.Conn, ctx.Item, open);
                throw;
            }
        }

        // 加锁重读蓝字行后再核一次剩余（计划时的剩余可能已被并发红冲用掉）。
        static void RefuseBlueRoom(BlueJob job)
        {
            List<Dictionary<string, object>> rows = Rows.Query(job.Ctx.Conn, BlueLineLockSql, new object[] { job.BlueId }, 5000);
            Dictionary<int, decimal> left = new Dictionary<int, decimal>();
            for (int i = 0; i < rows.Count; i++)
            {
                left[CoRows.AsId(CoRows.Col(rows[i], "AutoID"))] = BlueLeft(rows[i]);
            }
            for (int i = 0; i < job.Lines.Count; i++)
            {
                decimal room;
                if (!left.TryGetValue(job.Lines[i].LineId, out room) || job.Lines[i].Qty > room + 0.000001m)
                {
                    throw new BridgeException(409, "state_mismatch", "超过可生单数量");
                }
            }
        }

        static void RequireBlueRedSaved(object conn, BlueJob job, string before)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, BlueNewSql, new object[] { job.Code, job.Want.Vouch }, 500);
            Dictionary<int, decimal> got = new Dictionary<int, decimal>();
            decimal total = 0m;
            int pos = 0;
            for (int i = 0; i < rows.Count; i++)
            {
                decimal qty = PuInv.Num(CoRows.Col(rows[i], "qty"));
                int link = CoRows.AsId(CoRows.Col(rows[i], "link"));
                pos += qty <= 0m ? 1 : 0;
                total = total + qty;
                decimal sum;
                got.TryGetValue(link, out sum);
                got[link] = sum + qty;
            }
            NoteBlueMoves(conn, job, before);
            string bad = BlueMismatch(job.Lines, rows.Count, pos, total, got, BlueLinkRequired);
            if (bad.Length > 0)
            {
                NoteBack(job.Ctx.Item, "新发票 " + job.Code + " " + bad);
                throw new BridgeException(409, "u8_rejected", "U8 保存的红字发票行与请求不一致");
            }
        }

        // 行数、非负行、合计；requireLink 时每个蓝字行的红冲数量要对上。不连库，自检可调。不符返回原因。
        internal static string BlueMismatch(List<BlueLine> plan, int count, int nonNegative, decimal total,
            Dictionary<int, decimal> byLink, bool requireLink)
        {
            decimal want = 0m;
            for (int i = 0; i < plan.Count; i++)
            {
                want = want + plan[i].Qty;
            }
            if (count != plan.Count || nonNegative != 0 || !Near(total, want))
            {
                return "行数 " + Id(count) + " 非负行 " + Id(nonNegative) + " 合计 " + Num(total) + "/" + Num(want);
            }
            if (!requireLink)
            {
                return "";
            }
            for (int i = 0; i < plan.Count; i++)
            {
                decimal got;
                if (!byLink.TryGetValue(plan[i].LineId, out got) || !Near(got, plan[i].Qty))
                {
                    return "蓝字行 " + Id(plan[i].LineId) + " 的红字行没有 iSBVID 关联或数量不符";
                }
            }
            return "";
        }

        static string BlueSnap(object conn, int blueId)
        {
            Dictionary<string, object> row = Rows.One(conn, BlueSnapSql, new object[] { blueId, blueId, blueId });
            if (row == null)
            {
                return "?";
            }
            return Num(PuInv.Num(CoRows.Col(row, "st"))) + "/" + Num(PuInv.Num(CoRows.Col(row, "vb"))) + "/"
                + Num(PuInv.Num(CoRows.Col(row, "kp")));
        }

        // 只记审计：发货行累计开票 / 已复核开票 / 订单行累计开票 前→后，新发票 iDisp、U8 生成的发货单数。读失败不影响保存。
        static void NoteBlueMoves(object conn, BlueJob job, string before)
        {
            try
            {
                string after = BlueSnap(conn, job.BlueId);
                Dictionary<string, object> head = Rows.One(conn, BlueNewHeadSql, new object[] { job.Code, job.Want.Vouch });
                string shape = head == null ? "?" : CoRows.Col(head, "disp") + "/" + CoRows.Col(head, "made");
                CoRows.Note(job.Ctx.Item, "红冲回写 st/vb/kp " + before + "→" + after + " idisp/发货单 " + shape);
            }
            catch (Exception ex)
            {
                CoRows.Note(job.Ctx.Item, "红冲回写快照失败 " + ex.Message);
            }
        }
    }
}
