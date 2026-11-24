namespace U8Co
{
    // 改写 VO 的表头、表体 DOM（domHead / domBody）。
    internal delegate void QmVoApply(object head, object body);

    // VO 接口单据（其他报检单、其他检验单 QmOthEdit，不良品处理单 QmRejEdit）修改的共用流程：GetTheVoucher 载入 → 改 domHead /
    // domBody（表头 editprop=M）→ InitByXml → UpdateVoucher(login, vo) 引用 {0,1}。UpdateVoucher 返回 true、在 U8 自己的连接上
    // 提交、换 UFTS；ModifyVoucher 存在但什么也不写，SaveVoucher / EditVoucher / ModVoucher 不存在（DISP_E_UNKNOWNNAME），都不要用。
    // 不包 CoTrans：预演（校验模式）在调用前停，调用后请求连接上不能留着事务（有就回滚并 504），由调用方在新连接上回读核对。
    internal static class QmVoEdit
    {
        public const string Method = "UpdateVoucher";

        // apply 改 DOM，dry 在改完、交回 VO 之后记预演的 detail（可为 null）。
        public static QmOutcome Save(WorkContext ctx, QmRejCo co, int id, string component, QmVoApply apply, QmVoApply dry)
        {
            object[] doms = new object[2];
            QmOutcome outcome;
            try
            {
                co.Load(id);
                doms[0] = co.Head();
                doms[1] = co.Body();
                apply(doms[0], doms[1]);
                co.Push(doms[0], doms[1]);
                if (dry != null)
                {
                    dry(doms[0], doms[1]);
                }
                DryRun.Stop(ctx, component);
                outcome = co.Act(Method);
            }
            finally
            {
                QmDom.Release(doms);
            }
            QmOthOps.AfterCall(ctx);
            return outcome;
        }
    }
}
