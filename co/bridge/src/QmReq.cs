using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 质量单据生单请求：登录前和处理函数里各校验一次，字段名不分大小写。
    // 报检单（QM01 参照到货单、QM02 参照生产订单）：表头 ddate、cdepcode、cinspectdepcode、cdefine1–16；
    // 表体 1 到 200 行 {source_line_id, quantity, cwhcode?}。
    // 检验单（QM03 / QM04 参照报检单）：表头 ccheckpersoncode（必填）、cdepcode、project_code、cchkconclusion、fdtquantity、
    // ddate、cdefine1–16、chdefine11–16、items，另收 cyieldercode、dyielddate（让步接收核准人，QmYield）；表体恰好 1 行 {source_line_id, quantity, fregquantity?, fconquantiy?, fdisquantity?}。
    internal static class QmReq
    {
        public const int ItemsMax = 50;
        public const int ItemTextMax = 60;
        public const string Qualified = "合格";
        public const string Unqualified = "不合格";
        static readonly string[] InspectHead = new string[] { "ddate", "cdepcode", "cinspectdepcode" };
        static readonly string[] CheckHead = new string[]
        {
            "ddate", "cdepcode", "ccheckpersoncode", "project_code", "cchkconclusion", "fdtquantity", "items",
            QmYield.CodeKey, QmYield.DateKey
        };
        static readonly string[] InspectLine = new string[] { "source_line_id", "quantity", "cwhcode" };
        static readonly string[] CheckLine = new string[]
        {
            "source_line_id", "quantity", "fregquantity", "fconquantiy", "fdisquantity"
        };
        static readonly string[] ItemKeys = new string[] { "cchkitemcode", "cchkguidecode", "ccheckvalue", "ctargetqjug" };

        // RequestsP4.CheckKind 的钩子：报检单、检验单（QM01–QM04）的生单、删除改用 QM 登录，生单在登录前校验。其他返回 false。
        // 来料报检单不开放单独审核、弃审（Verifiable 为假，vouchers/verify 在 RequestsGuard 就 400）；产品报检单
        // 只开放弃审，action 不是 unverify 在登录前 400（QmInsUnverify）。
        public static bool Check(WorkItem item, string path)
        {
            // 检验单、其他报检单、其他检验单修改：同样用 QM 登录，登录前校验（QmEditReq）。
            if (QmEditReq.Check(item, path))
            {
                return true;
            }
            // 不良品处理单：生单、审核、删除同样用 QM 登录（QmRejReq）。
            if (QmRejReq.Check(item, path))
            {
                return true;
            }
            // 其他报检单新增、其他检验单生单、审核、删除同样用 QM 登录（QmOthReq）。
            if (QmOthReq.Check(item, path))
            {
                return true;
            }
            QmSpec spec = item == null ? null : QmSpec.Of(item.Type);
            if (spec == null)
            {
                return false;
            }
            if (path == "/u8co/v1/vouchers/generate")
            {
                Parse(spec, item.Head, item.Lines);
                item.SubId = QmCo.LoginSub;
                return true;
            }
            QmInsUnverify.PreLogin(item, path);
            if (path == "/u8co/v1/vouchers/delete" || path == "/u8co/v1/vouchers/verify")
            {
                item.SubId = QmCo.LoginSub;
                return true;
            }
            return false;
        }

        // Json.CheckMap 的钩子：检验单表头的 items 是数组，放行给 Parse 校验（其他检验单同样）。
        public static bool ListField(VoucherKind kind, string key, object value)
        {
            QmSpec spec = QmSpec.Of(kind);
            bool check = (spec != null && !spec.Inspect) || QmOthSpec.IsCheck(kind);
            return check && QmSpec.Same(key, "items") && value is IList;
        }

        public static QmAsk Parse(QmSpec spec, Dictionary<string, object> head, object[] lines)
        {
            QmAsk ask = new QmAsk();
            ask.Spec = spec;
            try
            {
                ParseHead(ask, head ?? new Dictionary<string, object>());
            }
            catch (BridgeException ex)
            {
                throw FieldPath.Under(ex, "head");
            }
            ParseLines(ask, lines);
            return ask;
        }

        static void ParseHead(QmAsk ask, Dictionary<string, object> head)
        {
            foreach (KeyValuePair<string, object> kv in head)
            {
                string low = kv.Key == null ? "" : kv.Key.ToLowerInvariant();
                if (!HeadKey(ask.Spec, low))
                {
                    throw BridgeException.BadField(kv.Key, "不能设置字段 " + kv.Key).WithHint(FieldPath.WritableHint);
                }
                if (low == "items")
                {
                    ask.Items = QmItemAsk.ParseList(kv.Value);
                    continue;
                }
                if (low == "fdtquantity")
                {
                    ask.Dt = Qty(kv.Value, "fdtquantity", false);
                    continue;
                }
                ask.Head[low] = Text(kv.Value, kv.Key, 255);
            }
            ask.Date = ask.Get("ddate");
            if (ask.Date.Length > 0 && !IsDate(ask.Date))
            {
                throw BridgeException.BadField("ddate", "单据日期必须是 yyyy-MM-dd");
            }
            if (!ask.Spec.Inspect && ask.Get("ccheckpersoncode").Length == 0)
            {
                throw BridgeException.BadField("ccheckpersoncode", "必须指定检验员 ccheckpersoncode");
            }
            QmYield.CheckDate(ask.Get(QmYield.DateKey), QmYield.DateKey);  // 让步接收核准日期
            string conclusion = ask.Get("cchkconclusion");
            if (conclusion.Length > ItemTextMax)
            {
                throw BridgeException.BadField("cchkconclusion", "cchkconclusion 不能超过 60 字");
            }
        }

        static void ParseLines(QmAsk ask, object[] lines)
        {
            int max = ask.Spec.Inspect ? 200 : 1;
            if (lines == null || lines.Length < 1 || lines.Length > max)
            {
                throw BridgeException.BadField("lines",
                    ask.Spec.Inspect ? "表体须为 1 到 200 行" : "参照报检单生成检验单只能有 1 行");
            }
            HashSet<int> seen = new HashSet<int>();
            for (int i = 0; i < lines.Length; i++)
            {
                QmLineAsk line;
                try
                {
                    line = ParseLine(ask.Spec, lines[i]);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
                if (!seen.Add(line.SourceLineId))
                {
                    throw BridgeException.BadField(FieldPath.Item("lines", i) + ".source_line_id", "来源明细重复");
                }
                ask.Lines.Add(line);
            }
        }

        static QmLineAsk ParseLine(QmSpec spec, object raw)
        {
            Dictionary<string, object> row = raw as Dictionary<string, object>;
            if (row == null)
            {
                throw new BridgeException(400, "bad_request", "表体行不是对象");
            }
            string[] keys = spec.Inspect ? InspectLine : CheckLine;
            foreach (KeyValuePair<string, object> kv in row)
            {
                if (!Listed(keys, kv.Key == null ? "" : kv.Key.ToLowerInvariant()))
                {
                    throw BridgeException.BadField(kv.Key, "不能设置字段 " + kv.Key).WithHint(FieldPath.WritableHint);
                }
            }
            QmLineAsk line = new QmLineAsk();
            line.SourceLineId = MfgReq.LineId(row);
            line.Qty = MfgReq.LineQty(row);
            line.Wh = Text(MfgReq.Raw(row, "cwhcode"), "cwhcode", 60);
            if (!spec.Inspect)
            {
                SplitOf(line, row);
            }
            return line;
        }

        // 合格 = 检验数量 − 让步 − 不良（没送时）；三者之和必须等于检验数量。
        static void SplitOf(QmLineAsk line, Dictionary<string, object> row)
        {
            object reg = MfgReq.Raw(row, "fregquantity");
            line.Con = Opt(MfgReq.Raw(row, "fconquantiy"), "fconquantiy");
            line.Dis = Opt(MfgReq.Raw(row, "fdisquantity"), "fdisquantity");
            line.Reg = QmMath.Qualified(line.Qty, reg != null, reg == null ? 0m : Qty(reg, "fregquantity", true),
                line.Con, line.Dis);
        }

        static decimal Opt(object value, string name)
        {
            return value == null ? 0m : Qty(value, name, true);
        }

        internal static bool HeadKey(QmSpec spec, string low)
        {
            if (Listed(spec.Inspect ? InspectHead : CheckHead, low) || Span(low, "cdefine", 1, 16))
            {
                return true;
            }
            return !spec.Inspect && Span(low, "chdefine", 11, 16);
        }

        internal static bool LineKey(QmSpec spec, string low)
        {
            return Listed(spec.Inspect ? InspectLine : CheckLine, low);
        }

        // meta 用的全部候选字段名。
        internal static string[] MetaNames()
        {
            List<string> all = new List<string>();
            all.AddRange(InspectHead);
            all.AddRange(CheckHead);
            all.AddRange(InspectLine);
            all.AddRange(CheckLine);
            all.AddRange(ItemKeys);
            all.AddRange(QmEditReq.MetaNames());
            for (int n = 11; n <= 16; n++)
            {
                all.Add("chdefine" + n.ToString(CultureInfo.InvariantCulture));
            }
            return all.ToArray();
        }

        internal static string[] ItemNames()
        {
            return (string[])ItemKeys.Clone();
        }

        // prefix + 区间内的编号，拒绝前导零（cdefine01）。
        internal static bool Span(string low, string prefix, int from, int to)
        {
            if (low == null || !low.StartsWith(prefix, StringComparison.Ordinal))
            {
                return false;
            }
            string tail = low.Substring(prefix.Length);
            int n;
            if (tail.Length == 0 || !int.TryParse(tail, NumberStyles.None, CultureInfo.InvariantCulture, out n))
            {
                return false;
            }
            return n >= from && n <= to && tail == n.ToString(CultureInfo.InvariantCulture);
        }

        static bool Listed(string[] names, string low)
        {
            return Array.IndexOf(names, low) >= 0;
        }

        // 字符串或数字（按不变区域格式）；布尔、对象、数组拒绝。
        internal static string Text(object value, string name, int max)
        {
            if (value == null)
            {
                return "";
            }
            if (!(value is string) && !(value is int) && !(value is long) && !(value is double) && !(value is decimal))
            {
                throw BridgeException.BadField(FieldPath.Clean(name), "字段 " + name + " 的值只能是字符串或数字");
            }
            string text = Convert.ToString(value, CultureInfo.InvariantCulture).Trim();
            if (text.Length > max)
            {
                throw BridgeException.BadField(FieldPath.Clean(name), "字段 " + name + " 过长");
            }
            return text;
        }

        // 数量：有限数、不超过 1000000000000、最多 6 位小数；zero 为真时允许 0。
        internal static decimal Qty(object value, string name, bool zero)
        {
            Dictionary<string, object> probe = new Dictionary<string, object>();
            probe["quantity"] = value;
            if (zero && IsZero(value))
            {
                return 0m;
            }
            try
            {
                return MfgReq.LineQty(probe);
            }
            catch (BridgeException)
            {
                throw BridgeException.BadField(FieldPath.Clean(name),
                    name + " 必须是" + (zero ? "不小于" : "大于") + " 0 且不超过 1000000000000 的数，最多 6 位小数");
            }
        }

        static bool IsZero(object value)
        {
            if (value is int)
            {
                return (int)value == 0;
            }
            if (value is long)
            {
                return (long)value == 0L;
            }
            if (value is decimal)
            {
                return (decimal)value == 0m;
            }
            return value is double && (double)value == 0d;
        }

        static bool IsDate(string text)
        {
            DateTime date;
            return DateTime.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out date);
        }
    }
}
