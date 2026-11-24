using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 无来源到货单与无来源采购退货单（SrcLess）：同参照采购订单生成（PuArr）的 Init(2, …, "0", …)、GetVoucherDataById 空白、GetVoucherNO(ah, "26")、
    // VoucherSave2(h, b, 2, curId) 引用 {3}，在 CoTrans 里；表头不写 cpocode，表体不写 iposid / cordercode / corufts / icorid。
    // 采购退货单是红字到货单：只把 Init 换成红字参数（PuRet.Open，bPositive=false、sBillType="1"），表头 ibilltype=1 / bnegative=1，
    // 数量、件数、金额取负（FreeLine 的 sign），其余同蓝字；不受 PU.bPTHavePO 限制。
    // 只收本币供应商、普通采购；单价按调用方的 ioricost（无税）或 ioritaxcost（含税）照 StockGen.PoAmounts 的口径算，
    // 税率缺省取存货档案。未覆盖：U8 保存时对无订单到货行的其它必填列（属性按测试账套实测结果写）。
    internal static partial class PuArr
    {
        const string FreeShapeSql = "select convert(varchar(5), isnull(h.iBillType,0)) as BillType,"
            + " convert(varchar(5), isnull(h.bNegative,0)) as Neg, convert(varchar(20), isnull(s.iCorId,0)) as CorId,"
            + " convert(varchar(20), isnull(s.iPOsID,0)) as PoLine, convert(varchar(40), isnull(s.iQuantity,0)) as Qty,"
            + " convert(varchar(5), isnull(s.bGsp,0)) as Gsp"
            + " from PU_ArrivalVouch h inner join PU_ArrivalVouchs s on s.ID=h.ID where h.cCode=?";
        static readonly string[] RedHead = new string[] { "ibilltype", "1", "bnegative", "1" };
        const string FreeVenSql = "select cVenCode, cVenExch_name, cVenPerson, cVenDepart from Vendor where cVenCode=?";
        const string FreeInvSql = "select cInvCode, convert(varchar(40), isnull(iTaxRate,0)) as iTaxRate,"
            + " convert(varchar(5), isnull(bPropertyCheck,0)) as PropCheck, cDefWareHouse,"
            + " convert(varchar(5), isnull(iGroupType,0)) as GroupType, convert(varchar(5), isnull(bPurchase,0)) as Buy,"
            + " isnull(cPUComUnitCode,'') as cUnitID, convert(varchar(40), c.iChangRate) as ChangRate"
            + " from Inventory i left join ComputationUnit c on c.cComunitCode=i.cPUComUnitCode where i.cInvCode=?";

        internal static ApiResult CreateFree(WorkContext ctx, VoucherKind kind, Dictionary<string, object> head, object[] lines)
        {
            ArrJob job = new ArrJob();
            job.Ctx = ctx;
            job.Kind = kind;
            job.Red = kind.Name == "purchase_return";
            string title = SrcLess.Plain(kind);
            job.Po = FreeHead(ctx, head, title);
            PurchaseCo.RefuseFlow(ctx.Conn, kind, new Dictionary<string, object>());
            job.Wh = "";
            job.Memo = MfgReq.Text(head, "cmemo");
            job.Dept = CoRows.Col(job.Po, "cDepCode");
            job.Date = Or(MfgReq.Text(head, "ddate"), ctx.Item.Date == null ? "" : ctx.Item.Date.Trim());
            job.Lines = new List<ArrLine>();
            for (int i = 0; i < lines.Length; i++)
            {
                job.Lines.Add(FreeLine(ctx.Conn, lines[i] as Dictionary<string, object>, job.Red ? -1m : 1m, title));
            }
            return SaveFree(job);
        }

        // 表头按采购订单行的键名拼一行（StampFree 读它）：供应商、部门、业务员、采购类型、本币、汇率 1、表头税率 0。
        static Dictionary<string, object> FreeHead(WorkContext ctx, Dictionary<string, object> head, string title)
        {
            object conn = ctx.Conn;
            string ven = MfgReq.Text(head, "cvencode");
            Dictionary<string, object> row = Rows.One(conn, FreeVenSql, new object[] { ven });
            if (row == null)
            {
                throw new BridgeException(400, "bad_request", "供应商不存在：" + ven);
            }
            string exch = CoRows.Col(row, "cVenExch_name");
            string home = ctx.HomeCurrency;
            if (exch.Length > 0 && exch != home)
            {
                throw new BridgeException(409, "state_mismatch", "外币供应商的" + title + "本期不支持");
            }
            Dictionary<string, object> po = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            po["cVenCode"] = ven;
            po["cDepCode"] = Or(MfgReq.Text(head, "cdepcode"), CoRows.Col(row, "cVenDepart"));
            if (CoRows.Col(po, "cDepCode").Length == 0)
            {
                // 实测：U8 保存无订单到货单要部门，缺了报「部门不能为空，请检查！」。供应商分管部门也没有时在调用 U8 前拒绝。
                throw BridgeException.BadField("head.cdepcode", "无来源" + title + "需要部门 cDepCode");
            }
            po["cPersonCode"] = Or(MfgReq.Text(head, "cpersoncode"), CoRows.Col(row, "cVenPerson"));
            po["cPTCode"] = ctx.PurchaseTypeOr(MfgReq.Text(head, "cptcode"));
            po["cBusType"] = "普通采购";
            po["cexch_name"] = home;
            po["nflat"] = "1";
            po["iTaxRate"] = "0";
            return po;
        }

        // sign：蓝字 1，红字 −1；Qty 和金额带符号（单价仍为正），后面的 DOM 直接写。
        static ArrLine FreeLine(object conn, Dictionary<string, object> raw, decimal sign, string title)
        {
            string inv = MfgReq.Text(raw, "cinvcode");
            Dictionary<string, object> src = Rows.One(conn, FreeInvSql, new object[] { inv });
            if (src == null)
            {
                throw new BridgeException(400, "bad_request", "存货不存在：" + inv);
            }
            if (!CoRows.FlagOf(src, "Buy"))
            {
                throw new BridgeException(409, "state_mismatch", "存货 " + inv + " 不是外购属性");
            }
            ArrLine line = new ArrLine();
            line.Qty = sign * SrcLessReq.Qty(raw);
            line.Src = src;
            FreeUnits(line, title);
            line.Amt = StockGen.PoAmounts(FreePrice(raw, src), line.Qty, 1m, "");
            line.Src["FreeWh"] = MfgReq.Text(raw, "cwhcode");
            return line;
        }

        // 价格键：ioritaxcost（含税）与 ioricost（无税）只能给一个，都不给按 0；税率缺省取存货档案。
        static Dictionary<string, object> FreePrice(Dictionary<string, object> raw, Dictionary<string, object> src)
        {
            string tax = MfgReq.Text(raw, "ioritaxcost");
            string plain = MfgReq.Text(raw, "ioricost");
            if (tax.Length > 0 && plain.Length > 0)
            {
                throw new BridgeException(400, "bad_request", "含税单价 ioritaxcost 不能和无税单价 ioricost 同时填写");
            }
            string rate = Or(MfgReq.Text(raw, "itaxrate"), CoRows.Col(src, "iTaxRate"));
            Dictionary<string, object> price = new Dictionary<string, object>();
            price["bTaxCost"] = tax.Length > 0 ? "1" : "0";
            price["iTaxPrice"] = NonNegative(tax);
            price["iUnitPrice"] = NonNegative(plain);
            price["iPerTaxRate"] = NonNegative(rate);
            return price;
        }

        // 空串按 0；非数或负数 400。
        static string NonNegative(string text)
        {
            decimal value;
            if (text.Length == 0)
            {
                return "0";
            }
            if (!StockUnits.Dec(text, out value) || value < 0m)
            {
                throw new BridgeException(400, "bad_request", "单价和税率必须是不小于 0 的数");
            }
            return text;
        }

        // 固定换算（1）用存货的采购默认单位；浮动换算（2）不支持；无换算不写辅计量。
        static void FreeUnits(ArrLine line, string title)
        {
            string group = CoRows.Col(line.Src, "GroupType");
            line.Unit = "";
            line.Rate = 0m;
            if (group == "2")
            {
                throw new BridgeException(409, "state_mismatch", "暂不支持浮动换算率存货的无来源" + title);
            }
            decimal rate = PuInv.Num(CoRows.Col(line.Src, "ChangRate"));
            string unit = CoRows.Col(line.Src, "cUnitID");
            if (group == "1" && unit.Length > 0 && rate > 0m)
            {
                line.Unit = unit;
                line.Rate = rate;
            }
        }

        static ApiResult SaveFree(ArrJob job)
        {
            WorkContext ctx = job.Ctx;
            object info = null;
            object co = null;
            object[] doms = new object[2];
            int id;
            try
            {
                ctx.PuInitType = CoRows.Col(job.Po, "cPTCode");
                OpenFree(job, out info, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                PuInv.ReadBlank(co, doms, ctx.Item);
                StampFree(job, doms[0]);
                job.Code = Allocate(co, doms, ctx.Item);
                FillFree(job, doms[1]);
                id = SaveFreeTran(job, co, doms);
            }
            finally
            {
                ComUtil.Final(doms[1]);
                ComUtil.Final(doms[0]);
                ComUtil.Final(co);
                ComUtil.Final(info);
            }
            return AfterFree(job, id);
        }

        // 红字走 PuRet 的红字 Init（同采购退货单生单），蓝字同参照订单生单。
        static void OpenFree(ArrJob job, out object info, out object co)
        {
            if (job.Red)
            {
                PuRet.Open(job.Ctx, true, false, out info, out co);
                return;
            }
            PuInv.Open(job.Ctx, 2, "0", out info, out co);
        }

        static void StampFree(ArrJob job, object dom)
        {
            Dictionary<string, object> po = job.Po;
            string maker = job.Ctx.Session.OperatorName == null ? "" : job.Ctx.Session.OperatorName.Trim();
            object row = DomRows.AddRow(dom);
            try
            {
                List<string> schema = DomRows.Schema(dom);
                PutPairs(dom, row, schema, HeadFixed);
                if (job.Red)
                {
                    PutPairs(dom, row, schema, RedHead);
                }
                Put(dom, row, schema, "ddate", job.Date);
                Put(dom, row, schema, "cvencode", CoRows.Col(po, "cVenCode"));
                Copy(dom, row, schema, "cdepcode", job.Dept);
                Copy(dom, row, schema, "cpersoncode", CoRows.Col(po, "cPersonCode"));
                Copy(dom, row, schema, "cptcode", CoRows.Col(po, "cPTCode"));
                Put(dom, row, schema, "cbustype", "普通采购");
                Put(dom, row, schema, "cexch_name", CoRows.Col(po, "cexch_name"));
                Put(dom, row, schema, "iexchrate", "1");
                Put(dom, row, schema, "itaxrate", "0");
                Copy(dom, row, schema, "cmemo", job.Memo);
                Put(dom, row, schema, "cmaker", maker);
            }
            finally
            {
                ComUtil.ReleaseOne(row);
            }
        }

        static void FillFree(ArrJob job, object dom)
        {
            List<string> schema = DomRows.Schema(dom);
            for (int i = 0; i < job.Lines.Count; i++)
            {
                object row = DomRows.AddRow(dom);
                try
                {
                    ArrLine line = job.Lines[i];
                    string no = (i + 1).ToString(CultureInfo.InvariantCulture);
                    PutPairs(dom, row, schema, BodyFixed);
                    for (int k = 0; k < AmountNames.Length; k += 2)
                    {
                        Put(dom, row, schema, AmountNames[k], line.Amt[AmountNames[k + 1]]);
                    }
                    Put(dom, row, schema, "cinvcode", CoRows.Col(line.Src, "cInvCode"));
                    Copy(dom, row, schema, "cwhcode", Or(CoRows.Col(line.Src, "FreeWh"), CoRows.Col(line.Src, "cDefWareHouse")));
                    Put(dom, row, schema, "iquantity", line.Qty.ToString("0.######", CultureInfo.InvariantCulture));
                    PutUnit(dom, row, schema, line);
                    // 红字（采购退货）不是待检入库，bgsp 恒 0，同有来源的采购退货生成。
                    Put(dom, row, schema, "bgsp", !job.Red && CoRows.Col(line.Src, "PropCheck") == "1" ? "1" : "0");
                    Put(dom, row, schema, "binspect", "0");
                    Put(dom, row, schema, "irowno", no);
                    Put(dom, row, schema, "ivouchrowno", no);
                }
                finally
                {
                    ComUtil.ReleaseOne(row);
                }
            }
        }

        // 没有来源，不核对回写：VoucherSave2 返回空串即成功；提交前核对落库的红蓝字标志、行无来源、数量符号。
        static int SaveFreeTran(ArrJob job, object co, object[] doms)
        {
            WorkContext ctx = job.Ctx;
            bool open = false;
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                object[] args = new object[] { doms[0], doms[1], (short)2, "" };
                object ret = ComUtil.CallRef(co, "VoucherSave2", args, new int[] { 3 });
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                string msg = Values.Text(ret).Trim();
                CoRows.Note(ctx.Item, "VoucherSave2 " + (msg.Length == 0 ? "ok" : msg));
                if (msg.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", msg);
                }
                StockCall.AfterCheck(ctx, delegate(object conn) { RequireShape(conn, job); }, job.Kind.Title + " " + job.Code);
                int id = CoRows.AsId(args[3]);
                DocMark.Created(ctx.Conn, job.Kind.Name, id, IdByCodeSql, new object[] { job.Code });
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

        // 单号与采购退货单共用卡片 26 的编号，按单号取行不分红蓝。
        static void RequireShape(object conn, ArrJob job)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, FreeShapeSql, new object[] { job.Code }, 500);
            if (rows == null || rows.Count != job.Lines.Count)
            {
                throw new BridgeException(409, "u8_rejected", "U8 保存的明细行数与请求不符");
            }
            for (int i = 0; i < rows.Count; i++)
            {
                RequireRow(rows[i], job.Red);
            }
        }

        internal static void RequireRow(Dictionary<string, object> row, bool red)
        {
            string flag = red ? "1" : "0";
            if (CoRows.Col(row, "BillType") != flag || CoRows.Col(row, "Neg") != flag)
            {
                throw new BridgeException(409, "u8_rejected", "U8 保存的红蓝字标志与请求不符");
            }
            if (CoRows.AsId(CoRows.Col(row, "CorId")) != 0 || CoRows.AsId(CoRows.Col(row, "PoLine")) != 0)
            {
                throw new BridgeException(409, "u8_rejected", "U8 给无来源明细行写了来源");
            }
            if (red && CoRows.Col(row, "Gsp") == "1")
            {
                throw new BridgeException(409, "u8_rejected", "U8 保存的采购退货单行带了待检标志");
            }
            decimal qty = PuInv.Num(CoRows.Col(row, "Qty"));
            if (red ? qty >= 0m : qty <= 0m)
            {
                throw new BridgeException(409, "u8_rejected", "U8 保存的数量符号与请求不符");
            }
        }

        static ApiResult AfterFree(ArrJob job, int id)
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
                    return EditMsg.Saved(ctx, job.Kind, id, null, 0);
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
    }
}
