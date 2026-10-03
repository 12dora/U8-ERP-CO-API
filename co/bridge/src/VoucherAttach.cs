using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 业务单据附件列表 vouchers/attachments/list（只读，全是 SQL，走读线程）。
    // U8 单据卡片的「附件」存在通用表 VoucherAccessories（VoucherTypeID = 卡片号 vouchers.CardNumber，
    // VoucherID = 该卡片主键列 vouchers.VchTblPrimarykeyNames 的值，如 Ap_Vouch 是 cLink），见 UFVoucherServer85 的 SQL。
    // 文件内容要么在 FileContent（旧版，存库），要么只有 FileID（文件服务器）；这里只列清单，不给下载。
    // 权限：该单据类型的读取规则（PermRegistry voucher:<type>，与 vouchers/load 相同），越权 403。
    // 共用表头表的类型（收款单 / 付款单、应收单 / 应付单、发货单 / 退货单、到货单 / 采购退货单、各质量单据）
    // 先按本类型的条件确认单据存在（别的类型的 id 一律 404），再只列本类型卡片上的附件。
    internal static class VoucherAttach
    {
        internal const string Path = "/u8co/v1/vouchers/attachments/list";
        const int MaxFiles = 500;
        const int MaxCards = 20;

        const string CardSql = "SELECT TOP 20 CardNumber card, VchTblPrimarykeyNames pk FROM vouchers WHERE BTTblName=?";

        // 共用表头表的类型 → { 表头条件（别名 h，常量）, 本类型的卡片号（逗号分隔） }。质量单据按 BizObjectId（见 TypeCond）。
        // 到货单与采购退货单共用卡片 26，只能靠表头条件区分（附件键是 ID，不会串）。
        static readonly Dictionary<string, string[]> Shared = BuildShared();
        const string ListSql = "SELECT TOP (?) a.VoucherTypeID card, a.VoucherID vid, a.FileID file_id, a.FileName name,"
            + " a.Memo memo, DATALENGTH(a.FileContent) size FROM VoucherAccessories a WHERE (1=0{PAIRS})"
            + " ORDER BY a.FileName, a.FileID";

        static Dictionary<string, string[]> BuildShared()
        {
            Dictionary<string, string[]> map = new Dictionary<string, string[]>(StringComparer.Ordinal);
            map.Add("dispatch", new string[] { "ISNULL(h.bReturnFlag,0)=0", "01" });
            map.Add("sale_return", new string[] { "h.bReturnFlag=1", "03" });
            map.Add("arrival", new string[] { "ISNULL(h.iBillType,0)<>1", "26" });
            map.Add("purchase_return", new string[] { "h.iBillType=1", "26" });
            map.Add("ar_receipt", new string[] { "h.cFlag=N'AR' AND h.cVouchType=N'48'", "AR48" });
            map.Add("ap_payment", new string[] { "h.cFlag=N'AP' AND h.cVouchType=N'49'", "AP49" });
            map.Add("ar_bill", new string[] { "h.cFlag=N'AR' AND h.cVouchType=N'R0'", "AR04" });
            map.Add("ap_bill", new string[] { "h.cFlag=N'AP' AND h.cVouchType=N'P0'", "AP04" });
            // 供应商退款、客户退款：与收付款单同表，附件挂在各自的卡片 AP48 / AR49 上。
            map.Add("ap_refund", new string[] { "h.cFlag=N'AP' AND h.cVouchType=N'48'", "AP48" });
            map.Add("ar_refund", new string[] { "h.cFlag=N'AR' AND h.cVouchType=N'49'", "AR49" });
            map.Add("transfer", new string[] { null, "0304" });
            // 形态转换单：AssemVouch 还放组装单、拆卸单（卡片 0308 / 0309），只认 cVouchType=15、卡片 0305。
            map.Add("shape_change", new string[] { "h.cVouchType=N'15'", "0305" });
            return map;
        }

        // 本类型的表头条件；没有（表头表只属于这一种类型）返回 null。BizObjectId 是 Kinds 里的常量。
        static string TypeCond(VoucherKind kind)
        {
            string[] shared;
            if (Shared.TryGetValue(kind.Name, out shared))
            {
                return shared[0];
            }
            return IsQm(kind) ? "h.CVOUCHTYPE=N'" + kind.BizObjectId + "'" : null;
        }

        // 本类型的卡片号；null 表示这张表头表的全部卡片（表头表只属于这一种类型）。
        static string[] OwnCards(VoucherKind kind)
        {
            string[] shared;
            if (Shared.TryGetValue(kind.Name, out shared))
            {
                return shared[1].Split(',');
            }
            return IsQm(kind) ? new string[] { kind.BizObjectId } : null;
        }

        static bool IsQm(VoucherKind kind)
        {
            return kind.Family == "qm" && !string.IsNullOrEmpty(kind.BizObjectId);
        }

        public static ApiResult Handle(WorkContext ctx)
        {
            VoucherKind kind = ctx.Item.Type;
            if (kind == null || string.IsNullOrEmpty(kind.HeadTable) || string.IsNullOrEmpty(kind.IdColumn))
            {
                throw GlReq.Bad("该单据类型不支持附件列表");
            }
            int id = ctx.Item.Id;
            string cond = TypeCond(kind);
            string exists = "SELECT TOP 1 1 AS x FROM " + kind.HeadTable + " h WHERE h." + kind.IdColumn + "=?"
                + (cond == null ? "" : " AND " + cond);
            if (Rows.One(ctx.Conn, exists, new object[] { id }) == null)
            {
                throw new BridgeException(404, "not_found", kind.Title + "不存在：" + id.ToString(CultureInfo.InvariantCulture));
            }
            // 数据权限：单据已确认是本类型，再按 vouchers/load 同一套表头条件探，越权 403。
            PermHook.Voucher(ctx, kind, id);
            List<string[]> pairs = Pairs(ctx, kind, id);
            List<object> items = new List<object>();
            bool truncated = false;
            if (pairs.Count > 0)
            {
                List<Dictionary<string, object>> rows = List(ctx, pairs);
                truncated = rows.Count > MaxFiles;
                for (int i = 0; i < rows.Count && i < MaxFiles; i++)
                {
                    items.Add(Item(rows[i]));
                }
            }
            Dictionary<string, object> body = Reports.Body();
            body["type"] = kind.Name;
            body["id"] = id;
            body["items"] = items;
            body["truncated"] = truncated;
            return ApiResult.Ok(body);
        }

        // （卡片号, 附件表里的单据键）：只取本类型的卡片（OwnCards），表头表只属于这一种类型时取它的全部卡片。
        // 未覆盖：VoucherID 取主键列的值（按 UFVoucherServer85 的 SQL 与 vouchers.VchTblPrimarykeyNames 推断）。
        static List<string[]> Pairs(WorkContext ctx, VoucherKind kind, int id)
        {
            List<Dictionary<string, object>> cards = Cards(ctx, kind);
            Dictionary<string, string> keys = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            List<string[]> pairs = new List<string[]>();
            for (int i = 0; i < cards.Count; i++)
            {
                string card = GlSql.Col(cards[i], "card");
                string pk = GlSql.Col(cards[i], "pk");
                if (pk.Length == 0)
                {
                    pk = kind.IdColumn;
                }
                string key;
                if (card.Length == 0 || !Identifier(pk))
                {
                    continue;
                }
                if (!keys.TryGetValue(pk, out key))
                {
                    key = KeyOf(ctx, kind, id, pk);
                    keys[pk] = key;
                }
                if (key.Length > 0)
                {
                    pairs.Add(new string[] { card, key });
                }
            }
            return pairs;
        }

        static List<Dictionary<string, object>> Cards(WorkContext ctx, VoucherKind kind)
        {
            List<object> ps = new List<object>();
            ps.Add(kind.HeadTable);
            StringBuilder sql = new StringBuilder(CardSql);
            string[] own = OwnCards(kind);
            if (own != null)
            {
                sql.Append(" AND CardNumber IN (");
                for (int i = 0; i < own.Length; i++)
                {
                    sql.Append(i == 0 ? "?" : ", ?");
                    ps.Add(own[i]);
                }
                sql.Append(")");
            }
            sql.Append(" ORDER BY CardNumber");
            return Rows.Query(ctx.Conn, sql.ToString(), ps.ToArray(), MaxCards);
        }

        // 主键列名来自 U8 的 vouchers 表（不是调用方），仍只收字母、数字和下划线后再拼进 SQL。
        static string KeyOf(WorkContext ctx, VoucherKind kind, int id, string pk)
        {
            string sql = "SELECT TOP 1 CONVERT(nvarchar(160), h." + pk + ") k FROM " + kind.HeadTable + " h WHERE h."
                + kind.IdColumn + "=?";
            string text = Rows.Scalar(ctx.Conn, sql, new object[] { id });
            return text == null ? "" : text.Trim();
        }

        static bool Identifier(string name)
        {
            if (name.Length == 0 || name.Length > 64)
            {
                return false;
            }
            for (int i = 0; i < name.Length; i++)
            {
                if (!IdentChar(name[i]))
                {
                    return false;
                }
            }
            return true;
        }

        static bool IdentChar(char c)
        {
            if (c == '_' || (c >= '0' && c <= '9'))
            {
                return true;
            }
            return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z');
        }

        static List<Dictionary<string, object>> List(WorkContext ctx, List<string[]> pairs)
        {
            List<object> ps = new List<object>();
            ps.Add(MaxFiles + 1);
            StringBuilder cond = new StringBuilder();
            for (int i = 0; i < pairs.Count; i++)
            {
                cond.Append(" OR (a.VoucherTypeID=? AND a.VoucherID=?)");
                ps.Add(pairs[i][0]);
                ps.Add(pairs[i][1]);
            }
            string sql = ListSql.Replace("{PAIRS}", cond.ToString());
            return Rows.Query(ctx.Conn, sql, ps.ToArray(), MaxFiles + 1);
        }

        // stored：database（内容在 FileContent）或 file_server（只有 FileID，内容在 U8 文件服务器）。
        static Dictionary<string, object> Item(Dictionary<string, object> row)
        {
            int size = GlSql.Int(row, "size");
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["card"] = GlSql.Col(row, "card");
            item["file_id"] = Reports.Text(row, "file_id");
            item["name"] = Reports.Text(row, "name");
            item["memo"] = Reports.Text(row, "memo");
            item["size"] = size > 0 ? (object)size : null;
            item["stored"] = size > 0 ? "database" : "file_server";
            return item;
        }
    }
}
