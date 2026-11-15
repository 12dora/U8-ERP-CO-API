using System;
using System.Collections.Generic;
using System.Globalization;
using System.Security;
using System.Text;
using System.Xml;

namespace U8Co
{
    internal sealed class GlReply
    {
        public string Succeed;
        public string Dsc;
        public int No;
        public int Period;
        public int Year;
    }

    // 外部系统来源（应收应付制单 ArapVoucher 用）：制单系统（coutsysname）、外部业务类型（reserve1 → coutsign）、
    // 外部业务号（reserve2 → coutno_id）、每条分录的原始单据类型 / 单据号 / 日期（bill_type / bill_id / bill_date）和业务员。
    // 总账自己的新增、修改不传（null），报文与原来完全一样。
    internal sealed class GlSource
    {
        public string MakingSystem = "GL";
        public string OutSign = "";
        public string OutNo = "";
        public string BillType = "";
        public string BillId = "";
        public string BillDate = "";
        public Dictionary<int, string> Operators = new Dictionary<int, string>();

        internal string[] At(int entry)
        {
            string op;
            return new string[] { BillType, BillId, Operators.TryGetValue(entry, out op) ? op : "" };
        }
    }

    // U8PzInsert.clsPZInsert.Transact 的报文。元素顺序和空元素同实测跑通的报文。
    // 状态字段（cashier、signature、checker、posting_*、revokeflag）一律留空，导入器会原样写进库。
    internal static class GlXml
    {
        // carry：修改时从原凭证带过来、调用方不能填的字段（备注、外部业务类型、原始单据、业务员）；新增传 null。
        public static string Envelope(bool add, GlKey key, GlDraft draft, string date, string maker, GlCarry carry)
        {
            return Envelope(add, key, draft, new string[] { date, maker }, carry, null);
        }

        // when：[制单日期, 制单人]；source：外部系统来源（只在新增时用，carry 为 null）。
        public static string Envelope(bool add, GlKey key, GlDraft draft, string[] when, GlCarry carry, GlSource source)
        {
            string date = when[0];
            string maker = when[1];
            StringBuilder sb = new StringBuilder(4096);
            sb.Append("<?xml version=\"1.0\" encoding=\"utf-8\"?><ufinterface roottag=\"voucher\" billtype=\"gl\"");
            sb.Append(" docid=\"u8co\" receiver=\"u8\" sender=\"999\" proc=\"").Append(add ? "add" : "edit");
            sb.Append("\" codeexchanged=\"Y\" exportneedexch=\"N\" renewproofno=\"").Append(add ? "y" : "false");
            sb.Append("\" version=\"2.0\"><voucher id=\"\"><voucher_head><company/>");
            Tag(sb, "voucher_type", key.Sign);
            Tag(sb, "fiscal_year", Int(key.Year));
            Tag(sb, "accounting_period", Int(key.Period));
            Tag(sb, "voucher_id", add ? "" : Int(key.No));
            Tag(sb, "attachment_number", Int(draft.Attachments));
            Tag(sb, "date", date);
            Tag(sb, "enter", maker);
            sb.Append("<cashier/><signature/><checker/><posting_date/><posting_person/>");
            Origin(sb, carry, source);
            sb.Append("<revokeflag></revokeflag></voucher_head><voucher_body>");
            string billDate = source == null ? "" : source.BillDate;
            for (int i = 0; i < draft.Lines.Count; i++)
            {
                Entry(sb, draft.Lines[i], i + 1, Old(carry, source, i + 1), billDate);
            }
            sb.Append("</voucher_body></voucher></ufinterface>");
            return sb.ToString();
        }

        // 制单系统、备注、外部业务类型、外部业务号：总账自己的新增是 GL 和空，修改沿用原凭证，外部来源按 source。
        static void Origin(StringBuilder sb, GlCarry carry, GlSource source)
        {
            Tag(sb, "voucher_making_system", source == null ? "GL" : source.MakingSystem);
            Tag(sb, "memo1", carry == null ? "" : carry.Memo1);
            Tag(sb, "memo2", carry == null ? "" : carry.Memo2);
            string outSign = carry == null ? "" : carry.OutSign;
            Tag(sb, "reserve1", source != null ? source.OutSign : outSign);
            Tag(sb, "reserve2", source == null ? "" : source.OutNo);
        }

        static string[] Old(GlCarry carry, GlSource source, int entry)
        {
            if (source != null)
            {
                return source.At(entry);
            }
            return carry == null ? GlCarry.Empty : carry.At(entry);
        }

