using System;
using System.Collections.Generic;
using System.Globalization;
using System.Xml;

namespace U8Co
{
    // 按 U8 的单据编号规则取号：USCOMMON.PublicSub.GetBillRuleXml 给出规则（单据类型=卡片号、各前缀），
    // 本类按表头值填好每个前缀的「种子」，再交给 UFBillComponent.clsBillComponent.GetNumber 取号并占用流水。
    // 例：其他入库单规则 RK + 单据日期(年月日) + 4 位流水，得到 RK202601010001；
    // 不填种子时 GetNumber 返回空串，PublicSub.SetBillSerial 会退回成不带前缀的流水，不能用。
    internal static class BillNo
    {
        public static string Allocate(object usLogin, object u8Login, string vouchType, Dictionary<string, object> head)
        {
            object pub = null;
            object bill = null;
            try
            {
                pub = Need(ComUtil.Create("USCOMMON.PublicSub"), "USCOMMON");
                string rule = Values.Text(ComUtil.Call(pub, "GetBillRuleXml", new object[] { usLogin, vouchType }));
                XmlDocument doc = new XmlDocument();
                doc.LoadXml(rule);
                string card = doc.DocumentElement == null ? "" : doc.DocumentElement.GetAttribute("单据类型").Trim();
                if (card.Length == 0)
                {
                    throw new BridgeException(409, "u8_rejected", "U8 没有该单据的编号规则");
                }
                FillSeeds(doc, head);
                bill = Need(ComUtil.Create("UFBillComponent.clsBillComponent"), "UFBillComponent");
                string conn = Values.Text(ComUtil.Get(u8Login, "UfDbName"));
                if (!Values.Flag(ComUtil.Call(bill, "InitBill", new object[] { conn, card })))
                {
                    throw new BridgeException(409, "u8_rejected", "U8 编号组件初始化失败");
                }
                string code = Values.Text(ComUtil.Call(bill, "GetNumber", new object[] { doc.OuterXml, true })).Trim();
                if (code.Length == 0)
                {
                    throw new BridgeException(409, "u8_rejected", "U8 没有返回单据号");
                }
                return code;
            }
            finally
            {
                ComUtil.Final(bill);
                ComUtil.Final(pub);
            }
        }

        static object Need(object com, string name)
        {
            if (com == null)
            {
                throw new BridgeException(503, "com_unavailable", name + " 未注册");
            }
            return com;
        }

        // 对象类型 2 是日期（规则 年月日/年月/年），其他带源字段的前缀取表头同名字段；手工输入（5）自带种子。
        static void FillSeeds(XmlDocument doc, Dictionary<string, object> head)
        {
            foreach (XmlNode node in doc.DocumentElement.ChildNodes)
            {
                XmlElement prefix = node as XmlElement;
                if (prefix == null || prefix.Name != "前缀" || prefix.GetAttribute("种子").Length > 0)
                {
                    continue;
                }
                string field = prefix.GetAttribute("源表字段名").Trim();
                string value = Field(head, field);
                if (prefix.GetAttribute("对象类型") == "2")
                {
                    value = DateSeed(value, prefix.GetAttribute("规则"));
                }
                if (value.Length == 0)
                {
                    throw new BridgeException(400, "bad_request", "编号规则需要表头字段 " + field);
                }
                prefix.SetAttribute("种子", value);
            }
        }

        static string Field(Dictionary<string, object> head, string field)
        {
            if (field.Length == 0 || head == null)
            {
                return "";
            }
            foreach (KeyValuePair<string, object> kv in head)
            {
                if (string.Equals(kv.Key, field, StringComparison.OrdinalIgnoreCase) && kv.Value != null)
                {
                    return Convert.ToString(kv.Value, CultureInfo.InvariantCulture).Trim();
                }
            }
            return "";
        }

        static string DateSeed(string text, string rule)
        {
            DateTime date;
            string[] formats = new string[] { "yyyy-MM-dd", "yyyy-MM-dd HH:mm:ss", "yyyy-MM-ddTHH:mm:ss" };
            if (!DateTime.TryParseExact(text, formats, CultureInfo.InvariantCulture, DateTimeStyles.None, out date))
            {
                throw new BridgeException(400, "bad_request", "单据日期必须是 yyyy-MM-dd");
            }
            if (rule == "年月")
            {
                return date.ToString("yyyyMM", CultureInfo.InvariantCulture);
            }
            if (rule == "年")
            {
                return date.ToString("yyyy", CultureInfo.InvariantCulture);
            }
            return date.ToString("yyyyMMdd", CultureInfo.InvariantCulture);
        }
    }
}
