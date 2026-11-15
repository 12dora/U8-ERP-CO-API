using System;
using System.Collections.Generic;

namespace U8Co
{
    internal sealed class QmOthInsJob
    {
        public WorkContext Ctx;
        public QmOthSpec Spec;
        public QmOthAsk Ask;
        public string Date = "";
        public string Dep = "";
        public int Digits = 6;
        // 每行的存货档案（规范编码、检验方式、库存计量单位与换算率），与 Ask.Lines 一一对应。
        public readonly List<Dictionary<string, object>> Invs = new List<Dictionary<string, object>>();
        public Dictionary<string, string> DefineNames;
    }

    // 其他报检单新增（vouchers/create，无来源）：UFQMCo.clsOtherInspectVoucherCO 的 VO 接口（同不良品处理单生单）。
    // 空白表头表体各一行交给 VO → AddNewVoucher → 改 domHead / domBody 交回 → 核对模板必输 → 取号 → AddVoucher。
    // AddVoucher 在 U8 自己的连接上提交（同族组件实测），请求连接的事务回滚不了：不包 CoTrans，预演（校验模式）在取号之前停，
    // 保存后在新连接上按单号（U8 换了号时按调用前 max(ID) 之后、本操作员建的唯一一张）找新单并核对表体。
    // 不写任何来源列（CSOURCE*）。QM.bOtherInspectAutoVerify 开着时保存后由桥补审核（AutoVerify），状态从回读取。
    internal static class QmOthIns
    {
        const string InvSql = "select i.cInvCode, convert(varchar(10), isnull(i.iTestStyle,3)) as TestStyle,"
            + " isnull(nullif(i.cSTComUnitCode,''), i.cComUnitCode) as Unit, convert(varchar(5), isnull(i.iGroupType,0)) as GroupType,"
            + " convert(varchar(40), isnull(u.iChangRate,0)) as Rate from Inventory i left join ComputationUnit u"
            + " on u.cComunitCode=isnull(nullif(i.cSTComUnitCode,''), i.cComUnitCode) where i.cInvCode=?";
        // 报检部门缺省：本操作员最近一张其他报检单的报检部门（账套里没有操作员到部门的对照）。
        const string LastDepSql = "select top 1 CINSPECTDEPCODE from QMINSPECTVOUCHER where CVOUCHTYPE=? and CMAKER=?"
            + " and isnull(CINSPECTDEPCODE,'')<>'' order by ID desc";
        const string DigitsSql = "select cValue from AccInformation where cSysID='AA' and cName='iNumDecDgt'";
        const string NoAuth = "无审核权限，报检单保存为未审核";
        const string AutoVerifySql = "select cValue from AccInformation where cSysID='QM' and cName='bOtherInspectAutoVerify'";

        public static ApiResult Run(WorkContext ctx, VoucherKind kind, Dictionary<string, object> head, object[] lines)
        {
            QmOthSpec spec = QmOthOps.Need(kind);
            QmOthAsk ask = QmOthReq.Parse(head, lines);
            PermContext perm = QmOthOps.RequireRule(ctx, spec, "create");
            QmOthInsJob job = Load(ctx, spec, ask);
            MoDelete.CheckRows(perm, PermRegistry.ForKey(spec.RuleKey("create")), PermRows(job));
            object[] doms = new object[4];
            ApiResult saved;
            try
            {
                using (QmRejCo co = spec.Open(ctx))
                {
                    Prepare(job, co, doms);
                    // 预演（校验模式）：保存自己提交，取号也不退；模板必输核对完就停，detail 带上要送给 U8 的表头、表体。
                    DryInput(doms);
                    DryRun.Stop(ctx, "UFQMCo.clsOtherInspectVoucherCO.AddVoucher");
                    int before = QmSql.MaxId(ctx.Conn, spec.HeadTable);
                    string code = QmNo.Allocate(ctx, spec.VouchType, doms[2], spec.CodeColumn);
                    co.Push(doms[2], doms[3]);
                    QmOutcome outcome = co.Save();
                    if (QmGen.TranLeft(ctx))
                    {
                        throw new BridgeException(504, "outcome_unknown",
                            "U8 新增" + spec.Title + "后请求连接上仍有未结束的事务，已回滚，结果未知");
                    }
                    saved = QmOthSaved.Inspect(job, outcome, code, before);
                }
            }
            finally
            {
                QmDom.Release(doms);
            }
            return AutoVerify(ctx, spec, perm, saved);
        }

