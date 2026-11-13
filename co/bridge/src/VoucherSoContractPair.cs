using System;
using System.Collections.Generic;

namespace U8Co
{
    // 销售订单合同号回写的成对设置（vouchers/so_contract，mode=pair）：合同号 cDefine1、内部合同号 cDefine2 一起设成请求值，
    // 已有别的值也覆盖（这正是用途），并发靠 expect：两列当前值须与 expect 相符（空值等价同 VoucherSoContract.Same）。
    // 两列都已是目标（去两端空白后逐字相等）时不写库；cascade=true 时照常连带下游。replace、customer_contract_no 不收。
    // 本类只放解析和纯判断（自检覆盖）；下游的查询和写入在 VoucherSoContractDown。
    internal static class VoucherSoContractPair
    {
        public const string Mode = "pair";
        // 下游单据的结论与跳过原因（响应 cascade[].result / reason）。
        public const string Updated = "updated";
        public const string Unchanged = "unchanged";
        public const string Skipped = "skipped";
        public const string MixedSources = "mixed_sources";
        public const string Locked = "locked";

        const string K1 = VoucherSoContract.ContractKey;
        const string K2 = VoucherSoContract.InternalKey;

        // 带 mode 键：必须是 "pair"（显式 null 也 400）。
        internal static bool Wanted(Dictionary<string, object> body)
        {
            if (body == null || !body.ContainsKey(VoucherSoContract.ModeKey))
            {
                return false;
            }
            string mode = body[VoucherSoContract.ModeKey] as string;
            if (mode != Mode)
            {
                throw ArcReq.Bad(VoucherSoContract.ModeKey + " 只能是 " + Mode, VoucherSoContract.ModeKey);
            }
            return true;
        }

        // 不带 mode 时不收成对设置的键（免得静默忽略）。
        internal static void RefuseOutside(Dictionary<string, object> body)
        {
            Refuse(body, K2, "只在 mode=pair 时可用");
            Refuse(body, VoucherSoContract.CascadeKey, "只在 mode=pair 时可用");
        }

        // contract_no、internal_contract_no 必填（规则同 contract_no），expect 带 contract_no、internal_contract_no 两键，cascade 可省。
        internal static SoContractAsk Parse(Dictionary<string, object> body)
        {
            Refuse(body, VoucherSoContract.ReplaceKey, "mode=pair 不收");
            Refuse(body, VoucherSoContract.CustomerKey, "mode=pair 不收（cDefine2 用 " + K2 + "）");
            SoContractAsk ask = new SoContractAsk();
            ask.Pair = true;
            ask.ContractNo = VoucherSoContract.Value(VoucherSoContract.Required(body, K1), K1);
            ask.CustomerNo = VoucherSoContract.Value(VoucherSoContract.Required(body, K2), K2);
            string cascade = VoucherSoContract.CascadeKey;
            ask.Cascade = body.ContainsKey(cascade) && VoucherSoContract.Flag(body[cascade], cascade);
            Dictionary<string, object> expect = VoucherSoContract.ExpectMap(body, K2);
            ask.Expect1 = VoucherSoContract.ExpectText(expect, K1);
            ask.Expect2 = VoucherSoContract.ExpectText(expect, K2);
            return ask;
        }

        static void Refuse(Dictionary<string, object> body, string key, string why)
        {
            if (body != null && body.ContainsKey(key))
            {
                throw ArcReq.Bad(key + " " + why, key);
            }
        }

        // 纯判断：先核对 expect（空值等价、去空白、不分大小写），再看两列各自是否已是目标（去两端空白后逐字相等）。
        internal static SoContractPlan Decide(SoContractAsk ask, SoContractSnap snap)
        {
            if (!VoucherSoContract.Same(ask.Expect1, snap.Contract) || !VoucherSoContract.Same(ask.Expect2, snap.Customer))
            {
                throw ArcCustomerRow.Mismatch("销售订单表头与 expect 不符：合同号 " + VoucherSoContract.Show(snap.Contract)
                    + "，内部合同号 " + VoucherSoContract.Show(snap.Customer), Current(snap.Contract, snap.Customer));
            }
            SoContractPlan plan = new SoContractPlan();
            plan.SetContract = !Exact(snap.Contract, ask.ContractNo);
            plan.SetCustomer = !Exact(snap.Customer, ask.CustomerNo);
            plan.Changed = plan.SetContract || plan.SetCustomer;
            return plan;
        }

