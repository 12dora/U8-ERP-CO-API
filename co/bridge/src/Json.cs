using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Text;
using System.Web.Script.Serialization;

namespace U8Co
{
    internal static class Json
    {
        public static Dictionary<string, object> Parse(byte[] body)
        {
            if (body == null || body.Length == 0)
            {
                throw new BridgeException(400, "bad_request", "请求体为空");
            }
            string text = Encoding.UTF8.GetString(body);
            object obj;
            try
            {
                obj = new JavaScriptSerializer().DeserializeObject(text);
            }
            catch (Exception)
            {
                throw new BridgeException(400, "bad_request", "请求体不是 JSON");
            }
            Dictionary<string, object> map = obj as Dictionary<string, object>;
            if (map == null)
            {
                throw new BridgeException(400, "bad_request", "请求体必须是 JSON 对象");
            }
            return map;
        }

        public const int ResponseLimit = 8 * 1024 * 1024;

        public static void Write(HttpListenerResponse res, int status, Dictionary<string, object> body)
        {
            JavaScriptSerializer ser = new JavaScriptSerializer();
            ser.MaxJsonLength = ResponseLimit;
            WriteRaw(res, status, ser.Serialize(body));
        }

        public static void WriteRaw(HttpListenerResponse res, int status, string json)
        {
            byte[] buf = Encoding.UTF8.GetBytes(json);
            res.StatusCode = status;
            res.ContentType = "application/json; charset=utf-8";
            res.ContentLength64 = buf.Length;
            res.OutputStream.Write(buf, 0, buf.Length);
            res.OutputStream.Close();
        }

        public static byte[] ReadBody(HttpListenerRequest req)
        {
            if (req.ContentLength64 > BodyLimit)
            {
                throw new BridgeException(400, "bad_request", "请求体超过 64 KiB");
            }
            using (Stream input = req.InputStream)
            {
                MemoryStream mem = new MemoryStream();
                byte[] buf = new byte[4096];
                int total = 0;
                while (true)
                {
                    int n = input.Read(buf, 0, buf.Length);
                    if (n <= 0)
                    {
                        break;
                    }
                    total += n;
                    if (total > BodyLimit)
                    {
                        throw new BridgeException(400, "bad_request", "请求体超过 64 KiB");
                    }
                    mem.Write(buf, 0, n);
                }
                return mem.ToArray();
            }
        }

        public const int BodyLimit = 64 * 1024;

        public static Dictionary<string, object> RequireObject(Dictionary<string, object> body, string key, VoucherKind kind)
        {
            if (!body.ContainsKey(key) || !(body[key] is Dictionary<string, object>))
            {
                throw BridgeException.BadField(key, key + " 必须是 JSON 对象");
            }
            Dictionary<string, object> map = (Dictionary<string, object>)body[key];
            CheckMap(kind, map, key);
            return map;
        }

        public static object[] RequireLines(Dictionary<string, object> body, VoucherKind kind)
        {
            if (!body.ContainsKey("lines"))
            {
                throw BridgeException.BadField("lines", "缺少字段 lines");
            }
            ArrayList list = AsList(body["lines"]);
            if (list == null)
            {
                throw BridgeException.BadField("lines", "lines 必须是 JSON 数组");
            }
            if (list.Count < 1 || list.Count > 200)
            {
                throw BridgeException.BadField("lines", "lines 必须是 1 到 200 行");
            }
            object[] lines = new object[list.Count];
            for (int i = 0; i < list.Count; i++)
            {
                Dictionary<string, object> row = list[i] as Dictionary<string, object>;
                if (row == null)
                {
                    throw BridgeException.BadField(FieldPath.Item("lines", i), "lines 的每一行必须是对象");
                }
                CheckMap(kind, row, FieldPath.Item("lines", i));
                lines[i] = row;
            }
            return lines;
        }

        public static Dictionary<string, object> OptionalHead(Dictionary<string, object> body, VoucherKind kind)
        {
            if (body == null || !body.ContainsKey("head"))
            {
                return new Dictionary<string, object>();
            }
            return RequireObject(body, "head", kind);
        }

        public static object[] UpdateLines(Dictionary<string, object> body, VoucherKind kind)
        {
            ArrayList list = NeedList(body, "lines", false, 0, 200, "lines 不能超过 200 行");
            object[] lines = new object[list.Count];
            HashSet<int> seen = new HashSet<int>();
            for (int i = 0; i < list.Count; i++)
            {
                lines[i] = OneUpdate(list[i], kind, seen, FieldPath.Item("lines", i));
            }
            return lines;
        }

        // whole：销售出库没带 lines，按整张发货单生成，不读明细。
        public static object[] GenerateLines(Dictionary<string, object> body, VoucherKind kind, bool whole)
        {
            if (whole)
            {
                return new object[0];
            }
            ArrayList list = NeedList(body, "lines", true, 1, 200, "lines 必须是 1 到 200 行");
            object[] lines = new object[list.Count];
            HashSet<int> seen = new HashSet<int>();
            for (int i = 0; i < list.Count; i++)
            {
                lines[i] = OneGenerate(list[i], kind, seen, FieldPath.Item("lines", i));
            }
            return lines;
        }

