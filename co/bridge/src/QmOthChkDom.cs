using System;
using System.Collections.Generic;

namespace U8Co
{
    // 其他检验单（QM15）的表头和检验项目字段：字段集同来料检验单（QmChkDom / QmItems，docs/u8-notes.md「质量单据新增」），
    // 属性名照模板字段名的大写写（QmOthDom）。没有来源单据：不写 CSOURCE / SOURCEID / SOURCEAUTOID / SOURCECODE，
    // 检验类型取报检单的（OTH）；不受审批流控制，ISWFCONTROLLED、IVERIFYSTATE、IVERIFYSTATENEW 都写 0。
    internal static class QmOthChkDom
    {
        static readonly string[] ZeroFlags = new string[]
        {
            "BEXIGENCY", "IDISBREAKQTYDEALTYPE", "IBATCHCHKRESULT", "BPUINFLAG", "BPROINFLAG", "BREJFLAG", "BSTNEXTYEAR",
            "IORDERTYPE", "ISOORDERTYPE", "IRETURNCOUNT", "IEXPIRATDATECALCU", "BMERGECHECKFLAG", "BWITHINSPECTINFO",
            "BPRODUCECHECKDETAIL", "IVERIFYSTATENEW", "IVERIFYSTATE", "ISWFCONTROLLED"
        };

        // 表头（不含单号 CCHECKCODE，核对之后才取号）。
        internal static List<string[]> Head(QmChkJob job, Dictionary<string, string> defineNames)
        {
            List<string[]> f = new List<string[]>();
            f.Add(QmOthDom.Pair("editprop", QmOthDom.Added));
            Links(f, job);
            People(f, job);
            Goods(f, job);
            Result(f, job);
            f.Add(QmOthDom.Pair("PROJECTID", CoRows.Col(job.Project, "ID")));
            f.Add(QmOthDom.Pair("CPROJECTCODE", CoRows.Col(job.Project, "CPROJECTCODE")));
            f.Add(QmOthDom.Pair("CPROJECTNAME", CoRows.Col(job.Project, "CPROJECTNAME")));
            f.Add(QmOthDom.Pair("CCHKNORMALCODE", CoRows.Col(job.Project, "CCHKNORMALCODE")));
            for (int i = 0; i < ZeroFlags.Length; i++)
            {
                f.Add(QmOthDom.Pair(ZeroFlags[i], "0"));
            }
            QmOthDom.Defines(f, job.Ask.Head, defineNames);
            return f;
        }

        // 单据类型、报检单关联（INSPECTID / INSPECTAUTOID 是其他报检单的 ID / 行 AUTOID）、检验类型。
        static void Links(List<string[]> f, QmChkJob job)
        {
            f.Add(QmOthDom.Pair("CVOUCHTYPE", job.Spec.VouchType));
            f.Add(QmOthDom.Pair("IVTID", QmOthDom.Int(job.Spec.Vt)));
            f.Add(QmOthDom.Pair("INSPECTID", QmOthDom.Int(job.InspectId)));
            f.Add(QmOthDom.Pair("CINSPECTCODE", CoRows.Col(job.Head, "CINSPECTCODE")));
            f.Add(QmOthDom.Pair("INSPECTAUTOID", QmOthDom.Int(job.LineAsk.SourceLineId)));
            f.Add(QmOthDom.Pair("CCHECKTYPECODE", Or(CoRows.Col(job.Head, "CCHECKTYPECODE"), QmOthSpec.CheckType)));
            f.Add(QmOthDom.Pair("CCHECKTYPENAME", job.TypeName));
        }

