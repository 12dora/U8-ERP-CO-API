using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using System.Web.Script.Serialization;

namespace U8Co
{
    // 预演响应的 docs：在给定连接上（回滚模式是同一事务里、回滚之前）读受影响单据的表头、表体。
    // 先是请求本身的 type/id（生单是来源单据），再按登记顺序是 Created / Touched，去重，最多 MaxDocs 张。
    // 表名、列名只来自 VoucherKind 并加方括号；id 走参数。列名转小写，值的写法见 DryRunValue。
    internal static class DryRunPreview
    {
        public const int MaxDocs = 10;
        public const int MaxLines = 200;
        public const long MaxBytes = 4L * 1024 * 1024;

        public static List<object> Docs(object conn, DryRunState s)
        {
            List<VoucherKind> kinds = new List<VoucherKind>();
            List<int> ids = new List<int>();
            Own(s, kinds, ids);
            for (int i = 0; i < s.RefKinds.Count; i++)
            {
                AddRef(kinds, ids, s.RefKinds[i], s.RefIds[i]);
            }
            List<object> docs = new List<object>();
            for (int i = 0; i < kinds.Count && i < MaxDocs; i++)
            {
                docs.Add(Doc(conn, kinds[i], ids[i]));
            }
            return docs;
        }

        // 请求本身的单据：带 id 的路由按 type/id；生单的 id 是来源单据，类型取 Source。新增没有。
        static void Own(DryRunState s, List<VoucherKind> kinds, List<int> ids)
        {
            WorkItem item = s.Ctx == null ? null : s.Ctx.Item;
            if (item == null || !item.HasId || item.Id <= 0)
            {
                return;
            }
            bool generate = item.Path == "/u8co/v1/vouchers/generate";
            AddRef(kinds, ids, generate ? item.Source : item.Type, item.Id);
        }

        static void AddRef(List<VoucherKind> kinds, List<int> ids, VoucherKind kind, int id)
        {
            if (kind == null || id <= 0)
            {
                return;
            }
            for (int i = 0; i < ids.Count; i++)
            {
                if (ids[i] == id && kinds[i].Name == kind.Name)
                {
                    return;
                }
            }
            kinds.Add(kind);
            ids.Add(id);
        }

        public static Dictionary<string, object> Doc(object conn, VoucherKind kind, int id)
        {
            Dictionary<string, object> doc = new Dictionary<string, object>();
            doc["type"] = kind.Name;
            doc["id"] = id;
            if (Blank(kind.HeadTable) || Blank(kind.IdColumn))
            {
                doc["state"] = "exists";
                return doc;
            }
            List<Dictionary<string, object>> heads = DryRunRows.Read(conn, HeadSql(kind), id, 1);
            if (heads.Count == 0)
            {
                doc["state"] = "deleted";
                return doc;
            }
            Dictionary<string, object> head = heads[0];
            object code;
            if (!Blank(kind.CodeColumn) && head.TryGetValue(kind.CodeColumn.ToLowerInvariant(), out code))
            {
                doc["code"] = code;
            }
            doc["state"] = "exists";
            doc["head"] = head;
            if (!Blank(kind.BodyTable) && !Blank(kind.BodyFk))
            {
                Lines(conn, kind, id, doc);
            }
            return doc;
        }

        static void Lines(object conn, VoucherKind kind, int id, Dictionary<string, object> doc)
        {
            List<Dictionary<string, object>> lines = DryRunRows.Read(conn, LinesSql(kind, false), id, MaxLines);
            List<object> list = new List<object>();
            for (int i = 0; i < lines.Count; i++)
            {
                list.Add(lines[i]);
            }
            doc["lines"] = list;
            int total = lines.Count;
            if (total >= MaxLines)
            {
                total = DryRunRows.Count(conn, LinesSql(kind, true), id);
            }
            doc["lines_total"] = total;
        }

        // 预演 docs 加 detail 序列化后超过 MaxBytes（UTF-8）时去掉各单的 lines（保留 lines_total），响应不会碰到 8 MiB 上限。
        // 返回 true 表示去掉过。detail 可为 null。
        public static bool Fit(List<object> docs, Dictionary<string, object> detail)
        {
            if (Bytes(docs) + (detail == null ? 0 : Bytes(detail)) <= MaxBytes)
            {
                return false;
            }
            for (int i = 0; i < docs.Count; i++)
            {
                Dictionary<string, object> doc = docs[i] as Dictionary<string, object>;
                if (doc != null)
                {
                    doc.Remove("lines");
                }
            }
            return true;
        }

        internal static long Bytes(object value)
        {
            JavaScriptSerializer ser = new JavaScriptSerializer();
            ser.MaxJsonLength = int.MaxValue;
            return Encoding.UTF8.GetByteCount(ser.Serialize(value));
        }

        internal static string HeadSql(VoucherKind kind)
        {
            return "SELECT * FROM " + Q(kind.HeadTable) + " WHERE " + Q(kind.IdColumn) + " = ?";
        }

        // 表体外键与表头主键同名时直接按 id；否则（应收应付单 cLink）经表头的同名列取。
        internal static string LinesSql(VoucherKind kind, bool count)
        {
            string select = count ? "SELECT COUNT(*) FROM " : "SELECT TOP " + MaxLines.ToString(CultureInfo.InvariantCulture) + " * FROM ";
            string where;
            if (string.Equals(kind.BodyFk, kind.IdColumn, StringComparison.OrdinalIgnoreCase))
            {
                where = Q(kind.BodyFk) + " = ?";
            }
            else
            {
                where = Q(kind.BodyFk) + " IN (SELECT h." + Q(kind.BodyFk) + " FROM " + Q(kind.HeadTable)
                    + " h WHERE h." + Q(kind.IdColumn) + " = ?)";
            }
            string order = count || Blank(kind.LineIdColumn) ? "" : " ORDER BY " + Q(kind.LineIdColumn);
            return select + Q(kind.BodyTable) + " WHERE " + where + order;
        }

        static string Q(string name)
        {
            return "[" + CoRows.Ident(name) + "]";
        }

        static bool Blank(string text)
        {
            return text == null || text.Length == 0;
        }
    }
}
