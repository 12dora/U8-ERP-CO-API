using System;
using System.Collections.Generic;

namespace U8Co
{
    // 其他检验单生单（vouchers/generate，source_type qm_other_inspect，id = 其他报检单 ID，source_line_id = 表体 AUTOID，
    // 一行一张）：UFQMCo.clsOtherCheckVoucherCO 的 VO 接口。空白表头表体交给 VO → AddNewVoucher →（QmOthSpec.CheckByRef 时）
    // AddVoucherByRef(conn, vo, 报检单行记录集) → 按来料检验单的字段集改写表头、检验项目交回 → 核对模板必输 → 取号 → AddVoucher。
    // AddVoucher 自己提交：不包 CoTrans，预演（校验模式）在取号之前停，保存后在新连接上按 INSPECTAUTOID 找新单。
    // 闸门：其他报检单存在且已审核，行属于它；该行还没有其他检验单（一行只生一张，U8 不写 FSUMCHECKQTY，「已检」看 BFLAG）。
    // 检验部门是 cdepcode，没送时取检验员的所属部门，不拿报检部门充数（两者可能不同）。
    internal static class QmOthChk
    {
        // 参照用的报检单行记录集：列表视图 QM_QOTHINSPECTLIST（表头、表体合一，按行 AUTOID 与报检单 ID）。
        // 只给表体视图时 AddVoucherByRef 报「参照生单失败！在对应所需名称或序数的集合中，未找到项目」（缺表头列）。
        const string RefSql = "select * from QM_QOTHINSPECTLIST where AUTOID=? and ID=?";
        const string UsedSql = "select count(*) from QMCHECKVOUCHER where INSPECTAUTOID=?";
        const string NoBodyRows = "单据体行不存在";
        const string PersonDepSql = "select isnull(cDepCode,'') from Person where cPersonCode=?";

        public static ApiResult Run(WorkContext ctx, VoucherKind kind, int inspectId, Dictionary<string, object> head,
            object[] lines)
        {
            QmOthSpec spec = QmOthOps.Need(kind);
            QmAsk ask = QmReq.Parse(QmOthSpec.CheckAsk, head, lines);
            PermContext perm = QmOthOps.RequireRule(ctx, spec, "create");
            QmChkJob job = Load(ctx, inspectId, ask);
            MoDelete.CheckRows(perm, PermRegistry.ForKey(spec.RuleKey("create")), QmChkSrc.PermRows(job));
            object[] doms = new object[4];
            try
            {
                using (QmRejCo co = spec.Open(ctx))
                {
                    Prepare(job, spec, co, doms);
                    // 预演（校验模式）：保存自己提交，取号也不退；闸门和模板核对完就停，detail 带上要送给 U8 的表头、检验项目。
                    DryInput(job, doms);
                    DryRun.Stop(ctx, "UFQMCo.clsOtherCheckVoucherCO.AddVoucher");
                    string code = QmNo.Allocate(ctx, spec.VouchType, doms[2], spec.CodeColumn);
                    co.Push(doms[2], doms[3]);
                    QmOutcome outcome = co.Save();
                    if (QmGen.TranLeft(ctx))
                    {
                        throw new BridgeException(504, "outcome_unknown",
                            "U8 新增" + spec.Title + "后请求连接上仍有未结束的事务，已回滚，结果未知");
                    }
                    return QmOthSaved.Check(job, spec, outcome, code);
                }
            }
            finally
            {
                QmDom.Release(doms);
            }
        }

        static QmChkJob Load(WorkContext ctx, int inspectId, QmAsk ask)
        {
            object conn = ctx.Conn;
            QmChkJob job = new QmChkJob();
            job.Ctx = ctx;
            job.Spec = QmOthSpec.CheckAsk;
            job.Ask = ask;
            job.LineAsk = ask.Lines[0];
            job.InspectId = inspectId;
            Inspect(conn, job);
            job.Date = ask.Date.Length > 0 ? ask.Date : (ctx.Item.Date ?? "").Trim();
            job.Time = QmDom.Now();
            string person = ask.Get("ccheckpersoncode");
            job.PersonName = QmChkSrc.Person(conn, person);
            job.DepCode = ask.Get("cdepcode");
            if (job.DepCode.Length == 0)
            {
                job.DepCode = QmSql.Scalar(conn, PersonDepSql, person);
            }
            if (job.DepCode.Length == 0)
            {
                throw BridgeException.BadField("head.cdepcode", "必须指定检验部门 cdepcode（检验员 " + person + " 没有所属部门）");
            }
            job.DepName = QmSql.DeptName(conn, job.DepCode);
            job.InspectDepName = QmSql.DeptName(conn, CoRows.Col(job.Head, "CINSPECTDEPCODE"));
            job.Project = QmChkSrc.Project(conn, job);
            job.ProjectLines = QmSql.Many(conn, QmSql.ProjectLinesSql, 500, CoRows.AsId(CoRows.Col(job.Project, "ID")));
            if (job.ProjectLines.Count == 0)
            {
                throw new BridgeException(409, "state_mismatch", "检验方案没有检验指标：" + CoRows.Col(job.Project, "CPROJECTCODE"));
            }
            job.TypeName = QmSql.CheckTypeName(conn, Or(CoRows.Col(job.Head, "CCHECKTYPECODE"), QmOthSpec.CheckType));
            job.Wf = false;
            job.Dt = ask.Dt > 0m ? ask.Dt : 1m;
            job.Conclusion = Or(ask.Get("cchkconclusion"), QmMath.Conclusion(job.LineAsk.Dis));
            // 让步接收核准人：其他检验单只写不要求（未实测模板是否必输）。
            job.Yield = QmYield.Resolve(conn, ask.Get(QmYield.CodeKey), ask.Get(QmYield.DateKey), job.Date, "head.");
            return job;
        }

