using System.Collections.Generic;

namespace U8Co
{
    // 产品检验单合并检验（QM04 参照 2 到 20 条产品报检单行，BMERGECHECKFLAG=1）：UFQMCo.clsProCheckCO 的 VO 接口
    // AddVoucherWithMergeDetail(login, VO, detailVO)——U8 合并检验卡片保存用的方法（VoucherOperate("add") 只收表头、检验项目
    // 两个 DOM，写不了合并明细）。VO = UFQMVOCom.clsVoucherVO（GetVTID 取模板后 InitByXml(表头, 检验项目)），detailVO 是另一个
    // clsVoucherVO，装合并明细（QM_QProCheckMergeDetailB，每条来源一行）。U8 写 QMMergeCheckDetail 并回写每条来源报检单行的
    // FSUMCHECKQTY / FSUMCHECKNUM / BFLAG；桥不写这些表。保存在 U8 自己的连接上提交：不包 CoTrans，调用前查完能查的、取号，
    // 调用后在新连接上按单号确认（QmMergeSaved）。表头、检验项目的填法同单行生单（QmChkDom、QmItems），数量是各来源之和。
    internal static class QmMergeCheck
    {
        const string VoProgId = "UFQMVOCom.clsVoucherVO";
        const string Method = "AddVoucherWithMergeDetail";

        public static ApiResult Run(WorkContext ctx, QmSpec spec, int[] ids, Dictionary<string, object> head, object[] lines)
        {
            List<QmMergeLine> merged = new List<QmMergeLine>();
            QmAsk ask = QmMergeReq.Parse(spec, head, ids, lines, merged);
            List<QmMergeRule> rules = QmMergeRules.Parse(Requests.Field(ctx.Item.Body, QmMergeRules.Field));
            PermContext perm = QmGen.RequireRule(ctx, spec, "create");
            QmChkJob job = QmMergeSrc.Load(ctx, spec, ask, merged, rules);
            QmGen.CheckRows(perm, spec, "create", QmMergeSrc.PermRows(job));
            // doms：0 表头、1 检验项目、2 合并明细。
            object[] doms = new object[3];
            object detail = null;
            try
            {
                Build(job, doms);
                // 组件、两个 VO 在预演停下之前就建好（建不出来预演也报 503）；保存用同一套。
                using (QmRejCo co = QmRejCo.OpenCheck(ctx, spec.ProgId, spec.Vt))
                {
                    detail = NewDetail();
                    // 预演（校验模式）：保存自己提交，取号也不退，闸门和模板核对完就停，detail 带上要送给 U8 的三份 DOM。
                    DryInput(job, doms);
                    DryRun.Stop(ctx, "UFQMCo.clsProCheckCO." + Method);
                    string code = QmNo.Allocate(ctx, spec.VouchType, doms[0], "CCHECKCODE");
                    int before = QmSql.MaxId(ctx.Conn, "QMCHECKVOUCHER");
                    decimal used = QmMergeSrc.Used(ctx.Conn, job.Merge);
                    QmOutcome outcome = Save(co, detail, doms);
                    if (QmGen.TranLeft(ctx))
                    {
                        throw new BridgeException(504, "outcome_unknown",
                            "U8 新增" + spec.KindOf().Title + "（合并检验，单号 " + code + "）后请求连接上仍有未结束的事务，已回滚，结果未知");
                    }
                    return QmMergeSaved.Confirm(job, outcome, code, before, used);
                }
            }
            finally
            {
                ComUtil.Final(detail);
                QmDom.Release(doms);
            }
        }

        static void Build(QmChkJob job, object[] doms)
        {
            object conn = job.Ctx.Conn;
            QmSpec spec = job.Spec;
            doms[0] = QmDom.Blank(conn, spec.HeadView);
            doms[1] = QmDom.Blank(conn, spec.BodyView);
            doms[2] = QmDom.Blank(conn, QmMergeDom.View);
            QmChkDom.FillHead(job, doms[0]);
            QmItems.Fill(job, doms[1]);
            QmMergeDom.Fill(job, doms[2]);
            QmTplReq.Require(conn, spec, doms, "CCHECKCODE");
        }

        static object NewDetail()
        {
            object detail = ComUtil.Create(VoProgId);
            if (detail == null)
            {
                throw new BridgeException(503, "com_unavailable", "组件无法创建 " + VoProgId);
            }
            return detail;
        }

        static QmOutcome Save(QmRejCo co, object detail, object[] doms)
        {
            co.Push(doms[0], doms[1]);
            // detailVO = (检验单表头, 合并明细)：UFQMDmo 插合并明细时对同一个 VO 用 HeaditemValue 取 ID / CINVCODE / FCHECKCHANGRATE、
            // 用 BodyItemValue(字段, 行) 取各来源（测试账套实测通过）。
            ComUtil.Call(detail, "InitByXml", new object[] { doms[0], doms[2] });
            return co.SaveWith(Method, detail);
        }

        static void DryInput(QmChkJob job, object[] doms)
        {
            if (!DryRun.Active)
            {
                return;
            }
            Dictionary<string, object> input = new Dictionary<string, object>();
            input["source_ids"] = QmMergeSaved.Ids(job.Merge);
            input["quantity"] = job.LineAsk.Qty;
            List<Dictionary<string, object>> heads = Rows.FromDom(doms[0], 1);
            if (heads != null && heads.Count > 0)
            {
                input["head"] = heads[0];
            }
            input["lines"] = Rows.FromDom(doms[1], 200);
            input["merge_details"] = QmMergeDom.Rows(doms[2]);
            DryRun.Set("input", input);
        }
    }
}
