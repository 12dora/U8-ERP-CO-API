using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    internal sealed class BridgeException : Exception
    {
        public int Status { get; private set; }
        public string Code { get; private set; }
        // 出错的请求体字段路径（如 lines.2.iquantity），只用于 4xx。
        public string Field { get; private set; }
        // 简短的中文修正提示，只用于 4xx。
        public string Hint { get; private set; }
        // 4xx 的结构化补充（如存货核算拒绝时列出的存货），出现在错误体的 detail。
        public Dictionary<string, object> Detail { get; private set; }

        public BridgeException(int status, string code, string message)
            : base(message ?? "")
        {
            Status = status;
            Code = code;
        }

        public BridgeException(int status, string code, string message, string field)
            : this(status, code, message)
        {
            Field = field;
        }

        public BridgeException(int status, string code, string message, string field, string hint)
            : this(status, code, message, field)
        {
            Hint = hint;
        }

        public BridgeException WithField(string field)
        {
            Field = field;
            return this;
        }

        public BridgeException WithHint(string hint)
        {
            Hint = hint;
            return this;
        }

        public BridgeException WithDetail(Dictionary<string, object> detail)
        {
            Detail = detail;
            return this;
        }

        // 400 bad_request 且带字段路径，校验代码的简写。
        public static BridgeException BadField(string field, string message)
        {
            return new BridgeException(400, "bad_request", message, field);
        }
    }

    // 错误里 field 的写法：点分路径，列表带 0 起的下标，如 lines.2.iquantity。
    internal static class FieldPath
    {
        public const string WritableHint = "可写字段见 /v1/co/meta";

        // 单据表头 / 表体的 field 前缀。
        public static string Side(bool head)
        {
            return head ? "head" : "lines";
        }

        public static string Item(string list, int index)
        {
            return list + "." + index.ToString(CultureInfo.InvariantCulture);
        }

        public static string Join(string at, string key)
        {
            if (string.IsNullOrEmpty(key))
            {
                return at;
            }
            return string.IsNullOrEmpty(at) ? key : at + "." + key;
        }

        // 数组元素里抛出的 400：field 是相对该元素的（没有就用元素本身），补上 at（如 items.2）。
        public static BridgeException Under(BridgeException ex, string at)
        {
            if (ex.Status == 400)
            {
                ex.WithField(Join(at, ex.Field));
            }
            return ex;
        }

        // 行内抛出的 400 补上行下标：没有 field 或就是「lines」→「lines.<i>」；不带下标的「lines.x」→「lines.<i>.x」；
        // 相对行的「x」→「lines.<i>.x」；已带下标的不动。
        public static BridgeException Index(BridgeException ex, string list, int i)
        {
            if (ex.Status != 400)
            {
                return ex;
            }
            string f = ex.Field;
            string at = Item(list, i);
            if (string.IsNullOrEmpty(f) || f == list)
            {
                ex.WithField(at);
            }
            else if (!f.StartsWith(list + ".", StringComparison.Ordinal))
            {
                ex.WithField(at + "." + f);
            }
            else if (f.Length > list.Length + 1 && !char.IsDigit(f[list.Length + 1]))
            {
                ex.WithField(at + f.Substring(list.Length));
            }
            return ex;
        }

        // 「items[].id」这类标签：取 [] 之后的相对路径（外层循环再用 Under 补下标）；没有 [] 时同 Clean。
        public static string Label(string label)
        {
            int cut = label == null ? -1 : label.LastIndexOf("[]", StringComparison.Ordinal);
            return cut < 0 ? Clean(label) : Clean(label.Substring(cut + 2).TrimStart('.'));
        }

        // 标签本身就是 ASCII 字段路径（如 code、head.sign）时原样返回，否则（中文说明等）返回 null。
        public static string Clean(string label)
        {
            if (string.IsNullOrEmpty(label))
            {
                return null;
            }
            for (int i = 0; i < label.Length; i++)
            {
                if (!PathChar(label[i]))
                {
                    return null;
                }
            }
            return label;
        }

        static bool PathChar(char c)
        {
            return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '.';
        }
    }

    internal sealed class ApiResult
    {
        public int Status;
        public string Code;
        public string AuditMessage;
        public Dictionary<string, object> Body;

        public static ApiResult Ok(Dictionary<string, object> body)
        {
            ApiResult result = new ApiResult();
            result.Status = 200;
            result.Code = "ok";
            result.AuditMessage = "";
            result.Body = body;
            return result;
        }

        public static ApiResult From(BridgeException ex)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = false;
            body["code"] = ex.Code;
            // 500 的具体原因只进审计。401 不带 message，避免透露是时钟、随机数还是签名。
            if (ex.Status != 401)
            {
                string shown = ex.Status == 500 ? "内部错误" : ex.Message;
                body["message"] = shown ?? "";
            }
            AddDetail(body, ex);
            ApiResult result = new ApiResult();
            result.Status = ex.Status;
            result.Code = ex.Code;
            result.AuditMessage = ex.Message ?? "";
            result.Body = body;
            return result;
        }

        // field/hint 只给 4xx（401 除外），且非空时才写。
        static void AddDetail(Dictionary<string, object> body, BridgeException ex)
        {
            if (ex.Status < 400 || ex.Status >= 500 || ex.Status == 401)
            {
                return;
            }
            if (!string.IsNullOrEmpty(ex.Field))
            {
                body["field"] = ex.Field;
            }
            if (!string.IsNullOrEmpty(ex.Hint))
            {
                body["hint"] = ex.Hint;
            }
            if (ex.Detail != null && ex.Detail.Count > 0)
            {
                body["detail"] = ex.Detail;
            }
        }
    }
}
