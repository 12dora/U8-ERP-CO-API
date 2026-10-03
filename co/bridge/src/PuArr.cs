using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 到货单参照采购订单生成（已在测试账套核对）。VoucherCO_PU.Init(2, …, "0", …)：sBillType 必须是 "0"，
    // 空串时 U8 跳过订单回写（PU_CheckPOByArr 的临时表是空的）。空白 DOM 同采购发票取 GetVoucherDataById(…, 0, …)，
    // GetVoucherNO(ah, "26") 取号，VoucherSave2 在 CoTrans 里保存；U8 回写 PO_Podetails.iArrQTY / fPoArrQuantity / iArrMoney，
    // 提交前在同一事务里核对 iArrQTY。删除沿用 PurchaseEdit.Delete（U8 回退 iArrQTY，已实测）。
    internal static partial class PuArr
    {
        const string PoSql = "select m.cPOID, m.cVerifier, m.cCloser, m.cVenCode, m.cDepCode, m.cPersonCode, m.cPTCode,"
            + " m.cBusType, m.cexch_name, convert(varchar(40), convert(decimal(28,10), isnull(m.nflat,1))) as nflat,"
            + " convert(varchar(40), isnull(m.iTaxRate,0)) as iTaxRate, convert(varchar(30), convert(money, m.ufts), 2) as PoUfts"
            + " from PO_Pomain m where m.POID=?";
        const string LineSql = "select d.cInvCode, convert(varchar(40), isnull(d.iQuantity,0)) as Qty,"
            + " convert(varchar(40), isnull(d.iArrQTY,0)) as Arrived, d.cbCloser,"
            + " convert(varchar(5), isnull(d.bTaxCost,0)) as bTaxCost, convert(varchar(40), d.iTaxPrice) as iTaxPrice,"
            + " convert(varchar(40), d.iUnitPrice) as iUnitPrice, convert(varchar(40), d.iPerTaxRate) as iPerTaxRate,"
            + " convert(varchar(5), isnull(i.bPropertyCheck,0)) as PropCheck, i.cDefWareHouse,"
            + " d.cUnitID, convert(varchar(40), d.iNum) as PoNum, convert(varchar(5), isnull(i.iGroupType,0)) as GroupType,"
            + " convert(varchar(40), c.iChangRate) as ChangRate,"
            + " case when isnull(d.cFree1,N'')+isnull(d.cFree2,N'')+isnull(d.cFree3,N'')+isnull(d.cFree4,N'')+isnull(d.cFree5,N'')+isnull(d.cFree6,N'')+isnull(d.cFree7,N'')+isnull(d.cFree8,N'')+isnull(d.cFree9,N'')+isnull(d.cFree10,N'')<>N'' then '1' else '0' end as HasFree"
            + " from PO_Podetails d left join Inventory i on i.cInvCode=d.cInvCode"
            + " left join ComputationUnit c on c.cComunitCode=d.cUnitID where d.ID=? and d.POID=?";
        const string ArrivedSql = "select convert(varchar(40), isnull(iArrQTY,0)) from PO_Podetails where ID=?";
        const string IdByCodeSql = "select convert(varchar(20), ID) from PU_ArrivalVouch where cCode=?";
        static readonly string[] HeadKeys = new string[] { "cwhcode", "ddate", "cmemo", "cdepcode" };

        // 登录前校验：表头只收 cWhCode、dDate、cMemo、cDepCode；表体 1 到 200 行，只收 source_line_id、quantity。
        public static void CheckGenerate(Dictionary<string, object> head, object[] lines)
        {
            CheckHeadKeys(head);
            string date = MfgReq.Text(head, "ddate");
            DateTime day;
            if (date.Length > 0 && !DateTime.TryParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out day))
            {
                throw BridgeException.BadField("head.ddate", "单据日期必须是 yyyy-MM-dd");
            }
            if (lines == null || lines.Length < 1 || lines.Length > 200)
            {
                throw BridgeException.BadField("lines", "表体须为 1 到 200 行");
            }
            HashSet<int> seen = new HashSet<int>();
            for (int i = 0; i < lines.Length; i++)
            {
                try
                {
                    Dictionary<string, object> line = PuInvReq.LineOf(lines[i]);
                    if (!seen.Add(MfgReq.LineId(line)))
                    {
                        throw BridgeException.BadField("lines.source_line_id", "来源明细重复");
                    }
                    MfgReq.LineQty(line);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
        }

        static void CheckHeadKeys(Dictionary<string, object> head)
        {
            if (head == null)
            {
                return;
            }
            foreach (KeyValuePair<string, object> kv in head)
            {
                if (Array.IndexOf(HeadKeys, kv.Key == null ? "" : kv.Key.ToLowerInvariant()) < 0)
                {
                    throw BridgeException.BadField(FieldPath.Join("head", kv.Key), "不能设置字段 " + kv.Key)
                        .WithHint(FieldPath.WritableHint);
                }
                if (!Plain(kv.Value))
                {
                    throw BridgeException.BadField(FieldPath.Join("head", kv.Key), "字段 " + kv.Key + " 的值只能是字符串、数字或布尔");
                }
            }
        }

        static bool Plain(object value)
        {
            if (value is string || value is bool || value is int || value is long)
            {
                return true;
            }
            return value is decimal || value is double;
        }

        public static ApiResult Generate(WorkContext ctx, VoucherKind kind, int poId,
            Dictionary<string, object> head, object[] lines)
        {
            CheckGenerate(head, lines);
            ArrJob job = new ArrJob();
            job.Ctx = ctx;
            job.Kind = kind;
            job.PoId = poId;
            job.Po = LoadPo(ctx.Conn, poId);
            // 到货单审批流已发布：生成的单据要走审批，暂不支持。
            PurchaseCo.RefuseFlow(ctx.Conn, kind, new Dictionary<string, object>());
            job.Wh = MfgReq.Text(head, "cwhcode");
            job.Memo = MfgReq.Text(head, "cmemo");
            job.Dept = Or(MfgReq.Text(head, "cdepcode"), CoRows.Col(job.Po, "cDepCode"));
            job.Date = Or(MfgReq.Text(head, "ddate"), ctx.Item.Date == null ? "" : ctx.Item.Date.Trim());
            job.Lines = LoadLines(ctx.Conn, job, lines);
            return Save(job);
        }

        static Dictionary<string, object> LoadPo(object conn, int poId)
        {
            Dictionary<string, object> po = Rows.One(conn, PoSql, new object[] { poId });
            if (po == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (CoRows.Col(po, "cVerifier").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            if (CoRows.Col(po, "cCloser").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            if (CoRows.Col(po, "cBusType") != "普通采购")
            {
                throw new BridgeException(409, "state_mismatch", "只支持普通采购的订单");
            }
            return po;
        }

        static List<ArrLine> LoadLines(object conn, ArrJob job, object[] lines)
        {
            decimal exch = Rate(CoRows.Col(job.Po, "nflat"));
            string headRate = CoRows.Col(job.Po, "iTaxRate");
            List<ArrLine> list = new List<ArrLine>();
            for (int i = 0; i < lines.Length; i++)
            {
                Dictionary<string, object> raw = PuInvReq.LineOf(lines[i]);
                ArrLine line = new ArrLine();
                line.PoLine = MfgReq.LineId(raw);
                line.Qty = MfgReq.LineQty(raw);
                line.Src = Rows.One(conn, LineSql, new object[] { line.PoLine, job.PoId });
                if (line.Src == null)
                {
                    throw BridgeException.BadField(FieldPath.Item("lines", i) + ".source_line_id", "明细行不存在");
                }
                if (CoRows.Col(line.Src, "cbCloser").Length > 0)
                {
                    throw new BridgeException(409, "state_mismatch", "单据已关闭");
                }
                line.PoQty = PuInv.Num(CoRows.Col(line.Src, "Qty"));
                line.Arrived = PuInv.Num(CoRows.Col(line.Src, "Arrived"));
                RequireLeft(line);
                Units(line);
                string price = CoRows.FlagOf(line.Src, "bTaxCost") ? "iTaxPrice" : "iUnitPrice";
                if (CoRows.Col(line.Src, price).Length == 0)
                {
                    throw new BridgeException(409, "state_mismatch", "订单行没有单价");
                }
                line.Amt = StockGen.PoAmounts(line.Src, line.Qty, exch, headRate);
                list.Add(line);
            }
            return list;
        }

        // 辅计量：固定换算（iGroupType=1）取订单行 cUnitID，换算率取 ComputationUnit.iChangRate，没有时用订单 iQuantity / iNum；
        // 无换算（0）不写辅计量。浮动换算（2）和带自由项的订单行不支持。
        static void Units(ArrLine line)
        {
            Dictionary<string, object> src = line.Src;
            string group = CoRows.Col(src, "GroupType");
            if (group == "2" || CoRows.Col(src, "HasFree") == "1")
            {
                throw new BridgeException(409, "state_mismatch", "暂不支持浮动换算率/自由项的到货生单");
            }
            line.Unit = "";
            line.Rate = 0m;
            string unit = CoRows.Col(src, "cUnitID");
            if (group != "1" || unit.Length == 0)
            {
                return;
            }
            decimal rate = PuInv.Num(CoRows.Col(src, "ChangRate"));
            decimal poNum = PuInv.Num(CoRows.Col(src, "PoNum"));
            if (rate <= 0m && poNum > 0m)
            {
                rate = line.PoQty / poNum;
            }
            if (rate > 0m)
            {
                line.Unit = unit;
                line.Rate = rate;
            }
        }

        // 不允许超订单到货：数量不超过订单数量 − 累计到货。
        static void RequireLeft(ArrLine line)
        {
            if (line.Qty > line.PoQty - line.Arrived)
            {
                throw new BridgeException(409, "state_mismatch", "超过可生单数量");
            }
        }

        static ApiResult Save(ArrJob job)
        {
            WorkContext ctx = job.Ctx;
            object info = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                ctx.PuInitType = CoRows.Col(job.Po, "cPTCode");
                PuInv.Open(ctx, 2, "0", out info, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                PuInv.ReadBlank(co, doms, ctx.Item);
                StampHead(job, doms[0]);
                job.Code = Allocate(co, doms, ctx.Item);
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

        // VoucherSave2(h, b, 2, curId) 引用 {3}。保存前重读 iArrQTY 作基准并再查剩余；保存后核对订单行累计到货加上了本次数量。
        static int SaveTran(ArrJob job, object co, object[] doms)
        {
            WorkContext ctx = job.Ctx;
            bool open = false;
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                ReadArrived(ctx.Conn, job);
                object[] args = new object[] { doms[0], doms[1], (short)2, "" };
                object ret = ComUtil.CallRef(co, "VoucherSave2", args, new int[] { 3 });
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                string msg = Values.Text(ret).Trim();
                CoRows.Note(ctx.Item, "VoucherSave2 " + (msg.Length == 0 ? "ok" : msg));
                if (msg.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", msg);
                }
                StockCall.AfterCheck(ctx, delegate(object conn) { RequireArrived(conn, job); }, "到货单 " + job.Code);
                int id = CoRows.AsId(args[3]);
                DocMark.Created(ctx.Conn, "arrival", id, IdByCodeSql, new object[] { job.Code });
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

        static void ReadArrived(object conn, ArrJob job)
        {
            for (int i = 0; i < job.Lines.Count; i++)
            {
                ArrLine line = job.Lines[i];
                line.Arrived = PuInv.Num(Rows.Scalar(conn, ArrivedSql, new object[] { line.PoLine }));
                RequireLeft(line);
            }
        }

        static void RequireArrived(object conn, ArrJob job)
        {
            for (int i = 0; i < job.Lines.Count; i++)
            {
                ArrLine line = job.Lines[i];
                decimal now = PuInv.Num(Rows.Scalar(conn, ArrivedSql, new object[] { line.PoLine }));
                if (Math.Abs(now - line.Arrived - line.Qty) > 0.000001m)
                {
                    throw new BridgeException(409, "u8_rejected", "U8 没有回写采购订单累计到货数量");
                }
            }
        }

        // 已提交。新主键先认 curId，读不到再按单号在新连接上找；都失败是 504。
        static ApiResult AfterSaved(ArrJob job, int id)
        {
            WorkContext ctx = job.Ctx;
            try
            {
                if (id <= 0)
                {
                    id = IdByCode(ctx, job.Code);
                }
                if (id > 0)
                {
                    return EditMsg.Saved(ctx, job.Kind, id, Kinds.Find("purchase_order"), job.PoId);
                }
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "Saved " + ex.Message);
            }
            string text = "已保存但未能确定单据标识，单号 " + job.Code;
            if (id > 0)
            {
                text = text + "，标识 " + id.ToString(CultureInfo.InvariantCulture);
            }
            throw new BridgeException(504, "outcome_unknown", text);
        }

        static int IdByCode(WorkContext ctx, string code)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                return CoRows.AsId(Rows.Scalar(conn, IdByCodeSql, new object[] { code }));
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static decimal Rate(string text)
        {
            decimal rate = PuInv.Num(text);
            return rate <= 0m ? 1m : rate;
        }

        static string Or(string value, string fallback)
        {
            return value != null && value.Length > 0 ? value : fallback ?? "";
        }

        sealed class ArrJob
        {
            public WorkContext Ctx;
            public VoucherKind Kind;
            public int PoId;
            public string Code;
            public string Wh;
            public string Memo;
            public string Dept;
            public string Date;
            public Dictionary<string, object> Po;
            public List<ArrLine> Lines;
        }

        sealed class ArrLine
        {
            public int PoLine;
            public decimal Qty;
            public decimal PoQty;
            public decimal Arrived;
            public string Unit;
            public decimal Rate;
            public Dictionary<string, object> Src;
            public Dictionary<string, string> Amt;
        }
    }
}
