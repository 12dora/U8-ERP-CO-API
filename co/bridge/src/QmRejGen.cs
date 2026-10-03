using System;
using System.Collections.Generic;

namespace U8Co
{
    // 不良品处理单生单（QM05 参照来料检验单、QM06 参照产品检验单）：UFQMCo 不良品处理单 CO 的 AddVoucher。
    // 实测 AddVoucher 在 U8 自己的连接上提交，请求连接的事务回滚不了：不包 CoTrans，调用前查完能查的，
    // 预演（校验模式）在取号和保存之前停；保存后在新连接上按检验单 ID 找新单并核对单号、制单人。
    // U8 保存后把检验单 BREJFLAG 置 1；单号按 U8 编号规则取（QmNo / BillNo，按 U8 单据编号规则生成，形如 QMRJ202601010001），取了不退。
    internal static class QmRejGen
    {
        const string FoundSql = "select convert(varchar(20), ID) as ID, CREJECTCODE as code, CMAKER as maker,"
            + " CVERIFIER as verifier, convert(varchar(19), DVERIFYDATE, 120) as verify_date,"
            + " convert(varchar(5), isnull(IsWfControlled,0)) as wf, convert(varchar(10), isnull(iVerifyStateNew,0)) as vs"
            + " from QMREJECTVOUCHER where CHECKID=? and CVOUCHTYPE=? order by ID";
        const string LinesSql = "select count(*) from QMREJECTVOUCHERS where ID=?";
        const string FlagSql = "select convert(varchar(5), isnull(BREJFLAG,0)) from QMCHECKVOUCHER where ID=?";

        public static ApiResult Run(WorkContext ctx, QmRejSpec spec, int checkId, Dictionary<string, object> head,
            object[] lines)
        {
            QmRejAsk ask = QmRejReq.Parse(spec, checkId, head, lines);
            PermContext perm = QmRejOps.RequireRule(ctx, spec, "create");
            QmRejJob job = QmRejSrc.Load(ctx, spec, ask);
            MoDelete.CheckRows(perm, PermRegistry.ForKey(spec.RuleKey("create")), QmRejSrc.PermRows(job));
            object[] doms = new object[4];
            try
            {
                using (QmRejCo co = QmRejCo.Open(ctx, spec))
                {
                    Prepare(job, co, doms);
                    // 预演（校验模式）：保存自己提交，取号也不退；U8 参照带入、必输项核对完就停，detail 带上要送给 U8 的表头、表体。
                    DryInput(job, doms);
                    DryRun.Stop(ctx, "UFQMCo.AddVoucher");
                    string code = QmNo.Allocate(ctx, spec.VouchType, doms[2], "CREJECTCODE");
                    co.Push(doms[2], doms[3]);
                    QmOutcome outcome = co.Save();
                    if (QmGen.TranLeft(ctx))
                    {
                        throw new BridgeException(504, "outcome_unknown",
                            "U8 新增" + spec.Title + "后请求连接上仍有未结束的事务，已回滚，结果未知");
                    }
                    return Confirm(job, outcome, code);
                }
            }
            finally
            {
                QmDom.Release(doms);
            }
        }

        // 空白表头表体各一行交给 VO → AddNewVoucher → AddVoucherByRef（检验单）→ 改 domHead / domBody 再交回 VO → 核对必输项。
        // doms：0、1 空白模板，2、3 VO 的表头、表体。
        static void Prepare(QmRejJob job, QmRejCo co, object[] doms)
        {
            object conn = job.Ctx.Conn;
            QmRejSpec spec = job.Spec;
            doms[0] = DomRows.Blank(conn, "select * from QMREJECTVOUCHER where 1=2");
            doms[1] = DomRows.Blank(conn, "select * from QMREJECTVOUCHERS where 1=2");
            ComUtil.ReleaseOne(DomRows.AddRow(doms[0]));
            ComUtil.ReleaseOne(DomRows.AddRow(doms[1]));
            co.Push(doms[0], doms[1]);
            co.FromCheck("select * from " + CoRows.Ident(spec.CheckView)
                + " where ID=? and CVOUCHTYPE=? and isnull(PU_CBCLOSER,'')=''", job.Ask.CheckId, spec.CheckType);
            doms[2] = co.Head();
            doms[3] = co.Body();
            QmRejDom.FillHead(doms[2], QmRejDom.HeadFields(spec, job.Ask, job.Wf, job.DefineNames));
            QmRejDom.FillBody(doms[3], job.Ask, job.Unit);
            co.Push(doms[2], doms[3]);
            if (co.RowCount() < 1)
            {
                throw new BridgeException(409, "u8_rejected", "U8 没有接收表体行");
            }
            QmRejTpl.Require(conn, spec.Vt, doms[2], doms[3], QmRejReq.LinesMax);
        }

