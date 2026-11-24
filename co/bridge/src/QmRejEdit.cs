using System.Collections.Generic;

namespace U8Co
{
    // 不良品处理单（QM05 / QM06）修改：UFQMCo.clsArrRejectCO / clsProRejectCO 的 VO 接口（没有 VoucherOperate），载入、改写、
    // UpdateVoucher 保存走 QmVoEdit（同其他检验单）。U8 卡片「修改」同样是 GetTheVoucher → 改 domHead / domBody → UpdateVoucher，
    // U8 盖修改人 CMODIFIER、修改日期与时间并换 UFTS；不用 ModifyVoucher（同族的其他检验单实测返回 true 但不写库）。保存在 U8 自己的连接上提交
    // （bOutTrans=False，同生单、审核、删除）：闸门与表体合并核对全在调用前（QmRejEditLoad），预演在调用前停，
    // 保存后在新连接上回读核对（QmRejEditSaved）。
    internal static class QmRejEdit
    {
        public static ApiResult Run(WorkContext ctx, QmRejSpec spec, int id, Dictionary<string, object> head, object[] lines)
        {
            QmRejEditAsk ask = QmRejEditReq.Parse(spec, head, lines);
            QmRejEditJob e = QmRejEditLoad.Load(ctx, spec, ask, id);
            e.Component = "UFQMCo." + spec.ProgId.Substring(spec.ProgId.IndexOf('.') + 1) + "." + QmVoEdit.Method;
            QmOutcome outcome;
            using (QmRejCo co = QmRejCo.Open(ctx, spec))
            {
                outcome = QmVoEdit.Save(ctx, co, id, e.Component,
                    delegate(object h, object b) { QmRejEditDom.Apply(e, h, b); },
                    delegate(object h, object b) { DryInput(e, h, b); });
            }
            return QmRejEditSaved.Confirm(e, outcome);
        }

        // 预演的 detail：要写的表头字段、请求的表体操作与合并后的处理行数，以及交给 U8 的表体。
        static void DryInput(QmRejEditJob e, object head, object body)
        {
            if (!DryRun.Active)
            {
                return;
            }
            Dictionary<string, object> fields = new Dictionary<string, object>();
            for (int i = 0; i < e.HeadFields.Count; i++)
            {
                fields[e.HeadFields[i][0]] = e.HeadFields[i][1];
            }
            Dictionary<string, object> input = new Dictionary<string, object>();
            input["id"] = e.Id;
            input["source_id"] = e.Job.Ask.CheckId;
            input["fields"] = fields;
            if (e.Ask.Ops.Count > 0)
            {
                input["quantity"] = e.Job.Ask.Total();
                input["lines"] = Rows.FromDom(body, QmRejReq.LinesMax * 2);
            }
            DryRun.Set("input", input);
        }
    }
}