        // 成对设置的「相同」：去两端空白后逐字相等（区分大小写；NULL 当空串）。
        internal static bool Exact(string a, string b)
        {
            return string.Equals((a ?? "").Trim(), (b ?? "").Trim(), StringComparison.Ordinal);
        }

        internal static Dictionary<string, object> Current(string contract, string internalNo)
        {
            Dictionary<string, object> current = new Dictionary<string, object>();
            current[K1] = contract;
            current[K2] = internalNo;
            return current;
        }

        // 下游单据的纯判断（顺序固定）：已是这一对 → unchanged；任一来源订单（本单按写后值）不是这一对、或有明细没有来源订单
        // → mixed_sources；被别的操作员锁定（表头有 cLocker 列时）→ locked；否则 updated。列宽另由 CheckWidths 核对（超出 400）。
        internal static void DecideDown(SoContractAsk ask, SoDownDoc doc, string user)
        {
            doc.Reason = null;
            doc.Result = Skipped;
            if (Exact(doc.Contract, ask.ContractNo) && Exact(doc.Internal, ask.CustomerNo))
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

        // 每张来源订单都是这一对：本单（ask.Id）按写后值算；来源 id 为 0 表示有明细没有来源订单，不算一致；一张来源都没有也不算。
        internal static bool SourcesAgree(SoContractAsk ask, List<SoDownSource> sources)
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
                if (s.Id != ask.Id && !(Exact(s.Contract, ask.ContractNo) && Exact(s.Internal, ask.CustomerNo)))
                {
                    return false;
                }
            }
            return true;
        }

        static bool Over(string value, int limit)
        {
            return limit > 0 && value != null && value.Length > limit;
        }

        // 要改的下游单据（updated）的列宽：任一值超过该表 cDefine1 / cDefine2 的列宽，400 bad_request（field 为请求字段，消息带单号），
        // 在任何写入之前（预演同样）。
        internal static void CheckWidths(SoContractAsk ask, List<SoDownDoc> docs)
        {
            foreach (SoDownDoc doc in docs)
            {
                if (doc.Result != Updated)
                {
                    continue;
                }
                if (Over(ask.ContractNo, doc.Limit1))
                {
                    throw Wide(K1, doc, doc.Limit1);
                }
                if (Over(ask.CustomerNo, doc.Limit2))
                {
                    throw Wide(K2, doc, doc.Limit2);
                }
            }
        }

        static BridgeException Wide(string field, SoDownDoc doc, int limit)
        {
            return BridgeException.BadField(field, field + " 超过下游单据 " + doc.Code + "（" + doc.Kind.Type + "）的列宽 "
                + limit.ToString(System.Globalization.CultureInfo.InvariantCulture));
        }

        // 响应 cascade 列表：每张下游单据 {type, id, code, result, reason?, before:{contract_no, internal_contract_no}}。
        internal static List<object> Entries(List<SoDownDoc> docs)
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
                entry["before"] = Current(doc.Contract, doc.Internal);
                list.Add(entry);
            }
            return list;
        }

        // 把 cascade 列表放进响应（docs 为 null 表示没要连带，不加这个键）。
        internal static Dictionary<string, object> WithCascade(Dictionary<string, object> body, List<SoDownDoc> docs)
        {
            if (docs != null)
            {
                body[VoucherSoContract.CascadeKey] = Entries(docs);
            }
            return body;
        }
    }

    // 一张下游单据：表头当前的两列原值（NULL 为 null）、锁定人、行版本、列宽上限（0 不限）、全部来源订单，以及判断结论。
    internal sealed class SoDownDoc
    {
        public SoDownKind Kind;
        public int Id;
        public string Code;
        public string Contract;
        public string Internal;
        public string Locker;
        public string Ts;
        public int Limit1;
        public int Limit2;
        public List<SoDownSource> Sources = new List<SoDownSource>();
        // 表体解析出的来源订单 ID（升序、去重，0 为无来源）。
        public List<int> SourceIds = new List<int>();
        public string Result;
        public string Reason;
    }

    // 一张来源订单（Id 为 0：有明细找不到来源订单）及其表头两列的当前原值。
    internal sealed class SoDownSource
    {
        public int Id;
        public string Contract;
        public string Internal;
    }
}
