using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 生产订单参照销售订单生成：U8 列表（GetMOGenBaseData）之外要先查的闸门、调用前的标记和调用后的回读。
    // 闸门条件照 U8 自己的列表：V_SA_GenOrdData_ForMO（已审核或变更已审核、行未关闭、存货 bATOModel=1）和
    // Usp_MO_GetMOGenBaseData（存货 bProductBill=1「允许生产订单」、类别业务流程 bOrderFlag、预发货日 / 预完工日在范围内）。
    // 这里只为了给出确切原因；行是否可生单、可生单数量以 U8 列表为准（MoGenArgs.Attach）。
    // 表名、列名都是固定的 U8 表；调用方的值只进 ADO 参数。
    internal static class MoGenSql
    {
        const string HeadSql = "select m.cSOCode, isnull(m.cVerifier,'') as verifier, isnull(m.cChanger,'') as changer,"
            + " isnull(m.cChangeVerifier,'') as changeverifier from SO_SOMain m where m.ID=?";
        const string LineSql = "select convert(varchar(20), d.ID) as soid, d.cInvCode, isnull(d.cSCloser,'') as closer,"
            + " convert(varchar(10), isnull(i.bATOModel,0)) as ato, convert(varchar(10), isnull(i.bProductBill,0)) as bill,"
            + " convert(varchar(40), isnull(d.iMoQuantity,0)) as moqty,"
            + " case when d.dPreDate is null then '0' else '1' end as senddate,"
            + " case when d.dPreMoDate is null then '0' else '1' end as duedate"
            + " from SO_SODetails d left join Inventory i on i.cInvCode=d.cInvCode where d.iSOsID=?";
        const string MoTypeSql = "select convert(varchar(20), MoTypeId) as id from mom_motype where MotypeCode=?";
        // Usp_MO_GetMOGenBaseData 第 140 行：类别的生产业务流程（v_mom_docparameter，标准 moclass=1、bMMOpeningProcess=1）
        // 不允许来源为销售订单（bOrderFlag=0；为空时 U8 照样列出）时，该类别下销售订单行不进列表。
        const string FlowSql = "select top 1 isnull(p.BusFlowDesc,'') as flow from v_mom_docparameter p"
            + " where p.moclass=1 and p.motypeid=? and p.bMMOpeningProcess=1 and p.bOrderFlag=0";
        const string CodeSql = "select count(*) as n from mom_order where MoCode=?";
        // MO05001 改生单数量时：数量 > 可生单数量且不允许超计划下达才拒绝（U8.MOM.1013142）；列为空时 U8 按允许。
        const string OverSql = "select top 1 convert(varchar(10), isnull(RlsOverPlanFlag,1)) as f from mom_parameter";
        // MO21 单据编号设置：bAllowHandWork=1 完全手工编号（U8 GenType ByHand），否则 bRepeatReDo=1 手工改动、重号时自动重取
        // （HandAuto），两者都为 0 是完全自动（ByAutomation，MO05001 不让输入单号、保存时送空串）。
        const string NumberSql = "select convert(varchar(5), isnull(bAllowHandWork,0)) as hand,"
            + " convert(varchar(5), isnull(bRepeatReDo,0)) as redo from VoucherNumber where CardNumber='MO21'";
        // 调用异常、回读不到本次的行时：单号已被别的订单（不含本次任一来源行）占用，说明 U8 在首行取号时就因重号拒绝了。
        const string TakenSql = "select count(*) as n from mom_order o where o.MoCode=? and not exists"
            + " (select 1 from mom_orderdetail d where d.MoId=o.MoId and d.OrderType=1 and d.OrderDId in (";
        const string DeptSql = "select convert(varchar(10), isnull(bDepEnd,0)) as leaf from Department where cDepCode=?";
        const string DigitsSql = "select cName, cValue from AccInformation where cSysID='AA'"
            + " and cName in ('iStrsQuanDecDgt','iNumDecDgt')";
        const string MarkSql = "select convert(varchar(20), isnull((select max(MoId) from mom_order),0)) as mo,"
            + " convert(varchar(20), isnull((select max(MoDId) from mom_orderdetail),0)) as mod,"
            + " convert(varchar(23), getdate(), 121) as t";
        // 回读：调用前最大 MoDId 之后、来源是请求里这些销售订单行的 ATO 订单行（OrdFlag=1，OrderType=1 销售订单），
        // 带销售订单行现在的 iMoQuantity 和每行子件数；制单人 / 时间或 U8 报告的 MoDId 条件由 Created 接上。
        const string CreatedSql = "select convert(varchar(20), d.MoId) as MoId, o.MoCode, convert(varchar(20), d.MoDId) as MoDId,"
            + " convert(varchar(10), d.SortSeq) as SortSeq, d.InvCode, convert(varchar(40), d.Qty) as Qty,"
            + " convert(varchar(10), d.Status) as Status, d.MDeptCode, convert(varchar(20), d.MoTypeId) as MoTypeId,"
            + " convert(varchar(20), d.OrderDId) as OrderDId, convert(varchar(20), isnull(d.CustBomId,0)) as CustBomId,"
            + " convert(varchar(10), (select count(*) from mom_moallocate a where a.MoDId=d.MoDId)) as allocates,"
            + " convert(varchar(40), isnull(s.iMoQuantity,0)) as SoMoQty"
            + " from mom_orderdetail d join mom_order o on o.MoId=d.MoId"
            + " left join SO_SODetails s on s.iSOsID=d.OrderDId"
            + " where d.MoDId > ? and d.OrderType=1 and isnull(d.OrdFlag,0)=1 and d.OrderDId in (";
        const int DigitsMax = 6;

        public static void Validate(object conn, MoGenAsk ask)
        {
            CheckHead(conn, ask);
            CheckNumbering(conn, ask);
            if (ask.Code.Length > 0 && Count(conn, CodeSql, ask.Code) > 0)
            {
                throw new BridgeException(409, "state_mismatch", "生产订单号已存在：" + ask.Code);
            }
            Digits(conn, ask);
            string over = Rows.Scalar(conn, OverSql, new object[0]);
            ask.OverPlan = over == null || over.Trim() != "0";
            CheckMoType(conn, ask);
            for (int i = 0; i < ask.Lines.Count; i++)
            {
                MoGenLine line = ask.Lines[i];
                CheckLine(conn, ask, line);
                if (decimal.Round(line.Qty, ask.QtyDigits) != line.Qty)
                {
                    throw Bad(line, "quantity 最多 " + ask.QtyDigits.ToString(CultureInfo.InvariantCulture)
                        + " 位小数（U8 存货数量小数位）");
                }
                if (line.Dept.Length > 0)
                {
                    CheckDept(conn, line, line.Dept);
                }
            }
        }

        // 销售订单号、类别编码会被 U8 拼进 Usp_MO_GetMOGenBaseData 的动态 SQL，含单引号时 U8 自己也查不出来。
        static void CheckHead(object conn, MoGenAsk ask)
        {
            Dictionary<string, object> head = Rows.One(conn, HeadSql, new object[] { ask.SoId });
            if (head == null)
            {
                throw new BridgeException(404, "not_found", "销售订单不存在");
            }
            ask.SoCode = CoRows.Col(head, "cSOCode").Trim();
            string changer = CoRows.Col(head, "changer").Trim();
            bool audited = changer.Length == 0
                ? CoRows.Col(head, "verifier").Trim().Length > 0
                : CoRows.Col(head, "changeverifier").Trim().Length > 0;
            if (!audited)
            {
                throw new BridgeException(409, "state_mismatch", changer.Length == 0 ? "销售订单未审核" : "销售订单变更未审核");
            }
            if (ask.SoCode.Length == 0 || ask.SoCode.IndexOf('\'') >= 0)
            {
                throw new BridgeException(409, "state_mismatch", "销售订单号含单引号，U8 生产订单生成不支持");
            }
        }

        // 同 MO05001：完全自动编号不收单号，完全手工编号必须给单号；手工改动（重号自动重取）两者都可以。没有设置行时不拦。
        static void CheckNumbering(object conn, MoGenAsk ask)
        {
            Dictionary<string, object> rule = Rows.One(conn, NumberSql, new object[0]);
            if (rule == null)
            {
                return;
            }
            bool hand = CoRows.Col(rule, "hand").Trim() == "1";
            bool auto = !hand && CoRows.Col(rule, "redo").Trim() != "1";
            if (auto && ask.Code.Length > 0)
            {
                throw BridgeException.BadField("head.mo_code", "该账套生产订单号为自动编号，不能指定 mo_code");
            }
            if (hand && ask.Code.Length == 0)
            {
                throw BridgeException.BadField("head.mo_code", "该账套生产订单号为手工编号，必须指定 mo_code");
            }
        }

        static void CheckMoType(object conn, MoGenAsk ask)
        {
            Dictionary<string, object> row = ask.MoType.IndexOf('\'') >= 0 ? null
                : Rows.One(conn, MoTypeSql, new object[] { ask.MoType });
            if (row == null)
            {
                throw BridgeException.BadField("head.mo_type", "生产订单类别不存在：" + ask.MoType);
            }
            ask.MoTypeId = CoRows.AsId(CoRows.Col(row, "id"));
            Dictionary<string, object> flow = Rows.One(conn, FlowSql, new object[] { ask.MoTypeId });
            if (flow != null)
            {
                string name = CoRows.Col(flow, "flow").Trim();
                throw new BridgeException(409, "state_mismatch", "生产订单类别 " + ask.MoType + " 的生产业务流程"
                    + (name.Length > 0 ? "「" + name + "」" : "") + "不允许来源为销售订单");
            }
        }

        static void CheckLine(object conn, MoGenAsk ask, MoGenLine line)
        {
            Dictionary<string, object> row = Rows.One(conn, LineSql, new object[] { line.SoLineId });
            if (row == null || CoRows.AsId(CoRows.Col(row, "soid")) != ask.SoId)
            {
                throw Bad(line, "明细行不存在");
            }
            line.InvCode = CoRows.Col(row, "cInvCode").Trim();
            line.MoQtyBefore = MoCreateSql.Dec(CoRows.Col(row, "moqty"));
            if (CoRows.Col(row, "closer").Trim().Length > 0)
            {
                throw Mismatch(line, "销售订单行已关闭");
            }
            if (CoRows.Col(row, "ato") != "1")
            {
                throw Mismatch(line, "存货不是 ATO 模型，请用 vouchers/create 新增生产订单");
            }
            if (CoRows.Col(row, "bill") != "1")
            {
                throw Mismatch(line, "存货档案未勾选「允许生产订单」，不能生成生产订单");
            }
            if (CoRows.Col(row, "senddate") != "1")
            {
                throw Mismatch(line, "销售订单行没有预发货日期，U8 不列入生产订单生成");
            }
            if (CoRows.Col(row, "duedate") != "1")
            {
                throw Mismatch(line, "销售订单行没有预完工日期，U8 不列入生产订单生成");
            }
        }

        // 生产部门：存在、末级（U8 界面的部门参照同样只给末级）。
        internal static void CheckDept(object conn, MoGenLine line, string dept)
        {
            Dictionary<string, object> row = Rows.One(conn, DeptSql, new object[] { dept });
            if (row == null)
            {
                throw Bad(line, "部门不存在：" + dept);
            }
            if (CoRows.Col(row, "leaf") != "1")
            {
                throw Bad(line, "部门 " + dept + " 不是末级部门");
            }
        }

        // 存货数量小数位（iStrsQuanDecDgt）限制请求数量；件数小数位（iNumDecDgt）用于按换算率重算件数。读不到按 6。
        static void Digits(object conn, MoGenAsk ask)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, DigitsSql, new object[0], 10);
            for (int i = 0; i < rows.Count; i++)
            {
                int n;
                string text = CoRows.Col(rows[i], "cValue").Trim();
                if (!int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out n) || n < 0 || n > DigitsMax)
                {
                    continue;
                }
                if (CoRows.Col(rows[i], "cName") == "iStrsQuanDecDgt")
                {
                    ask.QtyDigits = n;
                }
                else
                {
                    ask.AuxDigits = n;
                }
            }
        }

        public static void Mark(object conn, MoGenAsk ask)
        {
            Dictionary<string, object> row = Rows.One(conn, MarkSql, new object[0]);
            ask.MaxMoId = row == null ? 0 : CoRows.AsId(CoRows.Col(row, "mo"));
            ask.MaxMoDId = row == null ? 0 : CoRows.AsId(CoRows.Col(row, "mod"));
            ask.Since = row == null ? "" : CoRows.Col(row, "t");
            if (ask.Since.Length == 0)
            {
                throw new BridgeException(500, "internal", "读不到数据库时间");
            }
        }

        // 本操作员在调用前数据库时间之后建的行，或者 U8 结果里报告的 MoDId（不依赖 CreateUser 与操作员编码一致）。
        public static List<Dictionary<string, object>> Created(object conn, MoGenAsk ask, string user, List<int> reported)
        {
            StringBuilder sql = new StringBuilder(CreatedSql);
            List<object> args = new List<object>();
            args.Add(ask.MaxMoDId);
            SoLines(sql, args, ask);
            sql.Append(") and ((o.CreateUser=? and o.CreateTime >= convert(datetime, ?, 121))");
            args.Add(user);
            args.Add(ask.Since);
            for (int i = 0; i < reported.Count; i++)
            {
                sql.Append(i == 0 ? " or d.MoDId in (?" : ",?");
                args.Add(reported[i]);
            }
            sql.Append(reported.Count > 0 ? "))" : ")");
            sql.Append(" order by d.MoId, d.SortSeq, d.MoDId");
            return Rows.Query(conn, sql.ToString(), args.ToArray(), MoGenReq.LinesMax * 2 + 1);
        }

        public static bool CodeTaken(object conn, MoGenAsk ask)
        {
            if (ask.Code.Length == 0)
            {
                return false;
            }
            StringBuilder sql = new StringBuilder(TakenSql);
            List<object> args = new List<object>();
            args.Add(ask.Code);
            SoLines(sql, args, ask);
            sql.Append("))");
            string n = Rows.Scalar(conn, sql.ToString(), args.ToArray());
            return n != null && n.Trim() != "0";
        }

        // IN 列表只放请求行数个 ? 参数（来源行 iSOsID 都是解析过的整数）。
        static void SoLines(StringBuilder sql, List<object> args, MoGenAsk ask)
        {
            for (int i = 0; i < ask.Lines.Count; i++)
            {
                sql.Append(i == 0 ? "?" : ",?");
                args.Add(ask.Lines[i].SoLineId);
            }
        }

        static int Count(object conn, string sql, string value)
        {
            string text = Rows.Scalar(conn, sql, new object[] { value });
            int n;
            if (text != null && int.TryParse(text.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out n))
            {
                return n;
            }
            return 0;
        }

        internal static BridgeException Bad(MoGenLine line, string text)
        {
            return new BridgeException(400, "bad_request", "第 " + line.SeqText + " 行：" + text);
        }

        internal static BridgeException Mismatch(MoGenLine line, string text)
        {
            return new BridgeException(409, "state_mismatch", "第 " + line.SeqText + " 行：" + text);
        }
    }
}