        // 检验部门 CDEPCODE（不是报检部门）、报检部门、报检人（报检单制单人）、检验员、制单人（操作员姓名）。
        static void People(List<string[]> f, QmChkJob job)
        {
            f.Add(QmOthDom.Pair("DDATE", job.Date));
            f.Add(QmOthDom.Pair("CTIME", job.Time));
            f.Add(QmOthDom.Pair("DINSPECTDATE", CoRows.Col(job.Head, "DDATE")));
            f.Add(QmOthDom.Pair("CINSPECTTIME", Or(CoRows.Col(job.Head, "CTIME"), job.Time)));
            f.Add(QmOthDom.Pair("CINSPECTDEPCODE", CoRows.Col(job.Head, "CINSPECTDEPCODE")));
            f.Add(QmOthDom.Pair("CINSPECTDEPNAME", job.InspectDepName));
            f.Add(QmOthDom.Pair("CDEPCODE", job.DepCode));
            f.Add(QmOthDom.Pair("CDEPNAME", job.DepName));
            f.Add(QmOthDom.Pair("CINSPECTPERSON", CoRows.Col(job.Head, "CMAKER")));
            f.Add(QmOthDom.Pair("CCHECKPERSONCODE", job.Ask.Get("ccheckpersoncode")));
            f.Add(QmOthDom.Pair("CCHECKPERSONNAME", job.PersonName));
            f.Add(QmOthDom.Pair("CMAKER", job.Ctx == null ? "" : job.Ctx.OperatorName));
            if (job.Yield != null && job.Yield[0].Length > 0)
            {
                f.Add(QmOthDom.Pair("CYIELDERCODE", job.Yield[0]));
                f.Add(QmOthDom.Pair("CYIELDERNAME", job.Yield[1]));
                f.Add(QmOthDom.Pair("DYIELDDATE", job.Yield[2]));
            }
        }

        static void Goods(List<string[]> f, QmChkJob job)
        {
            f.Add(QmOthDom.Pair("CINVCODE", CoRows.Col(job.Line, "CINVCODE")));
            f.Add(QmOthDom.Pair("CWHCODE", CoRows.Col(job.Line, "CWHCODE")));
            f.Add(QmOthDom.Pair("ITESTSTYLE", CoRows.Col(job.Line, "TestStyle")));
            f.Add(QmOthDom.Pair("ITESTRULE", CoRows.Col(job.Line, "TestRule")));
            f.Add(QmOthDom.Pair("CCHECKUNIT", CoRows.Col(job.Line, "cComUnitCode")));
            f.Add(QmOthDom.Pair("CCOMUNITCODE", CoRows.Col(job.Line, "cComUnitCode")));
            f.Add(QmOthDom.Pair("FCHECKRATE", "1"));
        }

        // 数量、抽检量与结论；报检单行有辅单位时按同一换算率带件数。
        static void Result(List<string[]> f, QmChkJob job)
        {
            QmLineAsk a = job.LineAsk;
            f.Add(QmOthDom.Pair("FQUANTITY", a.Qty));
            f.Add(QmOthDom.Pair("FREGQUANTITY", a.Reg));
            f.Add(QmOthDom.Pair("FCONQUANTIY", a.Con));
            f.Add(QmOthDom.Pair("FDISQUANTITY", a.Dis));
            f.Add(QmOthDom.Pair("FDTQUANTITY", job.Dt));
            f.Add(QmOthDom.Pair("FDISUBREAKQUANTITY", job.Dt));
            f.Add(QmOthDom.Pair("CCHKCONCLUSION", job.Conclusion));
            string unit = CoRows.Col(job.Line, "CUNITID");
            decimal rate = QmSql.Dec(CoRows.Col(job.Line, "Rate"));
            if (unit.Length == 0 || rate <= 0m)
            {
                return;
            }
            f.Add(QmOthDom.Pair("CUNITID", unit));
            f.Add(QmOthDom.Pair("FCHANGRATE", rate));
            f.Add(QmOthDom.Pair("FNUM", Pieces(a.Qty, rate)));
            f.Add(QmOthDom.Pair("FREGNUM", Pieces(a.Reg, rate)));
            f.Add(QmOthDom.Pair("FCONNUM", Pieces(a.Con, rate)));
            f.Add(QmOthDom.Pair("FDISNUM", Pieces(a.Dis, rate)));
        }

