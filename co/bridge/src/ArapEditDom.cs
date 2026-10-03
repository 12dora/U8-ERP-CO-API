using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 把修改计划写进 GetVouchData 载入的 DOM：表头调用方字段、合计与未核销金额、修改人；
    // 表体按行 editprop（M 修改、D 删除、A 新增，不动的行不碰）。金额与税额拆分走新增的同一套函数（ArapDom）。
    internal static class ArapEditDom
    {
        const string True = "True";
        const string False = "False";

        public static void Head(WorkContext ctx, ArapSpec spec, ArapEditPlan plan, object dom, object row)
        {
            List<string> schema = DomRows.Schema(dom);
            ArapDom.Generic(dom, row, schema, plan.HeadSet);
            ArapInput input = plan.Input;
            DomRows.Set(dom, row, "iAmount", ArapReq.Money(input.Sum), schema);
            DomRows.Set(dom, row, "iAmount_f", ArapReq.Money(input.SumF), schema);
            DomRows.Set(dom, row, "iRAmount", ArapReq.Money(input.Sum), schema);
            DomRows.Set(dom, row, "iRAmount_f", ArapReq.Money(input.SumF), schema);
            if (!spec.Close)
            {
                ArapDom.HeadTax(dom, row, schema, input);
            }
            Period(dom, row, schema, spec, plan);
            Stamp(ctx, dom, row, schema);
        }

        // 收付款单表头 iPeriod = 单据日期的月份（库里全部如此，新增时 UFAPBO 自己填）。改日期跨月时桥写新月份，
        // 未覆盖：SaveVouch(IsAdd=false) 是否会自己重算。应收/应付单（Ap_Vouch）没有期间列，不用管。
        // 模板里没有 iPeriod 时不知道 U8 怎么记期间，拒绝跨月。
        static void Period(object dom, object row, List<string> schema, ArapSpec spec, ArapEditPlan plan)
        {
            if (!spec.Close || !ArapEditBuild.CrossMonth(plan))
            {
                return;
            }
            string name = ArapDom.Find(schema, "iPeriod");
            if (name == null)
            {
                throw new BridgeException(400, "bad_request", "暂不支持跨月修改单据日期");
            }
            DomRows.Set(dom, row, name, ArapEditBuild.NewMonth(plan).ToString(CultureInfo.InvariantCulture), schema);
        }

        public static void Body(ArapSpec spec, ArapDoc doc, ArapEditPlan plan, object dom)
        {
            List<string> schema = DomRows.Schema(dom);
            for (int i = 0; i < plan.Rows.Count; i++)
            {
                ArapEditRow r = plan.Rows[i];
                if (r.Op == "delete")
                {
                    DomRows.Set(dom, r.Row, "editprop", "D", schema);
                }
                else if (r.Op == "update")
                {
                    Revise(dom, schema, spec, plan.Input, r);
                }
                else if (r.Op == "add")
                {
                    Add(dom, schema, spec, doc, plan.Input, r);
                }
            }
        }

        // 修改行：调用方字段；送了金额或税率（或换科目改了行币种）才重写金额（未核销 = 金额，应收/应付单重拆税额）；应收/应付单的行必须带币种和汇率。
        static void Revise(object dom, List<string> schema, ArapSpec spec, ArapInput input, ArapEditRow r)
        {
            object row = r.Row;
            ArapDom.Generic(dom, row, schema, r.Line.Fields);
            if (r.AmountGiven)
            {
                ArapDom.PutAmounts(dom, row, schema, spec.Close, r.Line);
            }
            if (spec.Close && r.TypeGiven)
            {
                DomRows.Set(dom, row, "iType", r.Line.Type == 1 ? "1" : "0", schema);
                DomRows.Set(dom, row, "bPrePay", r.Line.Type == 1 ? True : False, schema);
            }
            if (!spec.Close)
            {
                KeepCurrency(dom, row, schema, input, r.Line);
            }
            DomRows.Set(dom, row, "editprop", "M", schema);
        }

        // 外币单据的行币种、汇率已由 ArapLineFx 按科目定好（Rate > 0），照写；否则行上缺了才补表头的。
        static void KeepCurrency(object dom, object row, List<string> schema, ArapInput input, ArapLine line)
        {
            if (!string.IsNullOrEmpty(line.Currency) && line.Rate > 0m)
            {
                DomRows.Set(dom, row, "cexch_name", line.Currency, schema);
                DomRows.Set(dom, row, "iExchRate", ArapReq.Plain(line.Rate), schema);
                return;
            }
            if (DomRows.Get(row, "cexch_name").Trim().Length == 0)
            {
                DomRows.Set(dom, row, "cexch_name", input.Currency, schema);
            }
            if (DomRows.Get(row, "iExchRate").Trim().Length == 0)
            {
                DomRows.Set(dom, row, "iExchRate", ArapReq.Plain(input.Rate), schema);
            }
        }

        // 新增行按新增的行构造（ArapDom.FillLine），再挂上本单外键：收付款单 iID，应收/应付单 cLink。
        static void Add(object dom, List<string> schema, ArapSpec spec, ArapDoc doc, ArapInput input, ArapEditRow r)
        {
            object row = DomRows.AddRow(dom);
            try
            {
                if (spec.Close)
                {
                    DomRows.Set(dom, row, "iID", doc.Id.ToString(CultureInfo.InvariantCulture), schema);
                }
                else
                {
                    DomRows.Set(dom, row, "cLink", doc.Link, schema);
                }
                ArapDom.FillLine(dom, row, schema, spec, input, r.Line);
                DomRows.Set(dom, row, "editprop", "A", schema);
            }
            finally
            {
                ComUtil.ReleaseOne(row);
            }
        }

        // 实测 SaveVouch 不写修改人：模板里有这几列才写。
        static void Stamp(WorkContext ctx, object dom, object row, List<string> schema)
        {
            if (ArapDom.Find(schema, "cmodifier") != null)
            {
                DomRows.Set(dom, row, "cmodifier", User(ctx), schema);
            }
            if (ArapDom.Find(schema, "dmoddate") != null)
            {
                DomRows.Set(dom, row, "dmoddate", LoginDate(ctx), schema);
            }
            if (ArapDom.Find(schema, "dmodifysystime") != null)
            {
                string now = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture);
                DomRows.Set(dom, row, "dmodifysystime", now, schema);
            }
        }

        static string User(WorkContext ctx)
        {
            string user = ctx.Session == null ? "" : ctx.Session.OperatorName ?? "";
            if (user.Length == 0 && ctx.Item != null)
            {
                user = ctx.Item.Operator ?? "";
            }
            return user;
        }

        static string LoginDate(WorkContext ctx)
        {
            string date = ctx.Item == null || ctx.Item.Date == null ? "" : ctx.Item.Date.Trim();
            if (date.Length == 0)
            {
                return DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
            }
            return date;
        }
    }
}
