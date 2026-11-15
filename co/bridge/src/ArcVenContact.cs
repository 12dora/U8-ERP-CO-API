using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 供应商联系人（vendor_contact，表 Ven_Contact）：一次一行。
    // 新增走 U8 官方 EAI 分发器（roottag vendorcontact）：<code> 留空，U8 自己编号（供应商编码 + 8 位流水，
    // 应答的 u8key 就是新编码）；新增的 code 写成 "<供应商编码>:"，调用后在新连接上按「这个供应商、这个名称、调用前没有的编码」
    // 找新行（恰好一行，且与 u8key 一致），响应的 code 是 U8 编的号。
    // 分发器对修改、删除回「供应商联系人不支持修改和删除导入」：修改、删除走受控 SQL（ArcVenContactSql）。
    // 主要联系人（bMajor）：每个供应商至多一个，已有其他主要联系人时设为主要 409（先取消原来的）。
    internal static class ArcVenContact
    {
        internal const string Root = "vendorcontact";
        const int MaxCodes = 20000;
        internal const string OtherMainSql = "SELECT TOP 1 cContactCode AS c FROM Ven_Contact WHERE cVenCode=? AND bMajor=1 AND cContactCode<>?";

        public static ApiResult Write(WorkContext ctx, ArcReq req)
        {
            ArcPartnerContactMap.Check(req);
            string[] parts = ArcPartnerRun.Parts(req);
            if (req.Op == "create")
            {
                return Create(ctx, req, parts);
            }
            if (parts[1].Length == 0)
            {
                throw ArcReq.Bad("code 必须写成 <供应商编码>:<联系人编码>");
            }
            return ArcVenContactSql.Write(ctx, req, parts);
        }

        static ApiResult Create(WorkContext ctx, ArcReq req, string[] parts)
        {
            if (parts[1].Length > 0)
            {
                throw ArcReq.Bad("供应商联系人编码由 U8 自动编号：新增时 code 写成 <供应商编码>:（冒号后留空）");
            }
            object conn = ctx.Conn;
            ArcPartnerRun.Partner(conn, PartnerSide.Vendor, parts[0]);
            if (req.Fields.Get(ArcPartnerContactMap.Major) == "1")
            {
                OtherMain(conn, parts[0], "");
            }
            HashSet<string> before = Codes(conn, parts[0]);
            string xml = Envelope(parts[0], req.Fields);
            string name = req.Fields.Get("name");
            string[] reply = new string[1];
            return ArcPartnerRun.Eai(ctx, req, "add", EaiDistribute.What,
                delegate
                {
                    reply[0] = EaiDistribute.Call(ctx, xml);
                    return reply[0];
                },
                delegate(object fresh) { return Created(fresh, req, parts[0], name, before, EaiDistribute.Key(reply[0])); });
        }

        // 已有其他主要联系人时 409（self 是本联系人编码，新增时为空串）。
        internal static void OtherMain(object conn, string vendor, string self)
        {
            string other = Rows.Scalar(conn, OtherMainSql, new object[] { vendor, self });
            if (other != null)
            {
                throw ArcGuard.State("供应商 " + vendor + " 已有主要联系人 " + other.Trim() + "，请先把它的 be_main_linker 改成 0");
            }
        }

        static HashSet<string> Codes(object conn, string vendor)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, "SELECT cContactCode AS c FROM Ven_Contact WHERE cVenCode=?",
                new object[] { vendor }, MaxCodes + 1);
            if (rows.Count > MaxCodes)
            {
                throw ArcGuard.State("供应商 " + vendor + " 的联系人太多，请在 U8 客户端处理");
            }
            HashSet<string> codes = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < rows.Count; i++)
            {
                codes.Add(ArcRead.Cell(rows[i], "c") ?? "");
            }
            return codes;
        }

        // 新增的回读：这个供应商、这个名称、调用前没有的编码恰好一行，U8 给了 u8key 时与它一致，且调用方给的字段一致；
        // 找到后把 req.Code 换成 U8 编的号。一行新行都没有返回 false（按 U8 的应答判断）；有新行但不唯一、与 u8key 不符或字段不符，
        // 不管 U8 应答如何都 504 并带上新行编码，免得调用方重发建出重复的联系人。
        static bool Created(object conn, ArcReq req, string vendor, string name, HashSet<string> before, string key)
        {
            List<string> fresh = NewCodes(conn, vendor, name, before);
            if (fresh.Count == 0)
            {
                return false;
            }
            bool keyOk = key.Length == 0 || string.Equals(fresh[0], key, StringComparison.OrdinalIgnoreCase);
            Dictionary<string, string> row = fresh.Count == 1 && keyOk ? ArcRead.Row(conn, req.Kind, vendor + ":" + fresh[0]) : null;
            if (row == null || !ArcVenContactSql.CallerMatches(req, row, null))
            {
                throw new BridgeException(504, "outcome_unknown", "U8 已新增供应商联系人，但回读不能确认是哪一行或字段不符（新行编码 "
                    + string.Join("、", fresh.ToArray()) + "），请先 get 核对，不要直接重发");
            }
            req.Code = vendor + ":" + fresh[0];
            return true;
        }

        // 这个供应商、这个名称、调用前没有的联系人编码。
        static List<string> NewCodes(object conn, string vendor, string name, HashSet<string> before)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, "SELECT cContactCode AS c FROM Ven_Contact WHERE cVenCode=? AND cContactName=?",
                new object[] { vendor, (name ?? "").Trim() }, MaxCodes);
            List<string> fresh = new List<string>();
            for (int i = 0; i < rows.Count; i++)
            {
                string code = (ArcRead.Cell(rows[i], "c") ?? "").Trim();
                if (code.Length > 0 && !before.Contains(code))
                {
                    fresh.Add(code);
                }
            }
            return fresh;
        }

        // 报文（Template\VendorContact.xml）：code 留空（U8 编号），先发 name、of_vendor，再发其余标签。
        internal static string Envelope(string vendor, ArcBag fields)
        {
            StringBuilder sb = EaiDistribute.Begin(Root, "add");
            ArcPartnerXml.Tag(sb, "code", "");
            if (fields.Has("name"))
            {
                ArcPartnerXml.Tag(sb, "name", fields.Get("name"));
            }
            ArcPartnerXml.Tag(sb, "of_vendor", vendor);
            IList<string> tags = fields.Tags;
            for (int i = 0; i < tags.Count; i++)
            {
                if (tags[i] != "name")
                {
                    ArcPartnerXml.Tag(sb, tags[i], fields.Get(tags[i]));
                }
            }
            return EaiDistribute.End(sb, Root);
        }
    }
}
