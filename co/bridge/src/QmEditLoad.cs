using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 质量单据修改的准备：功能权限 write:<类型>:update、读单据（404）、数据权限、闸门、请求字段 → 表头列（QmEditJob.Fields）。
    // 全部在 U8 调用之前做完；U8 的修改自己提交，闸门之后不能有任何写入。
    internal static class QmEditLoad
    {
        const string HeadSql = "select * from {0} where ID=? and CVOUCHTYPE=?";
        const string ExtraSql = "select * from {0}_extradefine where ID=?";
        const string ExtraExistsSql = "SELECT CASE WHEN OBJECT_ID(?, N'U') IS NULL THEN N'0' ELSE N'1' END";
        const string LineStateSql = "select convert(varchar(40), isnull(FSUMCHECKQTY,0)) + '|' + convert(varchar(5), isnull(BFLAG,0))"
            + " from QMINSPECTVOUCHERS where AUTOID=?";

        // 检验单：来料 / 产品检验单（QmSpec）或其他检验单（QmOthSpec）。
        public static QmEditJob Check(WorkContext ctx, QmEditAsk ask, int id, string vouchType, int vt, string ruleKey)
        {
            QmEditJob job = Load(Begin(ctx, ask, id, vouchType, vt, "QMCHECKVOUCHER"), ruleKey);
            QmEditGate.Check(ctx.Conn, job, id);
            job.Extra = Extra(ctx.Conn, job);
            job.LineState = LineState(ctx.Conn, job);
            CheckFields(ctx.Conn, job);
            return job;
        }

        // 其他报检单（QM11）。
        public static QmEditJob Inspect(WorkContext ctx, QmEditAsk ask, int id, QmOthSpec spec)
        {
            QmEditJob job = Load(Begin(ctx, ask, id, spec.VouchType, spec.Vt, spec.HeadTable), spec.RuleKey("update"));
            QmEditGate.Inspect(ctx.Conn, job, id);
            string dep = ask.Get("cinspectdepcode");
            if (dep.Length > 0)
            {
                try
                {
                    QmSql.DeptName(ctx.Conn, dep);
                }
                catch (BridgeException ex)
                {
                    throw ex.WithField("head.cinspectdepcode");
                }
                job.Add("CINSPECTDEPCODE", dep, "cinspectdepcode");
            }
            Common(job);
            return job;
        }

        // 扩展自定义项行（<表头表>_extradefine，没有行为 null）。账套没有建扩展自定义项表时（空账套）同样为 null。
        public static Dictionary<string, object> Extra(object conn, QmEditJob job)
        {
            string table = job.HeadTable + "_extradefine";
            if (QmSql.Scalar(conn, ExtraExistsSql, table) != "1")
            {
                return null;
            }
            string sql = string.Format(CultureInfo.InvariantCulture, ExtraSql, CoRows.Ident(job.HeadTable));
            return QmSql.One(conn, sql, job.Id);
        }

        // 报检单行的累计检验数量|检验标记（BFLAG）；修改不应改动它们，保存后在新连接上再读一次核对。
        public static string LineState(object conn, QmEditJob job)
        {
            int line = CoRows.AsId(CoRows.Col(job.Doc, "INSPECTAUTOID"));
            return line > 0 ? QmSql.Scalar(conn, LineStateSql, line) : "";
        }

        static QmEditJob Begin(WorkContext ctx, QmEditAsk ask, int id, string vouchType, int vt, string table)
        {
            QmEditJob job = new QmEditJob();
            job.Ctx = ctx;
            job.Ask = ask;
            job.Id = id;
            job.VouchType = vouchType;
            job.Vt = vt;
            job.HeadTable = table;
            job.Title = ask.Kind.Title;
            return job;
        }

        static QmEditJob Load(QmEditJob job, string ruleKey)
        {
            object conn = job.Ctx.Conn;
            PermContext perm = PermCheck.Of(job.Ctx);
            PermRule rule = PermRegistry.ForKey(ruleKey);
            PermCheck.RequireRule(perm, rule);
            string sql = string.Format(CultureInfo.InvariantCulture, HeadSql, CoRows.Ident(job.HeadTable));
            job.Doc = QmSql.One(conn, sql, job.Id, job.VouchType);
            if (job.Doc == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            MoDelete.CheckRows(perm, rule, PermRows(conn, job));
            job.DefineNames = QmOthDom.DefineNames(conn, job.Vt, job.Ask.Head.Keys);
            if (!job.Ask.Check)
            {
                return job;
            }
            Dictionary<string, string> extras = QmOthDom.DefineNames(conn, job.Vt, QmEditDom.ExtraNames());
            foreach (KeyValuePair<string, string> kv in extras)
            {
                job.DefineNames[kv.Key] = kv.Value;
            }
            return job;
        }

        // 数据权限：检验单一行（表头的供应商、部门、存货、仓库）；其他报检单按每一行（QmOthDel.PermRows），改报检部门时新部门也要有权限。
        static List<Dictionary<string, object>> PermRows(object conn, QmEditJob job)
        {
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            if (job.Ask.Check)
            {
                rows.Add(job.Doc);
                return rows;
            }
            QmOthSpec spec = QmOthSpec.Find(QmOthSpec.InspectKind);
            rows = QmOthDel.PermRows(conn, spec, job.Id, job.Doc);
            string dep = job.Ask.Get("cinspectdepcode");
            int n = rows.Count;
            for (int i = 0; dep.Length > 0 && i < n; i++)
            {
                Dictionary<string, object> copy = new Dictionary<string, object>(rows[i], StringComparer.OrdinalIgnoreCase);
                copy["CDEPCODE"] = dep;
                rows.Add(copy);
            }
            return rows;
        }

        // 检验单表头：检验员（同时写姓名）、合格 / 让步 / 不良（按已有检验数量核对，有辅单位时连件数）、结论、原因。
        static void CheckFields(object conn, QmEditJob job)
        {
            QmEditAsk ask = job.Ask;
            if (ask.Has("ccheckpersoncode"))
            {
                string person = ask.Get("ccheckpersoncode");
                string name;
                try
                {
                    name = QmChkSrc.Person(conn, person);
                }
                catch (BridgeException ex)
                {
                    throw ex.WithField("head.ccheckpersoncode");
                }
                job.Add("CCHECKPERSONCODE", person, "ccheckpersoncode");
                job.AddDerived("CCHECKPERSONNAME", name, "ccheckpersoncode");
            }
            decimal[] split = ask.Split ? Split(job) : null;
            decimal con = split != null ? split[1] : QmSql.Dec(CoRows.Col(job.Doc, "FCONQUANTIY"));
            QmEditReq.RequireReason(ask, split != null, con, CoRows.Col(job.Doc, "CREASONCODE"), QmOthSpec.IsCheck(ask.Kind));
            string conclusion = ask.Get("cchkconclusion");
            if (conclusion.Length == 0 && split != null)
            {
                conclusion = QmMath.Conclusion(split[2]);
            }
            if (conclusion.Length > 0)
            {
                job.Add("CCHKCONCLUSION", conclusion, "cchkconclusion");
            }
            if (ask.Has("creasoncode"))
            {
                job.Add("CREASONCODE", ask.Get("creasoncode"), "creasoncode");
            }
            Yield(conn, job, con);
            Common(job);
        }

        // 让步接收核准人（QmYield）：来料 / 产品检验单修改后让步数量大于 0 时必须有（请求或单据上已有）；送了编码桥写姓名，
        // 核准日期没送取请求的单据日期，再没有取登录日期。只送核准日期时只改日期。
        static void Yield(object conn, QmEditJob job, decimal con)
        {
            QmEditAsk ask = job.Ask;
            string code = ask.Get(QmYield.CodeKey);
            QmYield.Require(code, CoRows.Col(job.Doc, "CYIELDERCODE"), con, !QmOthSpec.IsCheck(ask.Kind), "head." + QmYield.CodeKey);
            if (code.Length == 0 && !ask.Has(QmYield.DateKey))
            {
                return;
            }
            string fallback = ask.Has("ddate") ? ask.Get("ddate") : (job.Ctx.Item.Date ?? "").Trim();
            string[] y = QmYield.Resolve(conn, code, ask.Get(QmYield.DateKey), fallback, "head.");
            if (code.Length > 0)
            {
                job.Add("CYIELDERCODE", y[0], QmYield.CodeKey);
                job.AddDerived("CYIELDERNAME", y[1], QmYield.CodeKey);
            }
            job.Add("DYIELDDATE", y[2], QmYield.DateKey);
        }

        static decimal[] Split(QmEditJob job)
        {
            decimal qty = QmSql.Dec(CoRows.Col(job.Doc, "FQUANTITY"));
            decimal[] split;
            try
            {
                split = QmEditReq.Resolve(job.Ask, qty);
            }
            catch (BridgeException ex)
            {
                throw FieldPath.Under(ex, "head");
            }
            string[] cols = new string[] { "FREGQUANTITY", "FCONQUANTIY", "FDISQUANTITY" };
            string[] nums = new string[] { "FREGNUM", "FCONNUM", "FDISNUM" };
            string[] fields = new string[] { "fregquantity", "fconquantiy", "fdisquantity" };
            decimal rate = QmSql.Dec(CoRows.Col(job.Doc, "FCHANGRATE"));
            bool pieces = CoRows.Col(job.Doc, "CUNITID").Length > 0 && rate > 0m;
            for (int i = 0; i < cols.Length; i++)
            {
                job.Add(cols[i], QmSql.Num(split[i]), fields[i]);
                if (pieces)
                {
                    job.AddDerived(nums[i], QmSql.Num(decimal.Round(split[i] / rate, 6, MidpointRounding.AwayFromZero)), fields[i]);
                }
            }
            return split;
        }

        // 两类共有：单据日期、自定义项（cdefine 写大写列名，chdefine 写模板字段名）。
        static void Common(QmEditJob job)
        {
            QmEditAsk ask = job.Ask;
            if (ask.Has("ddate"))
            {
                job.Add("DDATE", ask.Get("ddate"), "ddate");
            }
            foreach (KeyValuePair<string, string> kv in ask.Head)
            {
                if (kv.Key.StartsWith("chdefine", StringComparison.Ordinal))
                {
                    job.Add(QmEditDom.DefineName(job, kv.Key), kv.Value, kv.Key);
                }
                else if (kv.Key.StartsWith("cdefine", StringComparison.Ordinal))
                {
                    job.Add(kv.Key.ToUpperInvariant(), kv.Value, kv.Key);
                }
            }
        }
    }
}
