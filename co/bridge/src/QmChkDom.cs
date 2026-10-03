using System;
using System.Globalization;

namespace U8Co
{
    // 检验单表头（QM03 / QM04 一张单一个存货，表头即存货行）：照测试账套上跑通的字段集（docs/u8-notes.md「质量单据新增」）。
    // 检验部门 U8 按名称 CDEPNAME 校验；IsWfControlled 列不许为 NULL（有启用的审批流程为 1）；抽检量 FDTQUANTITY 必须大于 0。
    // FCHECKQTY 不写（U8 自己保存的检验单这一列是 NULL，它也不是抽检量）。
    internal static class QmChkDom
    {
        static readonly string[] ZeroFlags = new string[]
        {
            "BEXIGENCY", "IDISBREAKQTYDEALTYPE", "IBATCHCHKRESULT", "BPUINFLAG", "BPROINFLAG", "BREJFLAG", "BSTNEXTYEAR",
            "IORDERTYPE", "ISOORDERTYPE", "IRETURNCOUNT", "IEXPIRATDATECALCU", "BMERGECHECKFLAG", "BWITHINSPECTINFO",
            "BPRODUCECHECKDETAIL", "IVERIFYSTATENEW", "IVERIFYSTATE"
        };

        public static void FillHead(QmChkJob job, object dom)
        {
            using (QmRow r = QmRow.Add(dom))
            {
                Links(r, job);
                People(r, job);
                Goods(r, job);
                Result(r, job);
                Scheme(r, job);
                for (int i = 0; i < ZeroFlags.Length; i++)
                {
                    r.Put(ZeroFlags[i], "0");
                }
                r.Put("ISWFCONTROLLED", job.Wf ? "1" : "0");
                QmGenInspect.PutDefines(r, job.Ask);
                r.Raw("editprop", QmDom.Added);
            }
        }

        // 单据类型、报检单关联、原始来源（到货单 / 生产订单）。
        static void Links(QmRow r, QmChkJob job)
        {
            QmSpec spec = job.Spec;
            r.Put("CVOUCHTYPE", spec.VouchType);
            r.Put("IVTID", spec.Vt.ToString(CultureInfo.InvariantCulture));
            r.Put("INSPECTID", job.InspectId.ToString(CultureInfo.InvariantCulture));
            r.Put("CINSPECTCODE", CoRows.Col(job.Head, "CINSPECTCODE"));
            r.Put("INSPECTAUTOID", job.LineAsk.SourceLineId.ToString(CultureInfo.InvariantCulture));
            r.Put("SOURCEID", CoRows.Col(job.Head, "CSOURCEID"));
            r.Put("SOURCEAUTOID", CoRows.Col(job.Line, "SOURCEAUTOID"));
            r.Put("SOURCECODE", CoRows.Col(job.Head, "CSOURCECODE"));
            r.Put("CSOURCE", Or(CoRows.Col(job.Head, "CSOURCE"), spec.SourceText));
            r.Put("CPOCODE", CoRows.Col(job.Line, "CPOCODE"));
            r.Put("IPROORDERID", CoRows.Col(job.Line, "IPROORDERID"));
            r.Put("CPROORDERCODE", CoRows.Col(job.Line, "CPROORDERCODE"));
            r.Put("IPROORDERAUTOID", CoRows.Col(job.Line, "IPROORDERAUTOID"));
            if (!spec.Incoming)
            {
                r.Put("CBYPRODUCT", "0");
            }
            r.Put("CCHECKTYPECODE", Or(CoRows.Col(job.Head, "CCHECKTYPECODE"), spec.CheckType));
            r.Put("CCHECKTYPENAME", job.TypeName);
        }

