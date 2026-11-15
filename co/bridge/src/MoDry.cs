using System.Collections.Generic;

namespace U8Co
{
    // 生产订单写预演（校验模式）：U8API 自己开 TransactionScope 提交，桥查完闸门后在调用前停（DryRun.Stop），
    // detail.input 带上规整后的请求（新增：行；修改：要改的行和是否有改动）。
    internal static class MoDry
    {
        public static void CreateInput(MoCreateAsk ask)
        {
            if (!DryRun.Active)
            {
                return;
            }
            List<object> lines = new List<object>();
            for (int i = 0; i < ask.Lines.Count; i++)
            {
                MoLine one = ask.Lines[i];
                Dictionary<string, object> line = new Dictionary<string, object>();
                line["sort_seq"] = one.Seq;
                line["inv_code"] = one.InvCode;
                line["qty"] = one.Qty;
                line["start_date"] = one.Start;
                line["due_date"] = one.Due;
                line["mo_type"] = one.MoType;
                line["dept_code"] = one.Dept;
                line["wh_code"] = one.Wh;
                line["remark"] = one.Remark;
                lines.Add(line);
            }
            Dictionary<string, object> input = new Dictionary<string, object>();
            input["code"] = ask.Code;
            input["lines"] = lines;
            DryRun.Set("input", input);
        }

        public static void UpdateInput(MoPlan plan)
        {
            if (!DryRun.Active)
            {
                return;
            }
            List<object> lines = new List<object>();
            for (int i = 0; i < plan.Lines.Count; i++)
            {
                MoTarget one = plan.Lines[i];
                if (!one.Touched)
                {
                    continue;
                }
                Dictionary<string, object> line = new Dictionary<string, object>();
                line["line_id"] = one.Row.MoDId;
                line["sort_seq"] = one.Row.SortSeq;
                line["inv_code"] = one.Row.InvCode;
                line["qty"] = one.Qty;
                line["qty_changed"] = one.QtyChanged;
                line["due_date"] = one.Due;
                line["remark"] = one.Remark;
                lines.Add(line);
            }
            Dictionary<string, object> input = new Dictionary<string, object>();
            input["changed"] = plan.Changed;
            input["lines"] = lines;
            DryRun.Set("input", input);
        }
    }
}
