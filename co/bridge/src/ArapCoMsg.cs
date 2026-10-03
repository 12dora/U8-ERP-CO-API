using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 应收应付单据的响应体。state 在通用的 verified / verifier / verified_at 之外带制单与核销标记。
    internal static class ArapMsg
    {
        public static Dictionary<string, object> Base(VoucherKind kind, int id)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["id"] = id;
            return body;
        }

        public static Dictionary<string, object> State(ArapDoc doc)
        {
            string verifier = doc.Col("verifier");
            bool verified = verifier.Length > 0 && doc.Col("verify_date").Length > 0;
            Dictionary<string, object> state = new Dictionary<string, object>();
            state["verified"] = verified;
            state["verifier"] = verifier;
            state["verified_at"] = verified ? When(doc) : "";
            state["gl_voucher"] = doc.Col("voucher");
            state["settled"] = ArapSql.Settled(doc);
            state["amount"] = doc.Col("amount");
            state["unsettled_amount"] = doc.Col("ramount");
            return state;
        }

        public static string When(ArapDoc doc)
        {
            string sys = doc.Col("verify_sys");
            return sys.Length > 0 ? sys : doc.Col("verify_date");
        }

        public static void RequireVerifier(ArapDoc doc, string operatorName)
        {
            string verifier = doc.Col("verifier");
            if (verifier.Length == 0 || doc.Col("verify_date").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "审核后审核人或审核日期为空");
            }
            string name = operatorName == null ? "" : operatorName.Trim();
            if (!string.Equals(verifier, name, StringComparison.Ordinal))
            {
                throw new BridgeException(409, "state_mismatch", "审核人与登录操作员姓名不一致");
            }
        }

        public static ApiResult Created(VoucherKind kind, ArapDoc doc)
        {
            Dictionary<string, object> body = Base(kind, doc.Id);
            body["code"] = doc.Code;
            body["lines"] = Count(doc.Col("lines"));
            body["state"] = State(doc);
            return ApiResult.Ok(body);
        }

        public static string Unknown(int id, string code)
        {
            string text = "已保存但未能确定单据标识";
            if (code != null && code.Trim().Length > 0)
            {
                text = text + "，单号 " + code.Trim();
            }
            if (id > 0)
            {
                text = text + "，标识 " + id.ToString(CultureInfo.InvariantCulture);
            }
            return text;
        }

        static int Count(string text)
        {
            int n;
            if (int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out n) && n >= 0)
            {
                return n;
            }
            return 0;
        }
    }
}
