using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 现存量不足的 domMsg，以及库存单据的响应体。
    internal static class StockMsg
    {
        public static string Shortage(object dom)
        {
            if (dom == null)
            {
                return "";
            }
            object outs = null;
            try
            {
                Rows.UseXPath(dom);
                outs = ComUtil.Call(dom, "selectNodes", new object[]
                {
                    "//*[local-name()='zeroout']"
                });
                return FromOuts(outs);
            }
            catch (Exception)
            {
                return "";
            }
            finally
            {
                ComUtil.ReleaseOne(outs);
            }
        }

        public static ApiResult Loaded(VoucherKind kind, int id, Dictionary<string, object> head,
            List<Dictionary<string, object>> lines)
        {
            Dictionary<string, object> body = Base(kind, id);
            body["code"] = Col(head, kind.CodeColumn);
            body["head"] = head;
            body["lines"] = lines;
            body["state"] = State(head, kind);
            return ApiResult.Ok(body);
        }

        public static ApiResult Verified(WorkItem item, VoucherKind kind, int id, string action,
            Dictionary<string, object> row, List<Dictionary<string, object>> generated)
        {
            Dictionary<string, object> body = Base(kind, id);
            body["acc"] = item.Acc ?? "";
            body["action"] = action;
            body["verified_by"] = Col(row, kind.VerifierColumn);
            body["verified_at"] = Col(row, kind.VerifyDateColumn);
            if (generated != null)
            {
                body["generated"] = generated;
            }
            return ApiResult.Ok(body);
        }

        public static ApiResult Created(VoucherKind kind, int id, Dictionary<string, object> row)
        {
            Dictionary<string, object> body = Base(kind, id);
            body["code"] = Col(row, kind.CodeColumn);
            body["state"] = State(row, kind);
            return ApiResult.Ok(body);
        }

        public static ApiResult Gone(VoucherKind kind, int id)
        {
            Dictionary<string, object> body = Base(kind, id);
            body["deleted"] = true;
            return ApiResult.Ok(body);
        }

        public static string Col(Dictionary<string, object> row, string name)
        {
            if (row == null || name == null)
            {
                return "";
            }
            foreach (KeyValuePair<string, object> kv in row)
            {
                if (string.Equals(kv.Key, name, StringComparison.OrdinalIgnoreCase))
                {
                    return Values.Text(kv.Value).Trim();
                }
            }
            return "";
        }

        static string FromOuts(object outs)
        {
            if (outs == null)
            {
                return "";
            }
            int count = Convert.ToInt32(ComUtil.Get(outs, "length"));
            StringBuilder buf = new StringBuilder();
            int left = 10;
            for (int i = 0; i < count && left > 0; i++)
            {
                object node = null;
                try
                {
                    node = ComUtil.Call(outs, "item", new object[] { i });
                    left = AppendOut(buf, node, left);
                }
                finally
                {
                    ComUtil.ReleaseOne(node);
                }
            }
            return buf.ToString();
        }

        static int AppendOut(StringBuilder buf, object zero, int left)
        {
            object caption = null;
            object rows = null;
            try
            {
                caption = ComUtil.Call(zero, "selectSingleNode", new object[]
                {
                    ".//*[local-name()='zerocaption']"
                });
                string onHand = Column(caption, "现存");
                string avail = Column(caption, "可用");
                rows = ComUtil.Call(zero, "selectNodes", new object[]
                {
                    ".//*[local-name()='row']"
                });
                return AppendRows(buf, rows, onHand, avail, left);
            }
            finally
            {
                ComUtil.ReleaseOne(rows);
                ComUtil.ReleaseOne(caption);
            }
        }

        static int AppendRows(StringBuilder buf, object rows, string onHand, string avail, int left)
        {
            if (rows == null)
            {
                return left;
            }
            int count = Convert.ToInt32(ComUtil.Get(rows, "length"));
            for (int i = 0; i < count && left > 0; i++)
            {
                object row = null;
                try
                {
                    row = ComUtil.Call(rows, "item", new object[] { i });
                    if (buf.Length > 0)
                    {
                        buf.Append('；');
                    }
                    buf.Append(Line(row, onHand, avail));
                    left = left - 1;
                }
                finally
                {
                    ComUtil.ReleaseOne(row);
                }
            }
            return left;
        }

        // zerocaption 的属性名是列名，属性值是中文标题。标题里带「现存」或「可用」即可。
        static string Column(object caption, string label)
        {
            if (caption == null)
            {
                return "";
            }
            object attrs = null;
            try
            {
                attrs = ComUtil.Get(caption, "attributes");
                if (attrs == null)
                {
                    return "";
                }
                int count = Convert.ToInt32(ComUtil.Get(attrs, "length"));
                for (int i = 0; i < count; i++)
                {
                    object attr = null;
                    try
                    {
                        attr = ComUtil.Call(attrs, "item", new object[] { i });
                        string attrName = Values.Text(ComUtil.Get(attr, "nodeName"));
                        string text = Attr(caption, attrName);
                        if (text.Length > 0 && text.IndexOf(label, StringComparison.Ordinal) >= 0)
                        {
                            return attrName;
                        }
                    }
                    finally
                    {
                        ComUtil.ReleaseOne(attr);
                    }
                }
                return "";
            }
            catch (Exception)
            {
                return "";
            }
            finally
            {
                ComUtil.ReleaseOne(attrs);
            }
        }

        static string Line(object row, string onHand, string avail)
        {
            return "存货 " + Named(row, "cinvcode")
                + " " + Named(row, "cinvname")
                + " 现存 " + Named(row, onHand)
                + " 可用 " + Named(row, avail)
                + " 需要 " + Named(row, "iquantity");
        }

        static string Named(object node, string want)
        {
            if (node == null || want == null || want.Length == 0)
            {
                return "";
            }
            string direct = Attr(node, want);
            if (direct.Length > 0)
            {
                return direct;
            }
            return Scan(node, want);
        }

        static string Scan(object node, string want)
        {
            object attrs = null;
            try
            {
                attrs = ComUtil.Get(node, "attributes");
                if (attrs == null)
                {
                    return "";
                }
                int count = Convert.ToInt32(ComUtil.Get(attrs, "length"));
                for (int i = 0; i < count; i++)
                {
                    object attr = null;
                    try
                    {
                        attr = ComUtil.Call(attrs, "item", new object[] { i });
                        string attrName = Values.Text(ComUtil.Get(attr, "nodeName"));
                        if (string.Equals(attrName, want, StringComparison.OrdinalIgnoreCase))
                        {
                            return Attr(node, attrName);
                        }
                    }
                    finally
                    {
                        ComUtil.ReleaseOne(attr);
                    }
                }
                return "";
            }
            catch (Exception)
            {
                return "";
            }
            finally
            {
                ComUtil.ReleaseOne(attrs);
            }
        }

        static string Attr(object node, string name)
        {
            try
            {
                return Values.Text(ComUtil.Call(node, "getAttribute", new object[] { name })).Trim();
            }
            catch (Exception)
            {
                return "";
            }
        }

        static Dictionary<string, object> Base(VoucherKind kind, int id)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name ?? "";
            body["id"] = id;
            return body;
        }

        static Dictionary<string, object> State(Dictionary<string, object> row, VoucherKind kind)
        {
            string who = Col(row, kind.VerifierColumn);
            Dictionary<string, object> state = new Dictionary<string, object>();
            state["verified"] = who.Length > 0;
            state["verifier"] = who;
            state["verified_at"] = Col(row, kind.VerifyDateColumn);
            return state;
        }
    }
}
