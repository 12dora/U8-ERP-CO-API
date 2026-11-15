using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 采购结算：参照一张采购发票自动结算（vouchers/generate，type=purchase_settle，source_type=purchase_invoice，id 是 PBVID）。
    // 测试账套实测（docs/u8-notes.md「采购结算」）：Info_PU.Init + VoucherCO_PU.Init(4, …, purbill / ppurbill, …)、
    // bOutTrans，GetVoucherDataById(h, b, "", pbvid, "") 引用 {0,1,4}，CheckSettle(sErr) 引用 {0}，
    // bRdBVAutoSettle(h, b, "", "")：末尾两个 OUT 串必须按值传空串（传了非空串 U8 走合并开票临时表分支，整账套扫描十几分钟），
    // 不设 m_TmpTableName；返回空串为成功，非空是 U8 原文（409）。不自行提交，都在 CoTrans 里，提交前在同一事务里核对：
    // 正好一张新结算单，每行（iRdsID, iBsID, iSVQuantity）= 发票行（RdsId, ID, iPBVQuantity），发票表头、表体都写了 dSDate，
    // 应付审核人没变，@@TRANCOUNT 没变；对不上回滚 409。U8 不写应付（cPBVVerifier、Ap_Detail），应付审核另走 arap_verify。
    internal static class PuSettleGen
    {
        const string BodySql = "select convert(varchar(20), s.PSVID) as PSVID, convert(varchar(20), s.iRdsID) as Rds,"
            + " convert(varchar(20), s.iBsID) as Bs, convert(varchar(40), s.iSVQuantity) as Qty"
            + " from PurSettleVouchs s where s.PSVID in (select x.PSVID from PurSettleVouchs x where x.iBsID in"
            + " (select b.ID from PurBillVouchs b where b.PBVID=?))";
        const string StampSql = "select convert(varchar(10), h.dSDate, 23) as SDate, h.cPBVVerifier,"
            + " (select count(*) from PurBillVouchs b where b.PBVID=h.PBVID"
            + " and (b.dSDate is null or convert(varchar(10), b.dSDate, 23)<>?)) as Unstamped"
            + " from PurBillVouch h where h.PBVID=?";
        const string SavedSql = "select h.cSVCode, convert(varchar(10), h.dSVDate, 23) as SVDate,"
            + " (select count(*) from PurSettleVouchs d where d.PSVID=h.PSVID) as n from PurSettleVouch h where h.PSVID=?";
        const string DaySql = "select convert(varchar(10), h.dSVDate, 23) from PurSettleVouch h where h.PSVID=?";
        const decimal Eps = 0.000001m;

        public static ApiResult Generate(WorkContext ctx, VoucherKind kind, int pbvid)
        {
            SettleJob job = PuSettleGate.ForGenerate(ctx, pbvid);
            object info = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                ctx.PuInitType = CoRows.Col(job.Head, "cPTCode");
                PuInv.Open(ctx, 4, PuInv.BillKey(CoRows.Col(job.Head, "cPBVBillType")), true, out info, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                PurchaseCo.ReadPu(co, doms, pbvid, ctx.Item);
                CoRows.RequireHead(doms[0], "PBVID", pbvid);
                int psvid = Tran(ctx, kind, co, doms, job);
                return Saved(ctx, kind, psvid, job);
            }
            finally
            {
                ComUtil.Final(doms[1]);
                ComUtil.Final(doms[0]);
                ComUtil.Final(co);
                ComUtil.Final(info);
            }
        }

        static int Tran(WorkContext ctx, VoucherKind kind, object co, object[] doms, SettleJob job)
        {
            bool open = false;
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                string before = CoTrans.Count(ctx.Conn);
                ctx.Item.TranBefore = before;
                string msg = Check(ctx, co);
                if (msg.Length == 0)
                {
                    msg = AutoSettle(ctx, co, doms);
                }
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                if (msg.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", msg);
                }
                int psvid = 0;
                StockCall.AfterCheck(ctx, delegate(object conn) { psvid = Created(conn, job, before); },
                    "PBVID " + job.Pbvid.ToString(CultureInfo.InvariantCulture));
                DryRun.Created(kind, psvid);
                DocMark.Touched("purchase_invoice", job.Pbvid);
                CoTrans.CommitSeen(ctx.Conn);
                open = false;
                return psvid;
            }
            catch (Exception)
            {
                CoRows.CatchTran(ctx.Conn, ctx.Item, open);
                throw;
            }
        }

        // CheckSettle(out sErr)：false 时 sErr 是 U8 原文（例如「当前登录日期所在的会计月以前的月份未结账，不能结算」）。
        static string Check(WorkContext ctx, object co)
        {
            object[] args = new object[] { "" };
            object ret = ComUtil.CallRef(co, "CheckSettle", args, new int[] { 0 });
            string err = Values.Text(args[0]).Trim();
            bool ok = Values.Flag(ret);
            CoRows.Note(ctx.Item, "CheckSettle " + (ok ? "ok" : "false") + (err.Length > 0 ? " " + err : ""));
            if (ok)
            {
                return "";
            }
            return err.Length > 0 ? err : "U8 不允许结算（CheckSettle 返回 false）";
        }

        // 末尾两个 OUT 串按值传空串（见类注释），不要改成 by-ref、不要传操作员名。
        static string AutoSettle(WorkContext ctx, object co, object[] doms)
        {
            object ret = ComUtil.Call(co, "bRdBVAutoSettle", new object[] { doms[0], doms[1], "", "" });
            string msg = Values.Text(ret).Trim();
            CoRows.Note(ctx.Item, "bRdBVAutoSettle " + (msg.Length == 0 ? "ok" : msg));
            return msg;
        }

        // 事务里、提交前：返回新结算单的 PSVID；不符抛 409（事务还在时调用方回滚，U8 已自行提交时 AfterCheck 改报 504）。
        static int Created(object conn, SettleJob job, string before)
        {
            string now = CoTrans.Count(conn);
            int b0;
            int n0;
            // ADO 的 BeginTrans 到第一条语句才真正开事务：开头可能读到 0、U8 写过之后是 1（IaRun.TranIntact）。
            if (!int.TryParse(before, out b0) || !int.TryParse(now, out n0) || !IaRun.TranIntact(b0, n0))
            {
                throw Rejected("U8 改变了事务层数（@@TRANCOUNT " + before + " → " + now + "）");
            }
            List<Dictionary<string, object>> rows = Rows.Query(conn, BodySql, new object[] { job.Pbvid },
                PuSettleReq.LinesMax * 2 + 1) ?? new List<Dictionary<string, object>>();
            int psvid = OnlySettle(rows);
            if (Rows.Scalar(conn, DaySql, new object[] { psvid }) != job.Date)
            {
                throw Rejected("结算单的结算日期不是 " + job.Date);
            }
            RequirePairs(rows, job.Lines);
            RequireStamped(conn, job);
            return psvid;
        }

        static int OnlySettle(List<Dictionary<string, object>> rows)
        {
            int psvid = 0;
            for (int i = 0; i < rows.Count; i++)
            {
                int id = CoRows.AsId(CoRows.Col(rows[i], "PSVID"));
                if (psvid != 0 && id != psvid)
                {
                    throw Rejected("U8 为这张发票生成了不止一张结算单");
                }
                psvid = id;
            }
            if (psvid <= 0)
            {
                throw Rejected("U8 没有生成结算单");
            }
            return psvid;
        }

        // 结算单的行与发票行一一对应：行数相同，每个发票行 ID 恰有一行，入库行、数量对得上。
        static void RequirePairs(List<Dictionary<string, object>> rows, List<Dictionary<string, object>> lines)
        {
            if (rows.Count != lines.Count)
            {
                throw Rejected("结算单行数（" + rows.Count.ToString(CultureInfo.InvariantCulture) + "）与发票行数（"
                    + lines.Count.ToString(CultureInfo.InvariantCulture) + "）不一致");
            }
            Dictionary<string, Dictionary<string, object>> byBs = new Dictionary<string, Dictionary<string, object>>();
            for (int i = 0; i < rows.Count; i++)
            {
                byBs[CoRows.Col(rows[i], "Bs")] = rows[i];
            }
            for (int i = 0; i < lines.Count; i++)
            {
                Dictionary<string, object> row;
                string bs = CoRows.Col(lines[i], "ID");
                if (!byBs.TryGetValue(bs, out row))
                {
                    throw Rejected("发票行 " + bs + " 没有结算");
                }
                bool sameRd = CoRows.Col(row, "Rds") == CoRows.Col(lines[i], "RdsId");
                decimal diff = PuInv.Num(CoRows.Col(row, "Qty")) - PuInv.Num(CoRows.Col(lines[i], "Qty"));
                if (!sameRd || Math.Abs(diff) > Eps)
                {
                    throw Rejected("发票行 " + bs + " 的结算入库行或数量与发票不符");
                }
            }
        }

        // 发票表头、每行的结算日期都等于结算日期；应付审核人没变（U8 自动结算不做应付审核）。
        static void RequireStamped(object conn, SettleJob job)
        {
            Dictionary<string, object> row = Rows.One(conn, StampSql, new object[] { job.Date, job.Pbvid });
            if (row == null || CoRows.Col(row, "SDate") != job.Date || CoRows.Col(row, "Unstamped") != "0")
            {
                throw Rejected("U8 没有写发票的结算日期");
            }
            if (CoRows.Col(row, "cPBVVerifier") != CoRows.Col(job.Head, "cPBVVerifier"))
            {
                throw Rejected("结算改动了发票的应付审核人");
            }
        }

        // 已提交。新连接上回读结算单（不加 NOLOCK）；读不出来是 504。
        static ApiResult Saved(WorkContext ctx, VoucherKind kind, int psvid, SettleJob job)
        {
            object conn = null;
            Dictionary<string, object> row;
            try
            {
                conn = ctx.OpenFresh();
                row = Rows.One(conn, SavedSql, new object[] { psvid });
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "PuSettleGen " + ex.Message);
                row = null;
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (row == null)
            {
                throw new BridgeException(504, "outcome_unknown",
                    "已提交结算但未能回读确认，结算单标识 " + psvid.ToString(CultureInfo.InvariantCulture));
            }
            return ApiResult.Ok(Body(kind, psvid, row, job));
        }

        static Dictionary<string, object> Body(VoucherKind kind, int psvid, Dictionary<string, object> row, SettleJob job)
        {
            Dictionary<string, object> state = new Dictionary<string, object>();
            state["verified"] = false;
            state["verifier"] = "";
            state["verified_at"] = "";
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["id"] = psvid;
            body["code"] = CoRows.Col(row, "cSVCode");
            body["state"] = state;
            body["lines"] = (int)PuInv.Num(CoRows.Col(row, "n"));
            body["source_type"] = "purchase_invoice";
            body["source_id"] = job.Pbvid;
            body["settle_date"] = CoRows.Col(row, "SVDate");
            return body;
        }

        static BridgeException Rejected(string message)
        {
            return new BridgeException(409, "u8_rejected", message);
        }
    }
}
