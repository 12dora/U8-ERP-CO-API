using System;
using System.Collections.Generic;

namespace U8Co
{
    // meta 的字段白名单探测：用各领域类自己的判断函数试候选字段名，得到 exact（逐个列出的字段）
    // 和 spans（cdefine / cfree 编号区间）。候选名 = 各白名单常量的并集，判断逻辑只有原类一份。
    internal static class MetaFields
    {
        const int ProbeMax = 64;
        static readonly string[] SpanPrefixes = new string[] { "cdefine", "cfree" };
        static readonly string[] Universe = BuildUniverse();

        // allow 收小写字段名；抛 BridgeException 视为不允许。
        internal static Dictionary<string, object> Side(Func<string, bool> allow)
        {
            List<string> exact = new List<string>();
            for (int i = 0; i < Universe.Length; i++)
            {
                if (Try(allow, Universe[i]))
                {
                    exact.Add(Universe[i]);
                }
            }
            Dictionary<string, object> side = new Dictionary<string, object>();
            side["exact"] = exact.ToArray();
            side["spans"] = Spans(allow).ToArray();
            return side;
        }

        // 把只会抛 400 的行校验函数（如 MfgReq.LineOf）变成判断函数：单字段行不抛即允许。
        internal static Func<string, bool> RowCheck(Action<Dictionary<string, object>> check)
        {
            return delegate(string low)
            {
                Dictionary<string, object> row = new Dictionary<string, object>();
                row[low] = "1";
                check(row);
                return true;
            };
        }

        internal static Func<string, bool> InList(string[] names)
        {
            HashSet<string> set = new HashSet<string>(names, StringComparer.Ordinal);
            return delegate(string low) { return set.Contains(low); };
        }

        internal static string[] Csv(string csv)
        {
            return csv.Split(new char[] { ',' }, StringSplitOptions.RemoveEmptyEntries);
        }

        static bool Try(Func<string, bool> allow, string low)
        {
            try
            {
                return allow(low);
            }
            catch (BridgeException)
            {
                return false;
            }
        }

        static List<object> Spans(Func<string, bool> allow)
        {
            List<object> spans = new List<object>();
            for (int p = 0; p < SpanPrefixes.Length; p++)
            {
                AddSpans(spans, allow, SpanPrefixes[p]);
            }
            return spans;
        }

        static void AddSpans(List<object> spans, Func<string, bool> allow, string prefix)
        {
            int from = 0;
            for (int n = 1; n <= ProbeMax + 1; n++)
            {
                bool ok = n <= ProbeMax && Try(allow, prefix + n.ToString(System.Globalization.CultureInfo.InvariantCulture));
                if (ok && from == 0)
                {
                    from = n;
                }
                if (!ok && from > 0)
                {
                    spans.Add(Span(prefix, from, n - 1));
                    from = 0;
                }
            }
        }

        static Dictionary<string, object> Span(string prefix, int from, int to)
        {
            Dictionary<string, object> span = new Dictionary<string, object>();
            span["prefix"] = prefix;
            span["from"] = from;
            span["to"] = to;
            return span;
        }

        static string[] BuildUniverse()
        {
            SortedSet<string> all = new SortedSet<string>(StringComparer.Ordinal);
            AddAll(all, Csv(SoFields.HeadExact));
            AddAll(all, Csv(SoFields.LineExact));
            AddAll(all, Csv(PuFields.HeadExact));
            AddAll(all, Csv(PuFields.LineExact));
            AddAll(all, StockDom.MetaNames());
            AddAll(all, SaleGen.MetaNames());
            AddAll(all, PuArr.MetaHeadKeys());
            AddAll(all, PuInvReq.HeadKeyList());
            AddAll(all, Csv(ArapReq.CloseHead));
            AddAll(all, Csv(ArapReq.CloseLine));
            AddAll(all, Csv(ArapReq.VouchHead));
            AddAll(all, Csv(ArapReq.VouchLine));
            AddAll(all, new string[] { "source_line_id", "quantity" });
            // 请购单。
            AddAll(all, Csv(PuAppFields.HeadExact));
            AddAll(all, Csv(PuAppFields.LineExact));
            // 生产订单新增（MoCreateReq）。
            AddAll(all, MoCreateReq.HeadNames());
            AddAll(all, MoCreateReq.LineNames());
            AddAll(all, MoUpdateReq.LineNames());  // 生产订单修改（MoUpdateReq）：另有 define22–25、define28–33
            // 物料清单新增、修改（BomReq）。
            AddAll(all, BomReq.CreateHeadNames());
            AddAll(all, BomReq.LineNames());
            // 质量单据生单（QmReq）。
            AddAll(all, QmReq.MetaNames());
            // 不良品处理单生单（QmRejReq）。
            AddAll(all, QmRejReq.MetaNames());
            // 其他报检单新增（QmOthReq）。
            AddAll(all, QmOthReq.MetaNames());
            // 采购结算参照采购发票（PuSettleReq）。
            AddAll(all, PuSettleReq.MetaNames());
            AddAll(all, PuSettleManReq.MetaLineNames());  // 采购手工结算（PuSettleManReq）
            // 无来源新增（SrcLessReq）。
            AddAll(all, SrcLessReq.MetaNames());
            string[] list = new string[all.Count];
            all.CopyTo(list);
            return list;
        }

        static void AddAll(SortedSet<string> all, IEnumerable<string> names)
        {
            foreach (string name in names)
            {
                string low = name.Trim().ToLowerInvariant();
                if (low.Length > 0)
                {
                    all.Add(low);
                }
            }
        }
    }
}
