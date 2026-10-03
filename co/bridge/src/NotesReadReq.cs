using System;
using System.Collections.Generic;

namespace U8Co
{
    // notes/get 的请求：type（ar_note / ap_note）加 id（AP_Note.Auto_ID）或 code（票据号 cVouchID）二选一。
    internal sealed class NoteAsk
    {
        public string Type = "";
        public string Flag = "";
        public int Id;
        public string Code;
    }

    // 单张票据读取（只读，读线程池，只跑 SQL，不登录 U8）。字段表在 RequestsP4.P4Specs；登录前（Requests.ApplyNotesRead）
    // 与处理函数里各解析一次，规则相同。不带 VoucherKind（票据不是单据类型，写路由不收），权限规则同列表 voucher:<type>。
    internal static class NotesReadReq
    {
        internal const string Path = "/u8co/v1/notes/get";
        internal const string Action = "note_get";
        internal const string ArType = "ar_note";
        internal const string ApType = "ap_note";
        internal static readonly string[] Spec = new string[] { Path, "type", "id", "code" };

        public static bool IsPath(string path)
        {
            return string.Equals(path, Path, StringComparison.Ordinal);
        }

        // 票据列表类型的应收 / 应付；不是票据类型返回 null。也用作登录子系统。
        public static string FlagOf(string type)
        {
            if (type == ArType)
            {
                return "AR";
            }
            return type == ApType ? "AP" : null;
        }

        // 权限规则键（PermRegistry.OtherKey）：voucher:ar_note / voucher:ap_note。不是本路由或类型无效返回 null。
        public static string RuleKey(WorkItem item)
        {
            if (item == null || !IsPath(item.Path))
            {
                return null;
            }
            string type = Requests.Field(item.Body, "type") as string;
            return FlagOf(type) == null ? null : "voucher:" + type;
        }

        public static NoteAsk Parse(Dictionary<string, object> body)
        {
            if (body == null)
            {
                throw new BridgeException(400, "bad_request", "请求体为空");
            }
            NoteAsk ask = new NoteAsk();
            ask.Type = Requests.Field(body, "type") as string;
            ask.Flag = FlagOf(ask.Type);
            if (ask.Flag == null)
            {
                throw BridgeException.BadField("type", "type 只能是 ar_note 或 ap_note");
            }
            object id = Requests.Field(body, "id");
            ask.Code = ListArgs.OptText(body, "code", "code");
            if ((id == null) == (ask.Code == null))
            {
                throw BridgeException.BadField("id", "id 与 code 必须给且只给一个");
            }
            if (id != null)
            {
                ask.Id = IdOf(id);
            }
            return ask;
        }

        static int IdOf(object raw)
        {
            long number = raw is int ? (int)raw : (raw is long ? (long)raw : 0L);
            if (number < 1 || number > int.MaxValue)
            {
                throw BridgeException.BadField("id", "id 必须是 1 到 2147483647 的整数");
            }
            return (int)number;
        }
    }
}
