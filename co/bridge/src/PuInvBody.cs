using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 采购发票新 z:row 的属性：与 U8 自己加载出的发票同一套，值用 U8 的格式：
    // 布尔 True/False，bpayment 等整型 0，日期 yyyy-MM-dd，税率如 13。存货、入库行上的值由 SQL 取。
    // 普通发票（02）按 U8 客户端录入的做法：idiscounttaxtype=1，表头表体税率 0，btaxcost=False，iVTid 8165；专用发票（01）iVTid 8163。
    internal static partial class PuInv
    {
        static readonly string[] HeadFixed = new string[]
        {
            "editprop", "A", "pbvid", "", "csource", "采购", "bnegative", "False", "boriginal", "False", "bfirst", "False",
            "bpayment", "0", "bsettle", "0", "bcredit", "0", "iprintcount", "0", "iswfcontrolled", "False",
            "iverifystateex", "0", "ireturncount", "0", "bmerger", "0"
        };

        static readonly string[] BodyFixed = new string[]
        {
            "editprop", "A", "id", "", "pbvid", "", "cbaccounter", "", "ioritotal", "0", "itotal", "0",
            "bexbill", "False", "upsotype", "rd", "isPayment", "0", "isSettle", "0", "bcosting", "False",
            "brettax", "0", "bgift", "0"
        };

        static readonly string[] AmountNames = new string[]
        {
            "itaxrate", "itaxrate", "ioritaxcost", "ioritaxcost", "ioricost", "ioricost", "iorimoney", "iorimoney",
            "ioritaxprice", "ioritaxprice", "iorisum", "iorisum", "icost", "iunitcost", "imoney", "iprice",
            "itaxprice", "itaxprice", "isum", "isum"
        };

        static void StampHead(InvJob job, object dom)
        {
            Dictionary<string, object> rd = job.Rd;
            bool plain = job.BillType == "02";
            string maker = job.Ctx.Session.OperatorName == null ? "" : job.Ctx.Session.OperatorName.Trim();
            object row = DomRows.AddRow(dom);
            try
            {
                List<string> schema = DomRows.Schema(dom);
                PutPairs(dom, row, schema, HeadFixed);
                Put(dom, row, schema, "ivtid", plain ? "8165" : "8163");
                Put(dom, row, schema, "idiscounttaxtype", plain ? "1" : "0");
                Put(dom, row, schema, "cpbvbilltype", job.BillType);
                Put(dom, row, schema, "cpbvcode", job.Code);
                Put(dom, row, schema, "dpbvdate", job.Date);
                Put(dom, row, schema, "dgatheringdate", job.Date);
                Copy(dom, row, schema, "cptcode", job.Ctx.PurchaseTypeOr(CoRows.Col(rd, "cPTCode")));
                Copy(dom, row, schema, "cptname", CoRows.Col(rd, "cPTName"));
                Put(dom, row, schema, "cvencode", CoRows.Col(rd, "cVenCode"));
                Put(dom, row, schema, "cunitcode", CoRows.Col(rd, "cVenCode"));
                Copy(dom, row, schema, "cdepcode", CoRows.Col(rd, "cDepCode"));
                Copy(dom, row, schema, "cpersoncode", CoRows.Col(rd, "cPersonCode"));
                Put(dom, row, schema, "cexch_name", job.Ctx.HomeCurrencyOr(CoRows.Col(rd, "cExch_Name")));
                Copy(dom, row, schema, "cexch_code", CoRows.Col(rd, "cexch_code"));
                Put(dom, row, schema, "cexchrate", Rate(CoRows.Col(rd, "ExchRate")).ToString("0.##########", CultureInfo.InvariantCulture));
                Put(dom, row, schema, "ipbvtaxrate", plain ? "0" : job.Lines[0].Amt["itaxrate"]);
                Put(dom, row, schema, "cbustype", CoRows.Col(rd, "cBusType"));
                Put(dom, row, schema, "cincode", CoRows.Col(rd, "cCode"));
                Copy(dom, row, schema, "cpbvmemo", job.Memo);
                Put(dom, row, schema, "cpbvmaker", maker);
            }
            finally
            {
                ComUtil.ReleaseOne(row);
            }
        }

        static void FillBody(InvJob job, object dom)
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

        static void StampLine(InvJob job, InvLine line, int rowNo, object dom, object row, List<string> schema)
        {
            Dictionary<string, object> src = line.Src;
            PutPairs(dom, row, schema, BodyFixed);
            for (int k = 0; k < AmountNames.Length; k += 2)
            {
                Put(dom, row, schema, AmountNames[k], line.Amt[AmountNames[k + 1]]);
            }
            bool tax = job.BillType != "02" && line.Amt["btaxcost"] == "1";
            Put(dom, row, schema, "btaxcost", tax ? "True" : "False");
            Put(dom, row, schema, "cinvcode", CoRows.Col(src, "cInvCode"));
            Put(dom, row, schema, "ipbvquantity", line.Qty.ToString("0.######", CultureInfo.InvariantCulture));
            decimal rate = Num(CoRows.Col(src, "InvRate"));
            Put(dom, row, schema, "iinvexchrate", rate.ToString("0.######", CultureInfo.InvariantCulture));
            Put(dom, row, schema, "inum", rate > 0m ? StockUnits.Price(line.Qty / rate) : "0");
            Put(dom, row, schema, "igrouptype", Or(CoRows.Col(src, "GroupType"), "0"));
            Copy(dom, row, schema, "cgroupcode", CoRows.Col(src, "cGroupCode"));
            Copy(dom, row, schema, "ccomunitcode", CoRows.Col(src, "cComUnitCode"));
            Put(dom, row, schema, "bservice", Bool(CoRows.Col(src, "Service")));
            Put(dom, row, schema, "binvbatch", Bool(CoRows.Col(src, "InvBatch")));
            Put(dom, row, schema, "binvtype", Bool(CoRows.Col(src, "InvType")));
            // 蓝字必有订单行；无来源的红字入库行没有，不写这两列（PuInvRed）。
            Copy(dom, row, schema, "iposid", CoRows.Col(src, "PoLine"));
            Put(dom, row, schema, "rdsid", line.RdsId.ToString(CultureInfo.InvariantCulture));
            Copy(dom, row, schema, "cordercode", CoRows.Col(src, "cPOID"));
            Put(dom, row, schema, "ccode", CoRows.Col(job.Rd, "cCode"));
            Copy(dom, row, schema, "dindate", CoRows.Col(job.Rd, "RdDate"));
            Put(dom, row, schema, "ivouchrowno", rowNo.ToString(CultureInfo.InvariantCulture));
        }

        static void PutPairs(object dom, object row, List<string> schema, string[] pairs)
        {
            for (int k = 0; k < pairs.Length; k += 2)
            {
                Put(dom, row, schema, pairs[k], pairs[k + 1]);
            }
        }

        static string Bool(string flag)
        {
            return flag == "1" ? "True" : "False";
        }
    }
}
