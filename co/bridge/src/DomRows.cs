using System;
using System.Collections.Generic;

namespace U8Co
{
    // MSXML 行集。不 Final 调用方还拿着的 z:row：Final 会拆掉同一个 RCW。
    internal static partial class DomRows
    {
        const int AdUseClient = 3;
        const int AdOpenStatic = 3;
        const int AdLockReadOnly = 1;
        const int AdPersistXml = 1;
        const string RsNs = "urn:schemas-microsoft-com:rowset";
        const string RowNs = "#RowsetSchema";

        // 纯 schema 的空白 DOM，经 TplCache 按账套库和指纹缓存；每次拿到的都是新 DOM，可以随意改。
        public static object Blank(object conn, string sql)
        {
            return TplCache.AdoBlank(conn, sql, delegate { return LoadBlank(conn, sql); });
        }

        static object LoadBlank(object conn, string sql)
        {
            object rs = null;
            object dom = null;
            try
            {
                rs = OpenBlank(conn, sql);
                dom = Persist(rs);
                CloseRs(rs);
                rs = null;
                RowsDom.UseXPath(dom);
                object keep = dom;
                dom = null;
                return keep;
            }
            finally
            {
                CloseRs(rs);
                ComUtil.Final(dom);
            }
        }

        public static object AddRow(object dom)
        {
            if (dom == null)
            {
                throw new BridgeException(500, "internal", "DOM 为空");
            }
            RowsDom.UseXPath(dom);
            object data = DataOf(dom);
            object row = null;
            try
            {
                row = ComUtil.Call(dom, "createNode", new object[] { 1, "z:row", RowNs });
                ComUtil.Call(data, "appendChild", new object[] { row });
                object keep = row;
                row = null;
                return keep;
            }
            finally
            {
                ComUtil.Final(data);
                ComUtil.Final(row);
            }
        }

        public static List<object> RowsOf(object dom)
        {
            if (dom == null)
            {
                throw new BridgeException(500, "internal", "DOM 为空");
            }
            RowsDom.UseXPath(dom);
            object list = ComUtil.Call(dom, "selectNodes", new object[] { "//rs:data/z:row" });
            try
            {
                return TakeRows(list);
            }
            finally
            {
                ComUtil.ReleaseOne(list);
            }
        }

        public static List<string> Schema(object dom)
        {
            if (dom == null)
            {
                throw new BridgeException(500, "internal", "DOM 为空");
            }
            RowsDom.UseXPath(dom);
            object list = ComUtil.Call(dom, "selectNodes", new object[] { "//s:AttributeType" });
            try
            {
                return TakeNames(list);
            }
            finally
            {
                ComUtil.ReleaseOne(list);
            }
        }

        public static string Get(object row, string name)
        {
            string actual = FindAttr(row, name);
            if (actual == null)
            {
                return "";
            }
            return Values.Text(ComUtil.Call(row, "getAttribute", new object[] { actual }));
        }

        public static void Set(object dom, object row, string name, string value)
        {
            Set(dom, row, name, value, null);
        }

        // schema 为 null 时，行上没有该属性才读一次 DOM。同一 DOM 多次写入时传入已读名单。
        public static void Set(object dom, object row, string name, string value, List<string> schema)
        {
            if (name == null || name.Length == 0)
            {
                throw new BridgeException(500, "internal", "字段名为空");
            }
            WriteAttr(row, Resolve(dom, row, name, schema), value);
        }

        public static int CopyInto(object dstDom, object dstRow, object srcRow, string skipCsv)
        {
            if (dstDom == null || dstRow == null || srcRow == null)
            {
                throw new BridgeException(500, "internal", "DOM 为空");
            }
            List<string> schema = Schema(dstDom);
            object attrs = ComUtil.Get(srcRow, "attributes");
            try
            {
                return CopyEach(dstDom, dstRow, attrs, schema, skipCsv);
            }
            finally
            {
                ComUtil.ReleaseOne(attrs);
            }
        }

