using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 采购发票的 CO 会话、空白 DOM 与保存事务。表头表体属性见 PuInvBody。
    internal static partial class PuInv
    {
        const string BilledSql = "select convert(varchar(40), isnull(iSumBillQuantity,0)) from rdrecords01 where AutoID=?";
        const string IdByCodeSql = "select convert(varchar(20), PBVID) from PurBillVouch where cPBVCode=? and cPBVBillType=?";

        // 01 → purbill（专用），02 → ppurbill（普通）；其他发票类型不支持。
        internal static string BillKey(string billType)
        {
            string type = billType == null ? "" : billType.Trim();
            if (type == "01")
            {
                return "purbill";
            }
            if (type == "02")
            {
                return "ppurbill";
            }
            throw new BridgeException(409, "state_mismatch", "只支持采购专用发票和普通发票");
        }

        // Info_PU.Init(login, "普通采购", pt) 引用 {0}；VoucherCO_PU.Init(vt, login, conn, info, true, bill, "普通采购", 0, "", pt)
        // 引用 {1..7}；pt 是采购类型编码（PuSession.InitType）；bOutTrans=true，事务由桥管。发票 vt 4 + purbill/ppurbill，到货单 vt 2 + "0"。
        internal static void Open(WorkContext ctx, int vt, string bill, out object info, out object co)
        {
            Open(ctx, vt, bill, true, out info, out co);
        }

        // 同上，bPositive 由调用方给：红字采购发票（PuInvRed）传 false。
        internal static void Open(WorkContext ctx, int vt, string bill, bool positive, out object info, out object co)
        {
            info = ComUtil.Create("Info_PU.ClsS_Infor");
            co = ComUtil.Create("VoucherCO_PU.clsVoucherCO_PU");
            if (info == null || co == null)
            {
                throw new BridgeException(503, "com_unavailable", "U8 组件未注册");
            }
            object login = ctx.Session.Login;
            string pt = PuSession.InitType(ctx);
            object[] infoArgs = new object[] { login, "普通采购", pt };
            object infoRet = ComUtil.CallRef(info, "Init", infoArgs, new int[] { 0 });
            CoRows.LoginBack(ctx, login, infoArgs[0]);
            string infoMsg = Values.Text(infoRet).Trim();
            if (infoMsg.Length > 0)
            {
                throw new BridgeException(409, "u8_rejected", infoMsg);
            }
            object[] args = new object[] { vt, login, ctx.Conn, info, positive, bill, "普通采购", 0, "", pt };
            ComUtil.CallRef(co, "Init", args, new int[] { 1, 2, 3, 4, 5, 6, 7 });
            if (args[2] != null && !object.ReferenceEquals(ctx.Conn, args[2]))
            {
                CoRows.Note(ctx.Item, "Init 更换了连接");
            }
            CoRows.LoginBack(ctx, login, args[1]);
            CoRows.ReleaseIfNew(ctx.Conn, args[2]);
            CoRows.ReleaseIfNew(info, args[3]);
            ComUtil.Set(co, "bOutTrans", true);
        }

        static ApiResult Save(InvJob job)
        {
            WorkContext ctx = job.Ctx;
            object info = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                ctx.PuInitType = CoRows.Col(job.Rd, "cPTCode");
                Open(ctx, 4, BillKey(job.BillType), job.Red ? RedGenPositive : true, out info, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                ReadBlank(co, doms, ctx.Item);
                StampHead(job, doms[0]);
                if (job.Red)
                {
                    MarkRedHead(doms[0]);
                }
                FillBody(job, doms[1]);
                int id = SaveTran(job, co, doms);
                return AfterSaved(job, id);
            }
            finally
            {
                ComUtil.Final(doms[1]);
                ComUtil.Final(doms[0]);
                ComUtil.Final(co);
                ComUtil.Final(info);
            }
        }

        // GetVoucherDataById(h, b, "", 0, "") 引用 {0,1,4}：只回 schema、没有 z:row。不要用视图 where 1=2 的空白，
        // 那样 VoucherSave2 报「类型不匹配」。
        internal static void ReadBlank(object co, object[] doms, WorkItem item)
        {
            object[] args = new object[] { doms[0], doms[1], "", 0, "" };
            object ret = ComUtil.CallRef(co, "GetVoucherDataById", args, new int[] { 0, 1, 4 });
            CoRows.Swap(doms, 0, args[0]);
            CoRows.Swap(doms, 1, args[1]);
            CoRows.Note(item, "GetVoucherDataById 0 " + Values.Text(ret));
            if (DomRows.Schema(doms[0]).Count == 0 || DomRows.Schema(doms[1]).Count == 0)
            {
                throw new BridgeException(409, "u8_rejected", "U8 没有返回单据模板");
            }
        }

        // VoucherSave2(h, b, 2, curId) 引用 {3}：返回空为成功，curId 是新 PBVID。提交前在同一事务里核对入库行累计开票数量。
        static int SaveTran(InvJob job, object co, object[] doms)
        {
            WorkContext ctx = job.Ctx;
            bool open = false;
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                ReadBilled(ctx.Conn, job);
                object[] args = new object[] { doms[0], doms[1], (short)2, "" };
                object ret = ComUtil.CallRef(co, "VoucherSave2", args, new int[] { 3 });
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                string msg = Values.Text(ret).Trim();
                CoRows.Note(ctx.Item, "VoucherSave2 " + (msg.Length == 0 ? "ok" : msg));
                if (msg.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", msg);
                }
                StockCall.AfterCheck(ctx, delegate(object conn) { RequireBilled(conn, job); }, "发票号 " + job.Code);
                int id = CoRows.AsId(args[3]);
                DocMark.Created(ctx.Conn, "purchase_invoice", id, IdByCodeSql, new object[] { job.Code, job.BillType });
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

        // 保存前在同一事务里重读累计开票数量作核对基准，并再查一次剩余可开票数量。
        static void ReadBilled(object conn, InvJob job)
        {
            for (int i = 0; i < job.Lines.Count; i++)
            {
                InvLine line = job.Lines[i];
                line.Billed = Num(Rows.Scalar(conn, BilledSql, new object[] { line.RdsId }));
                RequireLeft(line);
            }
        }

        // U8 没把本次数量加到 iSumBillQuantity 就不提交，免得留下删不回去的发票。
        static void RequireBilled(object conn, InvJob job)
        {
            for (int i = 0; i < job.Lines.Count; i++)
            {
                InvLine line = job.Lines[i];
                decimal now = Num(Rows.Scalar(conn, BilledSql, new object[] { line.RdsId }));
                if (Math.Abs(now - line.Billed - line.Qty) > 0.000001m)
                {
                    throw new BridgeException(409, "u8_rejected", "U8 没有回写入库单累计开票数量");
                }
            }
        }

        // 已提交。新主键先认 curId，读不到再按发票号 + 类型在新连接上找；都失败是 504。
        static ApiResult AfterSaved(InvJob job, int id)
        {
            WorkContext ctx = job.Ctx;
            try
            {
                if (id <= 0)
                {
                    id = IdByCode(ctx, job);
                }
                if (id > 0)
                {
                    return EditMsg.Saved(ctx, job.Kind, id, Kinds.Find("purchase_in"), job.RdId);
                }
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "Saved " + ex.Message);
            }
            string text = "已保存但未能确定单据标识，发票号 " + job.Code;
            if (id > 0)
            {
                text = text + "，标识 " + id.ToString(CultureInfo.InvariantCulture);
            }
            throw new BridgeException(504, "outcome_unknown", text);
        }

        static int IdByCode(WorkContext ctx, InvJob job)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                return CoRows.AsId(Rows.Scalar(conn, IdByCodeSql, new object[] { job.Code, job.BillType }));
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static void Copy(object dom, object row, List<string> schema, string name, string value)
        {
            if (value == null || value.Length == 0)
            {
                return;
            }
            Put(dom, row, schema, name, value);
        }

        static void Put(object dom, object row, List<string> schema, string name, string value)
        {
            StockDom.SetCell(dom, row, name, value ?? "", schema);
        }

        static string Or(string value, string fallback)
        {
            return value != null && value.Length > 0 ? value : fallback;
        }
    }
}
