using System.Collections.Generic;

namespace U8Co
{
    // 其他检验单（QM15）、其他报检单（QM11）修改：VO 接口（QmRejCo，同删除、审核）。GetTheVoucher 载入 → 改 domHead / domBody
    // （表头 editprop=M，属性名大写）→ InitByXml → UpdateVoucher(login, vo) 引用 {0,1}。返回 true、自己提交、换 UFTS；
    // 其他检验单盖修改人，其他报检单不盖。SaveVoucher / EditVoucher / ModVoucher 不存在（DISP_E_UNKNOWNNAME），ModifyVoucher 存在
    // 但什么也不做，都不要用。U8 不拦已审核的单据（已审核的其他报检单同样能改），闸门全由桥做（QmEditGate）。
    // 不包 CoTrans，预演（校验模式）在调用前停，调用后请求连接上不能留着事务，在新连接上回读核对（QmEditSaved）。
    internal static class QmOthEdit
    {
        public const string Method = "UpdateVoucher";

        public static ApiResult Run(WorkContext ctx, VoucherKind kind, int id, Dictionary<string, object> head, object[] lines)
        {
            QmOthSpec spec = QmOthOps.Need(kind);
            QmEditAsk ask = QmEditReq.Parse(kind, head, lines);
            QmEditJob job = spec.Inspect ? QmEditLoad.Inspect(ctx, ask, id, spec)
                : QmEditLoad.Check(ctx, ask, id, spec.VouchType, spec.Vt, spec.RuleKey("update"));
            job.Component = "UFQMCo." + (spec.Inspect ? "clsOtherInspectVoucherCO." : "clsOtherCheckVoucherCO.") + Method;
            object[] doms = new object[2];
            try
            {
                QmOutcome outcome;
                using (QmRejCo co = spec.Open(ctx))
                {
                    co.Load(id);
                    doms[0] = co.Head();
                    doms[1] = co.Body();
                    QmEditDom.Apply(job, doms[0], doms[1], true);
                    co.Push(doms[0], doms[1]);
                    QmEdit.DryInput(job, doms);
                    DryRun.Stop(ctx, job.Component);
                    outcome = co.Act(Method);
                }
                QmOthOps.AfterCall(ctx);
                return QmEditSaved.Confirm(job, outcome);
            }
            finally
            {
                QmDom.Release(doms);
            }
        }
    }
}
