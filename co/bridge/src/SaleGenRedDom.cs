using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 红字销售发票的 DOM。两条路（哪条被 U8 接受未经实测）：
    // A. GetNegaVouchData(发票类型, 头, 体, 退货单 DLID, err, true)，by-ref {0,1,2,3,4,5}（同退货单 NegaDoms）。
    //    只有返回 true、有表头行、且表体 idlsid 覆盖全部请求的退货行时才用；否则丢掉，走 B。
    // B. 同蓝字 SaleGenInv：卡片 07 / 13 的空白模板 + 参照视图 Sales_FHD_T / Sales_FHD_W（视图含退货单，数量为负）。
    // 两条路之后同样处理：表头 sbvid=""、idisp=1、breturnflag=1；每行 iquantity=−qty、金额按比例、单行 BodyCheck、
    // idlsid = 退货行、cdlcode / cbdlcode = 退货单号、editprop=A。
    internal static partial class SaleGen
    {
        // 置 false 则跳过 A，直接按参照视图拼。
        static readonly bool RedTryNega = true;
        static readonly string[] RInvHeadClear = new string[] {
            "sbvid", "csbvcode", "ufts", "cchecker", "cverifier", "dverifydate", "dverifysystime", "ccloser",
            "cmodifier", "dmoddate", "dmodifysystime", "dcreatesystime", "iprintcount", "cinvalider", "iverifystate",
            "iswfcontrolled", "ccurrentauditor", "ivtid", "corufts"
        };
        static readonly string[] RInvLineClear = new string[] {
            "autoid", "sbvid", "ufts", "corufts", "cbsysbarcode", "isettlequantity", "isettlenum", "foutquantity",
            "foutnum", "fsalecost", "fsaleprice", "ikpquantity", "ikpnum", "ikpmoney"
        };

        static ApiResult BuildRed(RedJob job)
        {
            WorkContext ctx = job.Ctx;
            object sys = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                SaSession.OpenSa(ctx.Conn, ctx.Session.Login, job.Want.Vt, out sys, out co);
                if (RedTryNega && RedNegaDoms(job, co, doms))
                {
                    FillRedFromNega(job, sys, co, doms);
                }
                else
                {
                    // 路 A 试过（或跳过）之后换一个新 Init 的组件，免得 GetNegaVouchData 留在组件里的状态带进保存。
                    SaSession.Release(sys, co, doms);
                    sys = null;
                    co = null;
                    SaSession.OpenSa(ctx.Conn, ctx.Session.Login, job.Want.Vt, out sys, out co);
                    FillRedFromRef(job, sys, co, doms);
                }
                string code;
                int id = SaveRed(job, co, doms, out code);
                return AfterSave(ctx, job.Kind, id, code, Echo(InvIdSql, "SBVID", SaleReturn.KindName, job.SourceId));
            }
            finally
            {
                SaSession.Release(sys, co, doms);
            }
        }

        // 路 A。任何不合用都返回 false 并释放 DOM（调用异常也一样，记审计），由路 B 接手。
        static bool RedNegaDoms(RedJob job, object co, object[] doms)
        {
            WorkItem item = job.Ctx.Item;
            doms[0] = Rows.NewDom();
            doms[1] = Rows.NewDom();
            string why;
            try
            {
                object[] args = new object[] { job.Want.Vouch, doms[0], doms[1], job.SourceId, "", true };
                object ret = ComUtil.CallRef(co, "GetNegaVouchData", args, new int[] { 0, 1, 2, 3, 4, 5 });
                CoRows.Swap(doms, 0, args[1]);
                CoRows.Swap(doms, 1, args[2]);
                string err = Values.Text(args[4]).Trim();
                CoRows.Note(item, "GetNegaVouchData " + job.Want.Vouch + " " + Values.Text(ret) + " " + err);
                why = !Values.Flag(ret) ? "返回 false" : RedNegaFit(job, doms);
            }
            catch (Exception ex)
            {
                why = "异常 " + ex.Message;
            }
            if (why.Length == 0)
            {
                return true;
            }
            CoRows.Note(item, "红字发票不用 GetNegaVouchData：" + why);
            ComUtil.Final(doms[1]);
            ComUtil.Final(doms[0]);
            doms[0] = null;
            doms[1] = null;
            return false;
        }

        // 表头要有行；表体只留请求的退货行（按 idlsid），顺序照请求。缺任一行返回原因。
        static string RedNegaFit(RedJob job, object[] doms)
        {
            if (DomRows.RowsOf(doms[0]).Count < 1)
            {
                return "没有表头";
            }
            Dictionary<int, bool> want = new Dictionary<int, bool>();
            for (int i = 0; i < job.Lines.Count; i++)
            {
                want[job.Lines[i].LineId] = true;
            }
            Dictionary<int, object> found = new Dictionary<int, object>();
            List<object> rows = DomRows.RowsOf(doms[1]);
            for (int i = 0; i < rows.Count; i++)
            {
                int lineId = CoRows.AsId(DomRows.Get(rows[i], "idlsid"));
                if (!want.ContainsKey(lineId) || found.ContainsKey(lineId))
                {
                    DomRows.RemoveRow(rows[i]);
                    continue;
                }
                found[lineId] = rows[i];
            }
            if (found.Count != want.Count)
            {
                return "表体缺少请求的退货行（" + found.Count.ToString(CultureInfo.InvariantCulture) + "/"
                    + want.Count.ToString(CultureInfo.InvariantCulture) + "）";
            }
            RedOrder(job, doms[1]);
            return "";
        }

        // 按计划顺序排：把 U8 给出的顺序改成计划顺序（计划 Lines 按 DOM 顺序重排，行号随后按下标写）。
        static void RedOrder(RedJob job, object body)
        {
            List<object> rows = DomRows.RowsOf(body);
            Dictionary<int, RedLine> byId = new Dictionary<int, RedLine>();
            for (int i = 0; i < job.Lines.Count; i++)
            {
                byId[job.Lines[i].LineId] = job.Lines[i];
            }
            List<RedLine> order = new List<RedLine>();
            for (int i = 0; i < rows.Count; i++)
            {
                order.Add(byId[CoRows.AsId(DomRows.Get(rows[i], "idlsid"))]);
            }
            job.Lines = order;
        }

        static void FillRedFromNega(RedJob job, object sys, object co, object[] doms)
        {
            WorkContext ctx = job.Ctx;
            DropExtra(doms[0]);
            object row = OneRow(doms[0]);
            List<string> schema = DomRows.Schema(doms[0]);
            ClearAttrs(doms[0], row, RInvHeadClear, schema);
            ApplyFields(doms[0], row, job.Head, true);
            RedHeadTail(job, sys, doms);
            List<string> bodySchema = DomRows.Schema(doms[1]);
            for (int i = 0; i < job.Lines.Count; i++)
            {
                ClearAttrs(doms[1], At(doms[1], i), RInvLineClear, bodySchema);
                RedFinishLine(ctx, co, doms, job, i);
            }
        }

        // 路 B：同 SaleGenInv.BuildInvoice 的模板 + 参照视图。
        static void FillRedFromRef(RedJob job, object sys, object co, object[] doms)
        {
            WorkContext ctx = job.Ctx;
            object srcHead = null;
            try
            {
                srcHead = LoadRef(ctx.Conn, InvRefHead, job.SourceId, "参照视图没有该退货单");
                SoDom.Templates(co, ctx.Conn, job.Want.Card, ctx.Item, doms);
                List<object> srcRows = DomRows.RowsOf(srcHead);
                if (srcRows.Count < 1)
                {
                    throw new BridgeException(409, "state_mismatch", "参照视图没有该退货单");
                }
                DropExtra(doms[0]);
                DomRows.CopyInto(doms[0], OneRow(doms[0]), srcRows[0], InvHeadSkip);
                ApplyFields(doms[0], OneRow(doms[0]), job.Head, true);
                RedHeadTail(job, sys, doms);
            }
            finally
            {
                ComUtil.Final(srcHead);
            }
            DropAll(doms[1]);
            for (int i = 0; i < job.Lines.Count; i++)
            {
                RedCopyLine(ctx, doms, job.Lines[i]);
                RedFinishLine(ctx, co, doms, job, i);
            }
        }

        static void RedCopyLine(WorkContext ctx, object[] doms, RedLine line)
        {
            object src = null;
            try
            {
                src = LoadRef(ctx.Conn, InvRefBody, line.LineId, "参照视图没有该退货单行");
                List<object> srcRows = DomRows.RowsOf(src);
                if (srcRows.Count < 1)
                {
                    throw new BridgeException(409, "state_mismatch", "参照视图没有该退货单行");
                }
                object dst = DomRows.AddRow(doms[1]);
                DomRows.CopyInto(doms[1], dst, srcRows[0], InvBodySkip);
            }
            finally
            {
                ComUtil.Final(src);
            }
        }

        // 日期、制单人和模板号（Stamp 会释放表头行），然后写身份字段：先按蓝字钉住，再改成红字。
        static void RedHeadTail(RedJob job, object sys, object[] doms)
        {
            WorkContext ctx = job.Ctx;
            object row = OneRow(doms[0]);
            if (TextOf(job.Head, "ddate").Length == 0)
            {
                DomRows.Set(doms[0], row, "ddate", LoginDate(ctx));
            }
            SoSave.Stamp(ctx, sys, doms[0], job.Want.Card);
            PinInvHead(doms[0], job.Want.Vouch, job.Want.DlCode, job.Want.SoCode);
            DomRows.Set(doms[0], OneRow(doms[0]), "breturnflag", "1");
        }

        static void RedFinishLine(WorkContext ctx, object co, object[] doms, RedJob job, int index)
        {
            RedLine line = job.Lines[index];
            RedScale(doms[1], At(doms[1], index), line.Qty);
            string msg = SaleCalc.Check(co, doms[0], doms[1], At(doms[1], index), "iquantity", false);
            if (msg.Length > 0)
            {
                CoRows.Note(ctx.Item, "BodyCheck " + msg);
            }
            object row = At(doms[1], index);
            ApplyFields(doms[1], row, line.Fields, false);
            DomRows.Set(doms[1], row, "idlsid", line.LineId.ToString(CultureInfo.InvariantCulture));
            if (job.Want.DlCode.Length > 0)
            {
                DomRows.Set(doms[1], row, "cdlcode", job.Want.DlCode);
                DomRows.Set(doms[1], row, "cbdlcode", job.Want.DlCode);
            }
            DomRows.Set(doms[1], row, "irowno", (index + 1).ToString(CultureInfo.InvariantCulture));
            DomRows.Set(doms[1], row, "editprop", "A");
        }

        // 行上原有数量（负的退货全数）→ −qty；金额、辅数量按 qty / |原数量| 同比例，符号跟原值（金额 2 位、辅数量 6 位）。
        static void RedScale(object dom, object row, decimal qty)
        {
            decimal full = Math.Abs(Dec(DomRows.Get(row, "iquantity")));
            DomRows.Set(dom, row, "iquantity", QtyText(-qty));
            if (full > 0m)
            {
                decimal ratio = qty / full;
                for (int i = 0; i < Amounts.Length; i++)
                {
                    decimal amount;
                    if (!TryDec(DomRows.Get(row, Amounts[i]), out amount))
                    {
                        continue;
                    }
                    decimal scaled = Math.Round(amount * ratio, 2, MidpointRounding.AwayFromZero);
                    DomRows.Set(dom, row, Amounts[i], scaled.ToString("0.00", CultureInfo.InvariantCulture));
                }
            }
            decimal rate;
            if (TryDec(DomRows.Get(row, "iinvexchrate"), out rate) && rate > 0m)
            {
                decimal num = Math.Round(-qty / rate, 6, MidpointRounding.AwayFromZero);
                DomRows.Set(dom, row, "inum", num.ToString("0.######", CultureInfo.InvariantCulture));
            }
        }
    }
}
