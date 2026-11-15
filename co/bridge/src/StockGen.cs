using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 采购入库参照采购订单行手工 Insert（行上可带货位 cposition，生单前 CheckGenPositions）；销售出库参照发货单见 StockGenSalePart。
    internal static partial class StockGen
    {
        const string DispSql = "select cVouchType, bReturnFlag, cVerifier, cCloser from DispatchList where DLID=?";
        const string RemainSql = "select convert(varchar(40), sum(isnull(iQuantity,0)-isnull(fOutQuantity,0))) from DispatchLists where DLID=?";
        const string PoSql = "select cPOID, cVerifier, cCloser, cVenCode, cDepCode, cPersonCode, cPTCode, cBusType, cexch_name, nflat, iTaxRate from PO_Pomain where POID=?";
        const string LineSql = "select ID, cInvCode, iQuantity, iReceivedQTY, iUnitPrice, iTaxPrice, iPerTaxRate, bTaxCost, cbCloser from PO_Podetails where ID=? and POID=?";
        const string CheckSql = "select bPropertyCheck from Inventory where cInvCode=?";

        public static ApiResult PurchaseIn(WorkContext ctx, VoucherKind kind, int sourceId,
            Dictionary<string, object> head, object[] lines)
        {
            if (head == null)
            {
                head = new Dictionary<string, object>();
            }
            CheckKeys(head, true);
            if (Text(head, "cwhcode").Length == 0)
            {
                throw new BridgeException(400, "bad_request", "必须指定仓库");
            }
            Dictionary<string, object> po = LoadPo(ctx.Conn, sourceId);
            List<InLine> want = LoadLines(ctx.Conn, sourceId, lines);
            List<string> codes = new List<string>();
            for (int i = 0; i < want.Count; i++)
            {
                codes.Add(want[i].Pos);
            }
            CheckGenPositions(ctx.Conn, Text(head, "cwhcode"), codes);
            return InsertIn(ctx, kind, sourceId, head, po, want);
        }

        static ApiResult InsertIn(WorkContext ctx, VoucherKind kind, int sourceId,
            Dictionary<string, object> head, Dictionary<string, object> po, List<InLine> want)
        {
            object co = null;
            object domH = null;
            object domB = null;
            object pos = null;
            object msg = null;
            try
            {
                domH = Blank(ctx.Conn, true);
                domB = Blank(ctx.Conn, false);
                object row = DomRows.AddRow(domH);
                try
                {
                    StampHead(domH, row, head, po, ctx);
                }
                finally
                {
                    ComUtil.ReleaseOne(row);
                }
                FillBody(ctx.Conn, domB, po, want);
                pos = Rows.NewDom();
                msg = Rows.NewDom();
                co = StockCall.OpenCo(ctx);
                string code = StockCall.NewCode(ctx, co, kind, HeadMap(domH));
                List<object> heads = DomRows.RowsOf(domH);
                try
                {
                    if (heads.Count == 0)
                    {
                        throw new BridgeException(500, "internal", "表头模板没有行");
                    }
                    StockDom.SetCell(domH, heads[0], "cCode", code);
                }
                finally
                {
                    for (int i = 0; i < heads.Count; i++)
                    {
                        ComUtil.ReleaseOne(heads[i]);
                    }
                }
                int[] refs;
                object[] args = StockCall.InsertArgs(kind, StockCall.Forms(domH, domB, pos, ctx.Conn, msg), out refs);
                DryRun.Touched(Kinds.Find("purchase_order"), sourceId);
                StockCall.RunCo(ctx, co, "Insert", args, refs, null);
                int newId = Confirmed(ctx, kind, code, args[6]);
                return AfterSaved(ctx, kind, newId, code, Kinds.Find("purchase_order"), sourceId);
            }
            finally
            {
                ComUtil.Final(msg);
                ComUtil.Final(pos);
                ComUtil.Final(domB);
                ComUtil.Final(domH);
                ComUtil.Final(co);
            }
        }

        static int Confirmed(WorkContext ctx, VoucherKind kind, string code, object raw)
        {
            try
            {
                int id = StockCall.NewId(ctx, raw);
                if (id > 0 && Exists(ctx, kind, id))
                {
                    return id;
                }
                id = IdByCode(ctx, kind, code);
                if (id > 0)
                {
                    return id;
                }
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "Confirmed " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", Unknown(code));
            }
            throw new BridgeException(504, "outcome_unknown", Unknown(code));
        }

        static bool Exists(WorkContext ctx, VoucherKind kind, int id)
        {
            string sql = "select " + kind.IdColumn + " from " + kind.HeadTable + " where " + kind.IdColumn + "=?";
            return ReadFresh(ctx, sql, id) > 0;
        }

        static int IdByCode(WorkContext ctx, VoucherKind kind, string code)
        {
            string no = code == null ? "" : code.Trim();
            if (no.Length == 0)
            {
                return 0;
            }
            string sql = "select " + kind.IdColumn + " from " + kind.HeadTable + " where " + kind.CodeColumn + "=?";
            return ReadFresh(ctx, sql, no);
        }

        static int ReadFresh(WorkContext ctx, string sql, object arg)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                return ReadId(conn, sql, arg);
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static int ReadId(object conn, string sql, object arg)
        {
            Dictionary<string, object> row = Rows.One(conn, sql, new object[] { arg });
            if (row == null)
            {
                return 0;
            }
            foreach (object value in row.Values)
            {
                return CoRows.AsId(value);
            }
            return 0;
        }

        static object Blank(object conn, bool head)
        {
            string view = StockDom.ViewName("01", head);
            string alias = head ? "m" : "d";
            string sql = "select '' as editprop, " + alias + ".* from " + view + " " + alias + " with(nolock) where 1=2";
            return DomRows.Blank(conn, sql);
        }

        static Dictionary<string, object> HeadMap(object dom)
        {
            List<Dictionary<string, object>> rows = Rows.FromDom(dom, 1);
            if (rows == null || rows.Count == 0)
            {
                throw new BridgeException(500, "internal", "表头模板没有行");
            }
            return rows[0];
        }

        static ApiResult AfterSaved(WorkContext ctx, VoucherKind kind, int id, string code, VoucherKind source, int sourceId)
        {
            try
            {
                return EditMsg.Saved(ctx, kind, id, source, sourceId);
            }
            catch (Exception ex)
            {
                BridgeException bridge = ex as BridgeException;
                if (bridge != null && bridge.Code == "outcome_unknown")
                {
                    throw;
                }
                CoRows.Note(ctx.Item, "Saved " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", Unknown(id, code));
            }
        }
    }
}