        // old：原凭证同一分录号上的 [原始单据类型, 原始单据号, 业务员]。
        static void Entry(StringBuilder sb, GlLine line, int index, string[] old, string billDate)
        {
            // 借方有数（含红字负数，处理制单照 U8 记负数）就写借方，否则写贷方。
            bool debit = line.Debit != 0;
            decimal amount = debit ? line.Debit : line.Credit;
            sb.Append("<entry>");
            Tag(sb, "entry_id", Int(index));
            Tag(sb, "account_code", line.Account);
            Tag(sb, "abstract", line.Digest);
            Tag(sb, "settlement", line.Settle);
            Tag(sb, "document_id", line.DocNo);
            Tag(sb, "document_date", line.DocDate);
            Tag(sb, "currency", line.Currency);
            // 数量只有红字冲销（GlReverse）会是负数（与金额同号，单价仍为正）；新增、修改的数量都大于 0。
            Tag(sb, "unit_price", line.Qty != 0 ? Dec(decimal.Round(amount / line.Qty, 6, MidpointRounding.AwayFromZero)) : "");
            sb.Append("<exchange_rate1/>");
            Tag(sb, "exchange_rate2", line.Rate > 0 ? Dec(line.Rate) : "");
            Side(sb, "debit", debit, line, amount);
            Side(sb, "credit", !debit, line, amount);
            Tag(sb, "bill_type", old[0]);
            Tag(sb, "bill_id", old[1]);
            Tag(sb, "bill_date", billDate);
            sb.Append("<auxiliary_accounting>");
            Aux(sb, "dept_id", line.Dept);
            Aux(sb, "personnel_id", line.Person);
            Aux(sb, "cust_id", line.Customer);
            Aux(sb, "supplier_id", line.Supplier);
            Aux(sb, "item_class", line.ItemClass);
            Aux(sb, "item_id", line.Item);
            Aux(sb, "operator", old[2]);
            sb.Append("</auxiliary_accounting><detail><cash_flow_statement>");
            foreach (GlFlow flow in line.Flows)
            {
                sb.Append("<cash_flow cash_item=\"").Append(Esc(flow.Item));
                sb.Append("\" natural_debit_currency=\"").Append(Amount(flow.Debit));
                sb.Append("\" natural_credit_currency=\"").Append(Amount(flow.Credit)).Append("\"/>");
            }
            sb.Append("</cash_flow_statement><code_remark_statement/></detail></entry>");
        }

        // 数量、原币、辅币、本币；本方向填值，另一方向数量和原币留空、本币写 0（实测核对）。
        static void Side(StringBuilder sb, string side, bool active, GlLine line, decimal amount)
        {
            string qty = active && line.Qty != 0 ? Dec(line.Qty) : "";
            decimal fcValue = line.Fc != 0 ? line.Fc : (line.Rate > 0 ? decimal.Round(amount / line.Rate, 2, MidpointRounding.AwayFromZero) : 0);
            string fc = active && line.Rate > 0 ? Amount(fcValue) : "";
            Tag(sb, side + "_quantity", qty);
            Tag(sb, "primary_" + side + "_amount", fc);
            Tag(sb, "secondary_" + side + "_amount", "");
            Tag(sb, "natural_" + side + "_currency", active ? Amount(amount) : "0");
        }

        public static GlReply Parse(string xml)
        {
            if (xml == null || xml.Trim().Length == 0)
            {
                return null;
            }
            XmlElement item;
            try
            {
                XmlDocument doc = new XmlDocument();
                doc.XmlResolver = null;
                doc.LoadXml(xml);
                item = doc.SelectSingleNode("//item") as XmlElement;
            }
            catch (XmlException)
            {
                return null;
            }
            if (item == null)
            {
                return null;
            }
            GlReply reply = new GlReply();
            reply.Succeed = item.GetAttribute("succeed").Trim();
            reply.Dsc = item.GetAttribute("dsc").Trim();
            reply.No = ToInt(item.GetAttribute("u8voucher_id"));
            reply.Period = ToInt(item.GetAttribute("u8accounting_period"));
            reply.Year = ToInt(item.GetAttribute("u8accounting_year"));
            return reply;
        }

        static int ToInt(string text)
        {
            int value;
            int.TryParse((text ?? "").Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out value);
            return value;
        }

        static void Tag(StringBuilder sb, string name, string value)
        {
            if (value == null || value.Length == 0)
            {
                sb.Append('<').Append(name).Append("/>");
                return;
            }
            sb.Append('<').Append(name).Append('>').Append(Esc(value)).Append("</").Append(name).Append('>');
        }

        static void Aux(StringBuilder sb, string name, string value)
        {
            sb.Append("<item name=\"").Append(name).Append("\">").Append(Esc(value ?? "")).Append("</item>");
        }

        static string Esc(string text)
        {
            return SecurityElement.Escape(text ?? "") ?? "";
        }

        static string Int(int value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }

        static string Amount(decimal value)
        {
            if (value == 0)
            {
                return "0";
            }
            return value.ToString("0.00", CultureInfo.InvariantCulture);
        }

        static string Dec(decimal value)
        {
            return value.ToString("0.##########", CultureInfo.InvariantCulture);
        }
    }
}