        // U8 客户端保存其他报检单时按 QM.bOtherInspectAutoVerify 自动审核；VO 接口的 AddVoucher 不按选项自动审核（保存后审核人为空），
        // 选项开着时桥接着用同一组件 GetTheVoucher + AuditVoucher（审核人是本操作员）。只在操作员有审核权限（QM02060105）时做，
        // 没有就跳过。单据已经提交：这一步的任何失败（含组件创建、载入、残留事务）都不改变新增的结果，照常返回新单（200，带 id、code），
        // state 按回读，另带 auto_verify_error；之后可用 vouchers/verify 补审。
        static ApiResult AutoVerify(WorkContext ctx, QmOthSpec spec, PermContext perm, ApiResult saved)
        {
            try
            {
                if (!string.Equals(QmSql.Scalar(ctx.Conn, AutoVerifySql).Trim(), "True", StringComparison.OrdinalIgnoreCase))
                {
                    return saved;
                }
                if (perm != null && !perm.HasAny(new string[] { spec.VerifyAuth }))
                {
                    saved.Body["auto_verify_error"] = NoAuth;
                    return saved;
                }
                Audit(ctx, spec, saved);
            }
            catch (DryRunDone)
            {
                throw;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "QmOthIns 自动审核 " + ex.Message);
                saved.Body["auto_verify_error"] = "保存后自动审核出错，报检单保存为未审核或结果未知，请回读后用 vouchers/verify 补审："
                    + QmGen.Line(ex);
            }
            return saved;
        }

        static void Audit(WorkContext ctx, QmOthSpec spec, ApiResult saved)
        {
            int id = CoRows.AsId(saved.Body["id"]);
            QmOutcome outcome = QmOthOps.Call(ctx, spec, id, "AuditVoucher");
            bool read;
            Dictionary<string, object> doc = QmOthOps.Fresh(ctx, spec, id, out read);
            if (doc != null && QmDoc.Verified(doc))
            {
                Dictionary<string, object> state = CoRows.StateOf(doc);
                state["verified"] = true;
                saved.Body["state"] = state;
                return;
            }
            saved.Body["auto_verify_error"] = QmOthOps.Missed(outcome, read && doc != null, "自动审核").Message;
        }

