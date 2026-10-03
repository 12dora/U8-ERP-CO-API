using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 采购入库参照采购订单行。
    internal static partial class StockGen
    {

        static void StampHead(object dom, object row, Dictionary<string, object> head,
            Dictionary<string, object> po, WorkContext ctx)
        {
            string date = Text(head, "ddate");
            if (date.Length == 0)
            {
                date = ctx.Item.Date == null ? "" : ctx.Item.Date.Trim();
            }
            string maker = ctx.Session.OperatorName == null ? "" : ctx.Session.OperatorName.Trim();
            List<string> schema = DomRows.Schema(dom);
            Put(dom, row, schema, "editprop", "A");
            Put(dom, row, schema, "cwhcode", Text(head, "cwhcode"));
            Put(dom, row, schema, "crdcode", StockPurIn.GenRd(ctx.Conn, head, CoRows.Col(po, "cPTCode")));
            Copy(dom, row, schema, "cdepcode", Or(Text(head, "cdepcode"), CoRows.Col(po, "cDepCode")));
            Copy(dom, row, schema, "cpersoncode", Or(Text(head, "cpersoncode"), CoRows.Col(po, "cPersonCode")));
            Copy(dom, row, schema, "cvencode", CoRows.Col(po, "cVenCode"));
            Copy(dom, row, schema, "cptcode", CoRows.Col(po, "cPTCode"));
            Copy(dom, row, schema, "cbustype", CoRows.Col(po, "cBusType"));
            Copy(dom, row, schema, "cexch_name", CoRows.Col(po, "cexch_name"));
            Copy(dom, row, schema, "iexchrate", Or(CoRows.Col(po, "nflat"), "1"));
            Copy(dom, row, schema, "itaxrate", CoRows.Col(po, "iTaxRate"));
            Put(dom, row, schema, "csource", "采购订单");
            Put(dom, row, schema, "cordercode", CoRows.Col(po, "cPOID"));
            Put(dom, row, schema, "vt_id", "27");
            Put(dom, row, schema, "brdflag", "1");
            Put(dom, row, schema, "cvouchtype", "01");
            Put(dom, row, schema, "ddate", date);
            Put(dom, row, schema, "cmaker", maker);
            Copy(dom, row, schema, "cmemo", Text(head, "cmemo"));
            CopyDefines(dom, row, head, schema);
        }

        static void FillBody(object conn, object dom, Dictionary<string, object> po, List<InLine> want)
        {
            decimal exch = Rate(CoRows.Col(po, "nflat"));
            string poid = CoRows.Col(po, "cPOID");
            string headRate = CoRows.Col(po, "iTaxRate");
            List<string> schema = DomRows.Schema(dom);
            for (int i = 0; i < want.Count; i++)
            {
                object row = DomRows.AddRow(dom);
                try
                {
                    LineStamp stamp = LineOf(conn, dom, row, want[i], poid, headRate);
                    stamp.Exch = exch;
                    stamp.RowNo = i + 1;
                    stamp.Schema = schema;
                    StampLine(stamp);
                }
                finally
                {
                    ComUtil.ReleaseOne(row);
                }
            }
        }

        static void StampLine(LineStamp stamp)
        {
            object conn = stamp.Conn;
            object dom = stamp.Dom;
            object row = stamp.Row;
            InLine line = stamp.Line;
            string poid = stamp.PoId;
            string headRate = stamp.HeadRate;
            decimal exch = stamp.Exch;
            int rowNo = stamp.RowNo;
            Dictionary<string, string> amt = Amounts(line.Src, line.Qty, exch, headRate);
            List<string> schema = stamp.Schema;
            Put(dom, row, schema, "editprop", "A");
            Put(dom, row, schema, "cinvcode", CoRows.Col(line.Src, "cInvCode"));
            Put(dom, row, schema, "iquantity", line.Qty.ToString("0.######", CultureInfo.InvariantCulture));
            Put(dom, row, schema, "iposid", line.LineId.ToString(CultureInfo.InvariantCulture));
            Put(dom, row, schema, "cpoid", poid);
            Put(dom, row, schema, "irowno", rowNo.ToString(CultureInfo.InvariantCulture));
            Put(dom, row, schema, "itaxrate", amt["itaxrate"]);
            Put(dom, row, schema, "btaxcost", amt["btaxcost"]);
            Put(dom, row, schema, "ioritaxcost", amt["ioritaxcost"]);
            Put(dom, row, schema, "ioricost", amt["ioricost"]);
            Put(dom, row, schema, "iorimoney", amt["iorimoney"]);
            Put(dom, row, schema, "ioritaxprice", amt["ioritaxprice"]);
            Put(dom, row, schema, "iorisum", amt["iorisum"]);
            Put(dom, row, schema, "iunitcost", amt["iunitcost"]);
            Put(dom, row, schema, "iprice", amt["iprice"]);
            Put(dom, row, schema, "itaxprice", amt["itaxprice"]);
            Put(dom, row, schema, "isum", amt["isum"]);
            Copy(dom, row, schema, "cbatch", line.Batch);
            Copy(dom, row, schema, "cposition", line.Pos);
            Copy(dom, row, schema, "cbmemo", line.Memo);
            ApplyQty(conn, dom, row, schema);
        }

        // 用采购单价公式按本次数量重算，不把订单金额再乘一遍。
        static Dictionary<string, string> Amounts(Dictionary<string, object> src, decimal qty, decimal exch, string headRate)
        {
            bool tax = CoRows.FlagOf(src, "bTaxCost");
            decimal ratePct = Num(CoRows.Col(src, "iPerTaxRate"));
            if (CoRows.Col(src, "iPerTaxRate").Length == 0)
            {
                ratePct = Num(headRate);
            }
            decimal r = ratePct / 100m;
            if (r == -1m)
            {
                throw new BridgeException(400, "bad_request", "税率无效");
            }
            decimal taxPrice = Num(CoRows.Col(src, "iTaxPrice"));
            decimal unitPrice = Num(CoRows.Col(src, "iUnitPrice"));
            decimal iunit;
            decimal itaxp;
            decimal imoney;
            decimal itax;
            decimal isum;
            if (tax)
            {
                itaxp = taxPrice;
                isum = StockUnits.Round2(qty * itaxp);
                imoney = StockUnits.Round2(isum / (1m + r));
                itax = isum - imoney;
                iunit = StockUnits.Round6(itaxp / (1m + r));
            }
            else
            {
                iunit = unitPrice;
                imoney = StockUnits.Round2(qty * iunit);
                itax = StockUnits.Round2(imoney * r);
                isum = imoney + itax;
                itaxp = StockUnits.Round6(iunit * (1m + r));
            }
            Dictionary<string, string> amt = new Dictionary<string, string>();
            amt["itaxrate"] = ratePct.ToString("0.####", CultureInfo.InvariantCulture);
            amt["btaxcost"] = tax ? "1" : "0";
            amt["ioritaxcost"] = StockUnits.Price(itaxp);
            amt["ioricost"] = StockUnits.Price(iunit);
            amt["iorimoney"] = StockUnits.Money(imoney);
            amt["ioritaxprice"] = StockUnits.Money(itax);
            amt["iorisum"] = StockUnits.Money(isum);
            amt["iunitcost"] = StockUnits.Price(iunit * exch);
            amt["iprice"] = StockUnits.Money(imoney * exch);
            amt["itaxprice"] = StockUnits.Money(itax * exch);
            amt["isum"] = StockUnits.Money(isum * exch);
            return amt;
        }

        static Dictionary<string, object> LoadPo(object conn, int id)
        {
            Dictionary<string, object> po = Rows.One(conn, PoSql, new object[] { id });
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
            return po;
        }

        static List<InLine> LoadLines(object conn, int poid, object[] lines)
        {
            if (lines == null || lines.Length < 1 || lines.Length > 200)
            {
                throw BridgeException.BadField("lines", "表体须为 1 到 200 行");
            }
            List<InLine> list = new List<InLine>();
            for (int i = 0; i < lines.Length; i++)
            {
                list.Add(OneLine(conn, poid, lines[i]));
            }
            return list;
        }

        static InLine OneLine(object conn, int poid, object raw)
        {
            Dictionary<string, object> line = raw as Dictionary<string, object>;
            if (line == null)
            {
                throw BridgeException.BadField("lines", "表体行不是对象");
            }
            CheckKeys(line, false);
            int id = AsId(Raw(line, "source_line_id"));
            if (id <= 0)
            {
                throw BridgeException.BadField("lines.source_line_id", "明细行不存在");
            }
            decimal qty = QtyOf(Raw(line, "quantity"));
            Dictionary<string, object> src = Rows.One(conn, LineSql, new object[] { id, poid });
            if (src == null)
            {
                throw BridgeException.BadField("lines.source_line_id", "明细行不存在");
            }
            if (CoRows.Col(src, "cbCloser").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            decimal left = Num(CoRows.Col(src, "iQuantity")) - Num(CoRows.Col(src, "iReceivedQTY"));
            if (qty > left)
            {
                throw new BridgeException(409, "state_mismatch", "超过可生单数量");
            }
            RefuseCheck(conn, CoRows.Col(src, "cInvCode"));
            InLine item = new InLine();
            item.LineId = id;
            item.Qty = qty;
            item.Batch = Text(line, "cbatch");
            item.Memo = Text(line, "cbmemo");
            item.Pos = Text(line, "cposition");
            item.Src = src;
            return item;
        }

        static void RefuseCheck(object conn, string inv)
        {
            if (inv.Length == 0)
            {
                return;
            }
            Dictionary<string, object> row = Rows.One(conn, CheckSql, new object[] { inv });
            if (row != null && CoRows.FlagOf(row, "bPropertyCheck"))
            {
                throw new BridgeException(409, "state_mismatch", "该存货需来料检验，不能直接入库");
            }
        }

        static void CopyDefines(object dom, object row, Dictionary<string, object> head, List<string> schema)
        {
            for (int n = 1; n <= 16; n++)
            {
                string key = "cdefine" + n.ToString(CultureInfo.InvariantCulture);
                Copy(dom, row, schema, key, Text(head, key));
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
            StockDom.SetCell(dom, row, name, value, schema);
        }

        static void ApplyQty(object conn, object dom, object row, List<string> schema)
        {
            StockUnits.UnitJob job = new StockUnits.UnitJob();
            job.QtyName = "iQuantity";
            job.NumName = "iNum";
            job.Force = true;
            job.Schema = schema;
            StockUnits.ApplyDom(conn, dom, row, job);
        }

        static void CheckKeys(Dictionary<string, object> map, bool head)
        {
            foreach (KeyValuePair<string, object> kv in map)
            {
                string low = kv.Key == null ? "" : kv.Key.ToLowerInvariant();
                if (head ? HeadKey(low) : LineKey(low))
                {
                    continue;
                }
                throw BridgeException.BadField(FieldPath.Join(FieldPath.Side(head), kv.Key), "不能设置字段 " + kv.Key);
            }
        }

        static bool HeadKey(string low)
        {
            if (low == "cwhcode" || low == "crdcode" || low == "ddate" || low == "cmemo"
                || low == "cdepcode" || low == "cpersoncode")
            {
                return true;
            }
            return Span(low, "cdefine", 1, 16);
        }

        // 参照采购订单、采购退货单的采购入库行（StockGenRed 共用）；cposition 生单前按仓库核对（CheckGenPositions）。
        static bool LineKey(string low)
        {
            return low == "source_line_id" || low == "quantity" || low == "cwhcode" || low == "cbatch" || low == "cbmemo"
                || low == "cposition";
        }

        static bool Span(string low, string prefix, int from, int to)
        {
            if (!low.StartsWith(prefix, StringComparison.Ordinal))
            {
                return false;
            }
            string tail = low.Substring(prefix.Length);
            int n;
            if (tail.Length == 0 || !int.TryParse(tail, NumberStyles.None, CultureInfo.InvariantCulture, out n))
            {
                return false;
            }
            if (n < from || n > to)
            {
                return false;
            }
            return tail == n.ToString(CultureInfo.InvariantCulture);
        }

        static decimal QtyOf(object value)
        {
            decimal qty;
            if (!StockUnits.Dec(Values.Text(value), out qty) || qty <= 0m)
            {
                throw BridgeException.BadField("lines.quantity", "数量必须大于 0");
            }
            return qty;
        }

        static decimal Rate(string text)
        {
            decimal rate = Num(text);
            if (rate <= 0m)
            {
                return 1m;
            }
            return rate;
        }

        static decimal Num(string text)
        {
            decimal value;
            if (!StockUnits.Dec(text, out value))
            {
                return 0m;
            }
            return value;
        }

        static int AsId(object value)
        {
            int id = CoRows.AsId(value);
            if (id > 0)
            {
                return id;
            }
            decimal num;
            if (StockUnits.Dec(Values.Text(value), out num) && num == decimal.Truncate(num) && num > 0m && num <= int.MaxValue)
            {
                return (int)num;
            }
            return 0;
        }

        static object Raw(Dictionary<string, object> map, string name)
        {
            foreach (KeyValuePair<string, object> kv in map)
            {
                if (string.Equals(kv.Key, name, StringComparison.OrdinalIgnoreCase))
                {
                    return kv.Value;
                }
            }
            return null;
        }

        static string Text(Dictionary<string, object> map, string name)
        {
            return Values.Text(Raw(map, name)).Trim();
        }

        static string Or(string value, string fallback)
        {
            if (value != null && value.Length > 0)
            {
                return value;
            }
            return fallback ?? "";
        }

        static string Unknown(string code)
        {
            return Unknown(0, code);
        }

        static string Unknown(int id, string code)
        {
            string msg = "已保存但未能确定单据标识";
            string no = code == null ? "" : code.Trim();
            if (no.Length > 0)
            {
                msg = msg + "，单号 " + no;
            }
            if (id > 0)
            {
                msg = msg + "，标识 " + id.ToString(CultureInfo.InvariantCulture);
            }
            return msg;
        }

        static LineStamp LineOf(object conn, object dom, object row, InLine line, string poid, string headRate)
        {
            LineStamp stamp = new LineStamp();
            stamp.Conn = conn;
            stamp.Dom = dom;
            stamp.Row = row;
            stamp.Line = line;
            stamp.PoId = poid;
            stamp.HeadRate = headRate;
            return stamp;
        }

        sealed class InLine
        {
            public int LineId;
            public decimal Qty;
            public string Batch;
            public string Memo;
            public string Pos;
            public Dictionary<string, object> Src;
        }

        sealed class LineStamp
        {
            public object Conn;
            public object Dom;
            public object Row;
            public InLine Line;
            public string PoId;
            public string HeadRate;
            public decimal Exch;
            public int RowNo;
            public List<string> Schema;
        }

    }
}