        // 检验项目：每个方案行一行；缺省检验值 = 标准值、单项判定「合格」，请求的 items 覆盖（QmItems 的规则）。
        internal static List<List<string[]>> Items(QmChkJob job)
        {
            Dictionary<string, QmItemAsk> asked = QmItems.Asked(job);
            List<List<string[]>> rows = new List<List<string[]>>();
            for (int i = 0; i < job.ProjectLines.Count; i++)
            {
                Dictionary<string, object> src = job.ProjectLines[i];
                QmItemAsk ask;
                asked.TryGetValue(QmItems.Key(CoRows.Col(src, "CCHKITEMCODE"), CoRows.Col(src, "CCHKGUIDECODE")), out ask);
                rows.Add(Item(job, src, ask));
            }
            return rows;
        }

        static List<string[]> Item(QmChkJob job, Dictionary<string, object> src, QmItemAsk ask)
        {
            string standard = CoRows.Col(src, "CSTANDARD");
            string dt = QmSql.Num(job.Dt);
            string judge = ask != null && ask.Judge != null ? ask.Judge : QmReq.Qualified;
            List<string[]> f = new List<string[]>();
            f.Add(QmOthDom.Pair("editprop", QmOthDom.Added));
            f.Add(QmOthDom.Pair("CCHKITEMCODE", CoRows.Col(src, "CCHKITEMCODE")));
            f.Add(QmOthDom.Pair("CCHKGUIDECODE", CoRows.Col(src, "CCHKGUIDECODE")));
            f.Add(QmOthDom.Pair("CSTANDARD", standard));
            f.Add(QmOthDom.Pair("CGUIDEUNIT", CoRows.Col(src, "CGUIDEUNIT")));
            f.Add(QmOthDom.Pair("BGUIDETYPE", CoRows.Col(src, "BGUIDETYPE")));
            f.Add(QmOthDom.Pair("CCHECKVALUE", ask != null && ask.Value != null ? ask.Value : standard));
            f.Add(QmOthDom.Pair("CTARGETQJUG", judge));
            f.Add(QmOthDom.Pair(judge == QmReq.Qualified ? "FQUIDEREGQUANTITY" : "FQUIDEDISQUANTITY", dt));
            f.Add(QmOthDom.Pair("IDTMETHODB", Or(CoRows.Col(src, "IDTMETHOD"), "1")));
            f.Add(QmOthDom.Pair("FCHKQUANTITY", dt));
            f.Add(QmOthDom.Pair("FCHKVALIDQTY", dt));
            f.Add(QmOthDom.Pair("FTARGETQTY", dt));
            f.Add(QmOthDom.Pair("FDTQUANTITYB", dt));
            f.Add(QmOthDom.Pair("FGQUANTITY", job.LineAsk.Qty));
            f.Add(QmOthDom.Pair("FGCHANGERATE", "1"));
            string group = CoRows.Col(job.Line, "cGroupCode");
            string unit = CoRows.Col(job.Line, "cComUnitCode");
            if (group.Length > 0 && unit.Length > 0)
            {
                f.Add(QmOthDom.Pair("CGROUPCODE", group));
                f.Add(QmOthDom.Pair("CGCOMUNITCODE", unit));
            }
            f.Add(QmOthDom.Pair("DCHECKDATE", job.Date));
            f.Add(QmOthDom.Pair("CCHECKTIME", job.Time));
            f.Add(QmOthDom.Pair("BMUSTCHECK", Or(CoRows.Col(src, "BMUSTCHECK"), "0")));
            f.Add(QmOthDom.Pair("CBUGGRADE", Or(CoRows.Col(src, "CBUGGRADE"), "0")));
            return f;
        }

        static decimal Pieces(decimal qty, decimal rate)
        {
            return decimal.Round(qty / rate, 6, MidpointRounding.AwayFromZero);
        }

        static string Or(string value, string fallback)
        {
            return value != null && value.Length > 0 ? value : fallback;
        }
    }
}
