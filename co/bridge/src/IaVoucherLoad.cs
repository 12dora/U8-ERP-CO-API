using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 制单前读存货明细账（在事务里，带锁）并过闸门。只读已记账的行（IA_Subsidiary 有行即已记账）；
    // 部门、业务员、收发类别、供应商、客户在明细行上为空时取来源单据表头的（U8 制单的临时表同样从单据带，生产数据回测核对）。
    // 按单据选（ids）时，单据不合格一律 409 点名；按月份选（month）时跳过不合格的单据，原因在响应的 skipped。
    internal static class IaVoucherLoad
    {
        public const int RowMax = 5000;

        const string RowSql = "select convert(varchar(20), s.AutoID) aid, convert(varchar(20), isnull(s.ID,0)) sid, "
            + "convert(varchar(20), h.{hid}) bid, isnull(s.cVouCode,N'') code, isnull(convert(varchar(10), s.dVouDate, 23),N'') vdate, "
            + "convert(varchar(6), s.iYear) y, convert(varchar(4), s.iMonth) m, convert(varchar(4), isnull(s.bFlag,0)) bflag, "
            + "isnull(s.cBusType,N'') bt, isnull(s.cBusCode,N'') bc, isnull(s.cPZID,N'') pz, {amt} amt, {qty} qty, "
            + "case when s.{amtcol} is null then 1 else 0 end nocost, "
            + "case when isnull(s.iDebitDifCost,0)<>0 or isnull(s.iCreditDifCost,0)<>0 then 1 else 0 end dif, "
            + "isnull(s.cInvCode,N'') inv, isnull(i.cInvCCode,N'') invc, isnull(s.cWhCode,N'') wh, "
            + "isnull(isnull(s.cRdCode,h.cRdCode),N'') rd, isnull(isnull(s.cDepCode,h.cDepCode),N'') dept, "
            + "isnull(isnull(s.cPersonCode,h.cPersonCode),N'') person, isnull(isnull(s.cVenCode,h.{ven}),N'') ven, "
            + "{cus} cus, isnull(s.cItem_class,N'') itc, isnull(s.cItemCode,N'') itm, "
            + "isnull(s.cMemo,N'') memo, isnull(s.cSRcVoutype,N'') src, {en} en "
            + "from IA_Subsidiary s with (UPDLOCK, HOLDLOCK) left join Inventory i on i.cInvCode=s.cInvCode {join} "
            + "where s.cVouType=? and s.iYear=? and {filter} order by h.{hid}, s.AutoID";
        const string EnSql = "(select convert(varchar(12), count(*)) from IA_EnSubsidiary e where e.cVouType=s.cVouType "
            + "and e.iYear=s.iYear and e.InID=s.ID and e.cPZID is null)";

        public static void Load(object conn, IaJob job)
        {
            List<IaRow> rows = job.Ask.Month > 0 ? ByMonth(conn, job) : ByIds(conn, job);
            job.Bills = Group(IaVoucherPair.Pull(conn, job.Year, rows));
            if (job.Ask.Month == 0)
            {
                Order(job);
            }
            int eligible = Gate(job);
            Limit(job, eligible - IaVoucherPair.Strays(conn, job));
            MonthsOpen(conn, job);
        }

        // 逐个制单单元（成对业务的 08、09 是一个单元）过闸门：按单据选时不合格 409，按月份选时记下跳过原因
        // （单元里另一张单据的原因带上它的名字）。返回合格的单元数。
        static int Gate(IaJob job)
        {
            int eligible = 0;
            foreach (List<IaBill> unit in IaVoucherPair.Units(job.Bills))
            {
                IaBill culprit;
                string problem = UnitProblem(unit, out culprit);
                if (problem != null && job.Ask.Month == 0)
                {
                    throw Refuse("单据 " + culprit.Label() + "：" + problem);
                }
                SkipUnit(unit, culprit, problem);
                eligible += problem == null ? 1 : 0;
            }
            return eligible;
        }

        // 记下单元的跳过原因（null 清空）：出问题的单据记原因，单元里其他单据带上它的名字。
        internal static void SkipUnit(List<IaBill> unit, IaBill culprit, string problem)
        {
            foreach (IaBill bill in unit)
            {
                bill.Skip = problem == null ? "" : (bill == culprit ? problem : "单据 " + culprit.Label() + "：" + problem);
            }
        }

        static string UnitProblem(List<IaBill> unit, out IaBill culprit)
        {
            foreach (IaBill bill in unit)
            {
                culprit = bill;
                string problem = Problem(bill);
                if (problem != null)
                {
                    return problem;
                }
            }
            culprit = unit[0];
            return IaVoucherPair.Problem(unit);
        }

        static void Limit(IaJob job, int eligible)
        {
            if (eligible == 0)
            {
                throw Refuse("没有可制单的单据（已记账、未制单）" + (job.Bills.Count > 0
                    ? "；跳过 " + Int(job.Bills.Count) + " 张，如 " + job.Bills[0].Label() + "：" + job.Bills[0].Skip : ""));
            }
            if (job.Ask.All && eligible > IaVoucherReq.BillMax)
            {
                throw Refuse("本次选中 " + Int(eligible) + " 张单据，合并制单一次最多 " + Int(IaVoucherReq.BillMax) + " 张；请用 types 或 ids 分批制单");
            }
            if (!job.Ask.All && eligible > IaVoucherReq.PerBillMax)
            {
                Defer(job);
            }
        }

        // 按月份逐张制单：只做前 PerBillMax 个合格的制单单元，其余留到下次（响应的 pending）；成对单据不拆开。
        static void Defer(IaJob job)
        {
            List<IaBill> keep = new List<IaBill>();
            int taken = 0;
            foreach (List<IaBill> unit in IaVoucherPair.Units(job.Bills))
            {
                bool skip = unit[0].Skip.Length > 0;
                if (skip || taken < IaVoucherReq.PerBillMax)
                {
                    keep.AddRange(unit);
                    taken += skip ? 0 : 1;
                    continue;
                }
                job.Later.AddRange(unit);
            }
            job.Bills = keep;
        }

        static List<IaRow> ByMonth(object conn, IaJob job)
        {
            List<IaRow> rows = new List<IaRow>();
            List<string> types = job.Ask.Types;
            if (types.Count == 0)
            {
                foreach (IaType t in IaType.All)
                {
                    types.Add(t.Code);
                }
            }
            foreach (string code in types)
            {
                IaType type = Supported(code);
                rows.AddRange(Query(conn, type, "s.iMonth=? and s.cPZID is null", new object[] { job.Year, job.Ask.Month }, rows.Count));
            }
            return rows;
        }

        // 按单据：同类型的一起查；请求的每张单据都要在存货明细账里有行。
        static List<IaRow> ByIds(object conn, IaJob job)
        {
            List<IaRow> rows = new List<IaRow>();
            Dictionary<string, List<object>> byType = new Dictionary<string, List<object>>(StringComparer.Ordinal);
            List<string> order = new List<string>();
            foreach (IaBillRef r in job.Ask.Ids)
            {
                Supported(r.Type);
                List<object> ids;
                if (!byType.TryGetValue(r.Type, out ids))
                {
                    ids = new List<object>();
                    byType[r.Type] = ids;
                    order.Add(r.Type);
                }
                ids.Add(r.Id);
            }
            foreach (string code in order)
            {
                IaType type = IaType.Find(code);
                List<object> ids = byType[code];
                string filter = "h." + HeadId(type) + " in (?" + new StringBuilder().Insert(0, ",?", ids.Count - 1) + ")";
                rows.AddRange(Query(conn, type, filter, GlSql.With(new object[] { job.Year }, ids.ToArray()), rows.Count));
            }
            return rows;
        }

        static IaType Supported(string code)
        {
            IaType type = IaType.Find(code);
            if (type == null)
            {
                throw Refuse("该单据类型暂不支持制单（" + code + "）；支持 " + IaType.Codes());
            }
            return type;
        }

        internal static List<IaRow> Query(object conn, IaType type, string filter, object[] args, int before)
        {
            string sql = Sql(type).Replace("{filter}", filter);
            List<object> all = new List<object>();
            all.Add(type.Code);
            all.AddRange(args);
            List<Dictionary<string, object>> found = Rows.Query(conn, sql, all.ToArray(), RowMax + 1 - before);
            if (before + found.Count > RowMax)
            {
                throw Refuse("选中的存货明细账超过 " + Int(RowMax) + " 行；请用 types 或 ids 分批制单");
            }
            List<IaRow> rows = new List<IaRow>();
            foreach (Dictionary<string, object> raw in found)
            {
                rows.Add(Row(type, raw));
            }
            return rows;
        }

        // 表名、列名只来自 IaType 的常量。
        internal static string Sql(IaType type)
        {
            bool just = type.Table == "just";
            string join = just
                ? "left join JustInVouchs b on b.AutoID=s.JustID left join JustInVouch h on h.cJVCode=b.cJVCode and h.cVouchType=s.cVouType"
                : "left join RdRecords" + type.Table + " b on b.AutoID=s.ID" + SourceJoin(type) + " left join RdRecord" + type.Table
                    + " h on h.ID=b.ID";
            string amt = type.In ? "iAInPrice" : "iAOutPrice";
            string qty = type.In ? "iAInQuantity" : "iAOutQuantity";
            return RowSql.Replace("{hid}", HeadId(type)).Replace("{amt}", WriteoffSql.Dec("s." + amt, 2))
                .Replace("{qty}", WriteoffSql.Dec("s." + qty, 6)).Replace("{amtcol}", amt)
                .Replace("{ven}", just ? "cvencode" : "cVenCode").Replace("{cus}", Customer(type))
                .Replace("{en}", type.En ? EnSql : "'0'").Replace("{join}", join);
        }

        // 客户：明细行上为空时取来源单据表头的；销售出库单 32 同 U8 取表头开票单位，没有时发货客户。
        static string Customer(IaType type)
        {
            if (type.Code == "32")
            {
                return "isnull(isnull(h.cInvoiceCompany,h.cCusCode),isnull(s.cCusCode,N''))";
            }
            return type.Table == "just" ? "isnull(s.cCusCode,N'')" : "isnull(isnull(s.cCusCode,h.cCusCode),N'')";
        }

        // 回冲单（24 / 30）只按来源是采购入库单的行连 RdRecords01（同 U8：cSRcVoutype 为 01，或为空时按 01）；
        // 其他来源（如 VMI）的行连不上表头，在闸门里按来源拒绝。
        static string SourceJoin(IaType type)
        {
            return type.Code == "24" || type.Code == "30" ? " and isnull(s.cSRcVoutype,N'01')=N'01'" : "";
        }

        static string HeadId(IaType type)
        {
            return type.Table == "just" ? "id" : "ID";
        }

        static IaRow Row(IaType type, Dictionary<string, object> raw)
        {
            IaRow r = new IaRow();
            r.Type = type;
            r.Aid = CoRows.Col(raw, "aid");
            r.Sid = CoRows.Col(raw, "sid");
            r.BillId = CoRows.AsId(CoRows.Col(raw, "bid"));
            r.Code = CoRows.Col(raw, "code");
            r.VDate = CoRows.Col(raw, "vdate");
            r.Year = CoRows.AsId(CoRows.Col(raw, "y"));
            r.Month = CoRows.AsId(CoRows.Col(raw, "m"));
            r.BFlag = CoRows.Col(raw, "bflag");
            r.Business = CoRows.Col(raw, "bt");
            r.BusCode = CoRows.Col(raw, "bc");
            r.Pair = IaType.PairOf(type.Code, r.Business);
            r.Pz = CoRows.Col(raw, "pz");
            r.Amount = WriteoffSql.Num(CoRows.Col(raw, "amt"));
            r.Qty = WriteoffSql.Num(CoRows.Col(raw, "qty"));
            r.NoCost = CoRows.Col(raw, "nocost") == "1";
            r.Dif = CoRows.Col(raw, "dif") == "1";
            string[] keys = new string[] { "inv", "invc", "wh", "rd", "dept", "person", "ven", "cus", "itc", "itm" };
            string[] v = new string[keys.Length];
            for (int i = 0; i < keys.Length; i++)
            {
                v[i] = CoRows.Col(raw, keys[i]);
            }
            r.Inv = v[0];
            r.InvClass = v[1];
            r.Wh = v[2];
            r.Rd = v[3];
            r.Dept = v[4];
            r.Person = v[5];
            r.Vendor = v[6];
            r.Customer = v[7];
            r.ItemClass = v[8];
            r.Item = v[9];
            r.Memo = CoRows.Col(raw, "memo");
            r.Src = CoRows.Col(raw, "src");
            r.En = CoRows.AsId(CoRows.Col(raw, "en"));
            return r;
        }

        // 按（类型、表头主键）分组，保持查询顺序。明细行找不到来源单据（表头主键 0）时单独成组，在闸门里拒绝。
        static List<IaBill> Group(List<IaRow> rows)
        {
            List<IaBill> bills = new List<IaBill>();
            Dictionary<string, IaBill> seen = new Dictionary<string, IaBill>(StringComparer.Ordinal);
            foreach (IaRow row in rows)
            {
                string key = row.Type.Code + ":" + Int(row.BillId) + (row.BillId > 0 ? "" : ":" + row.Aid);
                IaBill bill;
                if (!seen.TryGetValue(key, out bill))
                {
                    bill = new IaBill();
                    bill.Type = row.Type;
                    bill.Id = row.BillId;
                    bill.Code = row.Code;
                    bill.Unit = IaVoucherPair.Key(row);
                    seen[key] = bill;
                    bills.Add(bill);
                }
                bill.Rows.Add(row);
            }
            return bills;
        }

        // 按单据选：按请求顺序排，每张都要有行；成对业务补进来的另一边紧跟在它后面。
        static void Order(IaJob job)
        {
            List<IaBill> ordered = new List<IaBill>();
            HashSet<IaBill> added = new HashSet<IaBill>();
            foreach (IaBillRef r in job.Ask.Ids)
            {
                IaBill hit = Find(job.Bills, r);
                foreach (IaBill bill in job.Bills)
                {
                    if ((bill == hit || (hit.Unit.Length > 0 && bill.Unit == hit.Unit)) && added.Add(bill))
                    {
                        ordered.Add(bill);
                    }
                }
            }
            job.Bills = ordered;
        }

        static IaBill Find(List<IaBill> bills, IaBillRef r)
        {
            foreach (IaBill bill in bills)
            {
                if (bill.Type.Code == r.Type && bill.Id == r.Id)
                {
                    return bill;
                }
            }
            throw Refuse("单据 " + r.Type + " id " + Int(r.Id) + " 在登录年度的存货明细账里没有记录（未记账或类型、id 不符）");
        }

        // 不合格的原因；合格返回 null。已制单的行先去掉（按单据选时一张单据可能部分已制单），全部已制单才拒绝。
        internal static string Problem(IaBill bill)
        {
            string done = Unvouchered(bill);
            if (done != null)
            {
                return "已制单（凭证外部业务号 " + done + "），不能重复制单";
            }
            if (!bill.Type.SourceOk(bill.Rows[0].Src))
            {
                return "回冲单的来源不是采购入库单（cSRcVoutype " + bill.Rows[0].Src + "），暂不支持";
            }
            if (bill.Id <= 0)
            {
                return "存货明细账行找不到来源单据";
            }
            foreach (IaRow row in bill.Rows)
            {
                if (row.Dif)
                {
                    return "有差异额（计划价 / 售价核算），差异制单暂不支持";
                }
                if (row.NoCost)
                {
                    return "有出库成本还没算出的记录，请先期末处理（ia/period_end）";
                }
            }
            return null;
        }

        // 去掉已制单的行；全部已制单时返回其中一个外部业务号，否则 null。
        static string Unvouchered(IaBill bill)
        {
            string first = bill.Rows[0].Pz;
            bill.Rows.RemoveAll(row => row.Pz.Length > 0);
            return bill.Rows.Count == 0 ? first : null;
        }

        // 单据所在月份存货核算未结账。
        static void MonthsOpen(object conn, IaJob job)
        {
            HashSet<int> seen = new HashSet<int>();
            foreach (IaBill bill in job.Bills)
            {
                foreach (IaRow row in bill.Skip.Length > 0 ? new List<IaRow>() : bill.Rows)
                {
                    if (!seen.Add(row.Year * 100 + row.Month))
                    {
                        continue;
                    }
                    if (Closed(conn, row.Year, row.Month))
                    {
                        throw Refuse("存货核算 " + Int(row.Year) + " 年 " + Int(row.Month) + " 月已结账，不能制单");
                    }
                }
            }
        }

        internal static bool Closed(object conn, int year, int month)
        {
            string flag = Rows.Scalar(conn, "select convert(varchar(4), isnull(bflag_IA,0)) from GL_mend where iyear=? and iperiod=?",
                new object[] { year, month });
            return flag != null && flag.Trim() != "0";
        }

        internal static string Int(int value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }

        public static BridgeException Refuse(string message)
        {
            return new BridgeException(409, "state_mismatch", message);
        }
    }
}