        public static int[] IdList(Dictionary<string, object> body, string key)
        {
            if (body == null || !body.ContainsKey(key))
            {
                return null;
            }
            ArrayList list = AsList(body[key]);
            if (list == null)
            {
                throw BridgeException.BadField(key, key + " 必须是 JSON 数组");
            }
            if (list.Count < 1 || list.Count > 200)
            {
                throw BridgeException.BadField(key, key + " 必须是 1 到 200 个");
            }
            int[] ids = new int[list.Count];
            HashSet<int> seen = new HashSet<int>();
            for (int i = 0; i < list.Count; i++)
            {
                int id = Positive(list[i], key, FieldPath.Item(key, i));
                if (!seen.Add(id))
                {
                    throw BridgeException.BadField(FieldPath.Item(key, i), key + " 有重复");
                }
                ids[i] = id;
            }
            return ids;
        }

        static ArrayList NeedList(
            Dictionary<string, object> body, string key, bool required, int min, int max, string rangeMessage)
        {
            if (body == null || !body.ContainsKey(key))
            {
                if (required)
                {
                    throw BridgeException.BadField(key, "缺少字段 " + key);
                }
                return new ArrayList();
            }
            ArrayList list = AsList(body[key]);
            if (list == null)
            {
                throw BridgeException.BadField(key, key + " 必须是 JSON 数组");
            }
            if (list.Count < min || list.Count > max)
            {
                throw BridgeException.BadField(key, rangeMessage);
            }
            return list;
        }

        static Dictionary<string, object> OneUpdate(object raw, VoucherKind kind, HashSet<int> seen, string at)
        {
            Dictionary<string, object> row = AsRow(raw, at);
            string op = OpOf(row, at);
            // 物料清单按 sort_seq 定位行（BomReq 在登录前细查），不用 line_id。
            if (kind != null && kind.Name == BomRoutes.KindName)
            {
                CheckMap(kind, Except(row, "op", "sort_seq"), at);
                return row;
            }
            if (op == "add")
            {
                if (row.ContainsKey("line_id"))
                {
                    throw BridgeException.BadField(FieldPath.Join(at, "line_id"), "新增行不能带 line_id");
                }
            }
            else
            {
                NoteLine(row, seen, at);
            }
            Dictionary<string, object> rest = Except(row, "op", "line_id");
            if (op == "delete" && rest.Count > 0)
            {
                throw BridgeException.BadField(at, "删除行只能带 line_id");
            }
            if (op == "update" && rest.Count == 0)
            {
                throw BridgeException.BadField(at, "修改行至少要改一个字段");
            }
            CheckMap(kind, rest, at);
            return row;
        }

        static Dictionary<string, object> OneGenerate(object raw, VoucherKind kind, HashSet<int> seen, string at)
        {
            Dictionary<string, object> row = AsRow(raw, at);
            if (!row.ContainsKey("source_line_id"))
            {
                throw BridgeException.BadField(FieldPath.Join(at, "source_line_id"), "缺少 source_line_id");
            }
            int id = Positive(row["source_line_id"], "source_line_id", FieldPath.Join(at, "source_line_id"));
            // 销售出库同一发货行可按批号 / 货位拆成多行，重复由 StockGen.ParseOut 按（行、批号、货位）判。
            // 不良品处理单的来源行就是检验单本身，多种处置各占一行，source_line_id 重复（QmRejReq）。
            if (!seen.Add(id) && (kind == null || (kind.Name != "sale_out" && !QmRejSpec.Handles(kind))))
            {
                throw BridgeException.BadField(FieldPath.Join(at, "source_line_id"), "来源明细重复");
            }
            if (!row.ContainsKey("quantity"))
            {
                throw BridgeException.BadField(FieldPath.Join(at, "quantity"), "缺少 quantity");
            }
            RequireQty(row["quantity"], FieldPath.Join(at, "quantity"));
            CheckMap(kind, Except(row, "source_line_id", "quantity"), at);
            return row;
        }

        static Dictionary<string, object> AsRow(object raw, string at)
        {
            Dictionary<string, object> row = raw as Dictionary<string, object>;
            if (row == null)
            {
                throw BridgeException.BadField(at, "lines 的每一行必须是对象");
            }
            return row;
        }

        static string OpOf(Dictionary<string, object> row, string at)
        {
            object value;
            if (!row.TryGetValue("op", out value) || !(value is string))
            {
                throw BridgeException.BadField(FieldPath.Join(at, "op"), "op 只能是 add、update 或 delete");
            }
            string op = (string)value;
            if (op != "add" && op != "update" && op != "delete")
            {
                throw BridgeException.BadField(FieldPath.Join(at, "op"), "op 只能是 add、update 或 delete");
            }
            return op;
        }