        static void DryInput(QmRejJob job, object[] doms)
        {
            if (!DryRun.Active)
            {
                return;
            }
            Dictionary<string, object> input = new Dictionary<string, object>();
            input["source_id"] = job.Ask.CheckId;
            input["quantity"] = job.Ask.Total();
            input["wf_controlled"] = job.Wf;
            List<Dictionary<string, object>> heads = Rows.FromDom(doms[2], 1);
            if (heads != null && heads.Count > 0)
            {
                input["head"] = heads[0];
            }
            input["lines"] = Rows.FromDom(doms[3], QmRejReq.LinesMax);
            DryRun.Set("input", input);
        }

        // 新连接上按检验单找本类型的不良品处理单：恰好一张、单号与本次取的号相同、制单人是本操作员、U8 没报错才算找到；
        // 找到后还要表体行数等于请求行数、检验单 BREJFLAG 已置 1，否则 504（已保存但不完整，需人工核对）。
        // 找到了但对不上或 U8 报错：504（可能已保存）；没找到：U8 原文 409，其他异常或「成功却没有单据」504。
        static ApiResult Confirm(QmRejJob job, QmOutcome outcome, string code)
        {
            WorkContext ctx = job.Ctx;
            object conn = null;
            List<Dictionary<string, object>> found = null;
            int items = 0;
            string flag = "";
            try
            {
                conn = ctx.OpenFresh();
                found = QmSql.Many(conn, FoundSql, 3, job.Ask.CheckId, job.Spec.VouchType);
                if (found.Count == 1)
                {
                    items = (int)QmSql.Dec(QmSql.Scalar(conn, LinesSql, CoRows.AsId(CoRows.Col(found[0], "ID"))));
                    flag = QmSql.Scalar(conn, FlagSql, job.Ask.CheckId);
                }
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "QmRejGen " + ex.Message);
                found = null;
            }
            finally
            {
                AdoXml.Close(conn);
            }
            Judge(job, outcome, found, code);
            Complete(job, code, items, flag);
            return Result(job, found[0], items);
        }

        static void Complete(QmRejJob job, string code, int items, string flag)
        {
            string head = "U8 已保存" + job.Spec.Title + " " + code + "，但";
            if (items != job.Ask.Lines.Count)
            {
                throw new BridgeException(504, "outcome_unknown", head + "表体回读到 " + items + " 行，请求是 "
                    + job.Ask.Lines.Count + " 行，需在 U8 中核对");
            }
            if (flag != "1")
            {
                throw new BridgeException(504, "outcome_unknown", head + "检验单的不良品处理标记（BREJFLAG）没有置 1，需在 U8 中核对");
            }
        }

        static void Judge(QmRejJob job, QmOutcome outcome, List<Dictionary<string, object>> found, string code)
        {
            string title = job.Spec.Title;
            if (found == null)
            {
                throw new BridgeException(504, "outcome_unknown", "已调用 U8 新增" + title + "（单号 " + code + "），回读失败，结果未知");
            }
            if (found.Count == 1 && outcome.Ok && CoRows.Col(found[0], "code") == code
                && CoRows.Col(found[0], "maker") == job.Ctx.OperatorName.Trim())
            {
                return;
            }
            if (found.Count > 0)
            {
                throw new BridgeException(504, "outcome_unknown", "U8 可能已保存" + title + "（单号 " + code + "），结果未知："
                    + QmGen.Text(outcome));
            }
            if (outcome.Error.Length > 0)
            {
                throw new BridgeException(409, "u8_rejected", outcome.Error);
            }
            string text = outcome.Lost == null ? "U8 返回成功但回读不到新" + title : "U8 新增" + title + "调用异常：" + QmGen.Text(outcome);
            throw new BridgeException(504, "outcome_unknown", text + "，结果未知");
        }

        static ApiResult Result(QmRejJob job, Dictionary<string, object> row, int items)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = job.Spec.Kind;
            body["id"] = CoRows.AsId(CoRows.Col(row, "ID"));
            body["code"] = CoRows.Col(row, "code");
            body["state"] = CoRows.StateOf(row);
            body["lines"] = items;
            body["source_type"] = job.Spec.CheckKind;
            body["source_id"] = job.Ask.CheckId;
            body["wf"] = QmRejOps.Wf(row);
            return ApiResult.Ok(body);
        }
    }
}
