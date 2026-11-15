using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 采购入库单（01）无来源：空模板 zpurrkdhead / zpurrkdtail，cSource=库存，业务类型普通采购，本币。
    // CO 的 Insert / Update 不算价税（u8-notes §4），桥按采购公式补原币、本币各列；汇率固定 1。
    internal static partial class StockDom
    {
        const string PurBusType = "普通采购";
        // 采购入库单的 VT_ID。参照采购订单生单与账套里的无来源单都是 27。
        const string PurVtId = "27";

        static readonly HashSet<string> PurHeadNames = new HashSet<string>(StringComparer.Ordinal)
        {
            "cwhcode", "crdcode", "cdepcode", "cpersoncode", "ddate", "cmemo", "cvencode", "cptcode"
        };

        static readonly HashSet<string> PurBodyNames = new HashSet<string>(StringComparer.Ordinal)
        {
            "cinvcode", "iquantity", "inum", "iunitcost", "iprice", "ioritaxcost", "itaxrate", "cbatch", "cposition", "cbmemo",
            "dmadedate", "dvdate", "imassdate", "cmassunit", "cassunit", "iinvexchrate"
        };

        internal static bool PurType(VoucherKind kind)
        {
            return kind != null && kind.StType == "01";
        }

        static bool PurField(bool head, string low)
        {
            if (head)
            {
                return PurHeadNames.Contains(low) || Span(low, "cdefine", 1, 16);
            }
            if (PurBodyNames.Contains(low) || Span(low, "cfree", 1, 10))
            {
                return true;
            }
            return Span(low, "cdefine", 22, 37);
        }

        internal static IEnumerable<string> PurMetaNames()
        {
            List<string> all = new List<string>(PurHeadNames);
            all.AddRange(PurBodyNames);
            return all;
        }

        static object PurHead(WorkContext ctx, VoucherKind kind, Dictionary<string, object> fields, string maker, string billDate)
        {
            if (fields == null)
            {
                throw new BridgeException(400, "bad_request", "缺少表头");
            }
            object conn = ctx.Conn;
            StockPurIn.HeadInfo info = StockPurIn.CheckHead(ctx, fields);
            List<string> names;
            object dom = SchemaDom(conn, TemplateSql(HeadView(kind), "m"), out names);
            try
            {
                RequireEdit(names);
                Dictionary<string, string> row = RowMap();
                Overlay(names, row, fields, kind, "head");
                PurFixed(names, row, info, maker);
                PutMissing(names, row, "crdcode", info.RdCode);
                PutMissing(names, row, "cptcode", info.PtCode);
                PutMissing(names, row, "ddate", billDate);
                // 表头税率只作缺省展示，取供应商税率；行税率另算。未覆盖：U8 是否要求表头税率。
                PutMissing(names, row, "itaxrate", info.TaxRate);
                StockCall.AppendRow(dom, row);
                object keep = dom;
                dom = null;
                return keep;
            }
            finally
            {
                ComUtil.Final(dom);
            }
        }

        static void PurFixed(List<string> names, Dictionary<string, string> row, StockPurIn.HeadInfo info, string maker)
        {
            Put(names, row, "editprop", "A");
            Put(names, row, "cvouchtype", "01");
            Put(names, row, "cbustype", PurBusType);
            Put(names, row, "csource", "库存");
            Put(names, row, "brdflag", "1");
            Put(names, row, "vt_id", PurVtId);
            Put(names, row, "iverifystate", "0");
            Put(names, row, "iswfcontrolled", "0");
            Put(names, row, "bredvouch", "0");
            Put(names, row, "bpufirst", "0");
            Put(names, row, "biafirst", "0");
            Put(names, row, "cexch_name", info.Exch);
            Put(names, row, "iexchrate", "1");
            Put(names, row, "cmaker", maker ?? "");
            Put(names, row, "id", "");
            Put(names, row, "ccode", "");
        }

        static object PurBody(object conn, VoucherKind kind, object[] lines)
        {
            RequireLines(lines);
            List<string> names;
            object dom = SchemaDom(conn, TemplateSql(ViewName("01", false), "d"), out names);
            try
            {
                RequireEdit(names);
                for (int i = 0; i < lines.Length; i++)
                {
                    PurLine(conn, dom, names, lines[i], i + 1, kind);
                }
                object keep = dom;
                dom = null;
                return keep;
            }
            finally
            {
                ComUtil.Final(dom);
            }
        }

        static void PurLine(object conn, object dom, List<string> names, object raw, int rowNo, VoucherKind kind)
        {
            Dictionary<string, object> line = raw as Dictionary<string, object>;
            if (line == null)
            {
                throw new BridgeException(400, "bad_request", "表体行不是对象");
            }
            StockPurIn.InvInfo inv = StockPurIn.CheckLine(conn, line);
            Dictionary<string, string> row = RowMap();
            Put(names, row, "editprop", "A");
            Put(names, row, "autoid", "");
            Put(names, row, "bcosting", "1");
            Put(names, row, "irowno", rowNo.ToString(CultureInfo.InvariantCulture));
            Overlay(names, row, line, kind, "lines");
            Put(names, row, "editprop", "A");
            PurDefaults(names, row, inv);
            PurPrice price = PurPriceOf(delegate(string name) { return CellOf(row, name); }, line, false);
            Dictionary<string, string> amt = PurAmounts(price);
            if (amt != null)
            {
                foreach (KeyValuePair<string, string> kv in amt)
                {
                    Put(names, row, kv.Key, kv.Value);
                }
            }
            StockUnits.Apply(conn, names, row, "iQuantity", "iNum", false);
            StockCall.AppendRow(dom, row);
        }

        static void PurDefaults(List<string> names, Dictionary<string, string> row, StockPurIn.InvInfo inv)
        {
            if (inv == null)
            {
                return;
            }
            PutMissing(names, row, "itaxrate", inv.TaxRate);
            if (inv.Quality)
            {
                PutMissing(names, row, "imassdate", inv.MassDate);
                PutMissing(names, row, "cmassunit", inv.MassUnit);
            }
        }

        // 修改：行被改动或新增后按行上现值重算税价；税率、保质期缺省取存货档案。
        internal static void PurTouch(object conn, object dom, object row, Dictionary<string, object> fields,
            List<string> schema)
        {
            if (!PurRepriced(fields))
            {
                return;
            }
            StockPurIn.InvInfo inv = StockPurIn.InvOf(conn, DomRows.Get(row, "cInvCode"));
            if (inv != null && DomRows.Get(row, "iTaxRate").Trim().Length == 0 && inv.TaxRate.Length > 0)
            {
                SetCell(dom, row, "iTaxRate", inv.TaxRate, schema);
            }
            if (inv != null && inv.Quality)
            {
                PurDomMissing(dom, row, "iMassDate", inv.MassDate, schema);
                PurDomMissing(dom, row, "cMassUnit", inv.MassUnit, schema);
            }
            PurPrice price = PurPriceOf(delegate(string name) { return DomRows.Get(row, name); }, fields, true);
            Dictionary<string, string> amt = PurAmounts(price);
            if (amt == null)
            {
                return;
            }
            foreach (KeyValuePair<string, string> kv in amt)
            {
                SetCell(dom, row, kv.Key, kv.Value, schema);
            }
        }

        // 只改备注、自定义项等时不动已有税价；新增行必带数量，总会重算。
        static bool PurRepriced(Dictionary<string, object> fields)
        {
            string[] keys = new string[] { "iquantity", "iunitcost", "iprice", "ioritaxcost", "itaxrate" };
            for (int i = 0; i < keys.Length; i++)
            {
                if (StockPurIn.Sent(fields, keys[i]))
                {
                    return true;
                }
            }
            return false;
        }

        static void PurDomMissing(object dom, object row, string name, string value, List<string> schema)
        {
            if (value == null || value.Length == 0 || DomRows.Get(row, name).Trim().Length > 0)
            {
                return;
            }
            SetCell(dom, row, name, value, schema);
        }

        // 单价口径：本次送了含税单价用含税；送了无税单价用无税；只送了金额按金额反算；
        // 都没送时（修改改数量或税率）沿用行上的 btaxcost 口径。没有单价则不算。
        static PurPrice PurPriceOf(Func<string, string> get, Dictionary<string, object> sent, bool keep)
        {
            PurPrice p = new PurPrice();
            if (!StockUnits.Dec(get("iQuantity"), out p.Qty) || p.Qty == 0m)
            {
                return null;
            }
            bool hasRate = StockUnits.Dec(get("iTaxRate"), out p.RatePct);
            if (StockPurIn.Given(sent, "ioritaxcost"))
            {
                p.Mode = 1;
            }
            else if (StockPurIn.Given(sent, "iunitcost"))
            {
                p.Mode = 2;
            }
            else if (StockPurIn.Given(sent, "iprice"))
            {
                p.Mode = 3;
            }
            else if (keep)
            {
                p.Mode = Values.Flag(get("bTaxCost")) ? 1 : 2;
            }
            if (!PurPriceValue(get, p))
            {
                return null;
            }
            if (!hasRate)
            {
                throw new BridgeException(400, "bad_request", "存货没有缺省税率，请填写 itaxrate");
            }
            return p;
        }

        static bool PurPriceValue(Func<string, string> get, PurPrice p)
        {
            string key = p.Mode == 1 ? "iOriTaxCost" : p.Mode == 2 ? "iUnitCost" : p.Mode == 3 ? "iPrice" : null;
            if (key == null)
            {
                return false;
            }
            decimal value;
            if (!StockUnits.Dec(get(key), out value))
            {
                return false;
            }
            p.Value = value;
            return true;
        }

        // 采购公式（u8-notes §4 采购）：单价 6 位、金额 2 位，AwayFromZero；本币 = 原币（汇率 1）。
        static Dictionary<string, string> PurAmounts(PurPrice p)
        {
            if (p == null)
            {
                return null;
            }
            decimal r = p.RatePct / 100m;
            decimal iunit;
            decimal itaxp;
            decimal imoney;
            decimal isum;
            if (p.Mode == 1)
            {
                itaxp = p.Value;
                isum = StockUnits.Round2(p.Qty * itaxp);
                imoney = StockUnits.Round2(isum / (1m + r));
                iunit = StockUnits.Round6(itaxp / (1m + r));
            }
            else
            {
                iunit = p.Mode == 3 ? StockUnits.Round6(p.Value / p.Qty) : p.Value;
                imoney = p.Mode == 3 ? StockUnits.Round2(p.Value) : StockUnits.Round2(p.Qty * iunit);
                isum = imoney + StockUnits.Round2(imoney * r);
                itaxp = StockUnits.Round6(iunit * (1m + r));
            }
            return PurCells(p, iunit, itaxp, imoney, isum);
        }

        static Dictionary<string, string> PurCells(PurPrice p, decimal iunit, decimal itaxp, decimal imoney, decimal isum)
        {
            decimal itax = isum - imoney;
            Dictionary<string, string> amt = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            amt["iTaxRate"] = p.RatePct.ToString("0.####", CultureInfo.InvariantCulture);
            amt["bTaxCost"] = p.Mode == 1 ? "1" : "0";
            amt["iOriTaxCost"] = StockUnits.Price(itaxp);
            amt["iOriCost"] = StockUnits.Price(iunit);
            amt["iOriMoney"] = StockUnits.Money(imoney);
            amt["iOriTaxPrice"] = StockUnits.Money(itax);
            amt["ioriSum"] = StockUnits.Money(isum);
            amt["iUnitCost"] = StockUnits.Price(iunit);
            amt["iPrice"] = StockUnits.Money(imoney);
            amt["iTaxPrice"] = StockUnits.Money(itax);
            amt["iSum"] = StockUnits.Money(isum);
            return amt;
        }

        sealed class PurPrice
        {
            public decimal Qty;
            public decimal RatePct;
            public decimal Value;
            // 1 含税单价，2 无税单价，3 无税金额。
            public int Mode;
        }
    }
}
