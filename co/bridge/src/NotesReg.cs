using System;
using System.Collections.Generic;

namespace U8Co
{
    // 票据登记（notes/create，应收票据；应付票据，第二级写入、只对测试账套开放）。U8 界面的登记用 NoteManageAR / NoteManageAP 的
    // NoteSave 写票据并生成收款单 48 / 付款单 49（bPjToClose），
    // 但无界面调用时 NoteSave 自行提交、不生成收款单，在桥里还会挂起（测试账套实测），所以不调用它，照 U8 的结果分两步：
    // 1. 桥自己检查（NotesRegGate）；
    // 2. 请求连接的一个事务里：写票据行（NoteInsert，列和值照 U8 登记的票据；分包票据另写一行整段可用子票区间），经收付款单新增的同一条路径（UFAPBO clsCloseBill.SaveVouch）
    //    生成收款单（应付票据是付款单），标成票据来源、回写票据的 iCloseID，核对后提交（NotesRegLink.Create）。预演在提交钩子回滚。
    // 应付票据的列和值按应收票据对称推断（未经实测）：cFlag AP、cLink AP50+号、条码「||ap50|号」、付款单 49。
    // 不建条码档案（AA_GeneralBarCode），票据上的 csysbarcode 照写。
    internal static class NotesReg
    {
        public static ApiResult Create(WorkContext ctx)
        {
            if (ctx == null || ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "缺少 U8 登录");
            }
            NoteRegAsk ask = NotesRegReq.ParseCreate(ctx.Item.Body);
            NotesRegReq.TestGate(ctx.Item, ask.Flag);
            PermRule rule = PermRegistry.ForKey(PermRegistry.NotesRegKey(false, ask.Flag));
            PermContext perm = PermCheck.Of(ctx);
            PermCheck.RequireRule(perm, rule);
            if (!PermCheck.RowAllowed(perm, rule, NotesRegReq.PermRow(ask.Partner, ask.Dept, ask.Person)))
            {
                throw new BridgeException(403, "no_permission", PermCheck.DeniedData);
            }
            NoteRegPlan plan = NotesRegGate.Create(ctx, ask);
            DryRun.Set(NotesRegReq.CreateAction, Planned(plan));
            int noteId;
            int receipt = NotesRegLink.Create(ctx, plan, out noteId);
            return Confirm(ctx, plan, noteId, receipt);
        }

        // 已提交。新连接上确认票据和收款单对上；读不出来或对不上 504。
        static ApiResult Confirm(WorkContext ctx, NoteRegPlan plan, int noteId, int receipt)
        {
            string no = plan.Ask.NoteNo;
            string code = null;
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                if (NotesRegSql.Tied(conn, noteId))
                {
                    code = NotesRegSql.ReceiptCode(conn, plan.Ask.Flag, receipt);
                }
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "notes/create confirm " + ex.Message);
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (code == null)
            {
                throw new BridgeException(504, "outcome_unknown", "票据 " + no + "（主键 " + noteId + "）和" + CloseTitle(plan.Ask.Flag)
                    + "（主键 " + receipt + "）已提交，但未能回读确认，请先查询该票据，不要直接重试");
            }
            Dictionary<string, object> body = Out(ctx, plan.Ask.Flag, noteId, no, receipt, code);
            body["amount"] = plan.Ask.Amount;
            body[PartnerField(plan.Ask.Flag)] = plan.Ask.Partner;
            body["note_km"] = plan.NoteKm;
            body["settle_code"] = plan.Ask.SettleCode;
            NoteSplit.Put(body, plan.Ask.Split, plan.Ask.SubStart, plan.Ask.SubEnd);
            body["receipt_verified"] = false;
            return ApiResult.Ok(body);
        }

        // 收付款单的名称：应收票据生成收款单，应付票据生成付款单。
        internal static string CloseTitle(string flag)
        {
            return flag == "AP" ? "付款单" : "收款单";
        }

        // 往来单位的字段名：应收 customer，应付 vendor（与请求一致）。
        internal static string PartnerField(string flag)
        {
            return flag == "AP" ? "vendor" : "customer";
        }

        // 响应：收付款单的键沿用 receipt_id / receipt_code（应付票据是付款单 49 的主键、单号）。
        internal static Dictionary<string, object> Out(WorkContext ctx, string flag, int noteId, string noteNo, int receiptId, string receiptCode)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["acc"] = ctx.Item.Acc;
            body["flag"] = flag;
            body["note_id"] = noteId;
            body["note_no"] = noteNo;
            body["receipt_id"] = receiptId > 0 ? (object)receiptId : null;
            body["receipt_code"] = receiptCode;
            return body;
        }

        // 预演的 detail：检查后的输入和将要生成的收款单要点。
        static Dictionary<string, object> Planned(NoteRegPlan plan)
        {
            Dictionary<string, object> d = new Dictionary<string, object>();
            d["note_no"] = plan.Ask.NoteNo;
            d["amount"] = plan.Ask.Amount;
            NoteSplit.Put(d, plan.Ask.Split, plan.Ask.SubStart, plan.Ask.SubEnd);
            d[PartnerField(plan.Ask.Flag)] = plan.Ask.Partner;
            d[PartnerField(plan.Ask.Flag) + "_name"] = plan.PartnerName;
            d["drawer"] = plan.Drawer;
            d["note_km"] = plan.NoteKm;
            d["receipt_km"] = plan.CtrlKm;
            d["receipt_date"] = plan.Ask.ReceiptDate;
            d["receipt_digest"] = plan.Digest;
            d["vt_id"] = plan.VtId > 0 ? (object)plan.VtId : null;
            return d;
        }
    }
}
