using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 客户联系人（customer_contact，Crm_Contact）：一次一行，走 EAI customerlinker → U8CRMEAINew.clsCRMEAI（Transact(xml, login)，
    // by-ref {1}，同 IClsCommon 的 <item succeed> 应答）。proc 照 EAI 样式表：add / edit / Delete。供应商联系人见 ArcVenContact。
    // 新增时 U8 不用报文里的联系人编码，自己编号（如 00000001）；所以新增的 code 写成 "<客户编码>:"，
    // 桥发一个占位编码，调用后在新连接上按「这个客户、这个名称、调用前没有的编码」找新行（恰好一行），响应的 code 是 U8 编的号。
    // 修改（edit）发整条当前记录再叠 fields，实测保留了没改的列；回读比较整行，调用方没改的列有任何变化 504。
    // 必填：code、name、of_customer、sex、marriage；性别、婚姻状况新增没给按「不详」。
    internal static class ArcPartnerContact
    {
        internal const string CustomerProgId = "U8CRMEAINew.clsCRMEAI";
        const int MaxCodes = 20000;
        const string Root = "customerlinker";

        // 删除前查引用：每条 SQL 两个参数（客户编码, 联系人编码），查到就 409。客户档案（及对照）上的主要联系人
        // 是 uniqueidentifier，存的是 Crm_Contact.OID（实测按联系人编码比较会报「将字符串转换为 uniqueidentifier 时失败」），
        // 所以经 Crm_Contact 按 OID 连；合同联系人表没有客户列，只按联系人编码查（第一个参数只占位）。
        static readonly string[] Refs = new string[]
        {
            "客户档案的主要联系人", "SELECT TOP 1 1 AS x FROM Customer c JOIN Crm_Contact k ON CONVERT(nvarchar(80), c.cCusContactCode)=k.OID"
                + " WHERE k.cCusCode=? AND k.cContactCode=?",
            "客户对照的联系人", "SELECT TOP 1 1 AS x FROM Customer_ContraRef c JOIN Crm_Contact k ON CONVERT(nvarchar(80), c.cCusContactCode)=k.OID"
                + " WHERE k.cCusCode=? AND k.cContactCode=?",
            "客户收货地址", "SELECT TOP 1 1 AS x FROM CusDeliverAdd WHERE cCusCode=? AND cLinkPerson=?",
            "销售订单", "SELECT TOP 1 1 AS x FROM SO_SOMain WHERE cCusCode=? AND ccuspersoncode=?",
            "发货单", "SELECT TOP 1 1 AS x FROM DispatchList WHERE cCusCode=? AND ccuspersoncode=?",
            "销售发票", "SELECT TOP 1 1 AS x FROM SaleBillVouch WHERE cCusCode=? AND ccuspersoncode=?",
            "销售报价单", "SELECT TOP 1 1 AS x FROM SA_QuoMain WHERE cCusCode=? AND ccuspersoncode=?",
            "销售支出单", "SELECT TOP 1 1 AS x FROM SalePayVouch WHERE cCusCode=? AND ccontactcode=?",
            "合同", "SELECT TOP 1 1 AS x FROM CM_Contract_B WHERE ? IS NOT NULL AND cContactCode=?",
            "合同", "SELECT TOP 1 1 AS x FROM CM_Contract_C WHERE ? IS NOT NULL AND cContactCode=?"
        };
        // 回读比较整行时不比的列：rowversion、U8 维护的变更人和变更日期。
        static readonly string[] Volatile = new string[] { "ufts", "dModifyDate", "cRevisor" };

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
                throw ArcReq.Bad("code 必须写成 <客户编码>:<联系人编码>");
            }
            Dictionary<string, string> row = ArcRead.Row(ctx.Conn, req.Kind, req.Code);
            if (row == null)
            {
                throw new BridgeException(404, "not_found", "档案不存在");
            }
            return req.Op == "update" ? Update(ctx, req, parts, row) : Delete(ctx, req, parts, row);
        }

        static ApiResult Create(WorkContext ctx, ArcReq req, string[] parts)
        {
            if (parts[1].Length > 0)
            {
                throw ArcReq.Bad("客户联系人编码由 U8 自动编号：新增时 code 写成 <客户编码>:（冒号后留空）");
            }
            object conn = ctx.Conn;
            ArcPartnerRun.Partner(conn, PartnerSide.Customer, parts[0]);
            HashSet<string> before = Codes(conn, parts[0]);
            ArcBag bag = new ArcBag();
            Overlay(req, bag);
            Missing(bag, ArcPartnerContactMap.Sex);
            Missing(bag, ArcPartnerContactMap.Marriage);
            // 占位编码：U8 不用它（实测），但模板要求 code 必填。
            string placeholder = "CO" + DateTime.Now.ToString("yyMMddHHmmssfff", CultureInfo.InvariantCulture);
            string xml = Envelope("add", parts[0], placeholder, bag);
            string name = req.Fields.Get("name");
            return ArcPartnerRun.Eai(ctx, req, "add", delegate { return Transact(ctx, xml); },
                delegate(object fresh) { return Created(fresh, req, parts[0], name, before); });
        }

        static ApiResult Update(WorkContext ctx, ArcReq req, string[] parts, Dictionary<string, string> row)
        {
            ArcBag bag = new ArcBag();
            ArcPartnerContactMap.Current(ctx.Conn, req, row, bag);
            Overlay(req, bag);
            Missing(bag, ArcPartnerContactMap.Sex);
            Missing(bag, ArcPartnerContactMap.Marriage);
            string xml = Envelope("edit", parts[0], parts[1], bag);
            return ArcPartnerRun.Eai(ctx, req, "edit", delegate { return Transact(ctx, xml); },
                delegate(object fresh) { return Unchanged(fresh, req, row); });
        }

        static ApiResult Delete(WorkContext ctx, ArcReq req, string[] parts, Dictionary<string, string> row)
        {
            object conn = ctx.Conn;
            for (int i = 0; i + 1 < Refs.Length; i += 2)
            {
                if (Rows.One(conn, Refs[i + 1], new object[] { parts[0], parts[1] }) != null)
                {
                    throw ArcGuard.State("联系人已被" + Refs[i] + "引用，不能删除");
                }
            }
            ArcBag bag = new ArcBag();
            string name;
            if (row.TryGetValue("cContactName", out name) && name.Trim().Length > 0)
            {
                bag.Put("name", name);
            }
            string xml = Envelope("Delete", parts[0], parts[1], bag);
            return ArcPartnerRun.Eai(ctx, req, "Delete", delegate { return Transact(ctx, xml); },
                delegate(object fresh) { return ArcRead.Row(fresh, req.Kind, req.Code) == null; });
        }

        static HashSet<string> Codes(object conn, string customer)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, "SELECT cContactCode AS c FROM Crm_Contact WHERE cCusCode=?",
                new object[] { customer }, MaxCodes + 1);
            if (rows.Count > MaxCodes)
            {
                throw ArcGuard.State("客户 " + customer + " 的联系人太多，请在 U8 客户端处理");
            }
            HashSet<string> codes = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < rows.Count; i++)
            {
                codes.Add(ArcRead.Cell(rows[i], "c") ?? "");
            }
            return codes;
        }

        // 新增的回读：这个客户、这个名称、调用前没有的编码恰好一行，且调用方给的字段一致；找到后把 req.Code 换成 U8 编的号。
        // 一行新行都没有返回 false（按 U8 的应答判断）；有新行但不唯一或字段不符，不管 U8 应答如何都 504 并带上新行编码，
        // 免得调用方当成没写进去再发一次、建出重复的联系人。
        static bool Created(object conn, ArcReq req, string customer, string name, HashSet<string> before)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, "SELECT cContactCode AS c FROM Crm_Contact WHERE cCusCode=? AND cContactName=?",
                new object[] { customer, (name ?? "").Trim() }, MaxCodes);
            List<string> fresh = new List<string>();
            for (int i = 0; i < rows.Count; i++)
            {
                string code = ArcRead.Cell(rows[i], "c") ?? "";
                if (code.Length > 0 && !before.Contains(code))
                {
                    fresh.Add(code);
                }
            }
            if (fresh.Count == 0)
            {
                return false;
            }
            Dictionary<string, string> row = fresh.Count == 1 ? ArcRead.Row(conn, req.Kind, customer + ":" + fresh[0]) : null;
            if (row == null || !CallerMatches(req, row))
            {
                throw new BridgeException(504, "outcome_unknown", "U8 已新增客户联系人，但回读不能确认是哪一行或字段不符（新行编码 "
                    + string.Join("、", fresh.ToArray()) + "），请先 get 核对，不要直接重发");
            }
            req.Code = customer + ":" + fresh[0];
            return true;
        }

        // 修改的回读：调用方给的文字字段与表里一致；其余每一列与修改前相同（不比 Volatile；性别、婚姻状况原来为空、
        // 桥按「不详」补发的也不比）。
        static bool Unchanged(object conn, ArcReq req, Dictionary<string, string> before)
        {
            Dictionary<string, string> after = ArcRead.Row(conn, req.Kind, req.Code);
            if (after == null || !CallerMatches(req, after))
            {
                return false;
            }
            HashSet<string> skip = new HashSet<string>(Volatile, StringComparer.OrdinalIgnoreCase);
            IList<string> tags = req.Fields.Tags;
            for (int i = 0; i < tags.Count; i++)
            {
                skip.Add(req.Map.Column(tags[i]));
            }
            SkipDefaulted(req, before, skip, ArcPartnerContactMap.Sex);
            SkipDefaulted(req, before, skip, ArcPartnerContactMap.Marriage);
            HashSet<string> cols = new HashSet<string>(before.Keys, StringComparer.OrdinalIgnoreCase);
            cols.UnionWith(after.Keys);
            foreach (string col in cols)
            {
                if (!skip.Contains(col) && Cell(before, col) != Cell(after, col))
                {
                    return false;
                }
            }
            return true;
        }

        static void SkipDefaulted(ArcReq req, Dictionary<string, string> before, HashSet<string> skip, string tag)
        {
            string col = req.Map.Column(tag);
            if (Cell(before, col).Length == 0)
            {
                skip.Add(col);
            }
        }

        static bool CallerMatches(ArcReq req, Dictionary<string, string> row)
        {
            IList<string> tags = req.Fields.Tags;
            for (int i = 0; i < tags.Count; i++)
            {
                if (Plain(tags[i]) && !string.Equals(Cell(row, req.Map.Column(tags[i])), (req.Fields.Get(tags[i]) ?? "").Trim(),
                    StringComparison.OrdinalIgnoreCase))
                {
                    return false;
                }
            }
            return true;
        }

        // 性别、婚姻状况在表里是编号，不逐字比较；生日（yyyy-mm-dd，表里零点的日期按同样格式读出）、主要联系人（0 / 1）照比。
        static bool Plain(string tag)
        {
            return tag != ArcPartnerContactMap.Sex && tag != ArcPartnerContactMap.Marriage;
        }

        static string Cell(Dictionary<string, string> row, string col)
        {
            string value;
            row.TryGetValue(col ?? "", out value);
            return (value ?? "").Trim();
        }

        static void Overlay(ArcReq req, ArcBag bag)
        {
            IList<string> tags = req.Fields.Tags;
            for (int i = 0; i < tags.Count; i++)
            {
                bag.Put(tags[i], req.Fields.Get(tags[i]));
            }
        }

        static void Missing(ArcBag bag, string tag)
        {
            string value = bag.Get(tag);
            if (value == null || value.Trim().Length == 0)
            {
                bag.Put(tag, ArcPartnerContactMap.Unknown);
            }
        }

        // 信封：先发 code、name、of_customer，再发其余标签（性别、婚姻状况等）。
        internal static string Envelope(string proc, string customer, string code, ArcBag bag)
        {
            StringBuilder sb = new StringBuilder();
            sb.Append("<ufinterface roottag='").Append(Root).Append("' billtype='' docid='").Append(Guid.NewGuid().ToString("N"))
                .Append("' receiver='u8' sender='' proc='").Append(proc)
                .Append("' codeexchanged='N' exportneedexch='N' version='2.0'><").Append(Root).Append('>');
            ArcPartnerXml.Tag(sb, "code", code);
            if (bag.Has("name"))
            {
                ArcPartnerXml.Tag(sb, "name", bag.Get("name"));
            }
            ArcPartnerXml.Tag(sb, "of_customer", customer);
            IList<string> tags = bag.Tags;
            for (int i = 0; i < tags.Count; i++)
            {
                if (tags[i] != "name")
                {
                    ArcPartnerXml.Tag(sb, tags[i], bag.Get(tags[i]));
                }
            }
            sb.Append("</").Append(Root).Append("></ufinterface>");
            return sb.ToString();
        }

        static string Transact(WorkContext ctx, string xml)
        {
            if (ctx.Session == null || ctx.Session.Login == null)
            {
                throw new BridgeException(500, "internal", "档案写入缺少 U8 登录");
            }
            object cc = null;
            try
            {
                cc = ComUtil.Create(CustomerProgId);
                if (cc == null)
                {
                    throw new BridgeException(503, "com_unavailable", CustomerProgId + " 未注册");
                }
                // 登录 by-ref 交给自己提交的导入组件，本次登录不放回缓存（LoginCache）。
                ctx.DropLogin();
                object[] args = new object[] { xml, ctx.Session.Login };
                return Values.Text(ComUtil.CallRef(cc, "Transact", args, new int[] { 1 }));
            }
            finally
            {
                ComUtil.Final(cc);
            }
        }
    }
}