        public static object SingleRowCopy(object bodyDom, object row)
        {
            int index = IndexOf(bodyDom, row);
            if (index < 0)
            {
                throw new BridgeException(500, "internal", "明细行不在单据中");
            }
            object clone = ComUtil.Call(bodyDom, "cloneNode", new object[] { true });
            try
            {
                RowsDom.UseXPath(clone);
                DropOthers(clone, index);
                return clone;
            }
            catch
            {
                ComUtil.Final(clone);
                throw;
            }
        }

        public static void CopyAttrs(object fromRow, object toRow)
        {
            object attrs = ComUtil.Get(fromRow, "attributes");
            try
            {
                int n = Len(attrs);
                for (int i = 0; i < n; i++)
                {
                    object attr = ComUtil.Call(attrs, "item", new object[] { i });
                    try
                    {
                        PutAttr(toRow, attr);
                    }
                    finally
                    {
                        ComUtil.ReleaseOne(attr);
                    }
                }
            }
            finally
            {
                ComUtil.ReleaseOne(attrs);
            }
        }

        public static void RemoveRow(object row)
        {
            if (row == null)
            {
                return;
            }
            object parent = ComUtil.Get(row, "parentNode");
            try
            {
                if (parent == null || parent is DBNull)
                {
                    return;
                }
                ComUtil.Call(parent, "removeChild", new object[] { row });
            }
            finally
            {
                ComUtil.ReleaseOne(parent);
            }
        }

        static object OpenBlank(object conn, string sql)
        {
            object rs = ComUtil.Create("ADODB.Recordset");
            if (rs == null)
            {
                throw new BridgeException(503, "com_unavailable", "ADODB 未注册");
            }
            try
            {
                ComUtil.Set(rs, "CursorLocation", AdUseClient);
                ComUtil.Call(rs, "Open", new object[] { sql, conn, AdOpenStatic, AdLockReadOnly });
                return rs;
            }
            catch
            {
                CloseRs(rs);
                throw;
            }
        }

        static object Persist(object rs)
        {
            object dom = ComUtil.Create("MSXML2.DOMDocument");
            if (dom == null)
            {
                throw new BridgeException(503, "com_unavailable", "MSXML 未注册");
            }
            try
            {
                ComUtil.Set(dom, "async", false);
                ComUtil.Call(rs, "Save", new object[] { dom, AdPersistXml });
                return dom;
            }
            catch
            {
                ComUtil.Final(dom);
                throw;
            }
        }

        static void CloseRs(object rs)
        {
            if (rs == null)
            {
                return;
            }
            try
            {
                ComUtil.Call(rs, "Close", new object[0]);
            }
            catch (Exception)
            {
            }
            ComUtil.Final(rs);
        }

        static object DataOf(object dom)
        {
            object found = ComUtil.Call(dom, "selectSingleNode", new object[] { "//rs:data" });
            if (found != null && !(found is DBNull))
            {
                return found;
            }
            ComUtil.Final(found);
            return CreateData(dom);
        }

        static object CreateData(object dom)
        {
            object data = null;
            object root = null;
            try
            {
                data = ComUtil.Call(dom, "createNode", new object[] { 1, "rs:data", RsNs });
                root = ComUtil.Get(dom, "documentElement");
                if (root == null || root is DBNull)
                {
                    throw new BridgeException(500, "internal", "模板没有字段");
                }
                ComUtil.Call(root, "appendChild", new object[] { data });
                object keep = data;
                data = null;
                return keep;
            }
            finally
            {
                ComUtil.ReleaseOne(root);
                ComUtil.Final(data);
            }
        }

        static List<object> TakeRows(object list)
        {
            List<object> rows = new List<object>();
            int n = Len(list);
            for (int i = 0; i < n; i++)
            {
                rows.Add(ComUtil.Call(list, "item", new object[] { i }));
            }
            return rows;
        }

        static List<string> TakeNames(object list)
        {
            List<string> names = new List<string>();
            int n = Len(list);
            for (int i = 0; i < n; i++)
            {
                object node = ComUtil.Call(list, "item", new object[] { i });
                try
                {
                    string name = Values.Text(ComUtil.Call(node, "getAttribute", new object[] { "name" })).Trim();
                    if (name.Length > 0)
                    {
                        names.Add(name);
                    }
                }
                finally
                {
                    ComUtil.ReleaseOne(node);
                }
            }
            return names;
        }

    }
}
