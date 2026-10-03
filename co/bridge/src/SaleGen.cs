using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 发货单 ← 销售订单，销售发票 ← 发货单。参照视图是 U8 客户端生单用的。
    internal static partial class SaleGen
    {
        const string HeadSql = "select cSOCode, cVerifier, cCloser from SO_SOMain where ID=?";
        const string BodySql = "select iSOsID, iQuantity, iFHQuantity, cSCloser, iRowNo from SO_SODetails where ID=?";
        const string RefHead = "select * from sale_RefSOVouch_T where id=?";
        const string RefBody = "select * from sale_RefSOVouch_B where isosid=?";
        const string IdSql = "select top 1 DLID from DispatchList where cDLCode=? order by DLID desc";
        const string HeadSkip = "id,dlid,cdlcode,ufts,cmaker,cverifier,dverifydate,ivtid,cvouchtype,ddate,"
            + "editprop,cmemo,ccloser,iswfcontrolled,iverifystate,corufts,csocode";
        const string BodySkip = "autoid,dlid,idlsid,id,editprop,ufts,corufts,cbsysbarcode,ifhquantity,ifhnum,"
            + "ifhmoney,ikpquantity,ikpnum,ikpmoney,foutquantity,cscloser";
        const string HeadKeys = "ddate,cmemo,cwhcode,cdepcode,cpersoncode,cshipaddress";
        const string LineKeys = "cwhcode,cbatch,cmemo";
        const string InvHeadKeys = "cvouchtype,ddate,cmemo";
        const string InvLineKeys = "cmemo";
        const string DispHeadSql = "select cDLCode, cSOCode, cVerifier, cVouchType, bReturnFlag, cCloser, bFirst "
            + "from DispatchList where DLID=?";
        // 可开票 = iQuantity - fretqtywkp（未开票退货）- iSettleQuantity，同 U8 视图 sale_DispToSaleVouchJS_B 的 iwkpquantity。
        const string DispLineSql = "select iDLsID, iQuantity, cSCloser, "
            + "isnull(iSettleQuantity,0) + isnull(fretqtywkp,0) as Billed from DispatchLists where DLID=?";
        const string InvRefHead = "select * from Sales_FHD_T where dlid=?";
        const string InvRefBody = "select * from Sales_FHD_W where idlsid=?";
        const string InvIdSql = "select top 1 SBVID from SaleBillVouch where cSBVCode=? order by SBVID desc";
        const string InvHeadSkip = "sbvid,csbvcode,dlid,cdlcode,ufts,cmaker,cverifier,cchecker,dverifydate,ivtid,"
            + "cvouchtype,ddate,editprop,cmemo,ccloser,iswfcontrolled,iverifystate,corufts,csocode,bfirst,cinvalider";
        const string InvBodySkip = "autoid,sbvid,dlid,editprop,ufts,corufts,cbsysbarcode,isettlequantity,isettlenum,"
            + "foutquantity,foutnum,fsalecost,fsaleprice,ikpquantity,ikpnum,ikpmoney,cbdlcode";
        static readonly string[] Amounts = new string[] {
            "imoney", "itax", "isum", "inatmoney", "inattax", "inatsum", "idiscount", "inatdiscount"
        };

        public static ApiResult Dispatch(WorkContext ctx, VoucherKind kind, int sourceId, Dictionary<string, object> head, object[] lines)
        {
            if (kind == null || kind.Name != "dispatch")
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持生单");
            }
            if (head == null)
            {
                head = new Dictionary<string, object>();
            }
            if (lines == null || lines.Length < 1 || lines.Length > 200)
            {
                throw BridgeException.BadField("lines", "表体行数必须在 1 到 200 之间");
            }
            CheckKeys(head, true, HeadKeys, false);
            string soCode;
            ShipLine[] plan = Plan(ctx.Conn, sourceId, lines, out soCode);
            return Build(ctx, kind, sourceId, head, soCode, plan);
        }

        public static ApiResult Invoice(WorkContext ctx, VoucherKind kind, int sourceId, Dictionary<string, object> head, object[] lines)
        {
            if (kind == null || kind.Name != "sale_invoice")
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持生单");
            }
            if (head == null)
            {
                head = new Dictionary<string, object>();
            }
            if (lines == null || lines.Length < 1 || lines.Length > 200)
            {
                throw BridgeException.BadField("lines", "表体行数必须在 1 到 200 之间");
            }
            CheckKeys(head, true, InvHeadKeys, false);
            InvHead want = Want(head);
            ShipLine[] plan = PlanInvoice(ctx.Conn, sourceId, lines, want);
            return BuildInvoice(ctx, kind, sourceId, head, want, plan);
        }

        static ApiResult Build(WorkContext ctx, VoucherKind kind, int sourceId, Dictionary<string, object> head,
            string soCode, ShipLine[] plan)
        {
            object srcHead = null;
            object sys = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                srcHead = LoadRef(ctx.Conn, RefHead, sourceId, "参照视图没有该销售订单");
                SaSession.OpenSa(ctx.Conn, ctx.Session.Login, SaSession.SaVt(kind), out sys, out co);
                string card = kind.SaCard == null || kind.SaCard.Length == 0 ? "01" : kind.SaCard;
                SoDom.Templates(co, ctx.Conn, card, ctx.Item, doms);
                soCode = FillHead(ctx, doms, HeadFillOf(sys, srcHead, head, soCode, card));
                FillLines(ctx, co, doms, head, soCode, plan);
                string code;
                int id = SoSave.SaveDispatch(ctx.Conn, co, doms, ctx.Item, out code);
                return AfterSave(ctx, kind, id, code, Echo(IdSql, "DLID", "sale_order", sourceId));
            }
            finally
            {
                ComUtil.Final(srcHead);
                SaSession.Release(sys, co, doms);
            }
        }

        static string FillHead(WorkContext ctx, object[] doms, HeadFill fill)
        {
            DropExtra(doms[0]);
            object row = OneRow(doms[0]);
            List<object> srcRows = DomRows.RowsOf(fill.SrcHead);
            if (srcRows.Count < 1)
            {
                throw new BridgeException(409, "state_mismatch", "参照视图没有该销售订单");
            }
            string code = fill.SoCode;
            if (code.Length == 0)
            {
                code = DomRows.Get(srcRows[0], "csocode").Trim();
            }
            DomRows.CopyInto(doms[0], row, srcRows[0], HeadSkip);
            // 表头仓库只作表体缺省，不写进发货单表头。
            ApplyFields(doms[0], row, fill.Head, true, true);
            if (TextOf(fill.Head, "ddate").Length == 0)
            {
                DomRows.Set(doms[0], row, "ddate", LoginDate(ctx));
            }
            DomRows.Set(doms[0], row, "cvouchtype", "05");
            if (code.Length > 0)
            {
                DomRows.Set(doms[0], row, "csocode", code);
            }
            DomRows.Set(doms[0], row, "breturnflag", "0");
            DomRows.Set(doms[0], row, "editprop", "A");
            DomRows.Set(doms[0], row, "cMaker", User(ctx));
            SoSave.Stamp(ctx, fill.Sys, doms[0], fill.Card);
            return code;
        }

        static void FillLines(WorkContext ctx, object co, object[] doms, Dictionary<string, object> head, string soCode, ShipLine[] plan)
        {
            DropAll(doms[1]);
            LineFill fill = LineFillOf(co, head, soCode);
            for (int i = 0; i < plan.Length; i++)
            {
                FillLine(ctx, doms, fill, plan[i], i);
            }
        }

        static void FillLine(WorkContext ctx, object[] doms, LineFill fill, ShipLine line, int index)
        {
            object src = null;
            try
            {
                src = LoadRef(ctx.Conn, RefBody, line.LineId, "参照视图没有该销售订单行");
                List<object> srcRows = DomRows.RowsOf(src);
                if (srcRows.Count < 1)
                {
                    throw new BridgeException(409, "state_mismatch", "参照视图没有该销售订单行");
                }
                string orderNo = DomRows.Get(srcRows[0], "irowno").Trim();
                if (orderNo.Length == 0)
                {
                    orderNo = line.OrderRow;
                }
                string code = fill.SoCode;
                if (code.Length == 0)
                {
                    code = DomRows.Get(srcRows[0], "csocode").Trim();
                }
                object dst = DomRows.AddRow(doms[1]);
                int at = DomRows.RowsOf(doms[1]).Count - 1;
                DomRows.CopyInto(doms[1], dst, srcRows[0], BodySkip);
                Scale(doms[1], dst, line.Qty);
                string msg = SaleCalc.Check(fill.Co, doms[0], doms[1], dst, "iquantity", false);
                if (msg.Length > 0)
                {
                    CoRows.Note(ctx.Item, "BodyCheck " + msg);
                }
                object row = At(doms[1], at);
                ApplyFields(doms[1], row, line.Fields, false);
                string wh = Warehouse(line.Fields, fill.Head, row);
                if (wh.Length == 0)
                {
                    throw BridgeException.BadField("lines.cwhcode", "必须指定仓库");
                }
                DomRows.Set(doms[1], row, "cwhcode", wh);
                DomRows.Set(doms[1], row, "isosid", line.LineId.ToString(CultureInfo.InvariantCulture));
                if (code.Length > 0)
                {
                    DomRows.Set(doms[1], row, "csocode", code);
                    DomRows.Set(doms[1], row, "cordercode", code);
                }
                if (orderNo.Length > 0)
                {
                    DomRows.Set(doms[1], row, "iorderrowno", orderNo);
                }
                DomRows.Set(doms[1], row, "irowno", (index + 1).ToString(CultureInfo.InvariantCulture));
                DomRows.Set(doms[1], row, "editprop", "A");
            }
            finally
            {
                ComUtil.Final(src);
            }
        }

        static void Scale(object dom, object row, decimal qty)
        {
            decimal source = Dec(DomRows.Get(row, "iquantity"));
            DomRows.Set(dom, row, "iquantity", QtyText(qty));
            if (source > 0m)
            {
                decimal ratio = qty / source;
                for (int i = 0; i < Amounts.Length; i++)
                {
                    decimal amount;
                    if (!TryDec(DomRows.Get(row, Amounts[i]), out amount))
                    {
                        continue;
                    }
                    decimal scaled = Math.Round(amount * ratio, 2, MidpointRounding.AwayFromZero);
                    DomRows.Set(dom, row, Amounts[i], scaled.ToString("0.00", CultureInfo.InvariantCulture));
                }
            }
            decimal rate;
            if (TryDec(DomRows.Get(row, "iinvexchrate"), out rate) && rate > 0m)
            {
                decimal num = Math.Round(qty / rate, 6, MidpointRounding.AwayFromZero);
                DomRows.Set(dom, row, "inum", num.ToString("0.######", CultureInfo.InvariantCulture));
            }
        }

        static ShipLine[] Plan(object conn, int sourceId, object[] lines, out string soCode)
        {
            soCode = "";
            Dictionary<string, object> order = Rows.One(conn, HeadSql, new object[] { sourceId });
            if (order == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (CoRows.Col(order, "cVerifier").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            if (CoRows.Col(order, "cCloser").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            soCode = CoRows.Col(order, "cSOCode");
            Dictionary<int, Dictionary<string, object>> body = IndexLines(conn, sourceId);
            ShipLine[] plan = new ShipLine[lines.Length];
            Dictionary<int, bool> seen = new Dictionary<int, bool>();
            for (int i = 0; i < lines.Length; i++)
            {
                plan[i] = ReadLine(lines[i], body, seen);
            }
            return plan;
        }

        static ShipLine ReadLine(object raw, Dictionary<int, Dictionary<string, object>> body, Dictionary<int, bool> seen)
        {
            Dictionary<string, object> map = raw as Dictionary<string, object>;
            if (map == null)
            {
                throw BridgeException.BadField("lines", "表体行必须是对象");
            }
            CheckKeys(map, false, LineKeys, true);
            int lineId = CoRows.AsId(RawKey(map, "source_line_id"));
            Dictionary<string, object> src;
            if (lineId <= 0 || !body.TryGetValue(lineId, out src))
            {
                throw BridgeException.BadField("lines.source_line_id", "明细行不存在");
            }
            if (seen.ContainsKey(lineId))
            {
                throw BridgeException.BadField("lines.source_line_id", "明细行重复");
            }
            seen[lineId] = true;
            if (CoRows.Col(src, "cSCloser").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            decimal qty = QtyOf(RawKey(map, "quantity"));
            decimal left = Remain(src, "iFHQuantity");
            if (qty > left)
            {
                throw new BridgeException(409, "state_mismatch", "超过可生单数量");
            }
            ShipLine line = new ShipLine();
            line.LineId = lineId;
            line.Qty = qty;
            line.OrderRow = CoRows.Col(src, "iRowNo");
            line.Fields = map;
            return line;
        }

        static Dictionary<int, Dictionary<string, object>> IndexLines(object conn, int sourceId)
        {
            return IndexBy(conn, BodySql, sourceId, "iSOsID");
        }

        static Dictionary<int, Dictionary<string, object>> IndexBy(object conn, string sql, int sourceId, string column)
        {
            Dictionary<int, Dictionary<string, object>> body = new Dictionary<int, Dictionary<string, object>>();
            List<Dictionary<string, object>> rows = Rows.Query(conn, sql, new object[] { sourceId }, 5000);
            for (int i = 0; i < rows.Count; i++)
            {
                int lineId = CoRows.AsId(CoRows.Col(rows[i], column));
                if (lineId > 0 && !body.ContainsKey(lineId))
                {
                    body[lineId] = rows[i];
                }
            }
            return body;
        }

        static decimal Remain(Dictionary<string, object> row, string used)
        {
            decimal left = Dec(CoRows.Col(row, "iQuantity")) - Dec(CoRows.Col(row, used));
            if (left < 0m)
            {
                return 0m;
            }
            return left;
        }

        static void CheckKeys(Dictionary<string, object> map, bool head, string listed, bool free)
        {
            foreach (KeyValuePair<string, object> pair in map)
            {
                if (!head && Meta(pair.Key))
                {
                    continue;
                }
                if (!Allowed(pair.Key, head, listed, free))
                {
                    throw BridgeException.BadField(FieldPath.Join(FieldPath.Side(head), pair.Key), "不能设置字段 " + pair.Key)
                        .WithHint(FieldPath.WritableHint);
                }
            }
        }

        static void ApplyFields(object dom, object row, Dictionary<string, object> fields, bool head)
        {
            ApplyFields(dom, row, fields, head, false);
        }

        // skipWh：发货单表头的 cwhcode 只是行仓库的缺省值。表头字段必须在行集 schema 里，否则不能写上去。
        static void ApplyFields(object dom, object row, Dictionary<string, object> fields, bool head, bool skipWh)
        {
            if (fields == null)
            {
                return;
            }
            List<string> schema = null;
            if (head)
            {
                schema = DomRows.Schema(dom);
            }
            foreach (KeyValuePair<string, object> pair in fields)
            {
                if (SkipField(pair.Key, head, skipWh))
                {
                    continue;
                }
                if (head && !InSchema(schema, pair.Key))
                {
                    throw BridgeException.BadField(FieldPath.Join("head", pair.Key), "不能设置字段 " + pair.Key);
                }
                string text = Cell(pair.Value);
                if (text == null)
                {
                    continue;
                }
                DomRows.Set(dom, row, pair.Key, text);
            }
        }

        static bool SkipField(string key, bool head, bool skipWh)
        {
            if (!head && Meta(key))
            {
                return true;
            }
            return head && skipWh && Same(key, "cwhcode");
        }

        static bool InSchema(List<string> schema, string key)
        {
            if (schema == null || key == null)
            {
                return false;
            }
            for (int i = 0; i < schema.Count; i++)
            {
                if (string.Equals(schema[i], key, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }

        static bool Allowed(string key, bool head, string listed, bool free)
        {
            string lower = key == null ? "" : key.ToLowerInvariant();
            if (head)
            {
                return Listed(lower, listed) || Span(lower, "cdefine", 1, 16);
            }
            if (Listed(lower, listed) || (free && Span(lower, "cfree", 1, 10)))
            {
                return true;
            }
            return Span(lower, "cdefine", 22, 37);
        }

        static bool Listed(string key, string csv)
        {
            return ("," + csv + ",").IndexOf("," + key + ",", StringComparison.Ordinal) >= 0;
        }

        static bool Span(string key, string prefix, int from, int to)
        {
            if (key.Length <= prefix.Length || !key.StartsWith(prefix, StringComparison.Ordinal))
            {
                return false;
            }
            int n;
            if (!int.TryParse(key.Substring(prefix.Length), NumberStyles.None, CultureInfo.InvariantCulture, out n))
            {
                return false;
            }
            return n >= from && n <= to;
        }

        static bool Meta(string key)
        {
            return Same(key, "source_line_id") || Same(key, "quantity");
        }

        static string Warehouse(Dictionary<string, object> line, Dictionary<string, object> head, object row)
        {
            string wh = TextOf(line, "cwhcode");
            if (wh.Length == 0)
            {
                wh = TextOf(head, "cwhcode");
            }
            if (wh.Length == 0)
            {
                wh = DomRows.Get(row, "cwhcode").Trim();
            }
            return wh;
        }
    }
}
