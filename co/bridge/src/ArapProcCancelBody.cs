using System.Collections.Generic;

namespace U8Co
{
    // arap/process/cancel 响应里的明细：items 是删掉的处理行按（账、单据、行、往来单位）汇总的借贷原币；
    // restored 是加回了余额的收付款单行、应收应付单（取消后的余额）。发票累计核销的回写不列（事务内已核对）。
    internal static class ArapProcCancelBody
    {
        public static List<object> Items(ProcCancelPlan plan)
        {
            List<object> items = new List<object>();
            Dictionary<string, Dictionary<string, object>> seen = new Dictionary<string, Dictionary<string, object>>();
            foreach (ProcRow row in plan.Rows)
            {
                string partner = CoRows.Col(row.Head, "cDwCode");
                string key = row.Ledger + "|" + row.CoType + "|" + row.CoCode + "|" + row.BVid + "|" + partner;
                Dictionary<string, object> one;
                if (!seen.TryGetValue(key, out one))
                {
                    one = Item(plan, row, partner);
                    seen[key] = one;
                    items.Add(one);
                }
                one["debit"] = (decimal)one["debit"] + row.DF;
                one["credit"] = (decimal)one["credit"] + row.CF;
            }
            return items;
        }

        static Dictionary<string, object> Item(ProcCancelPlan plan, ProcRow row, string partner)
        {
            ProcDoc doc = plan.DocOf(row.Ledger, row.CoType, row.CoCode);
            Dictionary<string, object> one = new Dictionary<string, object>();
            one["ledger"] = row.Ledger;
            one["type"] = doc == null ? row.CoType : doc.Name;
            one["id"] = doc == null || doc.Id <= 0 ? null : (object)doc.Id;
            one["code"] = row.CoCode;
            one["line_id"] = row.BVid > 0 ? (object)row.BVid : null;
            one["partner"] = partner;
            one["debit"] = 0m;
            one["credit"] = 0m;
            return one;
        }

        public static List<object> Restored(ProcCancelPlan plan)
        {
            List<object> list = new List<object>();
            foreach (ProcRemain line in plan.Lines)
            {
                list.Add(Remain(plan, line, line.Line));
            }
            foreach (ProcRemain bill in plan.Bills)
            {
                list.Add(Remain(plan, bill, 0));
            }
            return list;
        }

        static Dictionary<string, object> Remain(ProcCancelPlan plan, ProcRemain r, int line)
        {
            ProcDoc doc = plan.DocOf(r.Ledger, r.VType, r.Code);
            Dictionary<string, object> one = new Dictionary<string, object>();
            one["ledger"] = r.Ledger;
            one["type"] = doc == null ? r.VType : doc.Name;
            one["id"] = doc == null || doc.Id <= 0 ? null : (object)doc.Id;
            one["code"] = r.Code;
            one["line_id"] = line > 0 ? (object)line : null;
            one["amount"] = r.Back;
            one["remaining"] = r.Before + r.Back;
            return one;
        }
    }
}
