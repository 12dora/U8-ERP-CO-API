using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 表头、表体属性按测试账套上实测跑通的那一组写（材料出库 11、产成品入库 10），只多写调用方给的备注、自定义项、批次、货位。
    internal static partial class MfgGen
    {
        static void StampHead(MfgJob job, object dom)
        {
            object row = DomRows.AddRow(dom);
            try
            {
                List<string> schema = DomRows.Schema(dom);
                string maker = job.Ctx.Session == null || job.Ctx.Session.OperatorName == null
                    ? "" : job.Ctx.Session.OperatorName.Trim();
                Put(dom, row, schema, "editprop", "A");
                Put(dom, row, schema, "id", "");
                Put(dom, row, schema, "ccode", "");
                Put(dom, row, schema, "cwhcode", MfgReq.Text(job.Head, "cwhcode"));
                Put(dom, row, schema, "ddate", job.Date);
                Put(dom, row, schema, "crdcode", job.RdCode);
                Copy(dom, row, schema, "cdepcode", job.DeptCode);
                Copy(dom, row, schema, "cpersoncode", MfgReq.Text(job.Head, "cpersoncode"));
                if (!job.Merged)
                {
                    Put(dom, row, schema, "cmpocode", CoRows.Col(job.Src, "MoCode"));
                }
                Put(dom, row, schema, "iverifystate", "0");
                Put(dom, row, schema, "iswfcontrolled", "0");
                Put(dom, row, schema, "cmaker", maker);
                Copy(dom, row, schema, "cmemo", MfgReq.Text(job.Head, "cmemo"));
                for (int n = 1; n <= 16; n++)
                {
                    string key = "cdefine" + n.ToString(CultureInfo.InvariantCulture);
                    Copy(dom, row, schema, key, MfgReq.Text(job.Head, key));
                }
                if (job.Kind.StType == "11")
                {
                    OutHead(job, dom, row, schema);
                }
                else
                {
                    InHead(job, dom, row, schema);
                }
            }
            finally
            {
                ComUtil.ReleaseOne(row);
            }
        }

        static void OutHead(MfgJob job, object dom, object row, List<string> schema)
        {
            Put(dom, row, schema, "cvouchtype", "11");
            Put(dom, row, schema, "brdflag", "0");
            Put(dom, row, schema, "cbustype", "领料");
            Put(dom, row, schema, "csource", "生产订单");
            Put(dom, row, schema, "cpspcode", CoRows.Col(job.Src, "ProdCode"));
            Put(dom, row, schema, "imquantity", CoRows.Col(job.Src, "MoQty"));
            Put(dom, row, schema, "iproorderid", CoRows.Col(job.Src, "MoId"));
            Put(dom, row, schema, "vt_id", "65");
        }

        // csource 用来源类型的中文名：产品检验单 / 产品不良品处理单，与 U8 自己生成的单据一致。
        static void InHead(MfgJob job, object dom, object row, List<string> schema)
        {
            Put(dom, row, schema, "cvouchtype", "10");
            Put(dom, row, schema, "brdflag", "1");
            Put(dom, row, schema, "cbustype", "成品入库");
            Put(dom, row, schema, "csource", job.Source.Title);
            Put(dom, row, schema, "vt_id", "63");
        }

        static void FillBody(MfgJob job, object dom)
        {
            List<string> schema = DomRows.Schema(dom);
            for (int i = 0; i < job.Lines.Count; i++)
            {
                object row = DomRows.AddRow(dom);
                try
                {
                    MfgLine line = job.Lines[i];
                    Put(dom, row, schema, "editprop", "A");
                    Put(dom, row, schema, "autoid", "");
                    Put(dom, row, schema, "iquantity", line.Qty.ToString("0.######", CultureInfo.InvariantCulture));
                    Put(dom, row, schema, "cmocode", CoRows.Col(MoOf(job, line), "MoCode"));
                    Put(dom, row, schema, "imoseq", CoRows.Col(MoOf(job, line), "MoSeq"));
                    Put(dom, row, schema, "bcosting", "1");
                    Put(dom, row, schema, "irowno", (i + 1).ToString(CultureInfo.InvariantCulture));
                    Copy(dom, row, schema, "cbatch", line.Batch);
                    Copy(dom, row, schema, "cposition", line.Pos);
                    Copy(dom, row, schema, "cbmemo", line.Memo);
                    if (job.Kind.StType == "11")
                    {
                        OutLine(job, line, dom, row, schema);
                    }
                    else if (job.Source.Name == "qm_product_reject")
                    {
                        RejLine(job, line, dom, row, schema);
                    }
                    else if (job.Source.Name == "production_order")
                    {
                        MoLine(job, line, dom, row, schema);
                    }
                    else
                    {
                        InLine(job, line, dom, row, schema);
                    }
                    ApplyQty(job.Ctx.Conn, dom, row, schema);
                }
                finally
                {
                    ComUtil.ReleaseOne(row);
                }
            }
        }

        // impoids / ipesodid 都是子件 AllocateId；imoseq / ipesoseq 是订单行 SortSeq（U8 客户端生成的单据都是如此）。
        static void OutLine(MfgJob job, MfgLine line, object dom, object row, List<string> schema)
        {
            string alloc = line.Id.ToString(CultureInfo.InvariantCulture);
            string moCode = CoRows.Col(job.Src, "MoCode");
            Put(dom, row, schema, "cinvcode", CoRows.Col(line.Src, "InvCode"));
            Put(dom, row, schema, "inquantity", CoRows.Col(line.Src, "Qty"));
            Put(dom, row, schema, "impoids", alloc);
            Put(dom, row, schema, "iopseq", CoRows.Col(line.Src, "OpSeq"));
            Put(dom, row, schema, "invcode", CoRows.Col(job.Src, "ProdCode"));
            Put(dom, row, schema, "ipesodid", alloc);
            Put(dom, row, schema, "ipesotype", "7");
            Put(dom, row, schema, "cpesocode", moCode);
            Put(dom, row, schema, "ipesoseq", CoRows.Col(job.Src, "MoSeq"));
        }

        // 产成品行：impoids 是订单行 MoDId，icheckidbaks 是检验单 ID；辅计量由 ApplyQty 按存货补。
        // 合并检验：inquantity 是该来源的合格数，impoids 是该来源的订单行，imergecheckautoid 是来源 AUTOID；非合并写 -1。
        static void InLine(MfgJob job, MfgLine line, object dom, object row, List<string> schema)
        {
            Put(dom, row, schema, "cinvcode", CoRows.Col(line.Src, "CINVCODE"));
            Put(dom, row, schema, "inquantity", CoRows.Col(line.Src, "RegQty"));
            Put(dom, row, schema, "impoids", CoRows.Col(MoOf(job, line), "MoDId"));
            Put(dom, row, schema, "ccheckcode", CoRows.Col(line.Src, "CCHECKCODE"));
            Put(dom, row, schema, "icheckidbaks", job.SourceId.ToString(CultureInfo.InvariantCulture));
            Copy(dom, row, schema, "ccheckpersoncode", CoRows.Col(line.Src, "CCHECKPERSONCODE"));
            Copy(dom, row, schema, "dcheckdate", CoRows.Col(line.Src, "CheckDate"));
            Put(dom, row, schema, "imergecheckautoid", job.Merged ? line.Id.ToString(CultureInfo.InvariantCulture) : "-1");
        }

        // 表体行的生产订单行：合并检验取该行来源自己的订单行，其余取表头来源。
        static Dictionary<string, object> MoOf(MfgJob job, MfgLine line)
        {
            return job.Merged ? line.Src : job.Src;
        }

        static void ApplyQty(object conn, object dom, object row, List<string> schema)
        {
            StockUnits.UnitJob job = new StockUnits.UnitJob();
            job.QtyName = "iQuantity";
            job.NumName = "iNum";
            job.Force = true;
            job.Schema = schema;
            StockUnits.ApplyDom(conn, dom, row, job);
        }

        static void Copy(object dom, object row, List<string> schema, string name, string value)
        {
            if (value == null || value.Length == 0)
            {
                return;
            }
            Put(dom, row, schema, name, value);
        }

        static void Put(object dom, object row, List<string> schema, string name, string value)
        {
            StockDom.SetCell(dom, row, name, value ?? "", schema);
        }
    }
}
