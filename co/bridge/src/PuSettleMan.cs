using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 采购手工结算（vouchers/create，type=purchase_settle）。第一级写入，按写入策略放行。
    // VoucherCO_PU.DoSettle(7) 在这个版本里什么都不写（测试账套实测），Init(5) + VoucherSave2 卡片 99 也不落库；U8 自己的
    // 「手工结算」界面是直接写表，桥按它执行的 SQL 写（PuSettleManSql，实测核对）。
    // 顺序：闸门（PuSettleManGate.ForCreate，事务外）→ VoucherCO_PU.Init(4, purbill) + bOutTrans → CoTrans.Begin →
    // CheckSettle（结算日期即登录日期，之前各月采购未结账时 U8 原文 409）→ 加更新锁重读、重算 → 写入 → 提交前在同一事务里核对
    // （@@TRANCOUNT、结算行数与数量、入库行 iSQuantity、发票行已结算合计与结算日期）→ CommitSeen → 新连接回读（读不到 504）。
    // 删除仍走的 PuSettleDel（U8 的 Delete → PU_DelPurSettle，对任何结算单都按行回退）。
    internal static class PuSettleMan
    {
        const string LinesSql = "select convert(varchar(20), count(*)) as n, convert(varchar(40), isnull(sum(iSVQuantity),0)) as q"
            + " from PurSettleVouchs where PSVID=?";
        const string RdSql = "select convert(varchar(40), isnull(iSQuantity,0)) from rdrecords01 where AutoID=?";
        const string BsSql = "select convert(varchar(40), isnull((select sum(s.iSVQuantity) from PurSettleVouchs s where s.iBsID=b.ID),0))"
            + " + N'|' + isnull(convert(varchar(10), b.dSDate, 23), N'') + N'|' + convert(varchar(40), isnull(b.iPBVQuantity,0))"
            + " from PurBillVouchs b where b.ID=?";
        const string SavedSql = "select h.cSVCode, convert(varchar(10), h.dSVDate, 23) as SVDate,"
            + " (select count(*) from PurSettleVouchs d where d.PSVID=h.PSVID) as n from PurSettleVouch h where h.PSVID=?";

        public static ApiResult Create(WorkContext ctx, VoucherKind kind)
        {
            List<ManLine> lines = PuSettleManReq.Parse(ctx.Item.Lines);
            ManPlan plan = PuSettleManGate.ForCreate(ctx, lines);
            object info = null;
            object co = null;
            try
            {
                ctx.PuInitType = plan.PtCode;
                PuInv.Open(ctx, 4, PuInv.BillKey("01"), true, out info, out co);
                int psvid = Tran(ctx, kind, co, lines, plan.Date);
                return Saved(ctx, kind, psvid, lines.Count);
            }
            finally
            {
                ComUtil.Final(co);
                ComUtil.Final(info);
            }
        }

        static int Tran(WorkContext ctx, VoucherKind kind, object co, List<ManLine> lines, string date)
        {
            bool open = false;
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                string before = CoTrans.Count(ctx.Conn);
                ctx.Item.TranBefore = before;
                string msg = Check(ctx, co);
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                if (msg.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", msg);
                }
                ManPlan plan = PuSettleManGate.Build(ctx.Conn, lines, date, true);
                int psvid = Write(ctx, kind, plan);
                StockCall.AfterCheck(ctx, delegate(object conn) { Verify(conn, plan, psvid, before); },
                    "PSVID " + psvid.ToString(CultureInfo.InvariantCulture));
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

        // CheckSettle(out sErr)：同 PuSettleGen，false 时 sErr 是 U8 原文。
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

        static int Write(WorkContext ctx, VoucherKind kind, ManPlan plan)
        {
            object conn = ctx.Conn;
            int psvid = PuSettleManSql.NextId(conn, "PurSTID");
            int first = PuSettleManSql.NextId(conn, "PurSTsID");
            PuSettleManSql.RequireFree(conn, psvid, first, plan.Rows.Count);
            PuSettleManSql.InsertHead(conn, plan, psvid, (ctx.OperatorName ?? "").Trim());
            DryRun.Created(kind, psvid);
            for (int i = 0; i < plan.Rows.Count; i++)
            {
                PuSettleManSql.InsertLine(conn, plan.Rows[i], first + i, psvid);
            }
            foreach (ManRdSum sum in PuSettleManPlan.RdSums(plan.Rows))
            {
                PuSettleManSql.WriteBack(conn, plan, sum);
                DocMark.Touched("purchase_in", sum.Rd.RdId);
            }
            PuSettleManSql.Stamp(conn, plan, psvid);
            foreach (KeyValuePair<ManBs, decimal> pair in PuSettleManPlan.BsSums(plan.Rows))
            {
                DocMark.Touched("purchase_invoice", pair.Key.Pbvid);
            }
            CoRows.Note(ctx.Item, "手工结算 PSVID " + psvid.ToString(CultureInfo.InvariantCulture) + " "
                + plan.Rows.Count.ToString(CultureInfo.InvariantCulture) + " 行");
            return psvid;
        }

        // 事务里、提交前：层数没变；结算行数、数量合计对；每条入库行 iSQuantity 加了本单数量；每条发票行已结算合计加了本单数量，
        // 结清的写了结算日期、没结清的没写。不符抛 409（事务还在时调用方回滚）。
        static void Verify(object conn, ManPlan plan, int psvid, string before)
        {
            string now = CoTrans.Count(conn);
            int b0;
            int n0;
            if (!int.TryParse(before, out b0) || !int.TryParse(now, out n0) || !IaRun.TranIntact(b0, n0))
            {
                throw Rejected("事务层数变了（@@TRANCOUNT " + before + " → " + now + "）");
            }
            Dictionary<string, object> row = Rows.One(conn, LinesSql, new object[] { psvid });
            decimal total = 0m;
            for (int i = 0; i < plan.Rows.Count; i++)
            {
                total += plan.Rows[i].Qty;
            }
            if (CoRows.Col(row, "n") != plan.Rows.Count.ToString(CultureInfo.InvariantCulture)
                || !PuSettleManPlan.Same(PuInv.Num(CoRows.Col(row, "q")), total))
            {
                throw Rejected("结算单的行数或数量与请求不符");
            }
            foreach (ManRdSum sum in PuSettleManPlan.RdSums(plan.Rows))
            {
                decimal after = PuInv.Num(Rows.Scalar(conn, RdSql, new object[] { sum.Rd.Id }));
                if (!PuSettleManPlan.Same(after, sum.Rd.SQty + sum.Qty))
                {
                    throw Rejected("入库行 " + sum.Rd.Id.ToString(CultureInfo.InvariantCulture) + " 的已结算数量没有按本单回写");
                }
            }
            foreach (KeyValuePair<ManBs, decimal> pair in PuSettleManPlan.BsSums(plan.Rows))
            {
                VerifyBill(conn, pair.Key, pair.Value, plan.Date);
            }
        }

        static void VerifyBill(object conn, ManBs bs, decimal qty, string date)
        {
            string[] parts = (Rows.Scalar(conn, BsSql, new object[] { bs.Id }) ?? "").Split('|');
            string id = bs.Id.ToString(CultureInfo.InvariantCulture);
            if (parts.Length != 3 || !PuSettleManPlan.Same(PuInv.Num(parts[0]), bs.PriorQty + qty))
            {
                throw Rejected("发票行 " + id + " 的已结算数量与本单不符");
            }
            bool full = PuSettleManPlan.Same(PuInv.Num(parts[0]), PuInv.Num(parts[2]));
            if (full ? parts[1] != date : parts[1].Length > 0)
            {
                throw Rejected("发票行 " + id + " 的结算日期不对（" + (parts[1].Length > 0 ? parts[1] : "空") + "）");
            }
        }

        // 已提交。新连接上回读（不加 NOLOCK）；读不出来是 504。
        static ApiResult Saved(WorkContext ctx, VoucherKind kind, int psvid, int lines)
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
                CoRows.Note(ctx.Item, "PuSettleMan " + ex.Message);
                row = null;
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (row == null || CoRows.Col(row, "n") != lines.ToString(CultureInfo.InvariantCulture))
            {
                throw new BridgeException(504, "outcome_unknown",
                    "已提交手工结算但未能回读确认，结算单标识 " + psvid.ToString(CultureInfo.InvariantCulture));
            }
            return ApiResult.Ok(Body(kind, psvid, row));
        }

        static Dictionary<string, object> Body(VoucherKind kind, int psvid, Dictionary<string, object> row)
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
            body["settle_date"] = CoRows.Col(row, "SVDate");
            return body;
        }

        static BridgeException Rejected(string message)
        {
            return new BridgeException(409, "u8_rejected", message);
        }
    }
}