        // 其他报检单（QM11）存在、已审核；行属于它、还没有检验单；检验数量不超过行数量。
        static void Inspect(object conn, QmChkJob job)
        {
            QmOthSpec inspect = QmOthSpec.Find(QmOthSpec.InspectKind);
            job.Head = QmSql.One(conn, QmSql.InsHeadSql, job.InspectId, inspect.VouchType);
            if (job.Head == null)
            {
                throw new BridgeException(404, "not_found", "其他报检单不存在");
            }
            if (CoRows.Col(job.Head, "CVERIFIER").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "其他报检单未审核");
            }
            int line = job.LineAsk.SourceLineId;
            job.Line = QmSql.One(conn, QmSql.InsLineSql, line, job.InspectId);
            if (job.Line == null)
            {
                throw BridgeException.BadField("lines.0.source_line_id", "明细行不存在：" + line);
            }
            RequireFree(QmSql.Dec(QmSql.Scalar(conn, UsedSql, line)) > 0m, QmSql.Scalar(conn, QmOthSaved.LineFlagSql, line), line);
            decimal left = QmMath.Remaining(QmSql.Dec(CoRows.Col(job.Line, "Total")), QmSql.Dec(CoRows.Col(job.Line, "Used")));
            QmMath.RequireLeft(job.LineAsk.Qty, left, "其他报检单行 " + line);
        }

        // 已有检验单挂在这一行，或行已标记检验（BFLAG=1）：409。
        internal static void RequireFree(bool used, string flag, int line)
        {
            if (used)
            {
                throw new BridgeException(409, "state_mismatch", "其他报检单行 " + line + " 已生成其他检验单");
            }
            if (flag == "1")
            {
                throw new BridgeException(409, "state_mismatch", "其他报检单行 " + line + " 已标记检验，但找不到对应的检验单，需在 U8 中核对");
            }
        }

        // doms：0、1 空白模板，2、3 VO 的表头、表体。
        static void Prepare(QmChkJob job, QmOthSpec spec, QmRejCo co, object[] doms)
        {
            object conn = job.Ctx.Conn;
            object[] blank = QmOthDom.BlankPair(conn, spec);
            doms[0] = blank[0];
            doms[1] = blank[1];
            co.Push(doms[0], doms[1]);
            co.AddNew();
            if (QmOthSpec.CheckByRef)
            {
                ByRef(job, co, doms);
            }
            doms[2] = co.Head();
            doms[3] = co.Body();
            Dictionary<string, string> names = QmOthDom.DefineNames(conn, spec.Vt, job.Ask.Head.Keys);
            QmOthDom.FillHead(doms[2], QmOthChkDom.Head(job, names));
            QmOthDom.FillBody(doms[3], QmOthChkDom.Items(job));
            co.Push(doms[2], doms[3]);
            if (co.RowCount() < 1)
            {
                throw new BridgeException(409, "u8_rejected", "U8 没有接收检验项目行");
            }
            QmRejTpl.Require(conn, spec.Vt, doms[2], doms[3], 500, QmOthReq.TplAsk(spec.CodeColumn));
        }

        // AddVoucherByRef 对某些其他报检单行会报「单据体行不存在！」。
        // 桥之后本来就按同一字段集改写表头和检验项目，所以遇到这一句就重新 AddNewVoucher、不带入，由桥填（CheckByRef=false 的写法）。
        static void ByRef(QmChkJob job, QmRejCo co, object[] doms)
        {
            try
            {
                co.FromSource(RefSql, new object[] { job.LineAsk.SourceLineId, job.InspectId }, "其他报检单行不可参照（不存在）");
            }
            catch (BridgeException ex)
            {
                if (ex.Message.IndexOf(NoBodyRows, StringComparison.Ordinal) < 0)
                {
                    throw;
                }
                CoRows.Note(job.Ctx.Item, "AddVoucherByRef " + ex.Message + "，改由桥填表头表体");
                co.Push(doms[0], doms[1]);
                co.AddNew();
            }
        }

        static void DryInput(QmChkJob job, object[] doms)
        {
            if (!DryRun.Active)
            {
                return;
            }
            Dictionary<string, object> input = new Dictionary<string, object>();
            input["source_id"] = job.InspectId;
            input["source_line_id"] = job.LineAsk.SourceLineId;
            input["quantity"] = job.LineAsk.Qty;
            input["by_ref"] = QmOthSpec.CheckByRef;
            List<Dictionary<string, object>> heads = Rows.FromDom(doms[2], 1);
            if (heads != null && heads.Count > 0)
            {
                input["head"] = heads[0];
            }
            input["lines"] = Rows.FromDom(doms[3], 500);
            DryRun.Set("input", input);
        }

        static string Or(string value, string fallback)
        {
            return value != null && value.Length > 0 ? value : fallback;
        }
    }
}
