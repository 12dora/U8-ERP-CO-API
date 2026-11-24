using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 不良品处理单修改的上下文：Job 沿用生单的 QmRejJob（检验单行、计量、扩展自定义项的模板字段名），Job.Ask.Lines 是合并后的
    // 整单处理行；Touched 是要写进 DOM 的行（新增与修改，按请求次序），Deleted 是要删除的 AUTOID。
    // HeadFields 是要写进表头的 {列名, 值, 请求字段}，同时是保存后回读核对的期望值。
    internal sealed class QmRejEditJob
    {
        public QmRejJob Job;
        public QmRejEditAsk Ask;
        public int Id;
        // 合并检验的处理单所属的合并明细 AUTOID（不是合并检验时 0），回读时据此读不良品处理标记（QmRejSrc.Flag）。
        public int MergeId;
        public string Code = "";
        public string Ufts = "";
        public string Component = "";
        public readonly List<QmRejLine> Touched = new List<QmRejLine>();
        public readonly List<int> Deleted = new List<int>();
        public readonly List<string[]> HeadFields = new List<string[]>();

        public QmRejSpec Spec
        {
            get { return Job.Spec; }
        }
    }

    // 不良品处理单修改的准备，全部在 U8 调用之前做完（U8 的保存自己提交）：功能权限 write:<类型>:update、单据存在、数据权限；
    // 受审批流控制且已提交或已审核 409 workflow_enabled，已审核 409；有下游（入库、报废、到货、报检、领料申请）、
    // 行已处理（BFLAG）或已有返工数量 409；表体操作合并后：处理方式、不良原因、处理后存货、仓库按档案核对，降级类（流程 2）
    // 必须有处理后存货且处理后数量大于 0，不良原因 + 处理方式不能重复（U8 保存时同样拦），至少留一行，
    // 各行处理数量之和 = 本处理单的待处理不良品数量（表头 FSUMQUANTITY；U8 生单时写入，合并检验时是所属合并明细的不良数量，
    // U8 保存时按它核对表体）；新的处理后存货、仓库要在数据权限内；扩展自定义项 chdefineN 须是账套已有的列。
    internal static class QmRejEditLoad
    {
        const string HeadSql = "select * from QMREJECTVOUCHER where ID=? and CVOUCHTYPE=?";
        internal const string LinesSql = "select convert(varchar(20), AUTOID) as AUTOID, convert(varchar(40), isnull(FQUANTITY,0)) as FQUANTITY,"
            + " isnull(CSCRAPDISCODE,'') as CSCRAPDISCODE, isnull(CREASONCODE,'') as CREASONCODE, isnull(CDIMINVCODE,'') as CDIMINVCODE,"
            + " convert(varchar(40), isnull(FDIMQUANTITY,0)) as FDIMQUANTITY, isnull(CBWHCODE,'') as CBWHCODE,"
            + " convert(varchar(10), isnull(IDISPOSEFLOW,-1)) as IDISPOSEFLOW from QMREJECTVOUCHERS where ID=? order by AUTOID";

        public static QmRejEditJob Load(WorkContext ctx, QmRejSpec spec, QmRejEditAsk ask, int id)
        {
            object conn = ctx.Conn;
            Dictionary<string, object> doc = QmRejOps.Gate(ctx, spec, id, "update");
            RequireOpen(spec, doc);
            QmRejOps.RequireNoDownstream(conn, id, CoRows.Col(doc, "code"), "修改");
            Dictionary<string, object> head = QmSql.One(conn, HeadSql, id, spec.VouchType);
            if (head == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            QmRejEditJob e = new QmRejEditJob();
            e.Ask = ask;
            e.Id = id;
            e.Code = CoRows.Col(doc, "code");
            e.MergeId = CoRows.AsId(CoRows.Col(doc, "MergeId"));
            e.Ufts = CoRows.Col(head, "UFTS");
            e.Job = Source(ctx, spec, ask.Head, CoRows.AsId(CoRows.Col(head, "CHECKID")));
            QmRejReq.RequireExtraColumns(conn, ask.Head);
            if (ask.Ops.Count > 0)
            {
                QmRejEditLines.Merge(conn, e, QmSql.Many(conn, LinesSql, 500, id));
                QmRejSrc.RequireTotal(ask.Head.Total(), QmSql.Dec(CoRows.Col(head, "FSUMQUANTITY")), "本处理单的不良品数量");
                RequireLinePerms(ctx, spec, doc, e);
            }
            QmRejUnits.Fill(conn, e.Job);
            // 已有行、复制出的新行上已经有存货和部门：不重写。
            e.Job.Unit.Inv = "";
            e.Job.Unit.Dep = "";
            Fields(e);
            return e;
        }

        // 审批流与审核状态（同检验单修改 QmEditGate）：受控且已提交（iVerifyStateNew 不为 0）或已审核，或未受控却在途，都按审批流处理。
        static void RequireOpen(QmRejSpec spec, Dictionary<string, object> doc)
        {
            bool verified = QmDoc.Verified(doc);
            if (CoRows.Col(doc, "vs") != "0" || (CoRows.Col(doc, "wf") == "1" && verified))
            {
                throw new BridgeException(409, "workflow_enabled",
                    spec.Title + "已提交审批或已审核，请先在审批流中撤回或弃审（workflow/*）后再修改");
            }
            if (verified)
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核，不能修改");
            }
        }

        // 来源检验单（U8 参照用的视图，带待处理不良数量 Dis 与计量）。
        static QmRejJob Source(WorkContext ctx, QmRejSpec spec, QmRejAsk ask, int checkId)
        {
            ask.CheckId = checkId;
            QmRejJob job = new QmRejJob();
            job.Ctx = ctx;
            job.Spec = spec;
            job.Ask = ask;
            job.Check = checkId > 0 ? QmSql.One(ctx.Conn, QmRejSrc.CheckSql(spec), checkId, spec.CheckType) : null;
            if (job.Check == null)
            {
                throw new BridgeException(409, "state_mismatch", "找不到处理单的来源检验单，需在 U8 中核对");
            }
            return job;
        }

        // 数据权限：请求新给的处理后存货（按存货）、仓库（按仓库）也要在操作员的数据权限内；其余对象取表头（QmRejOps.Gate 已核对）。
        // Touched 与非删除的操作一一对应（QmRejEditLines.Merge 按请求次序加入），取档案核对后的编码。
        static void RequireLinePerms(WorkContext ctx, QmRejSpec spec, Dictionary<string, object> doc, QmRejEditJob e)
        {
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            int t = 0;
            for (int i = 0; i < e.Ask.Ops.Count; i++)
            {
                QmRejEditOp op = e.Ask.Ops[i];
                if (op.Op == QmRejEditOp.Delete)
                {
                    continue;
                }
                QmRejLine line = e.Touched[t++];
                AddPermRow(rows, doc, "CINVCODE", op.DimInv == null ? "" : line.DimInv);
                AddPermRow(rows, doc, "CWHCODE", op.Wh == null ? "" : line.Wh);
            }
            if (rows.Count > 0)
            {
                MoDelete.CheckRows(PermCheck.Of(ctx), PermRegistry.ForKey(spec.RuleKey("update")), rows);
            }
        }

        static void AddPermRow(List<Dictionary<string, object>> rows, Dictionary<string, object> doc, string column, string code)
        {
            if (code.Length == 0)
            {
                return;
            }
            Dictionary<string, object> row = new Dictionary<string, object>(doc, StringComparer.OrdinalIgnoreCase);
            row[column] = code;
            rows.Add(row);
        }

        // 表头：单据日期、自定义项（cdefine 写大写列名，chdefine 写模板字段名）。
        static void Fields(QmRejEditJob e)
        {
            QmRejAsk ask = e.Ask.Head;
            if (ask.Date.Length > 0)
            {
                e.HeadFields.Add(new string[] { "DDATE", ask.Date, "ddate" });
            }
            for (int n = 1; n <= 16; n++)
            {
                string key = "cdefine" + n.ToString(CultureInfo.InvariantCulture);
                string value;
                if (ask.Head.TryGetValue(key, out value))
                {
                    e.HeadFields.Add(new string[] { key.ToUpperInvariant(), value, key });
                }
            }
            for (int n = 11; n <= 16; n++)
            {
                string key = "chdefine" + n.ToString(CultureInfo.InvariantCulture);
                string value;
                string name;
                if (ask.Head.TryGetValue(key, out value))
                {
                    e.HeadFields.Add(new string[] { e.Job.DefineNames.TryGetValue(key, out name) ? name : key, value, key });
                }
            }
        }

        internal static bool IsExtra(string column)
        {
            return column.StartsWith("chdefine", StringComparison.OrdinalIgnoreCase);
        }
    }
}