        static void NoteLine(Dictionary<string, object> row, HashSet<int> seen, string at)
        {
            if (!row.ContainsKey("line_id"))
            {
                throw BridgeException.BadField(FieldPath.Join(at, "line_id"), "缺少 line_id");
            }
            int id = Positive(row["line_id"], "line_id", FieldPath.Join(at, "line_id"));
            if (!seen.Add(id))
            {
                throw BridgeException.BadField(FieldPath.Join(at, "line_id"), "明细行重复");
            }
        }

        static Dictionary<string, object> Except(Dictionary<string, object> row, string skipA, string skipB)
        {
            Dictionary<string, object> rest = new Dictionary<string, object>();
            foreach (string key in row.Keys)
            {
                if (key == skipA || key == skipB)
                {
                    continue;
                }
                rest[key] = row[key];
            }
            return rest;
        }

        static int Positive(object value, string label, string field)
        {
            if (value is int)
            {
                return Range((int)value, label, field);
            }
            if (value is long)
            {
                long number = (long)value;
                if (number < 1 || number > int.MaxValue)
                {
                    throw BridgeException.BadField(field, label + " 无效");
                }
                return (int)number;
            }
            throw BridgeException.BadField(field, label + " 必须是整数");
        }

        static int Range(int id, string label, string field)
        {
            if (id < 1)
            {
                throw BridgeException.BadField(field, label + " 无效");
            }
            return id;
        }

        const decimal QtyMax = 1000000000000m;

        static void RequireQty(object value, string field)
        {
            decimal qty;
            if (!TryQty(value, out qty) || qty <= 0 || qty > QtyMax)
            {
                throw BridgeException.BadField(field, "quantity 必须是大于 0 且不超过 1000000000000 的数");
            }
        }

        static bool TryQty(object value, out decimal qty)
        {
            qty = 0;
            try
            {
                if (value is int)
                {
                    qty = (int)value;
                    return true;
                }
                if (value is long)
                {
                    qty = (long)value;
                    return true;
                }
                if (value is decimal)
                {
                    qty = (decimal)value;
                    return true;
                }
                if (value is double)
                {
                    qty = (decimal)(double)value;
                    return true;
                }
            }
            catch (Exception)
            {
                return false;
            }
            return false;
        }

        static ArrayList AsList(object value)
        {
            ArrayList list = value as ArrayList;
            if (list != null)
            {
                return list;
            }
            object[] arr = value as object[];
            if (arr == null)
            {
                return null;
            }
            return new ArrayList(arr);
        }

        static void CheckMap(VoucherKind kind, Dictionary<string, object> row, string at)
        {
            foreach (string key in row.Keys)
            {
                if (Blocked(kind, key))
                {
                    throw BridgeException.BadField(FieldPath.Join(at, key), "不能填写字段 " + key);
                }
                if (!Plain(row[key]) && !QmReq.ListField(kind, key, row[key]))
                {
                    throw BridgeException.BadField(FieldPath.Join(at, key), "字段 " + key + " 的值只能是字符串、数字或布尔");
                }
            }
        }

        static bool Plain(object value)
        {
            if (value is string || value is bool || value is int || value is long)
            {
                return true;
            }
            return value is decimal || value is double;
        }

        // 表体主键和来源行 id：id、autoid、isosid（iSOsID）、idlsid、iposid、cbsysbarcode。
        // 销售订单行的关闭人与累计数量：cscloser（cSCloser）、ifhquantity、ikpquantity、foutquantity。
        // 不拦行号 irowno，不拦单价 fsaleprice。
        static readonly string[] BlockedNames = new string[]
        {
            "id", "autoid", "poid", "dlid", "isosid", "idlsid", "iposid", "cbsysbarcode",
            "code", "editprop", "ufts", "maker", "cmaker",
            "verifier", "cverifier", "chandler",
            "cscloser", "ifhquantity", "ikpquantity", "foutquantity"
        };

        // 给 meta 路由用：全局拦截名单的副本。
        internal static string[] BlockedList()
        {
            return (string[])BlockedNames.Clone();
        }

        static bool Blocked(VoucherKind kind, string key)
        {
            string name = key == null ? "" : key.ToLowerInvariant();
            if (name.Length == 0 || Listed(name))
            {
                return true;
            }
            if (kind == null)
            {
                return false;
            }
            if (Eq(name, kind.IdColumn) || Eq(name, kind.CodeColumn))
            {
                return true;
            }
            return Eq(name, kind.VerifierColumn) || Eq(name, kind.VerifyDateColumn);
        }

        static bool Listed(string name)
        {
            for (int i = 0; i < BlockedNames.Length; i++)
            {
                if (name == BlockedNames[i])
                {
                    return true;
                }
            }
            return false;
        }

        static bool Eq(string lower, string column)
        {
            if (column == null || column.Length == 0)
            {
                return false;
            }
            return lower == column.ToLowerInvariant();
        }
    }
}
