using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 请购单新增、修改（同采购订单：VoucherSave2，新增 2、修改 1）。调用方字段必须在 DOM schema 里，否则 400。
    internal static partial class PuApp
    {
        // false：空白 DOM 取 GetVoucherDataById(头, 体, "", 0, "") 的 schema（同采购发票）；
        // true：取视图 pu_AppHead / pu_AppBody 的 where 1=2（同采购订单的 zpurpoheader）。哪种能保存未经实测。
        static readonly bool BlankByView = false;
        const string HeadBlank = "select '' as editprop, pu_AppHead.* from pu_AppHead where 1=2";
        const string BodyBlank = "select '' as editprop, pu_AppBody.* from pu_AppBody where 1=2";

        public static ApiResult Create(WorkContext ctx, VoucherKind kind, Dictionary<string, object> head, object[] lines)
        {
            Dictionary<string, string> headFields = PuAppFields.Head(head ?? new Dictionary<string, object>());
            List<Dictionary<string, string>> body = PuAppFields.CreateLines(lines);
            object info = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                PuSession.Open(ctx, Vt, false, out info, out co);
                ReadBlank(ctx, co, doms);
                object headRow = DomRows.AddRow(doms[0]);
                string date = FillHead(ctx, doms[0], headRow, headFields, TemplateId(co, ctx));
                AppPaint paint = Painter(ctx, doms[1], date);
                for (int i = 0; i < body.Count; i++)
                {
                    object row = DomRows.AddRow(doms[1]);
                    PaintLine(paint, row, null, body[i]);
                    Soft(doms[1], row, paint.Schema, "ivouchrowno", (i + 1).ToString(CultureInfo.InvariantCulture));
                }
                string code;
                int id = CreateTran(ctx, co, doms, out code);
                return AfterSaved(ctx, kind, id, code);
            }
            finally
            {
                Release(info, co, doms);
            }
        }

        public static ApiResult Update(WorkContext ctx, VoucherKind kind, int id, Dictionary<string, object> head, object[] lines)
        {
            Dictionary<string, string> headFields = PuAppFields.Head(head ?? new Dictionary<string, object>());
            List<PuOp> ops = PuAppFields.Ops(lines);
            if (headFields.Count == 0 && ops.Count == 0)
            {
                throw BridgeException.BadField("lines", "没有要修改的内容");
            }
            Gate(ctx, kind, id, "update");
            object info = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                PuSession.Open(ctx, Vt, false, out info, out co);
                PurchaseCo.ReadPu(co, doms, id, ctx.Item);
                CoRows.RequireHead(doms[0], kind.IdColumn, id);
                ApplyUpdate(ctx, doms, id, headFields, ops);
                UpdateTran(ctx, co, doms, id);
                return AfterSaved(ctx, kind, id, "");
            }
            finally
            {
                Release(info, co, doms);
            }
        }

        static void ReadBlank(WorkContext ctx, object co, object[] doms)
        {
            if (!BlankByView)
            {
                PuInv.ReadBlank(co, doms, ctx.Item);
                return;
            }
            ComUtil.Final(doms[0]);
            doms[0] = null;
            doms[0] = DomRows.Blank(ctx.Conn, HeadBlank);
            ComUtil.Final(doms[1]);
            doms[1] = null;
            doms[1] = DomRows.Blank(ctx.Conn, BodyBlank);
        }

        // 返回单据日期，作为表体需求日期的缺省。
        static string FillHead(WorkContext ctx, object dom, object row, Dictionary<string, string> fields, string vtid)
        {
            List<string> schema = DomRows.Schema(dom);
            Caller(dom, row, schema, fields, "head");
            string date = PuAppFields.Has(fields, "ddate") ? fields["ddate"].Trim() : LoginDate(ctx);
            Soft(dom, row, schema, "ddate", date);
            if (!PuAppFields.Has(fields, "cbustype"))
            {
                Soft(dom, row, schema, "cbustype", PuAppFields.Bus);
            }
            // 表头主键属性要在、可以是空串，缺了 U8 回「使用 Null 无效」。
            Soft(dom, row, schema, "id", "");
            Soft(dom, row, schema, "ivtid", vtid);
            Soft(dom, row, schema, "cmaker", Maker(ctx));
            DomRows.Set(dom, row, "editprop", "A", schema);
            return date;
        }

        static void ApplyUpdate(WorkContext ctx, object[] doms, int id, Dictionary<string, string> headFields, List<PuOp> ops)
        {
            List<object> heads = DomRows.RowsOf(doms[0]);
            object headRow = heads[0];
            List<string> headSchema = DomRows.Schema(doms[0]);
            Caller(doms[0], headRow, headSchema, headFields, "head");
            DomRows.Set(doms[0], headRow, "editprop", "M", headSchema);
            Soft(doms[0], headRow, headSchema, "creviser", Maker(ctx));
            Soft(doms[0], headRow, headSchema, "cmodifydate", LoginDate(ctx));
            Soft(doms[0], headRow, headSchema, "cmodifytime", DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture));
            string date = DomRows.Get(headRow, "ddate").Trim();
            if (date.Length > 10)
            {
                date = date.Substring(0, 10);
            }
            ApplyLines(ctx, doms[1], id, ops, date.Length > 0 ? date : LoginDate(ctx));
        }

        static void ApplyLines(WorkContext ctx, object body, int id, List<PuOp> ops, string date)
        {
            List<object> rows = DomRows.RowsOf(body) ?? new List<object>();
            if (rows.Count == 0)
            {
                throw new BridgeException(409, "state_mismatch", "U8 返回的表体为空");
            }
            CheckOps(rows, ops);
            AppPaint paint = Painter(ctx, body, date);
            List<string> schema = paint.Schema;
            int no = 0;
            for (int i = 0; i < rows.Count; i++)
            {
                no = Math.Max(no, CoRows.AsId(DomRows.Get(rows[i], "ivouchrowno")));
                Touch(paint, rows[i], FindOp(ops, LineId(rows[i])));
            }
            for (int i = 0; i < ops.Count; i++)
            {
                if (ops[i].Op != "add")
                {
                    continue;
                }
                no++;
                object row = DomRows.AddRow(body);
                PaintLine(paint, row, null, ops[i].Fields);
                Soft(body, row, schema, "id", id.ToString(CultureInfo.InvariantCulture));
                Soft(body, row, schema, "ivouchrowno", no.ToString(CultureInfo.InvariantCulture));
            }
        }

        static void Touch(AppPaint paint, object row, PuOp hit)
        {
            if (hit == null)
            {
                DomRows.Set(paint.Dom, row, "editprop", "", paint.Schema);
            }
            else if (hit.Op == "delete")
            {
                DomRows.Set(paint.Dom, row, "editprop", "D", paint.Schema);
            }
            else
            {
                PaintLine(paint, row, row, hit.Fields);
            }
        }

        static AppPaint Painter(WorkContext ctx, object dom, string date)
        {
            AppPaint paint = new AppPaint();
            paint.Ctx = ctx;
            paint.Conn = ctx.Conn;
            paint.Dom = dom;
            paint.Date = date;
            paint.Schema = DomRows.Schema(dom);
            return paint;
        }

        // 一行：调用方字段 → 单位 → 金额 → 币种（新增行）→ editprop。source 为 null 表示新增行。
        static void PaintLine(AppPaint paint, object row, object source, Dictionary<string, string> fields)
        {
            object conn = paint.Conn;
            object dom = paint.Dom;
            List<string> schema = paint.Schema;
            bool adding = source == null;
            if (!adding)
            {
                PuAppCalc.RequireLocal(paint.Ctx, source);
            }
            Dictionary<string, string> calc = PuAppCalc.FromRow(source);
            Dictionary<string, string> units = PuAppCalc.Units(conn, calc, fields, adding);
            foreach (KeyValuePair<string, string> pair in fields)
            {
                calc[pair.Key] = pair.Value;
            }
            Caller(dom, row, schema, fields, "lines");
            Dictionary<string, string> put = PuAppCalc.Prices(paint.Ctx, calc, fields, adding);
            if (adding)
            {
                PuAppCalc.Currencies(paint.Ctx, put);
                if (!PuAppFields.Has(fields, "drequirdate"))
                {
                    put["drequirdate"] = paint.Date;
                }
            }
            foreach (KeyValuePair<string, string> pair in units)
            {
                put[pair.Key] = pair.Value;
            }
            foreach (KeyValuePair<string, string> pair in put)
            {
                Soft(dom, row, schema, pair.Key, pair.Value.Length == 0 ? null : pair.Value);
            }
            DomRows.Set(dom, row, "editprop", adding ? "A" : "M", schema);
        }

        static void CheckOps(List<object> rows, List<PuOp> ops)
        {
            int deletes = 0;
            int adds = 0;
            HashSet<int> seen = new HashSet<int>();
            for (int i = 0; i < ops.Count; i++)
            {
                if (ops[i].Op == "add")
                {
                    adds++;
                    continue;
                }
                if (!seen.Add(ops[i].Id))
                {
                    throw BridgeException.BadField(FieldPath.Item("lines", i) + ".line_id", "明细行重复");
                }
                if (FindRow(rows, ops[i].Id) == null)
                {
                    throw BridgeException.BadField(FieldPath.Item("lines", i) + ".line_id", "明细行不存在");
                }
                if (ops[i].Op == "delete")
                {
                    deletes++;
                }
            }
            if (deletes > 0 && rows.Count - deletes + adds < 1)
            {
                throw BridgeException.BadField("lines", "不能删除全部明细");
            }
        }

        static PuOp FindOp(List<PuOp> ops, int id)
        {
            for (int i = 0; id > 0 && i < ops.Count; i++)
            {
                if (ops[i].Op != "add" && ops[i].Id == id)
                {
                    return ops[i];
                }
            }
            return null;
        }

        static object FindRow(List<object> rows, int id)
        {
            for (int i = 0; i < rows.Count; i++)
            {
                if (LineId(rows[i]) == id)
                {
                    return rows[i];
                }
            }
            return null;
        }

        // 表体行主键是 autoid（id 是表头外键）。
        static int LineId(object row)
        {
            return CoRows.AsId(DomRows.Get(row, "autoid"));
        }

        // 调用方字段：名字必须在 schema 里。
        static void Caller(object dom, object row, List<string> schema, Dictionary<string, string> fields, string at)
        {
            foreach (KeyValuePair<string, string> pair in fields)
            {
                if (!InSchema(schema, pair.Key))
                {
                    throw BridgeException.BadField(FieldPath.Join(at, pair.Key), "未知字段 " + pair.Key);
                }
                DomRows.Set(dom, row, pair.Key, pair.Value, schema);
            }
        }

        // 桥自己补的字段：schema 里没有就不设。
        static void Soft(object dom, object row, List<string> schema, string name, string value)
        {
            if (InSchema(schema, name))
            {
                DomRows.Set(dom, row, name, value, schema);
            }
        }

        static bool InSchema(List<string> schema, string name)
        {
            for (int i = 0; schema != null && i < schema.Count; i++)
            {
                if (string.Equals(schema[i], name, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }

        static string Maker(WorkContext ctx)
        {
            string name = ctx.Session.OperatorName == null ? "" : ctx.Session.OperatorName.Trim();
            if (name.Length == 0 && ctx.Item != null && ctx.Item.Operator != null)
            {
                name = ctx.Item.Operator.Trim();
            }
            return name;
        }

        static string LoginDate(WorkContext ctx)
        {
            string date = ctx.Item == null || ctx.Item.Date == null ? "" : ctx.Item.Date.Trim();
            if (date.Length == 0)
            {
                return DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
            }
            return date;
        }
    }

    sealed class AppPaint
    {
        public WorkContext Ctx;
        public object Conn;
        public object Dom;
        public List<string> Schema;
        public string Date;
    }
}
