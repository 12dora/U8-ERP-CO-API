using System;

namespace U8Co
{
    internal static class ProbeAttr
    {
        public static void Write(object dom, object row, string name, string value)
        {
            if (name == null || name.Length == 0)
            {
                throw new BridgeException(400, "bad_request", "字段名为空");
            }
            ComUtil.Call(row, "setAttribute", new object[] { Resolve(dom, row, name), value });
        }

        public static void Remove(object row, string name)
        {
            string actual = Find(row, name);
            if (actual == null)
            {
                return;
            }
            ComUtil.Call(row, "removeAttribute", new object[] { actual });
        }

        public static string Get(object row, string name)
        {
            string actual = Find(row, name);
            if (actual == null)
            {
                return "";
            }
            return Values.Text(ComUtil.Call(row, "getAttribute", new object[] { actual }));
        }

        public static string Name(object attr)
        {
            string name = Values.Text(ComUtil.Get(attr, "baseName")).Trim();
            if (name.Length > 0)
            {
                return name;
            }
            return Values.Text(ComUtil.Get(attr, "nodeName")).Trim();
        }

        public static bool Skip(string name)
        {
            string lower = name.ToLowerInvariant();
            if (lower.StartsWith("xmlns", StringComparison.Ordinal))
            {
                return true;
            }
            if (lower.IndexOf("password", StringComparison.Ordinal) >= 0)
            {
                return true;
            }
            return lower.IndexOf("pwd", StringComparison.Ordinal) >= 0;
        }

        static string Resolve(object dom, object row, string name)
        {
            string actual = Find(row, name);
            if (actual != null)
            {
                return actual;
            }
            string schema = Schema(dom, name);
            if (schema != null)
            {
                return schema;
            }
            return name;
        }

        static string Find(object row, string name)
        {
            object attrs = ComUtil.Get(row, "attributes");
            try
            {
                return Scan(attrs, name);
            }
            finally
            {
                ComUtil.ReleaseOne(attrs);
            }
        }

        static string Scan(object attrs, string name)
        {
            int count = Len(attrs);
            for (int i = 0; i < count; i++)
            {
                object attr = ComUtil.Call(attrs, "item", new object[] { i });
                string attrName = Name(attr);
                ComUtil.ReleaseOne(attr);
                if (string.Equals(attrName, name, StringComparison.OrdinalIgnoreCase))
                {
                    return attrName;
                }
            }
            return null;
        }

        static string Schema(object dom, string name)
        {
            ProbeDom.UseXPath(dom);
            object list = ComUtil.Call(dom, "selectNodes", new object[] { "//s:AttributeType" });
            try
            {
                return Match(list, name);
            }
            finally
            {
                ComUtil.ReleaseOne(list);
            }
        }

        static string Match(object list, string name)
        {
            int count = Len(list);
            for (int i = 0; i < count; i++)
            {
                object node = ComUtil.Call(list, "item", new object[] { i });
                string found = OneName(node);
                ComUtil.ReleaseOne(node);
                if (string.Equals(found, name, StringComparison.OrdinalIgnoreCase))
                {
                    return found;
                }
            }
            return null;
        }

        static string OneName(object node)
        {
            return Values.Text(ComUtil.Call(node, "getAttribute", new object[] { "name" })).Trim();
        }

        static int Len(object list)
        {
            if (list == null || list is DBNull)
            {
                return 0;
            }
            return Convert.ToInt32(ComUtil.Get(list, "length"));
        }
    }
}
