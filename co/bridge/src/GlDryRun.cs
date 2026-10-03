using System;
using System.Collections.Generic;

namespace U8Co
{
    // 总账新增、修改、记账的预演（validate）：检查都做完之后、自提交的组件之前停下，
    // 把桥要送给 U8 的内容放进 detail。凭证导入 U8PzInsert.Transact、记账事务都不调用。
    internal static class GlDryRun
    {
        // 新增、修改：停在 U8PzInsert.Transact 之前。
        public static void Voucher(WorkContext ctx, GlKey key, GlDraft draft, string date, string maker, string op)
        {
            if (!DryRun.Active)
            {
                return;
            }
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["op"] = op;
            head["iyear"] = key.Year;
            head["iperiod"] = key.Period;
            head["csign"] = key.Sign;
            if (key.No > 0)
            {
                head["ino_id"] = key.No;
            }
            head["date"] = date;
            head["maker"] = maker;
            head["attachments"] = draft.Attachments;
            head["lines"] = Lines(draft);
            head["lines_total"] = draft.Lines.Count;
            DryRun.Set("voucher", head);
            DryRun.Stop(ctx, "U8PzInsert.Transact");
        }

        // 记账：GlPostCheck 之后、年度首张凭证的期初对账（它会写 GL_merror）和记账事务之前停下。
        public static void Post(WorkContext ctx, GlPostReq req)
        {
            if (!DryRun.Active)
            {
                return;
            }
            List<object> items = new List<object>();
            foreach (GlPostItem item in req.Items)
            {
                Dictionary<string, object> one = new Dictionary<string, object>();
                one["csign"] = item.Sign;
                one["ino_id"] = item.No;
                items.Add(one);
            }
            Dictionary<string, object> post = new Dictionary<string, object>();
            post["iyear"] = req.Year;
            post["iperiod"] = req.Period;
            post["poster"] = req.Poster;
            post["vouchers"] = items;
            post["first_posting_checks"] = "not_run";
            DryRun.Set("post", post);
            DryRun.Stop(ctx, "GlPostTx.VouchPostAll");
        }

        internal static List<object> Lines(GlDraft draft)
        {
            List<object> list = new List<object>();
            foreach (GlLine line in draft.Lines)
            {
                Dictionary<string, object> row = new Dictionary<string, object>();
                row["account"] = line.Account;
                row["debit"] = line.Debit;
                row["credit"] = line.Credit;
                Put(row, "digest", line.Digest);
                Put(row, "dept", line.Dept);
                Put(row, "person", line.Person);
                Put(row, "customer", line.Customer);
                Put(row, "supplier", line.Supplier);
                Put(row, "item_class", line.ItemClass);
                Put(row, "item", line.Item);
                Put(row, "settle", line.Settle);
                Put(row, "doc_no", line.DocNo);
                Put(row, "doc_date", line.DocDate);
                Put(row, "currency", line.Currency);
                if (line.Rate != 0m)
                {
                    row["rate"] = line.Rate;
                }
                if (line.Qty != 0m)
                {
                    row["qty"] = line.Qty;
                }
                if (line.Flows.Count > 0)
                {
                    row["cash_flow"] = Flows(line);
                }
                list.Add(row);
            }
            return list;
        }

        static List<object> Flows(GlLine line)
        {
            List<object> list = new List<object>();
            foreach (GlFlow flow in line.Flows)
            {
                Dictionary<string, object> one = new Dictionary<string, object>();
                one["item"] = flow.Item;
                one["debit"] = flow.Debit;
                one["credit"] = flow.Credit;
                list.Add(one);
            }
            return list;
        }

        static void Put(Dictionary<string, object> row, string name, string value)
        {
            if (!string.IsNullOrEmpty(value))
            {
                row[name] = value;
            }
        }
    }
}
