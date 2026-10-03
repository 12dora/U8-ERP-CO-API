using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    internal static partial class SaleGen
    {
        static object LoadRef(object conn, string sql, int id, string missing)
        {
            try
            {
                return AdoXml.LoadDom(conn, sql, id);
            }
            catch (BridgeException ex)
            {
                if (ex.Status == 404)
                {
                    throw new BridgeException(409, "state_mismatch", missing);
                }
                throw;
            }
        }

        static int FindId(WorkContext ctx, string sql, string column, string code)
        {
            string no = code == null ? "" : code.Trim();
            if (no.Length == 0)
            {
                return 0;
            }
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                List<Dictionary<string, object>> rows = Rows.Query(conn, sql, new object[] { no }, 1);
                if (rows == null || rows.Count != 1)
                {
                    return 0;
                }
                return CoRows.AsId(CoRows.Col(rows[0], column));
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "FindId " + ex.Message);
                return 0;
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static void DropAll(object dom)
        {
            for (int guard = 0; guard < 300; guard++)
            {
                List<object> rows = DomRows.RowsOf(dom);
                if (rows.Count == 0)
                {
                    return;
                }
                DomRows.RemoveRow(rows[rows.Count - 1]);
            }
            throw new BridgeException(500, "internal", "模板行数不符");
        }

        static void DropExtra(object dom)
        {
            for (int guard = 0; guard < 300; guard++)
            {
                List<object> rows = DomRows.RowsOf(dom);
                if (rows.Count <= 1)
                {
                    return;
                }
                DomRows.RemoveRow(rows[rows.Count - 1]);
            }
            throw new BridgeException(500, "internal", "模板行数不符");
        }

        static object OneRow(object dom)
        {
            List<object> rows = DomRows.RowsOf(dom);
            if (rows.Count == 0)
            {
                return DomRows.AddRow(dom);
            }
            return rows[0];
        }

        static object At(object body, int index)
        {
            List<object> rows = DomRows.RowsOf(body);
            if (index < 0 || index >= rows.Count)
            {
                throw new BridgeException(500, "internal", "模板行数不符");
            }
            return rows[index];
        }

        static string User(WorkContext ctx)
        {
            string user = ctx.Session.OperatorName ?? "";
            if (user.Length == 0)
            {
                user = ctx.Item.Operator ?? "";
            }
            return user;
        }

        static string LoginDate(WorkContext ctx)
        {
            string date = ctx.Item.Date == null ? "" : ctx.Item.Date.Trim();
            if (date.Length > 0)
            {
                return date;
            }
            return DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        }

        static decimal QtyOf(object value)
        {
            decimal qty;
            if (!TryDec(Text(value), out qty) || qty <= 0m)
            {
                throw new BridgeException(400, "bad_request", "数量必须大于 0");
            }
            return qty;
        }

        static string QtyText(decimal qty)
        {
            return qty.ToString("0.######", CultureInfo.InvariantCulture);
        }

        static decimal Dec(string text)
        {
            decimal value;
            if (!TryDec(text, out value))
            {
                return 0m;
            }
            return value;
        }

        static bool TryDec(string text, out decimal value)
        {
            value = 0m;
            if (text == null || text.Trim().Length == 0)
            {
                return false;
            }
            return decimal.TryParse(text.Trim(), NumberStyles.Any, CultureInfo.InvariantCulture, out value);
        }

        static string Text(object value)
        {
            if (value == null || value is DBNull)
            {
                return "";
            }
            string text = value as string;
            if (text != null)
            {
                return text;
            }
            IFormattable fmt = value as IFormattable;
            if (fmt == null)
            {
                return "";
            }
            return fmt.ToString(null, CultureInfo.InvariantCulture);
        }

        static string Cell(object value)
        {
            if (value == null || value is DBNull)
            {
                return null;
            }
            string text = value as string;
            if (text != null)
            {
                return text;
            }
            if (value is bool)
            {
                return (bool)value ? "1" : "0";
            }
            IFormattable fmt = value as IFormattable;
            if (fmt == null)
            {
                throw new BridgeException(400, "bad_request", "字段值类型不正确");
            }
            return fmt.ToString(null, CultureInfo.InvariantCulture);
        }

        static string TextOf(Dictionary<string, object> map, string name)
        {
            return Text(RawKey(map, name)).Trim();
        }

        static object RawKey(Dictionary<string, object> map, string name)
        {
            if (map == null)
            {
                return null;
            }
            foreach (KeyValuePair<string, object> pair in map)
            {
                if (Same(pair.Key, name) && pair.Value != null && !(pair.Value is DBNull))
                {
                    return pair.Value;
                }
            }
            return null;
        }

        static bool Same(string key, string name)
        {
            return string.Equals(key, name, StringComparison.OrdinalIgnoreCase);
        }

        static string Unknown(string code)
        {
            string no = code == null ? "" : code.Trim();
            if (no.Length == 0)
            {
                return "已保存但未能确定单据标识";
            }
            return "已保存但未能确定单据标识，单号 " + no;
        }

        // CommitSeen 之后回读失败不能再报成普通错误，调用方会重试而生成第二张单。
        static ApiResult AfterSave(WorkContext ctx, VoucherKind kind, int id, string code, SaveEcho echo)
        {
            try
            {
                int found = FindId(ctx, echo.IdSql, echo.IdColumn, code);
                if (found > 0)
                {
                    id = found;
                }
                if (id <= 0)
                {
                    throw new BridgeException(504, "outcome_unknown", Unknown(code));
                }
                return EditMsg.Saved(ctx, kind, id, Kinds.Find(echo.SourceName), echo.SourceId);
            }
            catch (Exception ex)
            {
                BridgeException bridge = ex as BridgeException;
                if (bridge != null && bridge.Code == "outcome_unknown")
                {
                    throw;
                }
                CoRows.Note(ctx.Item, "Saved " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", Unknown(code));
            }
        }

        static ApiResult BuildInvoice(WorkContext ctx, VoucherKind kind, int sourceId, Dictionary<string, object> head,
            InvHead want, ShipLine[] plan)
        {
            object srcHead = null;
            object sys = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                srcHead = LoadRef(ctx.Conn, InvRefHead, sourceId, "参照视图没有该发货单");
                SaSession.OpenSa(ctx.Conn, ctx.Session.Login, want.Vt, out sys, out co);
                SoDom.Templates(co, ctx.Conn, want.Card, ctx.Item, doms);
                FillInvHead(ctx, sys, doms, srcHead, head, want);
                FillInvLines(ctx, co, doms, want, plan);
                string code;
                int id = SoSave.SaveInvoice(ctx.Conn, co, doms, ctx.Item, out code);
                return AfterSave(ctx, kind, id, code, Echo(InvIdSql, "SBVID", "dispatch", sourceId));
            }
            finally
            {
                ComUtil.Final(srcHead);
                SaSession.Release(sys, co, doms);
            }
        }

        static void FillInvHead(WorkContext ctx, object sys, object[] doms, object srcHead, Dictionary<string, object> head, InvHead want)
        {
            DropExtra(doms[0]);
            object row = OneRow(doms[0]);
            List<object> srcRows = DomRows.RowsOf(srcHead);
            if (srcRows.Count < 1)
            {
                throw new BridgeException(409, "state_mismatch", "参照视图没有该发货单");
            }
            string soCode = want.SoCode;
            if (soCode.Length == 0)
            {
                soCode = DomRows.Get(srcRows[0], "csocode").Trim();
            }
            string dlCode = want.DlCode;
            if (dlCode.Length == 0)
            {
                dlCode = DomRows.Get(srcRows[0], "cdlcode").Trim();
            }
            DomRows.CopyInto(doms[0], row, srcRows[0], InvHeadSkip);
            ApplyFields(doms[0], row, head, true);
            if (TextOf(head, "ddate").Length == 0)
            {
                DomRows.Set(doms[0], row, "ddate", LoginDate(ctx));
            }
            SoSave.Stamp(ctx, sys, doms[0], want.Card);
            want.DlCode = dlCode;
            want.SoCode = soCode;
            PinInvHead(doms[0], want.Vouch, want.DlCode, want.SoCode);
        }

        // Stamp 会释放表头行。身份字段在这之后再写，sbvid 必须是空字符串而不是缺属性。
        // 实测：界面参照发货单开的发票 SaleBillVouch.iDisp=1；来源视图没有 idisp，不写就是缺省 0，U8 把它当先开票，
        // 之后修改报「先开票不可以参照发货单」。所以表头 idisp 写 1，表体 cbdlcode 写发货单号（FillInvLine）。
        static void PinInvHead(object dom, string vouch, string dlCode, string soCode)
        {
            object row = OneRow(dom);
            DomRows.Set(dom, row, "sbvid", "");
            DomRows.Set(dom, row, "cvouchtype", vouch);
            DomRows.Set(dom, row, "idisp", "1");
            if (dlCode.Length > 0)
            {
                DomRows.Set(dom, row, "cdlcode", dlCode);
            }
            if (soCode.Length > 0)
            {
                DomRows.Set(dom, row, "csocode", soCode);
            }
            DomRows.Set(dom, row, "csource", "销售");
            DomRows.Set(dom, row, "breturnflag", "0");
            DomRows.Set(dom, row, "editprop", "A");
        }

        static void FillInvLines(WorkContext ctx, object co, object[] doms, InvHead want, ShipLine[] plan)
        {
            DropAll(doms[1]);
            for (int i = 0; i < plan.Length; i++)
            {
                FillInvLine(ctx, co, doms, want, plan[i], i);
            }
        }

        static void FillInvLine(WorkContext ctx, object co, object[] doms, InvHead want, ShipLine line, int index)
        {
            object src = null;
            try
            {
                src = LoadRef(ctx.Conn, InvRefBody, line.LineId, "参照视图没有该发货单行");
                List<object> srcRows = DomRows.RowsOf(src);
                if (srcRows.Count < 1)
                {
                    throw new BridgeException(409, "state_mismatch", "参照视图没有该发货单行");
                }
                object dst = DomRows.AddRow(doms[1]);
                int at = DomRows.RowsOf(doms[1]).Count - 1;
                DomRows.CopyInto(doms[1], dst, srcRows[0], InvBodySkip);
                Scale(doms[1], dst, line.Qty);
                string msg = SaleCalc.Check(co, doms[0], doms[1], dst, "iquantity", false);
                if (msg.Length > 0)
                {
                    CoRows.Note(ctx.Item, "BodyCheck " + msg);
                }
                object row = At(doms[1], at);
                ApplyFields(doms[1], row, line.Fields, false);
                DomRows.Set(doms[1], row, "idlsid", line.LineId.ToString(CultureInfo.InvariantCulture));
                if (want.DlCode.Length > 0)
                {
                    DomRows.Set(doms[1], row, "cdlcode", want.DlCode);
                    DomRows.Set(doms[1], row, "cbdlcode", want.DlCode);
                }
                DomRows.Set(doms[1], row, "irowno", (index + 1).ToString(CultureInfo.InvariantCulture));
                DomRows.Set(doms[1], row, "editprop", "A");
            }
            finally
            {
                ComUtil.Final(src);
            }
        }

        // 这个入口只有一张来源发货单，客户和 cDLCode 都取它。多张发货单拼进一张发票不在这里。
        static ShipLine[] PlanInvoice(object conn, int sourceId, object[] lines, InvHead want)
        {
            Dictionary<string, object> disp = Rows.One(conn, DispHeadSql, new object[] { sourceId });
            if (disp == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (CoRows.Col(disp, "cVouchType") != "05" || CoRows.FlagOf(disp, "bReturnFlag"))
            {
                throw new BridgeException(400, "bad_request", "仅支持蓝字发货单");
            }
            if (CoRows.FlagOf(disp, "bFirst"))
            {
                throw new BridgeException(400, "bad_request", "不支持期初发货单");
            }
            if (CoRows.Col(disp, "cVerifier").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            if (CoRows.Col(disp, "cCloser").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            want.DlCode = CoRows.Col(disp, "cDLCode");
            want.SoCode = CoRows.Col(disp, "cSOCode");
            Dictionary<int, Dictionary<string, object>> body = IndexBy(conn, DispLineSql, sourceId, "iDLsID");
            ShipLine[] plan = new ShipLine[lines.Length];
            Dictionary<int, bool> seen = new Dictionary<int, bool>();
            for (int i = 0; i < lines.Length; i++)
            {
                plan[i] = ReadInvLine(lines[i], body, seen);
            }
            return plan;
        }

        static ShipLine ReadInvLine(object raw, Dictionary<int, Dictionary<string, object>> body, Dictionary<int, bool> seen)
        {
            Dictionary<string, object> map = raw as Dictionary<string, object>;
            if (map == null)
            {
                throw new BridgeException(400, "bad_request", "表体行必须是对象");
            }
            CheckKeys(map, false, InvLineKeys, false);
            int lineId = CoRows.AsId(RawKey(map, "source_line_id"));
            Dictionary<string, object> src;
            if (lineId <= 0 || !body.TryGetValue(lineId, out src))
            {
                throw new BridgeException(400, "bad_request", "明细行不存在");
            }
            if (seen.ContainsKey(lineId))
            {
                throw new BridgeException(400, "bad_request", "明细行重复");
            }
            seen[lineId] = true;
            if (CoRows.Col(src, "cSCloser").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            decimal qty = QtyOf(RawKey(map, "quantity"));
            // 已开票加未开票退货（生成的 bneedbill=0 退货单回写 fretqtywkp）。
            if (qty > Remain(src, "Billed"))
            {
                throw new BridgeException(409, "state_mismatch", "超过可生单数量");
            }
            ShipLine line = new ShipLine();
            line.LineId = lineId;
            line.Qty = qty;
            line.Fields = map;
            return line;
        }

        static InvHead Want(Dictionary<string, object> head)
        {
            InvHead want = new InvHead();
            want.DlCode = "";
            want.SoCode = "";
            string text = TextOf(head, "cvouchtype");
            if (text.Length == 0 || SameQty(text, 26m))
            {
                want.Vt = 0;
                want.Card = "07";
                want.Vouch = "26";
                return want;
            }
            if (SameQty(text, 27m))
            {
                want.Vt = 2;
                want.Card = "13";
                want.Vouch = "27";
                return want;
            }
            throw new BridgeException(400, "bad_request", "该发票类型不支持");
        }

        static bool SameQty(string text, decimal expect)
        {
            decimal n;
            return TryDec(text, out n) && n == expect;
        }

        sealed class InvHead
        {
            public int Vt;
            public string Card;
            public string Vouch;
            public string DlCode;
            public string SoCode;
        }

        sealed class ShipLine
        {
            public int LineId;
            public decimal Qty;
            public string OrderRow;
            public Dictionary<string, object> Fields;
        }

    }
}
