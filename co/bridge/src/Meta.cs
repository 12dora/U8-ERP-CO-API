using System;
using System.Collections;
using System.Collections.Generic;
using System.Net;
using System.Text;
using System.Web.Script.Serialization;

namespace U8Co
{
    // POST /u8co/v1/meta：字段元数据。与业务路由一样要 HMAC 签名，但不带账套和操作员、不登录 U8、
    // 不进 STA 队列，在 HTTP 线程上直接返回；只读进程内的表和 U8 目录下的 RsXml 文件，不碰 COM。
    // revision 是除 features 之外内容的规范 JSON（键按序数排序）的 SHA-256；features 由 MetaHook 提供，运行中可能变化。
    internal static class BridgeMeta
    {
        internal const string Path = "/u8co/v1/meta";
        const string Version = "1";
        static readonly object Gate = new object();
        static SortedDictionary<string, object> _schema;
        static string _revision;

        internal static void Serve(BridgeConfig cfg, HttpListenerContext ctx, AuditDraft draft, byte[] body)
        {
            RejectBody(body);
            Dictionary<string, object> result = Result();
            Json.Write(ctx.Response, 200, result);
            draft.Responded = true;
            AuditLog.Write(cfg, draft, "ok", "", null);
        }

        // 请求体只能为空或 {}。
        static void RejectBody(byte[] body)
        {
            if (body == null || body.Length == 0)
            {
                return;
            }
            if (Json.Parse(body).Count > 0)
            {
                throw new BridgeException(400, "bad_request", "含未知字段");
            }
        }

        internal static Dictionary<string, object> Result()
        {
            string revision;
            SortedDictionary<string, object> schema = Schema(out revision);
            Dictionary<string, object> result = new Dictionary<string, object>();
            result["ok"] = true;
            result["revision"] = revision;
            foreach (KeyValuePair<string, object> pair in schema)
            {
                result[pair.Key] = pair.Value;
            }
            result["features"] = Features();
            // 只读账套（全局；meta 不带账套）。运行时配置，不进 revision。
            result["read_only_accounts"] = ReadOnlyGate.Accounts();
            return result;
        }

        // 档案标签全部读到才缓存到进程结束；否则每次重建，U8 目录恢复后自动补全。
        static SortedDictionary<string, object> Schema(out string revision)
        {
            lock (Gate)
            {
                if (_schema != null)
                {
                    revision = _revision;
                    return _schema;
                }
            }
            bool complete;
            SortedDictionary<string, object> schema = Canonical(Build(out complete)) as SortedDictionary<string, object>;
            revision = Revision(schema);
            if (complete)
            {
                lock (Gate)
                {
                    _schema = schema;
                    _revision = revision;
                }
            }
            return schema;
        }

        static Dictionary<string, object> Build(out bool complete)
        {
            Dictionary<string, object> d = new Dictionary<string, object>();
            d["version"] = Version;
            d["kinds"] = MetaKinds.Build();
            d["archives"] = MetaArc.Archives(out complete);
            d["gl"] = MetaArc.Gl();
            d["list_kinds"] = MetaArc.ListKindNames();
            d["routes"] = MetaArc.Routes();
            // 写字段 → 档案（archives/resolve 用），单据路由以外的写路由的预演模式。
            d["field_refs"] = MetaRefs.FieldRefs();
            d["gl_field_refs"] = MetaRefs.GlFieldRefs();
            d["field_refs_notes"] = MetaRefs.Notes();
            d["dry_run_routes"] = MetaDryRun.Routes();
            d["complete"] = complete;
            return d;
        }

        static Dictionary<string, object> Features()
        {
            try
            {
                Dictionary<string, object> features = MetaHook.MetaFeatures();
                return features ?? new Dictionary<string, object>();
            }
            catch (Exception)
            {
                return new Dictionary<string, object>();
            }
        }

        static string Revision(SortedDictionary<string, object> schema)
        {
            JavaScriptSerializer ser = new JavaScriptSerializer();
            ser.MaxJsonLength = Json.ResponseLimit;
            byte[] bytes = Encoding.UTF8.GetBytes(ser.Serialize(schema));
            return Crypto.Hex(Crypto.Sha256(bytes));
        }

        // 字典换成按序数排序的 SortedDictionary、数组换成 List，序列化结果与插入顺序无关。
        static object Canonical(object value)
        {
            IDictionary<string, object> map = value as IDictionary<string, object>;
            if (map != null)
            {
                SortedDictionary<string, object> sorted = new SortedDictionary<string, object>(StringComparer.Ordinal);
                foreach (KeyValuePair<string, object> pair in map)
                {
                    sorted[pair.Key] = Canonical(pair.Value);
                }
                return sorted;
            }
            IEnumerable list = value as IEnumerable;
            if (list == null || value is string)
            {
                return value;
            }
            List<object> copy = new List<object>();
            foreach (object item in list)
            {
                copy.Add(Canonical(item));
            }
            return copy;
        }
    }
}
