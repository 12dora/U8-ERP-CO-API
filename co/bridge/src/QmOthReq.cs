using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 校验过的其他报检单新增请求（QmOthReq.Parse）。Head 的键是小写字段名，值已去两端空白。
    internal sealed class QmOthAsk
    {
        public string Date = "";
        public readonly Dictionary<string, string> Head = new Dictionary<string, string>(StringComparer.Ordinal);
        public readonly List<QmOthLine> Lines = new List<QmOthLine>();

        public string Get(string low)
        {
            string value;
            return Head.TryGetValue(low, out value) ? value ?? "" : "";
        }
    }

    // 一行：存货、数量、检验方式（-1 表示没送，取存货档案的 iTestStyle，档案为空取 3）、仓库。
    internal sealed class QmOthLine
    {
        public string Inv = "";
        public decimal Qty;
        public int TestStyle = -1;
        public string Wh = "";
    }

    // 其他报检单（qm_other_inspect）vouchers/create、其他检验单（qm_other_check）vouchers/generate 的请求校验，
    // 登录前和处理函数里各一次，字段名不分大小写。其他报检单：表头 ddate、cinspectdepcode、cdefine1–16、chdefine11–16
    // （模板把某些自定义项设为必输时，缺了走模板 400）；表体 1 到 200 行 {cinvcode, quantity, iteststyle?, cwhcode?}，
    // 不收来源字段。其他检验单同来料检验单（QmReq，表头必须有 ccheckpersoncode，表体恰好 1 行）。
    internal static class QmOthReq
    {
        public const int LinesMax = 200;
        public const int TestStyleMax = 3;
        static readonly string[] HeadKeys = new string[] { "ddate", "cinspectdepcode" };
        static readonly string[] LineKeys = new string[] { "cinvcode", "quantity", "iteststyle", "cwhcode" };
        static readonly string[] Required = new string[] { "cinvcode", "quantity" };
        // 模板必输缺项时 400 里写的请求字段名（QmRejTpl 的调用方专用表；表里没有的照共用表，再没有写模板字段名）。
        internal static readonly string[][] TplNames = new string[][]
        {
            new string[] { "CINSPECTDEPCODE", "cInspectDepCode" },
            new string[] { "CINVCODE", "cInvCode" },
            new string[] { "ITESTSTYLE", "iTestStyle" },
            new string[] { "CCHECKPERSONCODE", "cCheckPersonCode" }
        };

        internal static QmTplAsk TplAsk(string skip)
        {
            QmTplAsk ask = new QmTplAsk();
            ask.Skip = skip;
            ask.Names = TplNames;
            return ask;
        }

        // QmReq.Check 的钩子：两类单据的新增 / 生单、审核、删除都用 QM 登录，新增和生单在登录前校验。其他类型返回 false。
        public static bool Check(WorkItem item, string path)
        {
            QmOthSpec spec = item == null ? null : QmOthSpec.Of(item.Type);
            if (spec == null)
            {
                return false;
            }
            if (path == "/u8co/v1/vouchers/create" && spec.Inspect)
            {
                Parse(item.Head, item.Lines);
            }
            else if (path == "/u8co/v1/vouchers/generate" && !spec.Inspect)
            {
                QmReq.Parse(QmOthSpec.CheckAsk, item.Head, item.Lines);
            }
            else if (path != "/u8co/v1/vouchers/delete" && path != "/u8co/v1/vouchers/verify")
            {
                return false;
            }
            item.SubId = QmCo.LoginSub;
            return true;
        }

        public static QmOthAsk Parse(Dictionary<string, object> head, object[] lines)
        {
            QmOthAsk ask = new QmOthAsk();
            try
            {
                ParseHead(ask, head ?? new Dictionary<string, object>());
            }
            catch (BridgeException ex)
            {
                throw FieldPath.Under(ex, "head");
            }
            if (lines == null || lines.Length < 1 || lines.Length > LinesMax)
            {
                throw BridgeException.BadField("lines", "表体须为 1 到 200 行");
            }
            for (int i = 0; i < lines.Length; i++)
            {
                try
                {
                    ask.Lines.Add(ParseLine(lines[i]));
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
            return ask;
        }

        static void ParseHead(QmOthAsk ask, Dictionary<string, object> head)
        {
            foreach (KeyValuePair<string, object> kv in head)
            {
                string low = kv.Key == null ? "" : kv.Key.ToLowerInvariant();
                if (!HeadKey(low))
                {
                    throw BridgeException.BadField(kv.Key, "不能设置字段 " + kv.Key).WithHint(FieldPath.WritableHint);
                }
                ask.Head[low] = QmReq.Text(kv.Value, kv.Key, 120);
            }
            ask.Date = ask.Get("ddate");
            DateTime parsed;
            if (ask.Date.Length > 0 && !DateTime.TryParseExact(ask.Date, "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out parsed))
            {
                throw BridgeException.BadField("ddate", "单据日期必须是 yyyy-MM-dd");
            }
        }

        static QmOthLine ParseLine(object raw)
        {
            Dictionary<string, object> row = raw as Dictionary<string, object>;
            if (row == null)
            {
                throw new BridgeException(400, "bad_request", "表体行不是对象");
            }
            foreach (KeyValuePair<string, object> kv in row)
            {
                if (!LineKey(kv.Key == null ? "" : kv.Key.ToLowerInvariant()))
                {
                    throw BridgeException.BadField(kv.Key, "不能设置字段 " + kv.Key).WithHint(FieldPath.WritableHint);
                }
            }
            QmOthLine line = new QmOthLine();
            line.Inv = QmReq.Text(MfgReq.Raw(row, "cinvcode"), "cinvcode", 60);
            if (line.Inv.Length == 0)
            {
                throw BridgeException.BadField("cinvcode", "必须指定存货 cinvcode");
            }
            line.Qty = MfgReq.LineQty(row);
            line.TestStyle = Style(MfgReq.Raw(row, "iteststyle"));
            line.Wh = QmReq.Text(MfgReq.Raw(row, "cwhcode"), "cwhcode", 60);
            return line;
        }

        // 检验方式：iteststyle 为 0–3 的整数；未送则 -1。
        internal static int Style(object value)
        {
            if (value == null)
            {
                return -1;
            }
            long n = value is int ? (int)value : value is long ? (long)value : -1L;
            if (n < 0 || n > TestStyleMax)
            {
                throw BridgeException.BadField("iteststyle", "iteststyle 必须是 0 到 3 的整数");
            }
            return (int)n;
        }

        internal static bool HeadKey(string low)
        {
            return Array.IndexOf(HeadKeys, low) >= 0 || QmReq.Span(low, "cdefine", 1, 16) || QmReq.Span(low, "chdefine", 11, 16);
        }

        internal static bool LineKey(string low)
        {
            return Array.IndexOf(LineKeys, low) >= 0;
        }

        // meta 的 create 必填表体字段（MetaWritable.CreateLine 的兜底）：其他报检单 cinvcode、quantity，其余类型没有。
        internal static string[] CreateRequired(VoucherKind kind)
        {
            return QmOthSpec.IsInspect(kind) ? (string[])Required.Clone() : new string[0];
        }

        // meta 用的全部候选字段名。
        internal static string[] MetaNames()
        {
            List<string> all = new List<string>(HeadKeys);
            all.AddRange(LineKeys);
            return all.ToArray();
        }
    }
}
