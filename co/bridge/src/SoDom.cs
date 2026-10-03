using System;
using System.Collections.Generic;

namespace U8Co
{
    // 销售订单模板 DOM。行克隆在 SoFill.cs。
    internal static partial class SoDom
    {
        const string Ns = "xmlns:s='uuid:BDC6E3F0-6DA3-11d1-A2A3-00AA00C14882' "
            + "xmlns:dt='uuid:C2F41010-65B3-11d1-A29F-00AA00C14882' "
            + "xmlns:rs='urn:schemas-microsoft-com:rowset' xmlns:z='#RowsetSchema'";

        public static void Apply(object conn, object[] doms, Dictionary<string, object> head, object[] lines)
        {
            Dictionary<string, string> headFields = FieldMap(doms[0]);
            Dictionary<string, string> bodyFields = FieldMap(doms[1]);
            if (headFields.Count == 0 || bodyFields.Count == 0)
            {
                throw new BridgeException(500, "internal", "模板没有字段");
            }
            Dictionary<string, string> headRow = SoFields.Map(head, headFields, true, "表头必须是对象");
            List<Dictionary<string, string>> headRows = new List<Dictionary<string, string>>();
            SoUnits.FillHead(conn, headFields, headRow, head);
            headRows.Add(headRow);
            Fill(doms[0], headRows);
            List<Dictionary<string, string>> body = SoFields.Lines(lines, bodyFields);
            SoUnits.Fill(conn, bodyFields, body, headRow);
            Fill(doms[1], body);
        }

        static Dictionary<string, string> FieldMap(object dom)
        {
            Dictionary<string, string> map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            Prepare(dom);
            object list = ComUtil.Call(dom, "selectNodes", new object[] { "//s:AttributeType" });
            try
            {
                TakeNames(map, list);
            }
            finally
            {
                ComUtil.Final(list);
            }
            object rows = ComUtil.Call(dom, "selectNodes", new object[] { "//z:row" });
            try
            {
                TakeAttrs(map, rows);
            }
            finally
            {
                ComUtil.Final(rows);
            }
            return map;
        }

        static void TakeNames(Dictionary<string, string> map, object list)
        {
            int n = Len(list);
            for (int i = 0; i < n; i++)
            {
                object node = ComUtil.Call(list, "item", new object[] { i });
                try
                {
                    Remember(map, Values.Text(ComUtil.Call(node, "getAttribute", new object[] { "name" })));
                }
                finally
                {
                    ComUtil.Final(node);
                }
            }
        }

        static void TakeAttrs(Dictionary<string, string> map, object list)
        {
            if (Len(list) == 0)
            {
                return;
            }
            object row = ComUtil.Call(list, "item", new object[] { 0 });
            object attrs = null;
            try
            {
                attrs = ComUtil.Get(row, "attributes");
                int n = Len(attrs);
                for (int i = 0; i < n; i++)
                {
                    object attr = ComUtil.Call(attrs, "item", new object[] { i });
                    try
                    {
                        Remember(map, Values.Text(ComUtil.Get(attr, "baseName")));
                    }
                    finally
                    {
                        ComUtil.Final(attr);
                    }
                }
            }
            finally
            {
                ComUtil.ReleaseOne(attrs);
                ComUtil.Final(row);
            }
        }

        static void Remember(Dictionary<string, string> map, string name)
        {
            string text = name == null ? "" : name.Trim();
            if (text.Length > 0 && !map.ContainsKey(text))
            {
                map[text] = text;
            }
        }

        public static void SetAttr(object dom, string name, string value)
        {
            Dictionary<string, string> map = FieldMap(dom);
            string canon;
            if (map.TryGetValue(name, out canon))
            {
                name = canon;
            }
            object row = FindRow(dom);
            if (row == null)
            {
                throw new BridgeException(500, "internal", "模板没有字段");
            }
            try
            {
                ComUtil.Call(row, "setAttribute", new object[] { Actual(row, name), value });
            }
            finally
            {
                ComUtil.Final(row);
            }
        }

        // MSXML 属性名区分大小写：先按行集 schema 的写法（SetAttr 已做），再按行上已有属性，最后用给定写法。
        static string Actual(object row, string name)
        {
            object attrs = ComUtil.Get(row, "attributes");
            try
            {
                int count = Convert.ToInt32(ComUtil.Get(attrs, "length"));
                for (int i = 0; i < count; i++)
                {
                    object attr = ComUtil.Call(attrs, "item", new object[] { i });
                    string attrName = Values.Text(ComUtil.Get(attr, "nodeName"));
                    ComUtil.ReleaseOne(attr);
                    if (string.Equals(attrName, name, StringComparison.OrdinalIgnoreCase))
                    {
                        return attrName;
                    }
                }
                return name;
            }
            finally
            {
                ComUtil.Final(attrs);
            }
        }

