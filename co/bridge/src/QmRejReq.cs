using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 校验过的不良品处理单生单请求（QmRejReq.Parse）。Head 的键是小写字段名。
    internal sealed class QmRejAsk
    {
        public QmRejSpec Spec;
        public int CheckId;
        public string Date = "";
        public readonly Dictionary<string, string> Head = new Dictionary<string, string>(StringComparer.Ordinal);
        public readonly List<QmRejLine> Lines = new List<QmRejLine>();

        // 各行处理数量之和（须等于检验单的待处理不良数量）。
        public decimal Total()
        {
            decimal sum = 0m;
            for (int i = 0; i < Lines.Count; i++)
            {
                sum += Lines[i].Qty;
            }
            return sum;
        }
    }

    // 一行处置：处理方式（QMSCRAPDISPOSE）、不良原因（Reason，iReasontype=1）、降级后存货（处理流程 2 必填）、仓库。
    // Flow 与名称由 QmRejSrc 按档案补齐。
    internal sealed class QmRejLine
    {
        public int SourceLineId;
        public decimal Qty;
        public string Dispose = "";
        public string Reason = "";
        public string DimInv = "";
        public string Wh = "";
        public int Flow = -1;
        public string DisposeName = "";
        public string ReasonName = "";
        // 处理后存货的计量单位与换算率（流程 2 且存货有换算时，QmRejUnits.Dim）。
        public string DimUnit = "";
        public decimal DimRate;
    }

    // 不良品处理单生单请求：登录前和处理函数里各校验一次，字段名不分大小写。
    // 表头 ddate、cdefine1–16、chdefine11–16（扩展自定义项，同检验单；QMREJECTVOUCHER 没有备注列）；表体 1 到 200 行
    // {source_line_id（= 检验单 ID，一张检验单就是一个来源行，可重复）, quantity, cscrapdiscode, creasoncode, cdiminvcode?, cbwhcode?}。
    internal static class QmRejReq
    {
        public const int LinesMax = 200;
        static readonly string[] HeadKeys = new string[] { "ddate" };
        static readonly string[] LineKeys = new string[]
        {
            "source_line_id", "quantity", "cscrapdiscode", "creasoncode", "cdiminvcode", "cbwhcode"
        };

        // QmReq.Check 的钩子：不良品处理单的生单、审核、删除用 QM 登录，生单在登录前校验。其他类型返回 false。
        public static bool Check(WorkItem item, string path)
        {
            QmRejSpec spec = item == null ? null : QmRejSpec.Of(item.Type);
            if (spec == null)
            {
                return false;
            }
            if (path == "/u8co/v1/vouchers/generate")
            {
                Parse(spec, item.Id, item.Head, item.Lines);
            }
            else if (path != "/u8co/v1/vouchers/delete" && path != "/u8co/v1/vouchers/verify")
            {
                return false;
            }
            item.SubId = QmCo.LoginSub;
            return true;
        }

        public static QmRejAsk Parse(QmRejSpec spec, int checkId, Dictionary<string, object> head, object[] lines)
        {
            QmRejAsk ask = new QmRejAsk();
            ask.Spec = spec;
            ask.CheckId = checkId;
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

        static void ParseHead(QmRejAsk ask, Dictionary<string, object> head)
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
            string date;
            ask.Date = ask.Head.TryGetValue("ddate", out date) ? date : "";
            DateTime parsed;
            if (ask.Date.Length > 0 && !DateTime.TryParseExact(ask.Date, "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out parsed))
            {
                throw BridgeException.BadField("ddate", "单据日期必须是 yyyy-MM-dd");
            }
        }

        static void ParseLines(QmRejAsk ask, object[] lines)
        {
            if (lines == null || lines.Length < 1 || lines.Length > LinesMax)
            {
                throw BridgeException.BadField("lines", "表体须为 1 到 200 行");
            }
            for (int i = 0; i < lines.Length; i++)
            {
                try
                {
                    ask.Lines.Add(ParseLine(ask.CheckId, lines[i]));
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
        }

        static QmRejLine ParseLine(int checkId, object raw)
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
            QmRejLine line = new QmRejLine();
            line.SourceLineId = MfgReq.LineId(row);
            // 检验单只有一个来源行（检验单本身），source_line_id 必须是请求的检验单 ID。
            if (checkId > 0 && line.SourceLineId != checkId)
            {
                throw BridgeException.BadField("source_line_id", "source_line_id 必须等于检验单 ID");
            }
            line.Qty = MfgReq.LineQty(row);
            line.Dispose = Need(row, "cscrapdiscode", "必须指定处理方式 cscrapdiscode");
            line.Reason = Need(row, "creasoncode", "必须指定不良原因 creasoncode");
            line.DimInv = QmReq.Text(MfgReq.Raw(row, "cdiminvcode"), "cdiminvcode", 60);
            line.Wh = QmReq.Text(MfgReq.Raw(row, "cbwhcode"), "cbwhcode", 60);
            return line;
        }

        static string Need(Dictionary<string, object> row, string name, string message)
        {
            string text = QmReq.Text(MfgReq.Raw(row, name), name, 60);
            if (text.Length == 0)
            {
                throw BridgeException.BadField(name, message);
            }
            return text;
        }

        internal static bool HeadKey(string low)
        {
            return Array.IndexOf(HeadKeys, low) >= 0 || QmReq.Span(low, "cdefine", 1, 16) || QmReq.Span(low, "chdefine", 11, 16);
        }

        internal static bool LineKey(string low)
        {
            return Array.IndexOf(LineKeys, low) >= 0;
        }

        // meta 用的全部候选字段名。
        internal static string[] MetaNames()
        {
            List<string> all = new List<string>(HeadKeys);
            all.AddRange(LineKeys);
            for (int n = 11; n <= 16; n++)
            {
                all.Add("chdefine" + n.ToString(CultureInfo.InvariantCulture));
            }
            return all.ToArray();
        }
    }
}
