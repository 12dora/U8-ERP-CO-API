using System;
using System.Collections.Generic;

namespace U8Co
{
    // meta/fields 的请求：type（单据类型）、archive（档案）、gl: true 三者恰好一个；
    // 单据另收 op（create 缺省 | update | generate）和 source（op=generate 时必填，取该类型的来源类型之一）。
    internal sealed class MetaFieldsAsk
    {
        public VoucherKind Kind;
        public ArcKind Arc;
        public bool Gl;
        public string Op;
        public VoucherKind Source;
    }

    internal static class MetaFieldsReq
    {
        internal const string Path = "/u8co/v1/meta/fields";
        internal const string Action = "meta_fields";
        // 登录子系统：基础设置（同 idempotency/get），只核对口令，不要求任何功能权限。
        internal const string Sub = "AS";
        internal static readonly string[] Keys = new string[] { "type", "archive", "gl", "op", "source" };
        const string OneHint = "type（单据类型）、archive（档案）、gl: true 三者只能给一个；可用值见 /v1/co/meta";

        // 登录前和处理函数里各调一次，纯校验，不查库。
        public static MetaFieldsAsk Parse(Dictionary<string, object> body)
        {
            MetaFieldsAsk ask = new MetaFieldsAsk();
            object type = Requests.Field(body, "type");
            object arc = Requests.Field(body, "archive");
            ask.Gl = GlFlag(Requests.Field(body, "gl"));
            int count = (type != null ? 1 : 0) + (arc != null ? 1 : 0) + (ask.Gl ? 1 : 0);
            if (count == 0)
            {
                throw new BridgeException(400, "bad_request", "缺少 type、archive 或 gl", "type", OneHint);
            }
            if (count > 1)
            {
                throw new BridgeException(400, "bad_request", "type、archive、gl 只能给一个", arc != null ? "archive" : "gl", OneHint);
            }
            if (type != null)
            {
                ask.Kind = KindOf(type, "type");
                ask.Op = OpOf(Requests.Field(body, "op"));
                CheckOp(ask.Kind, ask.Op);
                ask.Source = SourceOf(ask.Kind, ask.Op, Requests.Field(body, "source"));
                return ask;
            }
            NoVoucherKeys(body);
            if (arc != null)
            {
                ask.Arc = ArcOf(arc);
            }
            return ask;
        }

        static bool GlFlag(object raw)
        {
            if (raw == null)
            {
                return false;
            }
            if (!(raw is bool))
            {
                throw BridgeException.BadField("gl", "gl 只能是 true 或 false");
            }
            return (bool)raw;
        }

        static VoucherKind KindOf(object raw, string field)
        {
            VoucherKind kind = Kinds.Find(raw as string);
            if (kind == null)
            {
                throw BridgeException.BadField(field, "单据类型无效").WithHint("可用的单据类型见 /v1/co/meta");
            }
            return kind;
        }

        static ArcKind ArcOf(object raw)
        {
            string name = raw as string;
            ArcKind kind = name == null ? null : ArcKind.Find(name);
            if (kind == null)
            {
                throw BridgeException.BadField("archive", "档案类型无效").WithHint("可用的档案见 /v1/co/meta");
            }
            return kind;
        }

        internal static string OpOf(object raw)
        {
            if (raw == null)
            {
                return "create";
            }
            string op = raw as string;
            if (op != "create" && op != "update" && op != "generate")
            {
                throw BridgeException.BadField("op", "op 只能是 create、update 或 generate");
            }
            return op;
        }

        static VoucherKind SourceOf(VoucherKind kind, string op, object raw)
        {
            if (op != "generate")
            {
                if (raw != null)
                {
                    throw BridgeException.BadField("source", "source 只用于 op=generate");
                }
                return null;
            }
            if (raw == null)
            {
                throw BridgeException.BadField("source", "op=generate 必须给 source（来源单据类型）").WithHint(SourceHint(kind));
            }
            string name = raw as string;
            if (name == null || kind.Sources == null || Array.IndexOf(kind.Sources, name) < 0)
            {
                throw BridgeException.BadField("source", "该单据类型不能参照 " + (name ?? "") + " 生成").WithHint(SourceHint(kind));
            }
            return KindOf(name, "source");
        }

        static string SourceHint(VoucherKind kind)
        {
            if (kind.Sources == null || kind.Sources.Length == 0)
            {
                return "该单据类型不支持生单";
            }
            return "可用来源：" + string.Join("、", kind.Sources);
        }

        // op 与 meta kinds[].writable 一致：create / update 为 null、没有来源类型时 400。
        static void CheckOp(VoucherKind kind, string op)
        {
            bool ok = op == "create" ? kind.Creatable : op == "update" ? kind.Updatable : kind.Sources != null && kind.Sources.Length > 0;
            if (!ok)
            {
                throw BridgeException.BadField("op", "该单据类型不支持 " + op);
            }
        }

        static void NoVoucherKeys(Dictionary<string, object> body)
        {
            if (Requests.Field(body, "op") != null)
            {
                throw BridgeException.BadField("op", "op 只用于 type");
            }
            if (Requests.Field(body, "source") != null)
            {
                throw BridgeException.BadField("source", "source 只用于 type");
            }
        }
    }
}