        public static string Attr(object dom, string name)
        {
            object row = FindRow(dom);
            if (row == null)
            {
                return "";
            }
            try
            {
                return Values.Text(ComUtil.Call(row, "getAttribute", new object[] { Actual(row, name) })).Trim();
            }
            finally
            {
                ComUtil.Final(row);
            }
        }

        static object FindRow(object dom)
        {
            Prepare(dom);
            object row = ComUtil.Call(dom, "selectSingleNode", new object[] { "//z:row" });
            if (row == null || row is DBNull)
            {
                return null;
            }
            return row;
        }

        static void Prepare(object dom)
        {
            ComUtil.Call(dom, "setProperty", new object[] { "SelectionNamespaces", Ns });
        }

        static int Len(object list)
        {
            return Convert.ToInt32(ComUtil.Get(list, "length"));
        }

        const string HeadBlank = "select saleorderq.*, '' as editprop from SaleOrderQ saleorderq where 1=2";
        const string BodyBlank = "select saleordersq.*, '' as editprop from SaleOrderSQ saleordersq where 1=2";

        public static void Templates(object co, object conn, string card, WorkItem item, object[] doms)
        {
            doms[0] = Rows.NewDom();
            doms[1] = Rows.NewDom();
            if (DefaultDom(co, conn, card, item, doms) && Fields(doms[0]) > 0 && Fields(doms[1]) > 0)
            {
                return;
            }
            ComUtil.Final(doms[0]);
            doms[0] = null;
            ComUtil.Final(doms[1]);
            doms[1] = null;
            // SaleOrderQ / SaleOrderSQ 只是销售订单卡片 17 的空模板。发货单、发票不能拿它充数。
            if (card != "17")
            {
                throw new BridgeException(409, "u8_rejected", "U8 没有返回单据模板");
            }
            doms[0] = Blank(conn, HeadBlank);
            doms[1] = Blank(conn, BodyBlank);
            if (Fields(doms[0]) == 0 || Fields(doms[1]) == 0)
            {
                throw new BridgeException(500, "internal", "模板没有字段");
            }
        }

        static bool DefaultDom(object co, object conn, string card, WorkItem item, object[] doms)
        {
            object[] args = new object[] { conn, card, doms[0], doms[1] };
            try
            {
                object ret = ComUtil.CallRef(co, "GetDefaultVoucherDom", args, new int[] { 0, 1, 2, 3 });
                CoRows.ReleaseIfNew(conn, args[0]);
                CoRows.Swap(doms, 0, args[2]);
                CoRows.Swap(doms, 1, args[3]);
                bool ok = Values.Flag(ret);
                CoRows.Note(item, "GetDefaultVoucherDom " + (ok ? "1" : "0"));
                return ok;
            }
            catch (Exception ex)
            {
                CoRows.Note(item, "GetDefaultVoucherDom " + ex.Message);
                CoRows.ReleaseIfNew(conn, args[0]);
                if (!object.ReferenceEquals(args[2], doms[0]))
                {
                    ComUtil.Final(args[2]);
                }
                if (!object.ReferenceEquals(args[3], doms[1]))
                {
                    ComUtil.Final(args[3]);
                }
                return false;
            }
        }

        static object Blank(object conn, string sql)
        {
            const int AdUseClient = 3;
            const int AdOpenStatic = 3;
            const int AdLockReadOnly = 1;
            const int AdPersistXml = 1;
            object rs = ComUtil.Create("ADODB.Recordset");
            if (rs == null)
            {
                throw new BridgeException(503, "com_unavailable", "ADODB 未注册");
            }
            object dom = null;
            try
            {
                ComUtil.Set(rs, "CursorLocation", AdUseClient);
                ComUtil.Call(rs, "Open", new object[] { sql, conn, AdOpenStatic, AdLockReadOnly, -1 });
                dom = Rows.NewDom();
                ComUtil.Call(rs, "Save", new object[] { dom, AdPersistXml });
                object keep = dom;
                dom = null;
                return keep;
            }
            finally
            {
                ComUtil.Final(dom);
                CloseRs(rs);
            }
        }

        static void CloseRs(object rs)
        {
            try
            {
                ComUtil.Call(rs, "Close", new object[0]);
            }
            catch (Exception)
            {
            }
            ComUtil.Final(rs);
        }

        static int Fields(object dom)
        {
            try
            {
                return FieldMap(dom).Count;
            }
            catch (Exception)
            {
                return 0;
            }
        }
    }
}
