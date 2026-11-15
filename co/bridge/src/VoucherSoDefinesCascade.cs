using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 销售订单表头自定义项写入的下游连带（cascade=true）的纯判断（自检覆盖）：每张下游单据的结论、列类型与列宽核对、响应 cascade 列表。
    // 下游的查询和写入在 VoucherSoDefinesDown，来源解析在 VoucherSoDefinesLinks。
    internal static class VoucherSoDefinesCascade
    {
        // 下游单据的结论与跳过原因（响应 cascade[].result / reason）。
        public const string Updated = "updated";
        public const string Unchanged = "unchanged";
        public const string Skipped = "skipped";
        public const string MixedSources = "mixed_sources";
        public const string Locked = "locked";

        // 下游单据的判断（顺序固定）：请求各列都已是目标 → unchanged；任一来源订单（本单按写后值）有一列不是目标、或有明细没有
        // 来源订单 → mixed_sources；被别的操作员锁定（表头有 cLocker 列时）→ locked；否则 updated。列宽另由 CheckWidths 核对。
        internal static void DecideDown(SoDefinesAsk ask, SoDownDoc doc, string user)
        {
            doc.Reason = null;
            doc.Result = Skipped;
            if (VoucherSoDefines.AllExact(ask, doc.Values))
            {
                doc.Result = Unchanged;
            }
            else if (!SourcesAgree(ask, doc.Sources))
            {
                doc.Reason = MixedSources;
            }
            else if (VoucherLockGate.Blocks(doc.Locker, user))
            {
                doc.Reason = Locked;
            }
            else
            {
                doc.Result = Updated;
            }
        }

        // 每张来源订单的请求各列都是目标值：本单（ask.Id）按写后值算；来源 id 为 0 表示有明细没有来源订单，不算一致；一张来源都没有也不算。
        internal static bool SourcesAgree(SoDefinesAsk ask, List<SoDownSource> sources)
        {
            if (sources == null || sources.Count == 0)
            {
                return false;
            }
            foreach (SoDownSource s in sources)
            {
                if (s.Id <= 0)
                {
                    return false;
                }
                if (s.Id != ask.Id && !VoucherSoDefines.AllExact(ask, s.Values))
                {
                    return false;
                }
            }
            return true;
        }

        // 下游单据涉及的每类表头表都须有请求的各列且是文本列：否则 400 bad_request（field 为 fields.cdefineN），在任何写入之前（预演同样）。
        // cols 的键是「表名.列名」（不区分大小写）。
        internal static void CheckColumns(SoDefinesAsk ask, List<SoDownDoc> docs, Dictionary<string, SoDefinesColumn> cols)
        {
            foreach (SoDownDoc doc in docs)
            {
                foreach (string key in ask.Keys)
                {
                    SoDefinesColumn c;
                    string col = VoucherSoDefines.Column(key);
                    if (!cols.TryGetValue(doc.Kind.Table + "." + col, out c) || !c.Text)
                    {
                        string label = VoucherSoDefines.FieldsKey + "." + key;
                        throw BridgeException.BadField(label, label + "：下游单据表 " + doc.Kind.Table + " 没有文本列 " + col);
                    }
                }
            }
        }

        // 要改的下游单据（updated）的列宽：任一值超过该表对应列的列宽，400 bad_request（field 为 fields.cdefineN，消息带单号），
        // 在任何写入之前（预演同样）。
        internal static void CheckWidths(SoDefinesAsk ask, List<SoDownDoc> docs)
        {
            foreach (SoDownDoc doc in docs)
            {
                if (doc.Result != Updated)
                {
                    continue;
                }
                foreach (string key in ask.Keys)
                {
                    int limit;
                    string value = ask.Values[key];
                    if (doc.Limits.TryGetValue(key, out limit) && limit > 0 && value.Length > limit)
                    {
                        string label = VoucherSoDefines.FieldsKey + "." + key;
                        throw BridgeException.BadField(label, label + " 超过下游单据 " + doc.Code + "（" + doc.Kind.Type
                            + "）的列宽 " + limit.ToString(CultureInfo.InvariantCulture));
                    }
                }
            }
        }

        // 响应 cascade 列表：每张下游单据 {type, id, code, result, reason?, before:{请求各列}}。
        internal static List<object> Entries(SoDefinesAsk ask, List<SoDownDoc> docs)
        {
            List<object> list = new List<object>();
            foreach (SoDownDoc doc in docs)
            {
                Dictionary<string, object> entry = new Dictionary<string, object>();
                entry["type"] = doc.Kind.Type;
                entry["id"] = doc.Id;
                entry["code"] = doc.Code;
                entry["result"] = doc.Result;
                if (doc.Reason != null)
                {
                    entry["reason"] = doc.Reason;
                }
                entry["before"] = VoucherSoDefines.Current(ask.Keys, doc.Values);
                list.Add(entry);
            }
            return list;
        }

        // 把 cascade 列表放进响应（docs 为 null 表示没要连带，不加这个键）。
        internal static Dictionary<string, object> WithCascade(Dictionary<string, object> body, SoDefinesAsk ask, List<SoDownDoc> docs)
        {
            if (docs != null)
            {
                body[VoucherSoDefines.CascadeKey] = Entries(ask, docs);
            }
            return body;
        }
    }

    // 一张下游单据：表头请求各列的原值（NULL 为 null）、锁定人、行版本、各列列宽（0 不限）、全部来源订单，以及判断结论。
    internal sealed class SoDownDoc
    {
        public SoDownKind Kind;
        public int Id;
        public string Code;
        public Dictionary<string, string> Values = new Dictionary<string, string>(System.StringComparer.Ordinal);
        public string Locker;
        public string Ts;
        public Dictionary<string, int> Limits = new Dictionary<string, int>(System.StringComparer.Ordinal);
        public List<SoDownSource> Sources = new List<SoDownSource>();
        // 表体解析出的来源订单 ID（升序、去重，0 为无来源）。
        public List<int> SourceIds = new List<int>();
        public string Result;
        public string Reason;
    }

    // 一张来源订单（Id 为 0：有明细找不到来源订单）及其表头请求各列的当前原值。
    internal sealed class SoDownSource
    {
        public int Id;
        public Dictionary<string, string> Values = new Dictionary<string, string>(System.StringComparer.Ordinal);
    }
}
