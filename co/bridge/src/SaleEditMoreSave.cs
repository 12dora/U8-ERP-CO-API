using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 保存与核对：CoTrans 里先 UPDLOCK 读来源累计数（SaleGen.EditBase），Save 状态 1（留在本事务里），
    // 提交前核对来源累计数按行数量差变化、本单的行数和数量与请求一致；不符回滚 409 u8_rejected，
    // U8 已自行提交时 StockCall.AfterCheck 改报 504。提交后的回读失败 504 outcome_unknown。
    internal static partial class SaleEditMore
    {
        static readonly string[] FreeCols = new string[] {
            "bFree1", "bFree2", "bFree3", "bFree4", "bFree5", "bFree6", "bFree7", "bFree8", "bFree9", "bFree10"
        };

        static void SaveEdit(WorkContext ctx, object co, object[] doms, SaleEditPlan plan)
        {
            bool open = false;
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                SaleEditBack back = SaleGen.EditBase(ctx.Conn, plan);
                RecheckAdds(plan, back);
                object[] args = new object[] { doms[0], doms[1], (short)1, "" };
                object ret = ComUtil.CallRef(co, "Save", args, new int[] { 3 });
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                string msg = ret == null ? "" : Convert.ToString(ret);
                CoRows.Note(ctx.Item, "Save " + msg);
                if (msg.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", msg);
                }
                WorkItem item = ctx.Item;
                StockCall.AfterCheck(ctx, delegate(object conn)
                {
                    SaleGen.EditCheck(conn, item, back);
                    RequireLines(conn, item, plan);
                }, plan.Kind.Title + " " + plan.Code);
                CoTrans.CommitSeen(ctx.Conn);
                open = false;
            }
            catch (Exception)
            {
                CoRows.CatchTran(ctx.Conn, ctx.Item, open);
                throw;
            }
        }

        // 本单：删掉的行不在了，其余行的数量等于请求（没改的等于原值），新增行一行对一行找得到，行数对得上
        // （原有行 − 删除 + 新增）。
        static void RequireLines(object conn, WorkItem item, SaleEditPlan plan)
        {
            List<Dictionary<string, object>> now = Rows.Query(conn, plan.Invoice ? InvLinesSql : DispLinesSql,
                new object[] { plan.Id }, 500);
            Dictionary<int, decimal> got = new Dictionary<int, decimal>();
            for (int i = 0; i < now.Count; i++)
            {
                got[CoRows.AsId(CoRows.Col(now[i], "id"))] = PuInv.Num(CoRows.Col(now[i], "q"));
            }
            int want = 0;
            for (int i = 0; i < plan.Rows.Count; i++)
            {
                SaleEditRow row = plan.Rows[i];
                if (!row.Deleted)
                {
                    want = want + 1;
                }
                if (!row.Added)
                {
                    RequireOld(item, row, got);
                }
            }
            RequireAdded(item, plan, now);
            if (got.Count != want)
            {
                CoRows.Note(item, "回写核对 本单行数 " + got.Count.ToString(CultureInfo.InvariantCulture)
                    + "/" + want.ToString(CultureInfo.InvariantCulture));
                throw new BridgeException(409, "u8_rejected", "U8 保存的明细与请求不一致");
            }
        }

        static void RequireOld(WorkItem item, SaleEditRow row, Dictionary<int, decimal> got)
        {
            decimal qty;
            bool has = got.TryGetValue(row.LineId, out qty);
            if (row.Deleted ? has : (!has || Math.Abs(qty - row.New) > 0.000001m))
            {
                LinesFail(item, row, has ? Num(qty) : "无");
            }
        }

        static void LinesFail(WorkItem item, SaleEditRow row, string actual)
        {
            CoRows.Note(item, "回写核对 本单行" + row.LineId.ToString(CultureInfo.InvariantCulture)
                + " 数量 " + Num(row.Old) + "→" + actual + " 应为 " + (row.Deleted ? "删除" : Num(row.New)));
            throw new BridgeException(409, "u8_rejected", "U8 保存的明细与请求不一致");
        }

        // 发货单表体的自由项只收存货启用了的（Inventory.bFreeN）。
        static void CheckFree(object conn, SaleEditPlan plan)
        {
            for (int i = 0; i < plan.Rows.Count; i++)
            {
                SaleEditRow row = plan.Rows[i];
                if (row.Fields == null)
                {
                    continue;
                }
                foreach (string key in row.Fields.Keys)
                {
                    if (ArapReq.Span(key, "cfree", 1, 10))
                    {
                        RequireFree(conn, row.Inv, int.Parse(key.Substring(5), CultureInfo.InvariantCulture));
                    }
                }
            }
        }

        static void RequireFree(object conn, string inv, int n)
        {
            string sql = "select convert(varchar(5), isnull(" + FreeCols[n - 1] + ",0)) from Inventory where cInvCode=?";
            if (!Values.Flag(Rows.Scalar(conn, sql, new object[] { inv })))
            {
                throw new BridgeException(400, "bad_request",
                    "存货 " + inv + " 没有启用自由项 cfree" + n.ToString(CultureInfo.InvariantCulture));
            }
        }

        // CommitSeen 之后回读失败不能报成普通错误，调用方会以为没改而重投。
        static ApiResult AfterSave(WorkContext ctx, VoucherKind kind, int id, string code)
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
                string no = code == null ? "" : code.Trim();
                throw new BridgeException(504, "outcome_unknown", "已保存但回读失败，单号 " + no
                    + "，标识 " + id.ToString(CultureInfo.InvariantCulture));
            }
        }
    }
}
