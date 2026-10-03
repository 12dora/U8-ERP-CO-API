using System.Collections.Generic;

namespace U8Co
{
    // 制单的预演（validate）：Prepare 的事务里拼好分录、取到外部业务号之后、提交之前，把计划的凭证交给 detail；
    // 提交钩子随后回滚（外部业务号 Ap_CancelNo 的分配一并撤销）并结束任务，U8PzInsert.Transact 不会被调用。
    // 合并制单时 sources 列出全部单据，每行分录另带来源单据号 bill_code。
    internal static class ArapVoucherDry
    {
        public static void Plan(VoucherPlan plan)
        {
            if (!DryRun.Active)
            {
                return;
            }
            VoucherDoc first = plan.Doc;
            List<object> sources = new List<object>();
            foreach (VoucherDoc doc in plan.Docs)
            {
                VoucherKind kind = Kinds.Find(doc.Ask.Kind);
                if (kind != null && doc.Id > 0)
                {
                    DryRun.Touched(kind, doc.Id);
                }
                Dictionary<string, object> source = new Dictionary<string, object>();
                source["type"] = doc.Ask.Kind;
                source["id"] = doc.Id;
                source["code"] = doc.Code;
                source["vouch_type"] = doc.VType;
                source["rows"] = doc.Rows.Count;
                sources.Add(source);
            }
            Dictionary<string, object> voucher = new Dictionary<string, object>();
            voucher["flag"] = first.Flag;
            voucher["iyear"] = plan.Key.Year;
            voucher["iperiod"] = plan.Key.Period;
            voucher["csign"] = plan.Key.Sign;
            voucher["date"] = plan.Date;
            voucher["pz_id"] = plan.PzId;
            voucher["pz_id_note"] = "预演取的外部业务号已随回滚撤销，实际制单会重新取号";
            voucher["making_system"] = first.Flag;
            voucher["lines"] = Lines(plan);
            voucher["lines_total"] = plan.Draft.Lines.Count;
            voucher["sources"] = sources;
            DryRun.Set("voucher", voucher);
        }

        static List<object> Lines(VoucherPlan plan)
        {
            List<object> lines = GlDryRun.Lines(plan.Draft);
            if (plan.Docs.Count < 2)
            {
                return lines;
            }
            for (int i = 0; i < lines.Count && i < plan.Bills.Count; i++)
            {
                Dictionary<string, object> line = lines[i] as Dictionary<string, object>;
                if (line != null)
                {
                    line["bill_code"] = plan.Docs[plan.Bills[i]].Code;
                }
            }
            return lines;
        }
    }
}
