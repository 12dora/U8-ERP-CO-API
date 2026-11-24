using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 合并检验的表头标记与合并明细 DOM（视图 QM_QProCheckMergeDetailB，即 QMMergeCheckDetail 的卡片视图）。
    // 表头：BMERGECHECKFLAG=1、MERGECHECKRULE=请求的合并规则编码（缺省 0）、BWITHINSPECTINFO=1（表头带取用来源的报检信息）、
    // BPRODUCECHECKDETAIL=1，CPROORDERCODE 是各来源订单号，自由项取表头取用的来源，件数是各行件数之和（与 U8 合并检验
    // 保存的表头一致）。合并明细每条来源一行，字段取自报检单表头、表体行，数量、件数是本来源的（各行之和等于表头，U8 保存时核对）；
    // CPROORDERCODE、CWHCODE 不写、ISOURCEPROORDERID / ISOURCEPROORDERAUTOID 写 0（同 U8 保存的合并明细）。明细由 U8 组件写入，桥不写 SQL。
    internal static class QmMergeDom
    {
        public const string View = "QM_QProCheckMergeDetailB";
        static readonly string[] ZeroFlags = new string[]
        {
            "IORDERTYPE", "BEXIGENCY", "IDISBREAKQTYDEALTYPE", "IBATCHCHKRESULT", "BPROINFLAG", "BREJFLAG", "BPUINFLAG",
            "ISOURCEPROORDERID", "ISOURCEPROORDERAUTOID"
        };

        public static void Flags(QmRow r, QmChkJob job)
        {
            if (job.Merge == null)
            {
                return;
            }
            r.Put("BMERGECHECKFLAG", "1");
            r.Put("MERGECHECKRULE", QmMergeRules.Text(job.Merge.Rules));
            r.Put("BWITHINSPECTINFO", "1");
            r.Put("BPRODUCECHECKDETAIL", "1");
            r.Put("CPROORDERCODE", job.Merge.MoCodes);
            Frees(r, job.Merge.Lead);
            if (job.Merge.Lead.Num <= 0m)
            {
                return;
            }
            decimal[] sum = new decimal[4];
            for (int i = 0; i < job.Merge.Sources.Count; i++)
            {
                QmMergeSource src = job.Merge.Sources[i];
                sum[0] += src.Num;
                sum[1] += src.RegNum;
                sum[2] += src.ConNum;
                sum[3] += src.DisNum;
            }
            Pieces(r, sum[0], sum[1], sum[2], sum[3]);
        }

        static void Frees(QmRow r, QmMergeSource src)
        {
            for (int n = 1; n <= 10; n++)
            {
                string col = "CFREE" + n.ToString(CultureInfo.InvariantCulture);
                r.Put(col, CoRows.Col(src.Peek, col));
            }
        }

        static void Pieces(QmRow r, decimal num, decimal reg, decimal con, decimal dis)
        {
            r.Put("FNUM", num);
            r.Put("FREGNUM", reg);
            r.Put("FCONNUM", con);
            r.Put("FDISNUM", dis);
        }

        public static void Fill(QmChkJob job, object dom)
        {
            for (int i = 0; i < job.Merge.Sources.Count; i++)
            {
                using (QmRow r = QmRow.Add(dom))
                {
                    Row(r, job, job.Merge.Sources[i]);
                }
            }
        }

        static void Row(QmRow r, QmChkJob job, QmMergeSource src)
        {
            r.Put("CVOUCHTYPE", job.Spec.VouchType);
            r.Put("INSPECTID", src.InspectId.ToString(CultureInfo.InvariantCulture));
            r.Put("CINSPECTCODE", CoRows.Col(src.Head, "CINSPECTCODE"));
            r.Put("INSPECTAUTOID", src.Ask.SourceLineId.ToString(CultureInfo.InvariantCulture));
            r.Put("SOURCEID", CoRows.Col(src.Head, "CSOURCEID"));
            r.Put("SOURCECODE", CoRows.Col(src.Head, "CSOURCECODE"));
            r.Put("SOURCEAUTOID", CoRows.Col(src.Line, "SOURCEAUTOID"));
            string type = CoRows.Col(src.Head, "CCHECKTYPECODE");
            r.Put("CCHECKTYPECODE", type.Length > 0 ? type : job.Spec.CheckType);
            r.Put("CCHECKTYPENAME", job.TypeName);
            r.Put("CINSPECTDEPCODE", CoRows.Col(src.Head, "CINSPECTDEPCODE"));
            r.Put("CINSPECTPERSON", CoRows.Col(src.Head, "CMAKER"));
            r.Put("DINSPECTDATE", CoRows.Col(src.Head, "DDATE"));
            r.Put("CINSPECTTIME", CoRows.Col(src.Head, "CTIME"));
            r.Put("CPOCODE", CoRows.Col(src.Line, "CPOCODE"));
            r.Put("IPROORDERAUTOID", CoRows.Col(src.Line, "IPROORDERAUTOID"));
            Frees(r, src);
            for (int i = 0; i < ZeroFlags.Length; i++)
            {
                r.Put(ZeroFlags[i], "0");
            }
            Qty(r, src);
            r.Raw("editprop", QmDom.Added);
        }

        // 数量；报检单行有辅单位时带件数（QmMergeSrc.PiecesOf 算好的）。
        static void Qty(QmRow r, QmMergeSource src)
        {
            QmLineAsk a = src.Ask;
            r.Put("FQUANTITY", a.Qty);
            r.Put("FREGQUANTITY", a.Reg);
            r.Put("FCONQUANTIY", a.Con);
            r.Put("FDISQUANTITY", a.Dis);
            if (src.Num <= 0m)
            {
                return;
            }
            r.Put("CUNITID", CoRows.Col(src.Line, "CUNITID"));
            r.Put("FCHANGRATE", QmSql.Dec(CoRows.Col(src.Line, "Rate")));
            Pieces(r, src.Num, src.RegNum, src.ConNum, src.DisNum);
        }

        // 预演 detail 和回读核对用：合并明细 DOM 的行。
        public static List<Dictionary<string, object>> Rows(object dom)
        {
            return U8Co.Rows.FromDom(dom, QmMergeReq.Max);
        }
    }
}