        static QmOthInsJob Load(WorkContext ctx, QmOthSpec spec, QmOthAsk ask)
        {
            object conn = ctx.Conn;
            QmOthInsJob job = new QmOthInsJob();
            job.Ctx = ctx;
            job.Spec = spec;
            job.Ask = ask;
            job.Date = ask.Date.Length > 0 ? ask.Date : (ctx.Item.Date ?? "").Trim();
            job.Dep = ask.Get("cinspectdepcode");
            if (job.Dep.Length == 0)
            {
                job.Dep = QmSql.Scalar(conn, LastDepSql, spec.VouchType, ctx.OperatorName.Trim());
            }
            try
            {
                QmSql.DeptName(conn, job.Dep);
            }
            catch (BridgeException ex)
            {
                throw ex.WithField("head.cinspectdepcode");
            }
            for (int i = 0; i < ask.Lines.Count; i++)
            {
                try
                {
                    job.Invs.Add(Inventory(conn, ask.Lines[i]));
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
            job.Digits = QmRejUnits.Digits(QmSql.Scalar(conn, DigitsSql));
            job.DefineNames = QmOthDom.DefineNames(conn, spec.Vt, ask.Head.Keys);
            return job;
        }

        static Dictionary<string, object> Inventory(object conn, QmOthLine line)
        {
            Dictionary<string, object> inv = QmSql.One(conn, InvSql, line.Inv);
            if (inv == null)
            {
                throw BridgeException.BadField("cinvcode", "存货不存在 " + line.Inv);
            }
            if (line.Wh.Length > 0 && Rows.Scalar(conn, QmSql.WhSql, new object[] { line.Wh }) == null)
            {
                throw BridgeException.BadField("cwhcode", "仓库不存在 " + line.Wh);
            }
            return inv;
        }

        // doms：0、1 空白模板，2、3 VO 的表头、表体。
        static void Prepare(QmOthInsJob job, QmRejCo co, object[] doms)
        {
            object conn = job.Ctx.Conn;
            object[] blank = QmOthDom.BlankPair(conn, job.Spec);
            doms[0] = blank[0];
            doms[1] = blank[1];
            co.Push(doms[0], doms[1]);
            co.AddNew();
            doms[2] = co.Head();
            doms[3] = co.Body();
            QmOthDom.FillHead(doms[2], HeadFields(job));
            List<List<string[]>> lines = new List<List<string[]>>();
            for (int i = 0; i < job.Ask.Lines.Count; i++)
            {
                lines.Add(LineFields(job.Ask.Lines[i], job.Invs[i], job.Digits));
            }
            QmOthDom.FillBody(doms[3], lines);
            co.Push(doms[2], doms[3]);
            if (co.RowCount() < 1)
            {
                throw new BridgeException(409, "u8_rejected", "U8 没有接收表体行");
            }
            QmRejTpl.Require(conn, job.Spec.Vt, doms[2], doms[3], QmOthReq.LinesMax, QmOthReq.TplAsk(job.Spec.CodeColumn));
        }

        // 表头（不含单号，核对之后才取号）。制单人（报检人）是操作员姓名；检验类型 OTH；不写来源列。
        internal static List<string[]> HeadFields(QmOthInsJob job)
        {
            QmOthSpec spec = job.Spec;
            List<string[]> f = new List<string[]>();
            f.Add(QmOthDom.Pair("editprop", QmOthDom.Added));
            f.Add(QmOthDom.Pair("CVOUCHTYPE", spec.VouchType));
            f.Add(QmOthDom.Pair("IVTID", QmOthDom.Int(spec.Vt)));
            f.Add(QmOthDom.Pair("CCHECKTYPECODE", QmOthSpec.CheckType));
            f.Add(QmOthDom.Pair("DDATE", job.Date));
            f.Add(QmOthDom.Pair("CTIME", QmDom.Now()));
            f.Add(QmOthDom.Pair("CINSPECTDEPCODE", job.Dep));
            f.Add(QmOthDom.Pair("CMAKER", job.Ctx == null ? "" : job.Ctx.OperatorName));
            QmOthDom.Defines(f, job.Ask.Head, job.DefineNames);
            return f;
        }

        // 一行：存货（档案的规范编码）、数量、检验方式、仓库；存货有换算（iGroupType 不为 0）时按库存计量单位带辅计量和件数
        // （件数 = 数量 / 换算率，按账套件数小数位四舍五入，同不良品处理单 QmRejUnits）。
        internal static List<string[]> LineFields(QmOthLine line, Dictionary<string, object> inv, int digits)
        {
            List<string[]> f = new List<string[]>();
            f.Add(QmOthDom.Pair("editprop", QmOthDom.Added));
            f.Add(QmOthDom.Pair("CINVCODE", CoRows.Col(inv, "cInvCode")));
            f.Add(QmOthDom.Pair("FQUANTITY", line.Qty));
            f.Add(QmOthDom.Pair("ITESTSTYLE", line.TestStyle >= 0 ? QmOthDom.Int(line.TestStyle) : CoRows.Col(inv, "TestStyle")));
            f.Add(QmOthDom.Pair("CWHCODE", line.Wh));
            f.Add(QmOthDom.Pair("IORDERTYPE", "0"));
            f.Add(QmOthDom.Pair("BEXIGENCY", "0"));
            string unit = CoRows.Col(inv, "Unit");
            decimal rate = QmSql.Dec(CoRows.Col(inv, "Rate"));
            string pieces = CoRows.Col(inv, "GroupType") == "0" ? "" : QmRejUnits.Pieces(line.Qty, unit, rate, digits);
            if (pieces.Length > 0)
            {
                f.Add(QmOthDom.Pair("CUNITID", unit));
                f.Add(QmOthDom.Pair("FCHANGRATE", rate));
                f.Add(QmOthDom.Pair("FNUM", pieces));
            }
            return f;
        }

        static void DryInput(object[] doms)
        {
            if (!DryRun.Active)
            {
                return;
            }
            Dictionary<string, object> input = new Dictionary<string, object>();
            List<Dictionary<string, object>> heads = Rows.FromDom(doms[2], 1);
            if (heads != null && heads.Count > 0)
            {
                input["head"] = heads[0];
            }
            input["lines"] = Rows.FromDom(doms[3], QmOthReq.LinesMax);
            DryRun.Set("input", input);
        }

        // 数据权限按每一行：存货、仓库，部门取报检部门。
        static List<Dictionary<string, object>> PermRows(QmOthInsJob job)
        {
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            for (int i = 0; i < job.Ask.Lines.Count; i++)
            {
                Dictionary<string, object> row = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
                row["CVENCODE"] = "";
                row["CDEPCODE"] = job.Dep;
                row["CINVCODE"] = CoRows.Col(job.Invs[i], "cInvCode");
                row["CWHCODE"] = job.Ask.Lines[i].Wh;
                rows.Add(row);
            }
            return rows;
        }
    }
}
