using System.Collections.Generic;

namespace U8Co
{
    // 其他检验单（QM15）、其他报检单（QM11）修改：VO 接口（QmRejCo，同删除、审核），载入、改写、保存走 QmVoEdit
    // （表头 editprop=M，属性名大写）。其他检验单盖修改人，其他报检单不盖。U8 不拦已审核的单据（已审核的其他报检单同样能改），
    // 闸门全由桥做（QmEditGate）；保存后在新连接上回读核对（QmEditSaved）。
    internal static class QmOthEdit
    {
        public static ApiResult Run(WorkContext ctx, VoucherKind kind, int id, Dictionary<string, object> head, object[] lines)
        {
            QmOthSpec spec = QmOthOps.Need(kind);
            QmEditAsk ask = QmEditReq.Parse(kind, head, lines);
            QmEditJob job = spec.Inspect ? QmEditLoad.Inspect(ctx, ask, id, spec)
                : QmEditLoad.Check(ctx, ask, id, spec.VouchType, spec.Vt, spec.RuleKey("update"));
            job.Component = "UFQMCo." + (spec.Inspect ? "clsOtherInspectVoucherCO." : "clsOtherCheckVoucherCO.") + QmVoEdit.Method;
            QmOutcome outcome;
            using (QmRejCo co = spec.Open(ctx))
            {
                outcome = QmVoEdit.Save(ctx, co, id, job.Component,
                    delegate(object h, object b) { QmEditDom.Apply(job, h, b, true); },
                    delegate(object h, object b) { QmEdit.DryInput(job, new object[] { h, b }); });
            }
            return QmEditSaved.Confirm(job, outcome);
        }
    }
}
