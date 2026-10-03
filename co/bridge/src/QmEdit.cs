using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 质量单据修改的入口（Dispatch.UpdateMore 分派）：来料 / 产品检验单（QM03 / QM04）在这里，其他报检单 / 其他检验单走 QmOthEdit。
    // 检验单：UFQMCo.clsArrCheckCO / clsProCheckCO 的 Init(login, conn, False) 后 VoucherOperate(表头, 表体, "update", "", ID)，
    // DOM 从视图 QM_QARRCHECKB/T（产品 QM_QPROCHECKB/T）按 ID 读出（保留 UFTS），表头 editprop=M。返回 true，经
    // clsPuCheckCO.UpdateVoucher → ModifyCheck 保存，盖修改人 CMODIFIER、换 UFTS；U8 自己提交（外层事务回滚撤不掉），
    // 所以不包 CoTrans，预演（校验模式）在调用前停，调用后在新连接上回读核对（QmEditSaved）。
    internal static class QmEdit
    {
        public static bool Handles(VoucherKind kind)
        {
            return QmEditReq.Handles(kind);
        }

        public static ApiResult Run(WorkContext ctx, VoucherKind kind, int id, Dictionary<string, object> head, object[] lines)
        {
            if (QmOthSpec.Handles(kind))
            {
                return QmOthEdit.Run(ctx, kind, id, head, lines);
            }
            QmSpec spec = QmSpec.Of(kind);
            if (spec == null || spec.Inspect)
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持修改");
            }
            QmEditAsk ask = QmEditReq.Parse(kind, head, lines);
            QmEditJob job = QmEditLoad.Check(ctx, ask, id, spec.VouchType, spec.Vt, spec.RuleKey("update"));
            job.Component = "UFQMCo.VoucherOperate(update)";
            object[] doms = null;
            QmCo co = null;
            try
            {
                co = QmCo.Open(ctx, spec);
                doms = QmDom.LoadedPair(ctx.Conn, spec, id);
                QmEditDom.Apply(job, doms[0], doms[1], false);
                DryInput(job, doms);
                DryRun.Stop(ctx, job.Component);
                QmOutcome outcome = co.Operate(doms, "update", id.ToString(CultureInfo.InvariantCulture));
                if (!outcome.Ok)
                {
                    CoRows.Note(ctx.Item, "VoucherOperate " + QmGen.Text(outcome));
                }
                if (QmGen.TranLeft(ctx))
                {
                    throw new BridgeException(504, "outcome_unknown",
                        "U8 修改" + job.Title + "后请求连接上仍有未结束的事务，已回滚，结果未知");
                }
                return QmEditSaved.Confirm(job, outcome);
            }
            finally
            {
                QmDom.Release(doms);
                if (co != null)
                {
                    co.Dispose();
                }
            }
        }

        // 预演的 detail：要送给 U8 的表头字段（列名、值）和检验项目行。
        internal static void DryInput(QmEditJob job, object[] doms)
        {
            if (!DryRun.Active)
            {
                return;
            }
            Dictionary<string, object> fields = new Dictionary<string, object>();
            for (int i = 0; i < job.Fields.Count; i++)
            {
                fields[job.Fields[i][0]] = job.Fields[i][1];
            }
            Dictionary<string, object> input = new Dictionary<string, object>();
            input["id"] = job.Id;
            input["fields"] = fields;
            if (job.Ask.Items != null)
            {
                input["items"] = Rows.FromDom(doms[1], 500);
            }
            DryRun.Set("input", input);
        }
    }
}
