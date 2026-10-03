using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 供应商联系人的修改、删除（受控 SQL；U8 的分发器回「供应商联系人不支持修改和删除导入」）。一个事务：UPDLOCK, HOLDLOCK
    // 读联系人行、过闸门、写、事务内再读一遍核对、提交，再在新连接上回读核对，不符 504。
    // 修改只写调用方给的标签（列名来自固定标签表 ArcPartnerContactMap.VendorPairs）：性别、婚姻状况按 U8 基础编码换成编号
    // （Crm_BaseCode_Base.ID，同客户联系人）；生日按日期写；空串写成 NULL；变更人、变更日期按本操作员、当前时间补上。
    // 主要联系人：设为主要时不能已有其他主要联系人；供应商档案上登记的主要联系人（Vendor.cVenContactCode，存联系人 OID）不能取消主要。
    // 删除照客户联系人的规则：被供应商档案（及对照）的主要联系人、采购和委外单据、进项发票登记、资格审批或合同引用的不能删除。
    internal static class ArcVenContactSql
    {
        const string LockSql = "SELECT OID, CONVERT(varchar(4), CONVERT(int, bMajor)) AS m FROM Ven_Contact WITH (UPDLOCK, HOLDLOCK)"
            + " WHERE cVenCode=? AND cContactCode=?";
        const string IdSql = "SELECT TOP 1 b.ID AS i FROM Crm_BaseCode_Base b JOIN Crm_BaseCode_Lang l ON l.BaseCodeOID=b.OID"
            + " AND l.LocaleID='zh-CN' WHERE b.cType=? AND l.cName=? ORDER BY b.ID";
        const string DelSql = "DELETE FROM Ven_Contact WHERE cVenCode=? AND cContactCode=?";
        const int RevisorMax = 20;
        const string VendorMainSql = "SELECT TOP 1 1 AS x FROM Vendor v JOIN Ven_Contact k ON CONVERT(nvarchar(80), v.cVenContactCode)=k.OID"
            + " WHERE k.cVenCode=? AND k.cContactCode=?";
        // 每条两个参数（供应商编码, 联系人编码），查到就 409。供应商档案（及对照）上的主要联系人是 uniqueidentifier，存的是
        // Ven_Contact.OID，经 Ven_Contact 按 OID 连；合同联系人表没有供应商列，只按联系人编码查（第一个参数只占位）。
        static readonly string[] Refs = new string[]
        {
            "供应商档案的主要联系人", VendorMainSql,
            "供应商对照的联系人", "SELECT TOP 1 1 AS x FROM Vendor_ContraRef v JOIN Ven_Contact k ON CONVERT(nvarchar(80), v.cVenContactCode)=k.OID"
                + " WHERE k.cVenCode=? AND k.cContactCode=?",
            "采购订单", "SELECT TOP 1 1 AS x FROM PO_Pomain WHERE cVenCode=? AND cContactCode=?",
            "采购发票", "SELECT TOP 1 1 AS x FROM PurBillVouch WHERE cVenCode=? AND cContactCode=?",
            "委外订单", "SELECT TOP 1 1 AS x FROM OM_MOMain WHERE cVenCode=? AND cContactCode=?",
            "进项发票登记", "SELECT TOP 1 1 AS x FROM TI_InVatInvoiceReg WHERE cVenCode=? AND cContactCode=?",
            "供应商资格审批", "SELECT TOP 1 1 AS x FROM pu_vendorverify WHERE cvencode=? AND cVenPersonCode=?",
            "合同", "SELECT TOP 1 1 AS x FROM CM_Contract_B WHERE ? IS NOT NULL AND cContactCode=?",
            "合同", "SELECT TOP 1 1 AS x FROM CM_Contract_C WHERE ? IS NOT NULL AND cContactCode=?"
        };

        public static ApiResult Write(WorkContext ctx, ArcReq req, string[] parts)
        {
            object conn = ctx.Conn;
            object[] key = new object[] { parts[0], parts[1] };
            Dictionary<string, string> coded = null;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                Dictionary<string, object> row = Rows.One(conn, LockSql, key);
                if (row == null)
                {
                    throw new BridgeException(404, "not_found", "档案不存在");
                }
                if (req.Op == "delete")
                {
                    Delete(conn, key);
                }
                else
                {
                    MainGate(conn, req, parts, row);
                    coded = Coded(conn, req);
                    Update(ctx, req, key, coded);
                }
                ArcDryRun.Set(req.Kind.Name, req.Code, req.Op, ArcDryRun.Row(conn, req.Kind, req.Code));
                ctx.Item.TranAfter = CoTrans.Count(conn);
                CoTrans.Commit(conn);
                open = false;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
            CoRows.Note(ctx.Item, "Ven_Contact " + req.Op + " " + req.Code);
            return Readback(ctx, req, coded);
        }

        // 设为主要：不能已有其他主要联系人；取消主要：不能是供应商档案上登记的主要联系人。
        static void MainGate(object conn, ArcReq req, string[] parts, Dictionary<string, object> row)
        {
            string major = req.Fields.Get(ArcPartnerContactMap.Major);
            if (major == "1")
            {
                ArcVenContact.OtherMain(conn, parts[0], parts[1]);
            }
            else if (major == "0" && (ArcRead.Cell(row, "m") ?? "").Trim() == "1" && Rows.One(conn, VendorMainSql, parts) != null)
            {
                throw ArcGuard.State("联系人是供应商档案上登记的主要联系人，不能取消主要联系人，请在 U8 供应商档案里处理");
            }
        }

        static void Delete(object conn, object[] key)
        {
            for (int i = 0; i + 1 < Refs.Length; i += 2)
            {
                if (Rows.One(conn, Refs[i + 1], key) != null)
                {
                    throw ArcGuard.State("联系人已被" + Refs[i] + "引用，不能删除");
                }
            }
            GlSql.Exec(conn, DelSql, key);
            if (Rows.One(conn, LockSql, key) != null)
            {
                throw new BridgeException(500, "internal", "供应商联系人删除后仍能读到，已回滚");
            }
        }

        // 性别、婚姻状况的文字换成 U8 基础编码的编号（列名 → 编号文本）。换不出 409。
        static Dictionary<string, string> Coded(object conn, ArcReq req)
        {
            Dictionary<string, string> coded = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            string[] tags = new string[] { ArcPartnerContactMap.Sex, ArcPartnerContactMap.Marriage };
            for (int i = 0; i < tags.Length; i++)
            {
                string text = req.Fields.Get(tags[i]);
                if (text == null)
                {
                    continue;
                }
                string id = Rows.Scalar(conn, IdSql, new object[] { ArcPartnerContactMap.BaseType(tags[i]), text });
                if (string.IsNullOrEmpty(id))
                {
                    throw ArcGuard.State("U8 基础编码里没有 " + tags[i] + "「" + text + "」");
                }
                coded[req.Map.Column(tags[i])] = id.Trim();
            }
            return coded;
        }

        // UPDATE Ven_Contact SET <列>=?, …, cRevisor=?, dModifyDate=GETDATE() WHERE cVenCode=? AND cContactCode=?；
        // 列名只来自固定标签表。写完在事务内读回核对。
        static void Update(WorkContext ctx, ArcReq req, object[] key, Dictionary<string, string> coded)
        {
            StringBuilder sql = new StringBuilder("UPDATE Ven_Contact SET ");
            List<object> args = new List<object>();
            IList<string> tags = req.Fields.Tags;
            for (int i = 0; i < tags.Count; i++)
            {
                string col = req.Map.Column(tags[i]);
                sql.Append(col).Append(tags[i] == ArcPartnerContactMap.Birthday ? "=CONVERT(datetime, CONVERT(date, ?, 23)), " : "=?, ");
                args.Add(Value(req, tags[i], col, coded));
            }
            string revisor = ctx.OperatorName.Trim();
            sql.Append("cRevisor=?, dModifyDate=GETDATE() WHERE cVenCode=? AND cContactCode=?");
            args.Add(revisor.Length > RevisorMax ? revisor.Substring(0, RevisorMax) : revisor);
            args.AddRange(key);
            GlSql.Exec(ctx.Conn, sql.ToString(), args.ToArray());
            Dictionary<string, string> after = ArcRead.Row(ctx.Conn, req.Kind, req.Code);
            if (after == null || !CallerMatches(req, after, coded))
            {
                throw new BridgeException(500, "internal", "供应商联系人修改后核对不一致，已回滚");
            }
        }

        // 参数值：性别、婚姻状况是编号，主要联系人 0 / 1，其余文字（空串写 NULL）。
        static object Value(ArcReq req, string tag, string col, Dictionary<string, string> coded)
        {
            string text = req.Fields.Get(tag) ?? "";
            string id;
            if (coded.TryGetValue(col, out id))
            {
                return int.Parse(id, NumberStyles.Integer, CultureInfo.InvariantCulture);
            }
            if (tag == ArcPartnerContactMap.Major)
            {
                return text == "1" ? 1 : 0;
            }
            return text.Length == 0 ? null : text;
        }

        // 新连接上回读：删除后读不到；修改后调用方给的字段一致。
        static ApiResult Readback(WorkContext ctx, ArcReq req, Dictionary<string, string> coded)
        {
            bool ok = ArcPartnerRun.Fresh(ctx, req, delegate(object fresh)
            {
                Dictionary<string, string> row = ArcRead.Row(fresh, req.Kind, req.Code);
                return req.Op == "delete" ? row == null : row != null && CallerMatches(req, row, coded);
            });
            if (!ok)
            {
                throw new BridgeException(504, "outcome_unknown", ArcPartnerRun.Unknown(req));
            }
            return ArcPartnerRun.Done(req);
        }

        // 调用方给的标签与表里一致（文字去掉首尾空白、不分大小写；生日按 yyyy-mm-dd、主要联系人按 0 / 1 读出）。
        // 性别、婚姻状况：coded 给了编号就比编号，否则不比（EAI 新增时由 U8 换编号）。
        internal static bool CallerMatches(ArcReq req, Dictionary<string, string> row, Dictionary<string, string> coded)
        {
            IList<string> tags = req.Fields.Tags;
            for (int i = 0; i < tags.Count; i++)
            {
                string col = req.Map.Column(tags[i]);
                string want = (req.Fields.Get(tags[i]) ?? "").Trim();
                bool code = tags[i] == ArcPartnerContactMap.Sex || tags[i] == ArcPartnerContactMap.Marriage;
                if (code && (coded == null || !coded.TryGetValue(col, out want)))
                {
                    continue;
                }
                if (!string.Equals(Cell(row, col), want, StringComparison.OrdinalIgnoreCase))
                {
                    return false;
                }
            }
            return true;
        }

        static string Cell(Dictionary<string, string> row, string col)
        {
            string value;
            row.TryGetValue(col ?? "", out value);
            return (value ?? "").Trim();
        }
    }
}
