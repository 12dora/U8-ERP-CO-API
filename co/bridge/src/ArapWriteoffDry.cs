using System.Collections.Generic;

namespace U8Co
{
    // 核销、取消核销的预演（rollback）：U8 / 桥的 SQL 照常在事务里执行、事务内核对通过之后，
    // 把核销号、金额和涉及的单据交给 detail，提交钩子随后回滚。body 是正常写入会返回的响应体（事务内算出的值）。
    internal static class ArapWriteoffDry
    {
        public static void Writeoff(WriteoffPlan plan, Dictionary<string, object> body)
        {
            if (!DryRun.Active)
            {
                return;
            }
            Touch(plan.ReceiptKind, plan.ReceiptId);
            foreach (WriteoffTarget t in plan.Targets)
            {
                Touch(t.Kind.Name, t.Id);
            }
            DryRun.Set("writeoff", Strip(body));
        }

        public static void Cancel(UnwriteoffPlan plan, Dictionary<string, object> body)
        {
            if (!DryRun.Active)
            {
                return;
            }
            Touch(plan.ReceiptKind, plan.ReceiptId);
            foreach (UnwriteoffTarget t in plan.Targets)
            {
                Touch(t.Kind.Name, t.Id);
            }
            DryRun.Set("writeoff_cancel", Strip(body));
        }

        // 类型名能对上 Kinds 表的才记（收付款单、发票、应收应付单都在表里）。
        static void Touch(string name, int id)
        {
            VoucherKind kind = Kinds.Find(name);
            if (kind != null && id > 0)
            {
                DryRun.Touched(kind, id);
            }
        }

        // 响应体里的 ok 属于信封，detail 里不要。
        static Dictionary<string, object> Strip(Dictionary<string, object> body)
        {
            Dictionary<string, object> copy = new Dictionary<string, object>(body);
            copy.Remove("ok");
            return copy;
        }
    }
}
