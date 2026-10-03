using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 无来源新增（vouchers/create）：发货单、先开票销售发票、到货单、材料出库单的字段名单和登录后、调用 U8 之前的校验。
    // 名单同时给 meta（MetaWritable 的钩子）。字段名不分大小写；值只能是字符串、数字或布尔。
    internal static class SrcLessReq
    {
        const decimal QtyMax = 1000000000000m;
        // 销售：表头同销售订单的常用列（不含预发货日期），另收 cvouchtype（仅发票，26 专票 / 27 普票）。
        const string SaHead = "ccuscode,cstcode,cdepcode,cpersoncode,cexch_name,iexchrate,itaxrate,ddate,cmemo,"
            + "cshipaddress,cscode,cpaycode";
        const string SaLine = "cwhcode,cinvcode,iquantity,inum,cunitid,iquotedprice,iunitprice,itaxunitprice,itaxrate,"
            + "kl,kl2,cbatch,cmemo";
        const string PuHead = "cvencode,cdepcode,cpersoncode,cptcode,ddate,cmemo";
        const string PuLine = "cwhcode,cinvcode,iquantity,ioricost,ioritaxcost,itaxrate";
        const string StLine = "cinvcode,iquantity,cbatch,cposition,cbmemo";

        static readonly string[] Kinds4 = new string[] { "dispatch", "sale_invoice", "arrival", "material_out" };

        public static bool Handles(VoucherKind kind)
        {
            return kind != null && Array.IndexOf(Kinds4, kind.Name) >= 0;
        }

        public static bool HeadAllowed(VoucherKind kind, string low)
        {
            string name = kind == null ? "" : kind.Name;
            if (name == "dispatch" || name == "sale_invoice")
            {
                return Listed(low, SaHead) || Span(low, "cdefine", 1, 16) || (name == "sale_invoice" && low == "cvouchtype");
            }
            if (name == "arrival")
            {
                return Listed(low, PuHead);
            }
            return name == "material_out" && MfgReq.HeadKey(low);
        }

        public static bool LineAllowed(VoucherKind kind, string low)
        {
            string name = kind == null ? "" : kind.Name;
            if (name == "dispatch" || name == "sale_invoice")
            {
                return Listed(low, SaLine) || Span(low, "cfree", 1, 10) || Span(low, "cdefine", 22, 37);
            }
            if (name == "arrival")
            {
                return Listed(low, PuLine);
            }
            return name == "material_out" && Listed(low, StLine);
        }

        // 必填：销售要客户、销售类型，行要仓库；到货单要供应商；材料出库要仓库和收发类别（缺收发类别先由 Check 报「必须指定收发类别」）。行一律要存货和数量。
        public static string[] RequiredHead(VoucherKind kind)
        {
            string name = kind == null ? "" : kind.Name;
            if (name == "dispatch" || name == "sale_invoice")
            {
                return new string[] { "ccuscode", "cstcode" };
            }
            if (name == "arrival")
            {
                return new string[] { "cvencode" };
            }
            return name == "material_out" ? new string[] { "cwhcode", "crdcode" } : null;
        }

        public static string[] RequiredLine(VoucherKind kind)
        {
            if (!Handles(kind))
            {
                return null;
            }
            if (kind.Name == "dispatch" || kind.Name == "sale_invoice")
            {
                return new string[] { "cwhcode", "cinvcode", "iquantity" };
            }
            return new string[] { "cinvcode", "iquantity" };
        }

        public static Func<string, bool>[] MetaRule(VoucherKind kind)
        {
            if (!Handles(kind))
            {
                return null;
            }
            return MetaWritable.Pair(
                delegate(string low) { return HeadAllowed(kind, low); },
                delegate(string low) { return LineAllowed(kind, low); });
        }

        public static string[] MetaNames()
        {
            List<string> all = new List<string>();
            all.AddRange(MetaFields.Csv(SaHead + ",cvouchtype," + SaLine + "," + PuHead + "," + PuLine + "," + StLine));
            all.AddRange(new string[] { "cwhcode", "crdcode", "cbmemo" });
            return all.ToArray();
        }

        // 名单、值类型、必填、行数、数量。
        public static void Check(VoucherKind kind, Dictionary<string, object> head, object[] lines)
        {
            if (head == null)
            {
                throw BridgeException.BadField("head", "表头必须是对象");
            }
            NeedRd(kind, head);
            try
            {
                CheckSide(kind, head, true);
                RequireAll(head, RequiredHead(kind));
            }
            catch (BridgeException ex)
            {
                throw FieldPath.Under(ex, "head");
            }
            if (lines == null || lines.Length < 1 || lines.Length > 200)
            {
                throw BridgeException.BadField("lines", "表体须为 1 到 200 行");
            }
            for (int i = 0; i < lines.Length; i++)
            {
                try
                {
                    CheckLine(kind, lines[i]);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
            CheckDate(MfgReq.Text(head, "ddate"));
            if (kind.Name == "sale_invoice")
            {
                InvoiceType(head);
            }
        }

        static void CheckLine(VoucherKind kind, object raw)
        {
            Dictionary<string, object> line = raw as Dictionary<string, object>;
            if (line == null)
            {
                throw new BridgeException(400, "bad_request", "表体行不是对象");
            }
            CheckSide(kind, line, false);
            RequireAll(line, RequiredLine(kind));
            Qty(line);
        }

        // 发票类型：26 专用发票（缺省）、27 普通发票。
        public static string InvoiceType(Dictionary<string, object> head)
        {
            string type = MfgReq.Text(head, "cvouchtype");
            if (type.Length == 0)
            {
                return "26";
            }
            if (type != "26" && type != "27")
            {
                throw BridgeException.BadField("head.cvouchtype", "cvouchtype 只能是 26 或 27");
            }
            return type;
        }

        // 销售无来源的币种与汇率，返回 { cexch_name, iexchrate }。U8 不给缺省，表头缺汇率时保存报「汇率不可以小于等于0」。
        // 不填或填本位币：写本位币、汇率 1（同参照生单从来源带出的值），另给的汇率必须是 1；外币必须给大于 0 的汇率。
        public static string[] Exch(Dictionary<string, object> head, string home)
        {
            string exch = MfgReq.Text(head, "cexch_name");
            string rate = MfgReq.Text(head, "iexchrate");
            decimal value = 0m;
            if (rate.Length > 0 && (!StockUnits.Dec(rate, out value) || value <= 0m))
            {
                throw BridgeException.BadField("head.iexchrate", "汇率必须大于 0");
            }
            if (exch.Length == 0 || exch == home)
            {
                if (rate.Length > 0 && value != 1m)
                {
                    throw BridgeException.BadField("head.iexchrate", "本位币 " + home + " 的汇率必须为 1");
                }
                return new string[] { home, "1" };
            }
            if (rate.Length == 0)
            {
                throw BridgeException.BadField("head.iexchrate", "外币 " + exch + " 必须填写汇率 iexchrate");
            }
            return new string[] { exch, value.ToString(CultureInfo.InvariantCulture) };
        }

        public static decimal Qty(Dictionary<string, object> line)
        {
            decimal qty;
            string text = MfgReq.Text(line, "iquantity");
            if (!StockUnits.Dec(text, out qty) || qty <= 0m || qty > QtyMax)
            {
                throw BridgeException.BadField("lines.iquantity", "数量必须大于 0 且不超过 1000000000000");
            }
            if (decimal.Round(qty, 6) != qty)
            {
                throw BridgeException.BadField("lines.iquantity", "数量最多 6 位小数");
            }
            return qty;
        }

        static void CheckSide(VoucherKind kind, Dictionary<string, object> map, bool head)
        {
            HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (KeyValuePair<string, object> kv in map)
            {
                string low = kv.Key == null ? "" : kv.Key.ToLowerInvariant();
                bool ok = head ? HeadAllowed(kind, low) : LineAllowed(kind, low);
                if (!ok)
                {
                    throw BridgeException.BadField(kv.Key, "不能设置字段 " + kv.Key).WithHint(FieldPath.WritableHint);
                }
                if (!seen.Add(low))
                {
                    throw BridgeException.BadField(kv.Key, "字段重复 " + kv.Key);
                }
                if (!Plain(kv.Value))
                {
                    throw BridgeException.BadField(kv.Key, "字段 " + kv.Key + " 的值只能是字符串、数字或布尔");
                }
            }
        }

        // 材料出库没有缺省收发类别：先于通用必填报「必须指定收发类别」（同生单）。
        static void NeedRd(VoucherKind kind, Dictionary<string, object> head)
        {
            if (kind.Name == "material_out")
            {
                MfgReq.NeedRd(head, false);
            }
        }

        static void RequireAll(Dictionary<string, object> map, string[] names)
        {
            for (int i = 0; names != null && i < names.Length; i++)
            {
                if (MfgReq.Text(map, names[i]).Length == 0)
                {
                    throw BridgeException.BadField(names[i], "必须填写 " + names[i]);
                }
            }
        }

        static void CheckDate(string text)
        {
            DateTime day;
            if (text.Length > 0 && !DateTime.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out day))
            {
                throw BridgeException.BadField("head.ddate", "单据日期必须是 yyyy-MM-dd");
            }
        }

        static bool Plain(object value)
        {
            if (value == null || value is string || value is bool || value is int || value is long)
            {
                return true;
            }
            return value is decimal || value is double;
        }

        static bool Listed(string key, string csv)
        {
            return ("," + csv + ",").IndexOf("," + key + ",", StringComparison.Ordinal) >= 0;
        }

        static bool Span(string key, string prefix, int from, int to)
        {
            if (key.Length <= prefix.Length || !key.StartsWith(prefix, StringComparison.Ordinal))
            {
                return false;
            }
            string tail = key.Substring(prefix.Length);
            int n;
            if (!int.TryParse(tail, NumberStyles.None, CultureInfo.InvariantCulture, out n))
            {
                return false;
            }
            return n >= from && n <= to && tail == n.ToString(CultureInfo.InvariantCulture);
        }
    }
}
