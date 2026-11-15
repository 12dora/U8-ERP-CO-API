using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 到货单新 z:row 的属性，按测试账套上的实测结果写；另外只多写调用方的 cMemo 和订单上的业务员。
    // corufts 不在 pu_arrbody 的 schema 里，照样写成属性：U8 的订单回写读它做并发核对。
    internal static partial class PuArr
    {
        static readonly string[] HeadFixed = new string[]
        {
            "id", "", "editprop", "A", "ibilltype", "0", "bnegative", "0", "idiscounttaxtype", "0", "ivtid", "8169",
            "iverifystateex", "0", "iswfcontrolled", "0"
        };

        static readonly string[] BodyFixed = new string[]
        {
            "autoid", "", "id", "", "editprop", "A", "frefusequantity", "0", "frefusenum", "0",
            "fvalidquantity", "0", "fvalidnum", "0", "fretquantity", "0", "fretnum", "0"
        };

        static readonly string[] AmountNames = new string[]
        {
            "itaxrate", "itaxrate", "btaxcost", "btaxcost", "ioritaxcost", "ioritaxcost", "ioricost", "ioricost",
            "iorimoney", "iorimoney", "ioritaxprice", "ioritaxprice", "iorisum", "iorisum", "icost", "iunitcost",
            "imoney", "iprice", "itaxprice", "itaxprice", "isum", "isum"
        };

        static void StampHead(ArrJob job, object dom)
        {
            Dictionary<string, object> po = job.Po;
            string maker = job.Ctx.Session.OperatorName == null ? "" : job.Ctx.Session.OperatorName.Trim();
            object row = DomRows.AddRow(dom);
            try
            {
                List<string> schema = DomRows.Schema(dom);
                PutPairs(dom, row, schema, HeadFixed);
                Put(dom, row, schema, "ddate", job.Date);
                Put(dom, row, schema, "cvencode", CoRows.Col(po, "cVenCode"));
                Copy(dom, row, schema, "cdepcode", job.Dept);
                Copy(dom, row, schema, "cpersoncode", CoRows.Col(po, "cPersonCode"));
                Copy(dom, row, schema, "cptcode", job.Ctx.PurchaseTypeOr(CoRows.Col(po, "cPTCode")));
                Put(dom, row, schema, "cbustype", CoRows.Col(po, "cBusType"));
                Put(dom, row, schema, "cexch_name", job.Ctx.HomeCurrencyOr(CoRows.Col(po, "cexch_name")));
                Put(dom, row, schema, "iexchrate", Rate(CoRows.Col(po, "nflat")).ToString("0.##########", CultureInfo.InvariantCulture));
                Put(dom, row, schema, "itaxrate", PuInv.Num(CoRows.Col(po, "iTaxRate")).ToString("0.####", CultureInfo.InvariantCulture));
                Put(dom, row, schema, "cpocode", CoRows.Col(po, "cPOID"));
                Copy(dom, row, schema, "cmemo", job.Memo);
                Put(dom, row, schema, "cmaker", maker);
            }
            finally
            {
                ComUtil.ReleaseOne(row);
            }
        }

        // GetVoucherNO(ah, "26", err, no) 四个参数都按引用；成功后把单号写进表头 ccode。采购退货单（PuRet）共用。
        internal static string Allocate(object co, object[] doms, WorkItem item)
        {
            object[] args = new object[] { doms[0], "26", "", "" };
            object ret = ComUtil.CallRef(co, "GetVoucherNO", args, new int[] { 0, 1, 2, 3 });
            CoRows.Swap(doms, 0, args[0]);
            string err = Values.Text(args[2]).Trim();
            string no = Values.Text(args[3]).Trim();
            CoRows.Note(item, "GetVoucherNO " + Values.Text(ret) + " " + err + " " + no);
            if (!Values.Flag(ret) || no.Length == 0)
            {
                throw new BridgeException(409, "u8_rejected", err.Length == 0 ? "未能取得单据号" : err);
            }
            List<object> rows = DomRows.RowsOf(doms[0]);
            try
            {
                if (rows.Count != 1)
                {
                    throw new BridgeException(500, "internal", "表头模板没有行");
                }
                DomRows.Set(doms[0], rows[0], "ccode", no);
            }
            finally
            {
                for (int i = 0; i < rows.Count; i++)
                {
                    ComUtil.ReleaseOne(rows[i]);
                }
            }
            return no;
        }

        static void FillBody(ArrJob job, object dom)
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

        static void StampLine(ArrJob job, ArrLine line, int rowNo, object dom, object row, List<string> schema)
        {
            Dictionary<string, object> src = line.Src;
            string no = rowNo.ToString(CultureInfo.InvariantCulture);
            string check = CoRows.Col(src, "PropCheck") == "1" ? "1" : "0";
            PutPairs(dom, row, schema, BodyFixed);
            for (int k = 0; k < AmountNames.Length; k += 2)
            {
                Put(dom, row, schema, AmountNames[k], line.Amt[AmountNames[k + 1]]);
            }
            Put(dom, row, schema, "cinvcode", CoRows.Col(src, "cInvCode"));
            Copy(dom, row, schema, "cwhcode", Or(job.Wh, CoRows.Col(src, "cDefWareHouse")));
            Put(dom, row, schema, "iquantity", line.Qty.ToString("0.######", CultureInfo.InvariantCulture));
            PutUnit(dom, row, schema, line);
            Put(dom, row, schema, "iposid", line.PoLine.ToString(CultureInfo.InvariantCulture));
            Put(dom, row, schema, "cordercode", CoRows.Col(job.Po, "cPOID"));
            Put(dom, row, schema, "corufts", CoRows.Col(job.Po, "PoUfts"));
            Put(dom, row, schema, "bgsp", check);
            // binspect 是「已全部报检」：报检满额时 U8 置 1、删掉报检单回 0（实测）。新到货单写 0，否则报检参照（QM_QREFARR）看不到这一行。
            Put(dom, row, schema, "binspect", "0");
            Put(dom, row, schema, "irowno", no);
            Put(dom, row, schema, "ivouchrowno", no);
        }

        // 没有辅计量时 inum 写 0（实测核对）；有时写 cunitid、iinvexchrate 和 inum = round(数量 / 换算率, 6)。
        static void PutUnit(object dom, object row, List<string> schema, ArrLine line)
        {
            if (line.Rate <= 0m)
            {
                Put(dom, row, schema, "inum", "0");
                return;
            }
            Put(dom, row, schema, "cunitid", line.Unit);
            Put(dom, row, schema, "iinvexchrate", StockUnits.Price(line.Rate));
            Put(dom, row, schema, "inum", StockUnits.Price(line.Qty / line.Rate));
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
