using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 采购退货单的新 z:row 与保存事务。表头同蓝字到货单（PuArrDom），只把 ibilltype / bnegative 改成 1；
    // 表体数量、件数、金额写负数，参照到货单时 icorid = 原行 Autoid，有订单行时带 iposid / cordercode / corufts。
    internal static partial class PuRet
    {
        const string IdByCodeSql = "select convert(varchar(20), ID) from PU_ArrivalVouch where cCode=? and iBillType=1";

        static readonly string[] HeadFixed = new string[]
        {
            "id", "", "editprop", "A", "ibilltype", "1", "bnegative", "1", "idiscounttaxtype", "0", "ivtid", "8169",
            "iverifystateex", "0", "iswfcontrolled", "0"
        };

        static readonly string[] BodyFixed = new string[]
        {
            "autoid", "", "id", "", "editprop", "A", "frefusequantity", "0", "frefusenum", "0",
            "fvalidquantity", "0", "fvalidnum", "0", "fretquantity", "0", "fretnum", "0", "bgsp", "0", "binspect", "0"
        };

        static readonly string[] AmountNames = new string[]
        {
            "itaxrate", "itaxrate", "btaxcost", "btaxcost", "ioritaxcost", "ioritaxcost", "ioricost", "ioricost",
            "iorimoney", "iorimoney", "ioritaxprice", "ioritaxprice", "iorisum", "iorisum", "icost", "iunitcost",
            "imoney", "iprice", "itaxprice", "itaxprice", "isum", "isum"
        };

        static ApiResult Save(RetJob job)
        {
            WorkContext ctx = job.Ctx;
            object info = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                ctx.PuInitType = CoRows.Col(job.Src, "cPTCode");
                Open(ctx, true, false, out info, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                PuInv.ReadBlank(co, doms, ctx.Item);
                StampHead(job, doms[0]);
                job.Code = PuArr.Allocate(co, doms, ctx.Item);
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

        static void StampHead(RetJob job, object dom)
        {
            Dictionary<string, object> src = job.Src;
            string maker = job.Ctx.Session.OperatorName == null ? "" : job.Ctx.Session.OperatorName.Trim();
            object row = DomRows.AddRow(dom);
            try
            {
                List<string> schema = DomRows.Schema(dom);
                PutPairs(dom, row, schema, HeadFixed);
                Put(dom, row, schema, "ddate", job.Date);
                Put(dom, row, schema, "cvencode", CoRows.Col(src, "cVenCode"));
                Copy(dom, row, schema, "cdepcode", job.Dept);
                Copy(dom, row, schema, "cpersoncode", CoRows.Col(src, "cPersonCode"));
                Copy(dom, row, schema, "cptcode", job.Ctx.PurchaseTypeOr(CoRows.Col(src, "cPTCode")));
                Put(dom, row, schema, "cbustype", CoRows.Col(src, "cBusType"));
                Put(dom, row, schema, "cexch_name", job.Ctx.HomeCurrencyOr(CoRows.Col(src, "cexch_name")));
                Put(dom, row, schema, "iexchrate", Rate(CoRows.Col(src, "nflat")).ToString("0.##########", CultureInfo.InvariantCulture));
                Put(dom, row, schema, "itaxrate", PuInv.Num(CoRows.Col(src, "iTaxRate")).ToString("0.####", CultureInfo.InvariantCulture));
                Copy(dom, row, schema, "cpocode", CoRows.Col(src, "PoCode"));
                Copy(dom, row, schema, "cmemo", job.Memo);
                Put(dom, row, schema, "cmaker", maker);
            }
            finally
            {
                ComUtil.ReleaseOne(row);
            }
        }

        static void FillBody(RetJob job, object dom)
        {
            List<string> schema = DomRows.Schema(dom);
            for (int i = 0; i < job.Lines.Count; i++)
            {
                object row = DomRows.AddRow(dom);
                try
                {
                    StampLine(job, job.Lines[i], i + 1, dom, row, schema);
                }
                finally
                {
                    ComUtil.ReleaseOne(row);
                }
            }
        }

        static void StampLine(RetJob job, RetLine line, int rowNo, object dom, object row, List<string> schema)
        {
            Dictionary<string, object> src = line.Src;
            string no = rowNo.ToString(CultureInfo.InvariantCulture);
            PutPairs(dom, row, schema, BodyFixed);
            for (int k = 0; k < AmountNames.Length; k += 2)
            {
                Put(dom, row, schema, AmountNames[k], line.Amt[AmountNames[k + 1]]);
            }
            Put(dom, row, schema, "cinvcode", CoRows.Col(src, "cInvCode"));
            Copy(dom, row, schema, "cwhcode", Or(job.Wh, CoRows.Col(src, "SrcWh")));
            Put(dom, row, schema, "iquantity", (-line.Qty).ToString("0.######", CultureInfo.InvariantCulture));
            PutUnit(dom, row, schema, line);
            if (!job.FromPo)
            {
                Put(dom, row, schema, "icorid", line.SrcLine.ToString(CultureInfo.InvariantCulture));
                Copy(dom, row, schema, "cbatch", CoRows.Col(src, "SrcBatch"));
            }
            if (line.PoLine > 0)
            {
                Put(dom, row, schema, "iposid", line.PoLine.ToString(CultureInfo.InvariantCulture));
                Copy(dom, row, schema, "cordercode", CoRows.Col(src, "OrderCode"));
            }
            // 实测：参照到货单时 U8 用 corufts 核对原到货单表头的 ufts（填订单的 ufts 报「所参照的到货单已被他人操作」）；
            // 参照订单时核对订单表头的 ufts。
            Copy(dom, row, schema, "corufts", CoRows.Col(src, job.FromPo ? "PoUfts" : "ArrUfts"));
            Put(dom, row, schema, "irowno", no);
            Put(dom, row, schema, "ivouchrowno", no);
        }

        // 没有辅计量时 inum 写 0；有时写 cunitid、iinvexchrate 和 inum = round(−数量 / 换算率, 6)。
        static void PutUnit(object dom, object row, List<string> schema, RetLine line)
        {
            if (line.Rate <= 0m)
            {
                Put(dom, row, schema, "inum", "0");
                return;
            }
            Put(dom, row, schema, "cunitid", line.Unit);
            Put(dom, row, schema, "iinvexchrate", StockUnits.Price(line.Rate));
            Put(dom, row, schema, "inum", StockUnits.Price(-line.Qty / line.Rate));
        }

        // VoucherSave2(h, b, 2, curId) 引用 {3}。保存前在事务里重读可退数量和回写基准；保存后按 Checks 核对回写。
        static int SaveTran(RetJob job, object co, object[] doms)
        {
            WorkContext ctx = job.Ctx;
            List<RetSnap> snaps = Plan(KeysOf(job));
            bool open = false;
            try
            {
                CoTrans.Begin(ctx.Conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(ctx.Conn);
                ReadLeft(ctx.Conn, job);
                ReadBefore(ctx.Conn, snaps);
                object[] args = new object[] { doms[0], doms[1], (short)2, "" };
                object ret = ComUtil.CallRef(co, "VoucherSave2", args, new int[] { 3 });
                ctx.Item.TranAfter = CoTrans.Count(ctx.Conn);
                string msg = Values.Text(ret).Trim();
                CoRows.Note(ctx.Item, "VoucherSave2 " + (msg.Length == 0 ? "ok" : msg));
                if (msg.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", msg);
                }
                StockCall.AfterCheck(ctx, delegate(object conn) { RequireWritten(conn, ctx.Item, snaps, 1); },
                    "采购退货单 " + job.Code);
                int id = CoRows.AsId(args[3]);
                DocMark.Created(ctx.Conn, "purchase_return", id, IdByCodeSql, new object[] { job.Code });
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

        static List<RetKey> KeysOf(RetJob job)
        {
            List<RetKey> keys = new List<RetKey>();
            for (int i = 0; i < job.Lines.Count; i++)
            {
                RetKey key = new RetKey();
                key.Source = job.Source.Name;
                key.Src = job.Lines[i].SrcLine;
                key.Po = job.Lines[i].PoLine;
                key.Qty = job.Lines[i].Qty;
                keys.Add(key);
            }
            return keys;
        }

        // 已提交。新主键先认 curId，读不到再按单号在新连接上找；都失败是 504。
        static ApiResult AfterSaved(RetJob job, int id)
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
                    return EditMsg.Saved(ctx, job.Kind, id, job.Source, job.SourceId);
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

        static void PutPairs(object dom, object row, List<string> schema, string[] pairs)
        {
            for (int k = 0; k < pairs.Length; k += 2)
            {
                Put(dom, row, schema, pairs[k], pairs[k + 1]);
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
    }
}
