using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 采购发票参照采购入库生成（01 专用 / 02 普通）与删除。PU 模板 vt 4，sBillType 用 purbill / ppurbill（不是 01/02）；
    // 空白 DOM 取 GetVoucherDataById(h, b, "", 0, "") 的 schema 再挂 z:row，VoucherSave2 在 CoTrans 里保存，
    // U8 同时回写 rdrecords01.iSumBillQuantity、PO_Podetails.iInvQTY。属性按测试账套上的实测结果写。
    internal static partial class PuInv
    {
        const string RdSql = "select h.cCode, h.cHandler, h.cSource, h.cBusType, h.cPTCode, t.cPTName, h.cVenCode, h.cDepCode,"
            + " h.cPersonCode, h.cExch_Name, c.cexch_code, convert(varchar(10), h.dDate, 23) as RdDate,"
            + " convert(varchar(40), convert(decimal(28,10), isnull(h.iExchRate,1))) as ExchRate"
            + " from RdRecord01 h left join PurchaseType t on t.cPTCode=h.cPTCode"
            + " left join foreigncurrency c on c.cexch_name=h.cExch_Name where h.ID=?";
        const string RdLineSql = "select r.cInvCode, convert(varchar(40), isnull(r.iQuantity,0)) as Qty,"
            + " convert(varchar(40), isnull(r.iSumBillQuantity,0)) as Billed, convert(varchar(20), r.iPOsID) as PoLine,"
            + " convert(varchar(5), isnull(r.bTaxCost,0)) as bTaxCost, convert(varchar(40), r.iOriTaxCost) as iTaxPrice,"
            + " convert(varchar(40), r.iOriCost) as iUnitPrice, convert(varchar(40), isnull(r.iTaxRate,0)) as iPerTaxRate,"
            + " convert(varchar(40), r.iUnitCost) as UnitCost, convert(varchar(40), isnull(r.iInvExchRate,0)) as InvRate,"
            + " p.cPOID, convert(varchar(5), isnull(i.iGroupType,0)) as GroupType, i.cGroupCode, i.cComUnitCode,"
            + " convert(varchar(5), isnull(i.bService,0)) as Service, convert(varchar(5), isnull(i.bInvBatch,0)) as InvBatch,"
            + " convert(varchar(5), isnull(i.bInvType,0)) as InvType"
            + " from rdrecords01 r left join PO_Podetails d on d.ID=r.iPOsID left join PO_Pomain p on p.POID=d.POID"
            + " left join Inventory i on i.cInvCode=r.cInvCode where r.AutoID=? and r.ID=?";
        const string SettledSql = "select top 1 convert(varchar(20), s.iRdsID) from PurSettleVouchs s where s.iRdsID=?";
        const string CodeSql = "select top 1 convert(varchar(20), PBVID) from PurBillVouch where cPBVCode=? and cPBVBillType=?";
        const string PeriodSql = "select convert(varchar(5), isnull(bflag_PU,0)) from GL_mend where iyear=? and iperiod=?";

        public static ApiResult Generate(WorkContext ctx, VoucherKind kind, int rdId,
            Dictionary<string, object> head, object[] lines)
        {
            PuInvReq.CheckGenerate(head, lines);
            InvJob job = new InvJob();
            job.Ctx = ctx;
            job.Kind = kind;
            job.RdId = rdId;
            job.Code = PuInvReq.Text(head, "cpbvcode");
            job.BillType = PuInvReq.BillType(head);
            job.Memo = PuInvReq.Text(head, "cpbvmemo");
            job.Date = PuInvReq.Text(head, "dpbvdate");
            if (job.Date.Length == 0)
            {
                job.Date = ctx.Item.Date == null ? "" : ctx.Item.Date.Trim();
            }
            // 红字入库单生成红字发票（PuInvRed）。
            if (IsRedRd(ctx.Conn, rdId))
            {
                return GenerateRed(job, lines);
            }
            job.Rd = LoadRd(ctx.Conn, rdId);
            job.Lines = LoadLines(ctx.Conn, job, lines);
            if (Rows.Scalar(ctx.Conn, CodeSql, new object[] { job.Code, job.BillType }) != null)
            {
                throw new BridgeException(409, "state_mismatch", "发票号已存在");
            }
            RequireOpen(ctx.Conn, job.Date);
            return Save(job);
        }

        // 来源入库单：已审核、来源采购订单、来料检验单或采购到货单（参照到货单生成，行上同样带订单行 iPOsID）、普通采购。
        static Dictionary<string, object> LoadRd(object conn, int rdId)
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
            string source = CoRows.Col(rd, "cSource");
            if (source != "采购订单" && source != "来料检验单" && source != "采购到货单")
            {
                throw new BridgeException(409, "state_mismatch", "只能参照来源为采购订单、来料检验单或采购到货单的入库单");
            }
            if (CoRows.Col(rd, "cBusType") != "普通采购")
            {
                throw new BridgeException(409, "state_mismatch", "只支持普通采购的入库单");
            }
            return rd;
        }

        static List<InvLine> LoadLines(object conn, InvJob job, object[] lines)
        {
            decimal exch = Rate(CoRows.Col(job.Rd, "ExchRate"));
            List<InvLine> list = new List<InvLine>();
            for (int i = 0; i < lines.Length; i++)
            {
                Dictionary<string, object> raw = PuInvReq.LineOf(lines[i]);
                InvLine line = new InvLine();
                line.RdsId = MfgReq.LineId(raw);
                line.Qty = MfgReq.LineQty(raw);
                line.Src = Rows.One(conn, RdLineSql, new object[] { line.RdsId, job.RdId });
                if (line.Src == null)
                {
                    throw new BridgeException(400, "bad_request", "明细行不存在");
                }
                line.RdQty = Num(CoRows.Col(line.Src, "Qty"));
                line.Billed = Num(CoRows.Col(line.Src, "Billed"));
                RequireLine(conn, line, job.BillType);
                line.Amt = StockGen.PoAmounts(PriceSrc(line.Src, job.BillType), line.Qty, exch);
                list.Add(line);
            }
            return list;
        }

        static void RequireLine(object conn, InvLine line, string billType)
        {
            Dictionary<string, object> src = line.Src;
            if (line.RdQty <= 0m)
            {
                throw new BridgeException(409, "state_mismatch", "红字入库单不能生成发票");
            }
            if (CoRows.AsId(CoRows.Col(src, "PoLine")) <= 0 || CoRows.Col(src, "cPOID").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "入库单行没有对应的采购订单行");
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

        // 红字入库行（数量为负，PuInvRed）：本次数量也是负数，不能比 入库 − 已开票 更小。
        static void RequireLeft(InvLine line)
        {
            bool over = line.RdQty < 0m ? line.Qty < line.RdQty - line.Billed : line.Qty > line.RdQty - line.Billed;
            if (over)
            {
                throw new BridgeException(409, "state_mismatch", "超过可生单数量");
            }
        }

        // 普通发票（02）按 U8 客户端录入的做法：税率 0、不含税价 = 含税价（入库行含税单价，没有时用无税单价），金额 = 价税合计。
        static Dictionary<string, object> PriceSrc(Dictionary<string, object> src, string billType)
        {
            if (billType != "02")
            {
                return src;
            }
            string price = CoRows.Col(src, "iTaxPrice");
            if (price.Length == 0)
            {
                price = CoRows.Col(src, "iUnitPrice");
            }
            Dictionary<string, object> plain = new Dictionary<string, object>();
            plain["bTaxCost"] = "1";
            plain["iPerTaxRate"] = "0";
            plain["iTaxPrice"] = price;
            plain["iUnitPrice"] = price;
            return plain;
        }

        // GL_mend 按发票日期的年、月查采购结账标志；没有该期间的行不拦，交给 U8。
        internal static void RequireOpen(object conn, string date)
        {
            DateTime day;
            if (!DateTime.TryParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out day))
            {
                throw new BridgeException(400, "bad_request", "发票日期必须是 yyyy-MM-dd");
            }
            string flag = Rows.Scalar(conn, PeriodSql, new object[] { day.Year, day.Month });
            if (flag != null && flag.Trim() == "1")
            {
                throw new BridgeException(409, "state_mismatch", "发票日期所在期间采购已结账");
            }
        }

        static decimal Rate(string text)
        {
            decimal rate = Num(text);
            return rate <= 0m ? 1m : rate;
        }

        internal static decimal Num(string text)
        {
            decimal value;
            if (!StockUnits.Dec(text, out value))
            {
                return 0m;
            }
            return value;
        }

        sealed class InvJob
        {
            public WorkContext Ctx;
            public VoucherKind Kind;
            public int RdId;
            public string Code;
            public string BillType;
            public string Memo;
            public string Date;
            public bool Red;
            public Dictionary<string, object> Rd;
            public List<InvLine> Lines;
        }

        sealed class InvLine
        {
            public int RdsId;
            public decimal Qty;
            public decimal RdQty;
            public decimal Billed;
            public Dictionary<string, object> Src;
            public Dictionary<string, string> Amt;
        }
    }
}
