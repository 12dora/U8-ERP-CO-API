using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 空模板来自表头/表体视图 where 1=2。editprop 只由本类写入。调用方字段按白名单。
    internal static partial class StockDom
    {
        const int AdPersistXml = 1;
        const int AdUseClient = 3;
        const int AdOpenStatic = 3;
        const int AdLockReadOnly = 1;

        public static string HeadView(VoucherKind kind)
        {
            if (kind == null)
            {
                return null;
            }
            return ViewName(kind.StType, true);
        }

        public static object BuildHead(WorkContext ctx, VoucherKind kind, Dictionary<string, object> fields, string maker, string billDate)
        {
            object conn = ctx.Conn;
            if (kind != null && kind.StType == "12")
            {
                return TransferHead(conn, kind, fields, maker, billDate);
            }
            if (PurType(kind))
            {
                return PurHead(ctx, kind, fields, maker, billDate);
            }
            string view = HeadView(kind);
            if (view == null || !OtherType(kind))
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持新增");
            }
            if (fields == null)
            {
                throw BridgeException.BadField("head", "缺少表头");
            }
            List<string> names;
            object dom = SchemaDom(conn, TemplateSql(view, "m"), out names);
            try
            {
                RequireEdit(names);
                Dictionary<string, string> row = RowMap();
                FillHead(kind, names, row, maker);
                Overlay(names, row, fields, kind, "head");
                FillHead(kind, names, row, maker);
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

        public static object BuildBody(object conn, VoucherKind kind, object[] lines)
        {
            if (kind != null && kind.StType == "12")
            {
                return TransferBody(conn, kind, lines);
            }
            if (PurType(kind))
            {
                return PurBody(conn, kind, lines);
            }
            string view = OtherBody(kind);
            RequireLines(lines);
            List<string> names;
            object dom = SchemaDom(conn, TemplateSql(view, "d"), out names);
            try
            {
                RequireEdit(names);
                for (int i = 0; i < lines.Length; i++)
                {
                    AppendLine(conn, dom, names, lines[i], i + 1, kind);
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

        static string OtherBody(VoucherKind kind)
        {
            string view = kind == null ? null : ViewName(kind.StType, false);
            if (view == null || !OtherType(kind))
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持新增");
            }
            return view;
        }

        // 其他入库、其他出库，以及无来源销售出库单（32，cSource=库存，StockSaleOut）。
        static bool OtherType(VoucherKind kind)
        {
            return kind.StType == "08" || kind.StType == "09" || kind.StType == "32";
        }

        static void RequireLines(object[] lines)
        {
            if (lines == null || lines.Length < 1 || lines.Length > 200)
            {
                throw BridgeException.BadField("lines", "表体须为 1 到 200 行");
            }
        }

        // 按模板里的大小写写表头第一行的属性；模板没有该列时按给定名字新建。
        public static void SetHeadValue(object dom, string name, string value)
        {
            object row = null;
            object attrs = null;
            try
            {
                Rows.UseXPath(dom);
                row = ComUtil.Call(dom, "selectSingleNode", new object[] { "//*[local-name()='row']" });
                if (row == null)
                {
                    throw new BridgeException(500, "internal", "表头模板没有行");
                }
                attrs = ComUtil.Get(row, "attributes");
                int count = Convert.ToInt32(ComUtil.Get(attrs, "length"));
                string actual = name;
                for (int i = 0; i < count; i++)
                {
                    object attr = ComUtil.Call(attrs, "item", new object[] { i });
                    string attrName = Values.Text(ComUtil.Get(attr, "nodeName"));
                    ComUtil.ReleaseOne(attr);
                    if (string.Equals(attrName, name, StringComparison.OrdinalIgnoreCase))
                    {
                        actual = attrName;
                        break;
                    }
                }
                ComUtil.Call(row, "setAttribute", new object[] { actual, value });
            }
            finally
            {
                ComUtil.Final(attrs);
                ComUtil.Final(row);
            }
        }

        static void AppendLine(object conn, object dom, List<string> names, object raw, int rowNo, VoucherKind kind)
        {
            Dictionary<string, object> line = raw as Dictionary<string, object>;
            if (line == null)
            {
                throw BridgeException.BadField(FieldPath.Item("lines", rowNo - 1), "表体行不是对象");
            }
            Dictionary<string, string> row = RowMap();
            Put(names, row, "editprop", "A");
            Put(names, row, "autoid", "");
            Put(names, row, "bcosting", "1");
            Put(names, row, "irowno", rowNo.ToString(CultureInfo.InvariantCulture));
            Overlay(names, row, line, kind, FieldPath.Item("lines", rowNo - 1));
            Put(names, row, "editprop", "A");
            if (OtherType(kind))
            {
                StockUnits.FixPrice(names, row);
                StockUnits.Apply(conn, names, row, "iQuantity", "iNum", false);
            }
            StockCall.AppendRow(dom, row);
        }

        static void FillHead(VoucherKind kind, List<string> names, Dictionary<string, string> row, string maker)
        {
            bool inn = kind.StType == "08";
            Put(names, row, "editprop", "A");
            Put(names, row, "cvouchtype", kind.StType);
            Put(names, row, "cbustype", BusType(kind.StType));
            Put(names, row, "csource", "库存");
            Put(names, row, "brdflag", inn ? "1" : "0");
            Put(names, row, "vt_id", TemplateId(kind.StType));
            Put(names, row, "iverifystate", "0");
            Put(names, row, "iswfcontrolled", "0");
            Put(names, row, "bredvouch", "0");
            Put(names, row, "cmaker", maker ?? "");
            Put(names, row, "id", "");
            Put(names, row, "ccode", "");
        }

        // 销售出库单用 U8 的显示模版 87（卡片 0303，参照发货单生成的也是 87）。
        internal static string BusType(string st)
        {
            if (st == "32")
            {
                return "普通销售";
            }
            return st == "08" ? "其他入库" : "其他出库";
        }

        internal static string TemplateId(string st)
        {
            if (st == "32")
            {
                return "87";
            }
            return st == "08" ? "67" : "85";
        }

        // at：field 前缀，"head" 或 "lines.<下标>"。
        static void Overlay(List<string> names, Dictionary<string, string> row, Dictionary<string, object> fields,
            VoucherKind kind, string at)
        {
            bool head = at == "head";
            if (fields == null)
            {
                return;
            }
            foreach (KeyValuePair<string, object> kv in fields)
            {
                if (!Allowed(kind, head, kv.Key))
                {
                    throw BridgeException.BadField(FieldPath.Join(at, kv.Key), "不能设置字段 " + kv.Key).WithHint(FieldPath.WritableHint);
                }
                string canon = Canonical(names, Alias(names, kind, kv.Key));
                if (canon == null)
                {
                    throw BridgeException.BadField(FieldPath.Join(at, kv.Key), "未知字段 " + kv.Key);
                }
                string text = Cell(kv.Key, kv.Value, at);
                if (text == null)
                {
                    continue;
                }
                row[canon] = text;
            }
        }

        static string TemplateSql(string view, string alias)
        {
            return "select '' as editprop, " + alias + ".* from " + view + " " + alias + " with(nolock) where 1=2";
        }

        static object SchemaDom(object conn, string sql, out List<string> names)
        {
            object cmd = null;
            object rs = null;
            object dom = null;
            names = null;
            try
            {
                rs = OpenSchema(conn, sql, out cmd);
                names = StockCall.FieldNames(rs);
                dom = Rows.NewDom();
                if (dom == null)
                {
                    throw new BridgeException(503, "com_unavailable", "MSXML 未注册");
                }
                ComUtil.Set(dom, "async", false);
                ComUtil.Call(rs, "Save", new object[] { dom, AdPersistXml });
                object keep = dom;
                dom = null;
                return keep;
            }
            finally
            {
                StockCall.CloseRs(rs);
                ComUtil.Final(dom);
                ComUtil.Final(cmd);
            }
        }

        static object OpenSchema(object conn, string sql, out object cmd)
        {
            cmd = null;
            cmd = ComUtil.Create("ADODB.Command");
            if (cmd == null)
            {
                throw new BridgeException(503, "com_unavailable", "ADODB 未注册");
            }
            object rs = null;
            try
            {
                ComUtil.Set(cmd, "ActiveConnection", conn);
                ComUtil.Set(cmd, "CommandText", sql);
                ComUtil.Set(cmd, "CommandTimeout", 45);
                rs = ComUtil.Create("ADODB.Recordset");
                if (rs == null)
                {
                    throw new BridgeException(503, "com_unavailable", "ADODB 未注册");
                }
                ComUtil.Set(rs, "CursorLocation", AdUseClient);
                ComUtil.Call(rs, "Open", new object[] { cmd, Type.Missing, AdOpenStatic, AdLockReadOnly, -1 });
                return rs;
            }
            catch
            {
                ComUtil.Final(rs);
                ComUtil.Final(cmd);
                cmd = null;
                throw;
            }
        }

        static void RequireEdit(List<string> names)
        {
            if (Canonical(names, "editprop") == null)
            {
                throw new BridgeException(500, "internal", "模板缺少 editprop");
            }
        }

        static string Canonical(List<string> names, string key)
        {
            if (names == null || key == null)
            {
                return null;
            }
            for (int i = 0; i < names.Count; i++)
            {
                if (string.Equals(names[i], key, StringComparison.OrdinalIgnoreCase))
                {
                    return names[i];
                }
            }
            return null;
        }

        static void Put(List<string> names, Dictionary<string, string> row, string key, string value)
        {
            string canon = Canonical(names, key);
            if (canon != null)
            {
                row[canon] = value;
            }
        }

        static Dictionary<string, string> RowMap()
        {
            return new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        }

        // 同一 DOM 多次写入时传入已读 schema，避免每格再查一次名单。
        internal static void WriteAll(object dom, object row, VoucherKind kind, bool head,
            Dictionary<string, object> fields, List<string> schema)
        {
            if (fields == null)
            {
                return;
            }
            DomPut put = Prep(dom, row, kind, head, schema);
            foreach (KeyValuePair<string, object> kv in fields)
            {
                PutField(put, kv.Key, kv.Value);
            }
        }

        static DomPut Prep(object dom, object row, VoucherKind kind, bool head, List<string> schema)
        {
            DomPut put = new DomPut();
            put.Dom = dom;
            put.Row = row;
            put.Kind = kind;
            put.Head = head;
            put.Names = schema == null ? DomRows.Schema(dom) : schema;
            return put;
        }

        static void PutField(DomPut put, string key, object value)
        {
            if (!Allowed(put.Kind, put.Head, key))
            {
                throw BridgeException.BadField(FieldPath.Join(FieldPath.Side(put.Head), key), "不能设置字段 " + key)
                    .WithHint(FieldPath.WritableHint);
            }
            string alias = Alias(put.Names, put.Kind, key);
            if (Canonical(put.Names, alias) == null)
            {
                throw BridgeException.BadField(FieldPath.Join(FieldPath.Side(put.Head), key), "未知字段 " + key);
            }
            string text = Cell(key, value, FieldPath.Side(put.Head));
            if (text == null)
            {
                return;
            }
            SetCell(put.Dom, put.Row, alias, text, put.Names);
        }

        // 空行上还没有属性，按模板 schema 的大小写写，避免 U8 对不上列名。
        internal static void SetCell(object dom, object row, string name, string value)
        {
            SetCell(dom, row, name, value, null);
        }

        internal static void SetCell(object dom, object row, string name, string value, List<string> schema)
        {
            if (name == null)
            {
                return;
            }
            List<string> names = schema == null ? DomRows.Schema(dom) : schema;
            string found = Canonical(names, name);
            DomRows.Set(dom, row, found == null ? name : found, value, names);
        }

        sealed class DomPut
        {
            public object Dom;
            public object Row;
            public VoucherKind Kind;
            public bool Head;
            public List<string> Names;
        }
    }
}
