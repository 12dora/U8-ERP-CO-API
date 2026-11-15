using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // notes/get：按 Auto_ID 或票据号读一张应收 / 应付票据（AP_Note）及其处理记录（AP_Note_Sub，按 cLink 关联）。
    // 读路由，跑在读线程池上，只用 ctx.Conn，不碰 ctx.Session、不登录 U8。数据权限同 vouchers/list 的 voucher:<type>：
    // 带权限条件查不到、不带条件查得到时 403，都查不到 404 not_found（事件服务确认票据已删除就靠这个 404）。
    // 不选银行账号类列（cReceiveAccount、cPayBillAccount）。
    internal static class NotesRead
    {
        internal const int MaxSubs = 500;

        internal const string HeadCols = "h.Auto_ID AS id, h.cVouchID AS code, h.cFlag AS flag, h.cVouchType AS vouch_type, "
            + "h.cLink AS link, h.dSignDate AS doc_date, h.dReceiptDate AS receipt_date, h.dExpireDate AS expire_date, "
            + "h.cDwCode AS partner_code, h.cDWName AS dw_name, h.cDeptCode AS dep_code, h.cPerson AS person_code, "
            + "ISNULL(NULLIF(h.cOperator, N''), h.cBill) AS maker, h.cSettleCode AS settle_code, h.cexch_name AS currency, "
            + "h.nfrat AS rate, h.iAmount AS amount, h.iAmount_Local AS amount_local, h.iRAmount AS remainder, "
            + "h.iRAmount_Local AS remainder_local, h.iRate AS interest_rate, h.cCode AS km_code, h.cBank AS bank, "
            + "h.cDigest AS digest, h.cItem_Class AS item_class, h.cItemCode AS item_code, h.cEndorser AS endorser, "
            + "h.iCloseID AS close_id, h.bStartFlag AS opening, h.bsubpackage AS sub_package, h.csubnostart AS sub_start, "
            + "h.csubnoend AS sub_end, h.iChangeType AS change_type, h.dcreatesystime AS created_at, "
            + "h.dmodifysystime AS modified_at, CONVERT(varchar(20), CONVERT(bigint, h.Ufts)) AS ufts";

        internal const string SubSql = "SELECT TOP (?) s.ID AS id, s.cProcStyle AS style, s.dDate AS date, "
            + "s.iAmount AS amount, s.iAmount_Local AS amount_local, s.iIntrest AS interest, s.iExpense AS expense, "
            + "s.iDisctIntrest AS discount_rate, s.cBank AS bank, s.cCode AS km_code, s.cOperator AS operator, "
            + "s.cCancelNo AS cancel_no, s.cCoVouchType AS source_type, s.cCoVouchID AS source_code, s.cPzID AS gl_ref, "
            + "s.csbnstart AS sub_start, s.csbnend AS sub_end FROM AP_Note_Sub s WHERE s.cLink = ? ORDER BY s.ID";

        internal static readonly string[] HeadKeys = new string[]
        {
            "id", "code", "flag", "vouch_type", "doc_date", "receipt_date", "expire_date", "partner_code", "dw_name",
            "dep_code", "person_code", "maker", "settle_code", "currency", "rate", "amount", "amount_local", "remainder",
            "remainder_local", "interest_rate", "km_code", "bank", "digest", "item_class", "item_code", "endorser",
            "close_id", "opening", "sub_package", "sub_start", "sub_end", "change_type", "created_at", "modified_at", "ufts"
        };

        internal static readonly string[] SubKeys = new string[]
        {
            "id", "style", "style_name", "date", "amount", "amount_local", "interest", "expense", "discount_rate", "bank",
            "km_code", "operator", "cancel_no", "source_type", "source_code", "gl_ref", "sub_start", "sub_end"
        };

        static readonly string[] IntKeys = new string[] { "id", "close_id", "change_type" };
        static readonly string[] BoolKeys = new string[] { "opening", "sub_package" };

        public static ApiResult Run(WorkContext ctx)
        {
            Dictionary<string, object> body = ctx == null || ctx.Item == null ? null : ctx.Item.Body;
            NoteAsk ask = NotesReadReq.Parse(body);
            Dictionary<string, object> head = FindHead(ctx, ask);
            string link = Text(head, "link");
            List<Dictionary<string, object>> subs = Rows.Query(ctx.Conn, SubSql, new object[] { MaxSubs + 1, link },
                MaxSubs + 1);
            Dictionary<string, object> result = new Dictionary<string, object>();
            result["ok"] = true;
            result["type"] = ask.Type;
            result["head"] = Shape(head, HeadKeys);
            List<object> items = new List<object>();
            for (int i = 0; i < subs.Count && i < MaxSubs; i++)
            {
                Dictionary<string, object> sub = subs[i];
                sub["style_name"] = StyleName(Text(sub, "style"));
                items.Add(Shape(sub, SubKeys));
            }
            result["subs"] = items;
            result["subs_truncated"] = subs.Count > MaxSubs;
            return ApiResult.Ok(result);
        }

        // 带数据权限查；查不到时不带条件再查一次，区分越权（403）与不存在（404）。
        static Dictionary<string, object> FindHead(WorkContext ctx, NoteAsk ask)
        {
            List<object> ps = new List<object>();
            Dictionary<string, object> head = Rows.One(ctx.Conn, HeadSql(ask, ps, ctx, true), ps.ToArray());
            if (head != null)
            {
                return head;
            }
            List<object> bare = new List<object>();
            if (Rows.One(ctx.Conn, HeadSql(ask, bare, ctx, false), bare.ToArray()) != null)
            {
                throw new BridgeException(403, "no_permission", PermCheck.DeniedData);
            }
            throw new BridgeException(404, "not_found", "票据不存在");
        }

        // SELECT TOP 1 … FROM AP_Note h WHERE <cFlag 条件> AND (Auto_ID | cVouchID) = ? [数据权限] ORDER BY h.Auto_ID
        internal static string HeadSql(NoteAsk ask, List<object> ps, WorkContext ctx, bool perm)
        {
            ListKind kind = ListKinds.Find(ask.Type);
            if (kind == null)
            {
                throw new BridgeException(500, "internal", "票据列表类型未登记");
            }
            StringBuilder sb = new StringBuilder("SELECT TOP 1 ").Append(HeadCols).Append(" FROM ")
                .Append(kind[ListKind.Head]).Append(" h WHERE ").Append(kind[ListKind.Cond]);
            if (ask.Code != null)
            {
                sb.Append(" AND ").Append(kind[ListKind.Code]).Append(" = ?");
                ps.Add(ask.Code);
            }
            else
            {
                sb.Append(" AND h.").Append(kind[ListKind.Id]).Append(" = ?");
                ps.Add(ask.Id);
            }
            if (perm)
            {
                // 数据权限：与列表同一套条件，放在 WHERE 最后。
                PermHook.Where(sb, ps, ctx, "h");
            }
            sb.Append(" ORDER BY h.").Append(kind[ListKind.Id]);
            return sb.ToString();
        }

        // 处理方式：9A 结算（托收）、9C 退回、9D 贴现、9E 背书；其他原样给代码。
        internal static string StyleName(string style)
        {
            string code = style == null ? "" : style.Trim();
            switch (code)
            {
                case "9A":
                    return "结算";
                case "9C":
                    return "退回";
                case "9D":
                    return "贴现";
                case "9E":
                    return "背书";
                default:
                    return code;
            }
        }

        static string Text(Dictionary<string, object> row, string key)
        {
            object raw;
            if (row == null || !row.TryGetValue(key, out raw))
            {
                return null;
            }
            return raw as string;
        }

        // 键固定，查询里为 NULL 的列也给出 null；主键类列转整数，标志列转布尔，其余保持字符串。
        internal static Dictionary<string, object> Shape(Dictionary<string, object> row, string[] keys)
        {
            Dictionary<string, object> item = new Dictionary<string, object>(keys.Length);
            for (int i = 0; i < keys.Length; i++)
            {
                item[keys[i]] = Value(keys[i], Text(row, keys[i]));
            }
            return item;
        }

        static object Value(string key, string text)
        {
            if (text == null)
            {
                return null;
            }
            string trimmed = text.Trim();
            if (Array.IndexOf(BoolKeys, key) >= 0)
            {
                return trimmed.Length > 0 && trimmed != "0";
            }
            int number;
            if (Array.IndexOf(IntKeys, key) >= 0
                && int.TryParse(trimmed, NumberStyles.Integer, CultureInfo.InvariantCulture, out number))
            {
                return number;
            }
            return text;
        }
    }
}
