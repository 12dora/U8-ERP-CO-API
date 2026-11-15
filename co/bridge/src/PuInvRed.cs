using System;
using System.Collections.Generic;

namespace U8Co
{
    // 红字采购发票：参照红字采购入库单（RdRecord01.bredvouch=1）生成，同 PurBillVouch / PurBillVouchs，表头 bNegative=1，
    // 行数量、金额为负。与蓝字同一 CO 单据类型（vt 4，purbill / ppurbill），只是 Init 的 bPositive 不同（见下面的开关）。
    // U8 回写入库行 iSumBillQuantity（账套数据里红字入库行开票后为负，与发票行数量相等），提交前核对它按本次负数量移动。
    // 删除、复核、取消复核沿用蓝字路径（PuInvDel / PuInvReview），只把 Init 的 bPositive 按 bNegative 换掉；修改仍拒绝（PuEditMoreInv）。
    internal static partial class PuInv
    {
        // ===== 可调整：红字采购发票 Init，只改这一块 =====
        // 生单 Init(4, login, conn, info, bPositive, purbill|ppurbill, "普通采购", 0, "", pt) 的 bPositive。
        internal const bool RedGenPositive = false;
        // 红字发票（bNegative=1）删除、复核、取消复核的 Init bPositive。
        internal const bool RedOpPositive = false;
        // 可参照的红字入库单来源：无来源（库存，账套里现有的红字入库都是这种）、采购订单、来料检验单、采购到货单（参照采购退货单生成）。
        static readonly string[] RedSources = new string[] { "库存", "采购订单", "来料检验单", "采购到货单" };
        // ===== 可调整块结束 =====

        const string RedRdSql = "select convert(varchar(5), isnull(bredvouch,0)) from RdRecord01 where ID=?";
        const string RedBillSql = "select convert(varchar(5), isnull(bNegative,0)) from PurBillVouch where PBVID=?";

        // 来源入库单是否红字。不存在时返回 false，交给蓝字路径报 404。
        static bool IsRedRd(object conn, int rdId)
        {
            string flag = Rows.Scalar(conn, RedRdSql, new object[] { rdId });
            return flag != null && flag.Trim() == "1";
        }

        // 删除、复核用的 Init bPositive：红字发票（bNegative=1）用 RedOpPositive，其余 true。
        // 表头 bNegative=0 而行数量为负的混合发票仍按蓝字打开。
        internal static bool PositiveOf(object conn, int pbvid)
        {
            string flag = Rows.Scalar(conn, RedBillSql, new object[] { pbvid });
            if (flag != null && flag.Trim() == "1")
            {
                return RedOpPositive;
            }
            return true;
        }

        // Generate 在登录后按来源入库单的 bredvouch 分到这里。job 的表头字段已填好。
        static ApiResult GenerateRed(InvJob job, object[] lines)
        {
            WorkContext ctx = job.Ctx;
            job.Red = true;
            job.Rd = LoadRedRd(ctx.Conn, job.RdId);
            job.Lines = LoadRedLines(ctx.Conn, job, lines);
            if (Rows.Scalar(ctx.Conn, CodeSql, new object[] { job.Code, job.BillType }) != null)
            {
                throw new BridgeException(409, "state_mismatch", "发票号已存在");
            }
            RequireOpen(ctx.Conn, job.Date);
            return Save(job);
        }

        // 红字入库单：已审核、普通采购、来源在 RedSources 里。
        static Dictionary<string, object> LoadRedRd(object conn, int rdId)
        {
            Dictionary<string, object> rd = Rows.One(conn, RdSql, new object[] { rdId });
            if (rd == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (CoRows.Col(rd, "cHandler").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            if (Array.IndexOf(RedSources, CoRows.Col(rd, "cSource")) < 0)
            {
                throw new BridgeException(409, "state_mismatch", "只能参照来源为库存、采购订单、来料检验单或采购到货单的红字入库单");
            }
            if (CoRows.Col(rd, "cBusType") != "普通采购")
            {
                throw new BridgeException(409, "state_mismatch", "只支持普通采购的入库单");
            }
            return rd;
        }

        // 请求数量是正数，桥写负数：InvLine.Qty 为负，金额随之为负，单价仍为正。
        static List<InvLine> LoadRedLines(object conn, InvJob job, object[] lines)
        {
            decimal exch = Rate(CoRows.Col(job.Rd, "ExchRate"));
            List<InvLine> list = new List<InvLine>();
            for (int i = 0; i < lines.Length; i++)
            {
                Dictionary<string, object> raw = PuInvReq.LineOf(lines[i]);
                InvLine line = new InvLine();
                line.RdsId = MfgReq.LineId(raw);
                line.Qty = -MfgReq.LineQty(raw);
                line.Src = Rows.One(conn, RdLineSql, new object[] { line.RdsId, job.RdId });
                if (line.Src == null)
                {
                    throw new BridgeException(400, "bad_request", "明细行不存在");
                }
                line.RdQty = Num(CoRows.Col(line.Src, "Qty"));
                line.Billed = Num(CoRows.Col(line.Src, "Billed"));
                RequireRedLine(conn, line, job.BillType);
                line.Amt = StockGen.PoAmounts(PriceSrc(line.Src, job.BillType), line.Qty, exch);
                list.Add(line);
            }
            return list;
        }

        // 同蓝字 RequireLine，但入库行数量必须为负，也不要求采购订单行（无来源的红字入库没有 iPOsID）。
        static void RequireRedLine(object conn, InvLine line, string billType)
        {
            Dictionary<string, object> src = line.Src;
            if (line.RdQty >= 0m)
            {
                throw new BridgeException(409, "state_mismatch", "红字入库单行数量不是负数");
            }
            RequireLeft(line);
            string price = CoRows.FlagOf(src, "bTaxCost") ? "iTaxPrice" : "iUnitPrice";
            if (billType == "02")
            {
                price = CoRows.Col(src, "iTaxPrice").Length > 0 ? "iTaxPrice" : "iUnitPrice";
            }
            if (CoRows.Col(src, price).Length == 0 || CoRows.Col(src, "UnitCost").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "入库单行没有单价");
            }
            if (Rows.Scalar(conn, SettledSql, new object[] { line.RdsId }) != null)
            {
                throw new BridgeException(409, "state_mismatch", "入库单行已结算");
            }
        }

        // 红字发票表头：bNegative=True（布尔按 U8 格式）。其余表头同蓝字 StampHead。
        static void MarkRedHead(object dom)
        {
            StockDom.SetHeadValue(dom, "bnegative", "True");
        }
    }
}
