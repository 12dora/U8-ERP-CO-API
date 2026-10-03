using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    internal static partial class PurchaseEdit
    {
        const string HeadBlank = "select '' as editprop, zpurpoheader.* from zpurpoheader where 1=2";
        const string BodyBlank = "select '' as editprop, zpurpotail.* from zPurpotail zpurpotail where 1=2";

        public static ApiResult Create(WorkContext ctx, VoucherKind kind, Dictionary<string, object> head, object[] lines)
        {
            if (kind == null || kind.Name != "purchase_order")
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持新增");
            }
            Dictionary<string, string> headFields = PuFields.Head(head);
            List<Dictionary<string, string>> body = PuFields.CreateLines(lines);
            PuFields.RequireCreate(headFields, body);
            object info = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                ctx.PuInitType = PuFields.Get(headFields, "cptcode").Trim();
                PuSession.Open(ctx, 1, false, out info, out co);
                doms[0] = DomRows.Blank(ctx.Conn, HeadBlank);
                doms[1] = DomRows.Blank(ctx.Conn, BodyBlank);
                RequireSchema(doms);
                object headRow = EnsureRow(doms[0]);
                FillHead(ctx, doms[0], headRow, headFields, TemplateId(co, ctx.Conn, ctx.Item));
                FillNewLines(doms[1], body, DraftOf(headFields, ctx));
                int id = CreateTran(ctx, kind, co, doms);
                return AfterSaved(ctx, kind, id, doms[0]);
            }
            finally
            {
                Release(info, co, doms);
            }
        }

        public static ApiResult Update(WorkContext ctx, VoucherKind kind, int id, Dictionary<string, object> head, object[] lines)
        {
            if (kind == null || kind.Name != "purchase_order")
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持修改");
            }
            if (head == null)
            {
                head = new Dictionary<string, object>();
            }
            if (lines == null)
            {
                lines = new object[0];
            }
            if (head.Count == 0 && lines.Length == 0)
            {
                throw new BridgeException(400, "bad_request", "没有要修改的内容");
            }
            Dictionary<string, string> headFields = PuFields.Head(head);
            PuFields.CheckHead(headFields);
            DropBlankCodes(lines);
            List<PuOp> ops = PuFields.Ops(lines);
            GateMutable(ctx, kind, id);
            object info = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                PuSession.UseStored(ctx, kind, id);
                PuSession.Open(ctx, 1, false, out info, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                PurchaseCo.ReadPu(co, doms, id, ctx.Item);
                CoRows.RequireHead(doms[0], kind.IdColumn, id);
                ApplyUpdate(ctx, doms, id, headFields, ops);
                CommitSave(ctx, co, doms, (short)1, id.ToString(CultureInfo.InvariantCulture));
                return AfterSaved(ctx, kind, id, doms[0]);
            }
            finally
            {
                Release(info, co, doms);
            }
        }

        public static ApiResult Delete(WorkContext ctx, VoucherKind kind, int id)
        {
            if (kind == null || (kind.Name != "purchase_order" && kind.Name != "arrival"))
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持删除");
            }
            if (kind.Name == "arrival")
            {
                GateArrival(ctx, kind, id);
                // 预演：到货单删除回退采购订单的累计到货，登记该订单。
                DocMark.Upstream(ctx.Conn, kind, id);
            }
            else
            {
                GateMutable(ctx, kind, id);
            }
            object info = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                PuSession.UseStored(ctx, kind, id);
                PuSession.Open(ctx, Vt(kind.Name), false, out info, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                PurchaseCo.ReadPu(co, doms, id, ctx.Item);
                CoRows.RequireHead(doms[0], kind.IdColumn, id);
                DeleteTran(ctx, co, doms);
                return Gone(ctx, kind, id);
            }
            finally
            {
                Release(info, co, doms);
            }
        }

        public static ApiResult Close(WorkContext ctx, VoucherKind kind, int id, string action, int[] lineIds)
        {
            if (kind == null || kind.Name != "purchase_order")
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持关闭");
            }
            if (action != "close" && action != "open")
            {
                throw new BridgeException(400, "bad_request", "action 必须是 close 或 open");
            }
            Dictionary<string, object> head = CoRows.HeadRow(ctx.Conn, kind, id);
            if (head == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            Dictionary<int, string> closers = LoadClosers(ctx.Conn, kind, id);
            GateClose(action, head, closers, lineIds);
            object info = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                PuSession.UseStored(ctx, kind, id);
                PuSession.Open(ctx, 1, false, out info, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                PurchaseCo.ReadPu(co, doms, id, ctx.Item);
                CoRows.RequireHead(doms[0], kind.IdColumn, id);
                if (lineIds != null)
                {
                    KeepLines(doms[1], lineIds);
                }
                CloseTran(ctx, co, doms, action, lineIds == null);
                return FreshClose(ctx, kind, id, action);
            }
            finally
            {
                Release(info, co, doms);
            }
        }

        static void FillHead(WorkContext ctx, object dom, object row, Dictionary<string, string> fields, string vtid)
        {
            foreach (KeyValuePair<string, string> pair in fields)
            {
                if (!string.Equals(pair.Key, "darrivedate", StringComparison.OrdinalIgnoreCase))
                {
                    DomRows.Set(dom, row, pair.Key, pair.Value);
                }
            }
            // 采购类型缺省取默认采购类型，没有就不写、交给 U8；币种缺省本位币（汇率 1）。业务类型「普通采购」是 U8 固定值。
            string pt = ctx.DefaultPurchaseType;
            if (pt.Length > 0)
            {
                PutMissing(dom, row, fields, "cptcode", pt);
            }
            PutMissing(dom, row, fields, "cbustype", "普通采购");
            if (!PuFields.Has(fields, "cexch_name"))
            {
                DomRows.Set(dom, row, "cexch_name", ctx.HomeCurrency);
            }
            PutMissing(dom, row, fields, "nflat", "1");
            PutMissing(dom, row, fields, "itaxrate", "13");
            PutMissing(dom, row, fields, "dpodate", LoginDate(ctx));
            DomRows.Set(dom, row, "editprop", "A");
            DomRows.Set(dom, row, "cstate", "0");
            DomRows.Set(dom, row, "idiscounttaxtype", "0");
            DomRows.Set(dom, row, "ivtid", vtid);
            DomRows.Set(dom, row, "cmaker", Maker(ctx));
        }

        static void PutMissing(object dom, object row, Dictionary<string, string> fields, string key, string value)
        {
            if (PuFields.Has(fields, key))
            {
                return;
            }
            DomRows.Set(dom, row, key, value);
        }

        static void ApplyHead(object dom, object row, Dictionary<string, string> fields)
        {
            foreach (KeyValuePair<string, string> pair in fields)
            {
                if (!string.Equals(pair.Key, "darrivedate", StringComparison.OrdinalIgnoreCase))
                {
                    DomRows.Set(dom, row, pair.Key, pair.Value);
                }
            }
        }

        static void StampEdit(WorkContext ctx, object dom, object row)
        {
            List<string> schema = DomRows.Schema(dom);
            DomRows.Set(dom, row, "editprop", "M", schema);
            string now = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture);
            StampNamed(dom, row, schema, "creviser", Maker(ctx));
            StampNamed(dom, row, schema, "cmodifydate", LoginDate(ctx));
            StampNamed(dom, row, schema, "cmodifytime", now);
        }

        static void StampNamed(object dom, object row, List<string> schema, string name, string value)
        {
            if (!InSchema(schema, name))
            {
                return;
            }
            DomRows.Set(dom, row, name, value, schema);
        }

        static bool InSchema(List<string> schema, string name)
        {
            if (schema == null || name == null)
            {
                return false;
            }
            for (int i = 0; i < schema.Count; i++)
            {
                if (string.Equals(schema[i], name, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }

        static PuDraft DraftOf(Dictionary<string, string> fields, WorkContext ctx)
        {
            string date = Pick(fields, "dpodate", LoginDate(ctx));
            PuDraft draft = new PuDraft();
            draft.Conn = ctx.Conn;
            draft.Tax = Pick(fields, "itaxrate", "13");
            draft.InvTax = !PuFields.Has(fields, "itaxrate");
            draft.Arrive = Pick(fields, "darrivedate", date);
            draft.Exch = PuCalc.Need(Pick(fields, "nflat", "1"), "汇率不正确");
            if (draft.Exch <= 0m)
            {
                throw new BridgeException(400, "bad_request", "汇率必须大于 0");
            }
            return draft;
        }

        static string TaxOf(object headRow, Dictionary<string, string> fields)
        {
            if (PuFields.Has(fields, "itaxrate"))
            {
                return fields["itaxrate"];
            }
            string tax = Clean(DomRows.Get(headRow, "itaxrate"));
            if (tax.Length == 0)
            {
                return "13";
            }
            return tax;
        }

        static string ArriveOf(object headRow, Dictionary<string, string> fields)
        {
            if (PuFields.Has(fields, "darrivedate"))
            {
                return fields["darrivedate"];
            }
            return Clean(DomRows.Get(headRow, "dpodate"));
        }

        static decimal ExchOf(object headRow, Dictionary<string, string> fields)
        {
            string text = PuFields.Has(fields, "nflat") ? fields["nflat"] : Clean(DomRows.Get(headRow, "nflat"));
            if (text == null || text.Trim().Length == 0)
            {
                text = "1";
            }
            decimal exch = PuCalc.Need(text, "汇率不正确");
            if (exch <= 0m)
            {
                throw new BridgeException(400, "bad_request", "汇率必须大于 0");
            }
            return exch;
        }

        static void GateMutable(WorkContext ctx, VoucherKind kind, int id)
        {
            Dictionary<string, object> row = CoRows.HeadRow(ctx.Conn, kind, id);
            if (row == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            PurchaseCo.RefuseFlow(ctx.Conn, kind, row);
            VoucherLockGate.Refuse(ctx.Conn, kind.Name, id, ctx.OperatorName);
            if (Closed(ctx.Conn, kind, id, row))
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            if (CoRows.Col(row, "verifier").Length > 0 || CoRows.Col(row, "cstate") != "0")
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核");
            }
            PurchaseCo.RefuseChild(ctx.Conn, kind, id);
        }

        static bool Closed(object conn, VoucherKind kind, int id, Dictionary<string, object> row)
        {
            if (CoRows.Col(row, "closer").Length > 0 || CoRows.Col(row, "cstate") == "2")
            {
                return true;
            }
            string sql = "select top 1 d.cbCloser from " + kind.BodyTable
                + " d where d." + kind.BodyFk + "=? and d.cbCloser is not null and ltrim(rtrim(d.cbCloser))<>''";
            return Rows.Scalar(conn, sql, new object[] { id }) != null;
        }

        static void GateArrival(WorkContext ctx, VoucherKind kind, int id)
        {
            Dictionary<string, object> row = CoRows.HeadRow(ctx.Conn, kind, id);
            if (row == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            PurchaseCo.RefuseFlow(ctx.Conn, kind, row);
            if (CoRows.Col(row, "verifier").Length > 0 || CoRows.Col(row, "verify_date").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核");
            }
            PurchaseCo.RefuseArrival(ctx.Conn, id, true);
        }

        static void GateClose(string action, Dictionary<string, object> head, Dictionary<int, string> closers, int[] lineIds)
        {
            if (action == "close" && CoRows.Col(head, "verifier").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            if (lineIds == null)
            {
                GateWhole(action, head, closers);
                return;
            }
            if (lineIds.Length == 0 || lineIds.Length > 200)
            {
                throw new BridgeException(400, "bad_request", "没有要处理的明细");
            }
            HashSet<int> seen = new HashSet<int>();
            for (int i = 0; i < lineIds.Length; i++)
            {
                GateOne(action, closers, seen, lineIds[i]);
            }
        }

        static void GateOne(string action, Dictionary<int, string> closers, HashSet<int> seen, int id)
        {
            string closer;
            if (id <= 0 || !closers.TryGetValue(id, out closer))
            {
                throw new BridgeException(400, "bad_request", "明细行不存在");
            }
            if (!seen.Add(id))
            {
                throw new BridgeException(400, "bad_request", "明细行重复");
            }
            bool shut = closer != null && closer.Length > 0;
            if (action == "close" && shut)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            if (action == "open" && !shut)
            {
                throw new BridgeException(409, "state_mismatch", "单据未关闭");
            }
        }

        static void GateWhole(string action, Dictionary<string, object> head, Dictionary<int, string> closers)
        {
            bool headShut = CoRows.Col(head, "closer").Length > 0;
            bool any = false;
            foreach (KeyValuePair<int, string> pair in closers)
            {
                if (pair.Value != null && pair.Value.Length > 0)
                {
                    any = true;
                }
            }
            if (action == "close" && headShut)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            if (action == "open" && !headShut && !any)
            {
                throw new BridgeException(409, "state_mismatch", "单据未关闭");
            }
        }

        static Dictionary<int, string> LoadClosers(object conn, VoucherKind kind, int id)
        {
            string sql = "select ID as line_id, cbCloser as closer from " + kind.BodyTable
                + " where " + kind.BodyFk + "=? order by ID";
            List<Dictionary<string, object>> rows = Rows.Query(conn, sql, new object[] { id }, 5000);
            Dictionary<int, string> map = new Dictionary<int, string>();
            for (int i = 0; i < rows.Count; i++)
            {
                int lineId = AsInt(Cell(rows[i], "line_id"));
                if (lineId > 0)
                {
                    map[lineId] = Show(Cell(rows[i], "closer"));
                }
            }
            return map;
        }

        static void KeepLines(object body, int[] ids)
        {
            RequireDom(body, ids);
            for (int guard = 0; guard < 10000; guard++)
            {
                List<object> rows = DomRows.RowsOf(body);
                object drop = FirstDrop(rows, ids);
                if (drop == null)
                {
                    int count = rows == null ? 0 : rows.Count;
                    if (count != ids.Length)
                    {
                        throw new BridgeException(409, "state_mismatch", "U8 返回的表体与订单不一致");
                    }
                    return;
                }
                DomRows.RemoveRow(drop);
            }
            throw new BridgeException(500, "internal", "未能筛出关闭行");
        }

        static void RequireDom(object body, int[] ids)
        {
            List<object> rows = DomRows.RowsOf(body);
            for (int i = 0; i < ids.Length; i++)
            {
                if (FindRow(rows, ids[i]) == null)
                {
                    throw new BridgeException(409, "state_mismatch", "U8 返回的表体缺少明细");
                }
            }
        }

        static object FirstDrop(List<object> rows, int[] ids)
        {
            if (rows == null)
            {
                return null;
            }
            for (int i = 0; i < rows.Count; i++)
            {
                if (IndexOf(ids, LineId(rows[i])) < 0)
                {
                    return rows[i];
                }
            }
            return null;
        }
    }
}