        // 日期时间、部门、人员。报检人 CINSPECTPERSON 是报检单的制单人；制单人 CMAKER 是操作员姓名。
        static void People(QmRow r, QmChkJob job)
        {
            r.Put("DDATE", job.Date);
            r.Put("CTIME", job.Time);
            r.Put("DINSPECTDATE", CoRows.Col(job.Head, "DDATE"));
            r.Put("CINSPECTTIME", Or(CoRows.Col(job.Head, "CTIME"), job.Time));
            r.Put("DARRIVALDATE", CoRows.Col(job.Head, "DARRIVALDATE"));
            r.Put("CINSPECTDEPCODE", CoRows.Col(job.Head, "CINSPECTDEPCODE"));
            r.Put("CINSPECTDEPNAME", job.InspectDepName);
            r.Put("CDEPCODE", job.DepCode);
            r.Put("CDEPNAME", job.DepName);
            r.Put("CINSPECTPERSON", CoRows.Col(job.Head, "CMAKER"));
            r.Put("CCHECKPERSONCODE", job.Ask.Get("ccheckpersoncode"));
            r.Put("CCHECKPERSONNAME", job.PersonName);
            r.Put("CVENCODE", CoRows.Col(job.Head, "CVENCODE"));
            r.Put("CMAKER", job.Ctx.OperatorName);
            // 让步接收核准人（QmYield）：送了编码才写三列。
            if (job.Yield != null && job.Yield[0].Length > 0)
            {
                r.Put("CYIELDERCODE", job.Yield[0]);
                r.Put("CYIELDERNAME", job.Yield[1]);
                r.Put("DYIELDDATE", job.Yield[2]);
            }
        }

        // 存货、仓库、批次、检验方式与规则、主计量单位。
        static void Goods(QmRow r, QmChkJob job)
        {
            r.Put("CINVCODE", CoRows.Col(job.Line, "CINVCODE"));
            r.Put("CWHCODE", CoRows.Col(job.Line, "CWHCODE"));
            r.Put("CBATCH", CoRows.Col(job.Line, "CBATCH"));
            r.Put("ITESTSTYLE", CoRows.Col(job.Line, "TestStyle"));
            r.Put("ITESTRULE", CoRows.Col(job.Line, "TestRule"));
            r.Put("CCHECKUNIT", CoRows.Col(job.Line, "cComUnitCode"));
            r.Put("CCOMUNITCODE", CoRows.Col(job.Line, "cComUnitCode"));
            r.Put("FCHECKRATE", "1");
        }

        // 数量与结论。报检单行有辅单位时按同一换算率带件数。
        static void Result(QmRow r, QmChkJob job)
        {
            QmLineAsk a = job.LineAsk;
            r.Put("FQUANTITY", a.Qty);
            r.Put("FREGQUANTITY", a.Reg);
            r.Put("FCONQUANTIY", a.Con);
            r.Put("FDISQUANTITY", a.Dis);
            r.Put("FDTQUANTITY", job.Dt);
            // 审核时 U8 校验「抽检量 = 样本不合格数 + 样本合格数」；U8 自己保存的检验单把抽检量整数记在这一列。
            r.Put("FDISUBREAKQUANTITY", job.Dt);
            r.Put("CCHKCONCLUSION", job.Conclusion);
            string unit = CoRows.Col(job.Line, "CUNITID");
            decimal rate = QmSql.Dec(CoRows.Col(job.Line, "Rate"));
            if (unit.Length == 0 || rate <= 0m)
            {
                return;
            }
            r.Put("CUNITID", unit);
            r.Put("FCHANGRATE", rate);
            r.Put("FNUM", Pieces(a.Qty, rate));
            r.Put("FREGNUM", Pieces(a.Reg, rate));
            r.Put("FCONNUM", Pieces(a.Con, rate));
            r.Put("FDISNUM", Pieces(a.Dis, rate));
        }

        static void Scheme(QmRow r, QmChkJob job)
        {
            r.Put("PROJECTID", CoRows.Col(job.Project, "ID"));
            r.Put("CPROJECTCODE", CoRows.Col(job.Project, "CPROJECTCODE"));
            r.Put("CPROJECTNAME", CoRows.Col(job.Project, "CPROJECTNAME"));
            r.Put("CCHKNORMALCODE", CoRows.Col(job.Project, "CCHKNORMALCODE"));
        }

        static decimal Pieces(decimal qty, decimal rate)
        {
            return decimal.Round(qty / rate, 6, MidpointRounding.AwayFromZero);
        }

        static string Or(string value, string fallback)
        {
            return value != null && value.Length > 0 ? value : fallback ?? "";
        }
    }
}
