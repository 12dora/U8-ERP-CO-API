using System;
using System.Collections.Generic;

namespace U8Co
{
    // GetVouchData 的模板只有 schema：表头、表体的 z:row 都自己挂（AutoAddBodyRecord 会报「未设置对象变量」）。
    // 属性集合按测试账套上的实测结果写（应收/应付单表体每行要带币种和汇率；外币单据的行币种按科目定，见 ArapLineFx）。
    internal static class ArapDom
    {
        const string True = "True";
        const string False = "False";

        public static void FillHead(object dom, ArapSpec spec, ArapInput input, string operatorName)
        {
            List<string> schema = DomRows.Schema(dom);
            object row = DomRows.AddRow(dom);
            try
            {
                Put(dom, row, schema, "cVouchType", spec.VouchType);
                Put(dom, row, schema, "cFlag", spec.Flag);
                Put(dom, row, schema, "dVouchDate", input.Date);
                Put(dom, row, schema, "cexch_name", input.Currency);
                Put(dom, row, schema, "iExchRate", ArapReq.Plain(input.Rate));
                Put(dom, row, schema, "iAmount", ArapReq.Money(input.Sum));
                Put(dom, row, schema, "iAmount_f", ArapReq.Money(input.SumF));
                Put(dom, row, schema, "iRAmount", ArapReq.Money(input.Sum));
                Put(dom, row, schema, "iRAmount_f", ArapReq.Money(input.SumF));
                Put(dom, row, schema, "cOperator", operatorName ?? "");
                if (!spec.Close)
                {
                    // 应收单借方（bd_c=True），应付单贷方；表体取反。
                    Put(dom, row, schema, "bd_c", spec.Flag == "AR" ? True : False);
                    HeadTax(dom, row, schema, input);
                }
                Generic(dom, row, schema, input.Head, "head");
            }
            finally
            {
                ComUtil.ReleaseOne(row);
            }
        }

        // 各行税率相同就写到表头，否则 0（与 U8 客户端录入的单据一致）。模板没有该列就不写。
        // 只有表头的期初单据（OpeningsArap）没有行，写 0（与 U8 期初单据一致）。
        internal static void HeadTax(object dom, object row, List<string> schema, ArapInput input)
        {
            if (Find(schema, "iTaxRate") == null)
            {
                return;
            }
            if (input.Lines.Count == 0)
            {
                Put(dom, row, schema, "iTaxRate", "0");
                return;
            }
            decimal rate = input.Lines[0].TaxRate;
            for (int i = 1; i < input.Lines.Count; i++)
            {
                if (input.Lines[i].TaxRate != rate)
                {
                    rate = 0m;
                    break;
                }
            }
            Put(dom, row, schema, "iTaxRate", ArapReq.Plain(rate));
        }

        public static void FillBody(object dom, ArapSpec spec, ArapInput input)
        {
            List<string> schema = DomRows.Schema(dom);
            for (int i = 0; i < input.Lines.Count; i++)
            {
                object row = DomRows.AddRow(dom);
                try
                {
                    FillLine(dom, row, schema, spec, input, input.Lines[i]);
                }
                finally
                {
                    ComUtil.ReleaseOne(row);
                }
            }
        }

        // 新增和修改里新增的行（ArapEdit）共用。
        internal static void FillLine(object dom, object row, List<string> schema, ArapSpec spec, ArapInput input, ArapLine line)
        {
            if (spec.Close)
            {
                CloseLine(dom, row, schema, input, line);
            }
            else
            {
                VouchLine(dom, row, schema, spec, input, line);
            }
            Generic(dom, row, schema, line.Fields, "lines");
        }

        // 收付款单表体：iType 0 应收付款 / 1 预收付款（bPrePay）。往来单位取表头，部门、业务员缺省取表头。
        static void CloseLine(object dom, object row, List<string> schema, ArapInput input, ArapLine line)
        {
            Put(dom, row, schema, "iType", line.Type == 1 ? "1" : "0");
            if (line.Type == 1)
            {
                Put(dom, row, schema, "bPrePay", True);
            }
            Put(dom, row, schema, "cCusVen", HeadText(input, "cdwcode"));
            Fallback(dom, row, schema, line, "cDepCode", HeadText(input, "cdeptcode"));
            Fallback(dom, row, schema, line, "cPersonCode", HeadText(input, "cperson"));
            PutAmounts(dom, row, schema, true, line);
        }

        // 应收/应付单表体：金额含税；不含税 = round(金额 / (1 + 税率/100), 2)，税额 = 金额 − 不含税（原币、本币各算一次）。
        static void VouchLine(object dom, object row, List<string> schema, ArapSpec spec, ArapInput input, ArapLine line)
        {
            Put(dom, row, schema, "cDwCode", HeadText(input, "cdwcode"));
            Put(dom, row, schema, "bd_c", spec.Flag == "AR" ? False : True);
            // 外币单据里非外币科目的行记本位币、汇率 1（ArapLineFx）。
            bool own = line.Currency != null;
            Put(dom, row, schema, "cexch_name", own ? line.Currency : input.Currency);
            Put(dom, row, schema, "iExchRate", ArapReq.Plain(own ? line.Rate : input.Rate));
            Fallback(dom, row, schema, line, "cDeptCode", HeadText(input, "cdeptcode"));
            Fallback(dom, row, schema, line, "cPerson", HeadText(input, "cperson"));
            Fallback(dom, row, schema, line, "cDigest", HeadText(input, "cdigest"));
            PutAmounts(dom, row, schema, false, line);
        }

        // 金额与未核销金额（= 金额）；应收/应付单再按税率拆不含税与税额。修改时（ArapEdit）也走这里。
        internal static void PutAmounts(object dom, object row, List<string> schema, bool close, ArapLine line)
        {
            if (close)
            {
                Put(dom, row, schema, "iAmt", ArapReq.Money(line.Amt));
                Put(dom, row, schema, "iAmt_f", ArapReq.Money(line.AmtF));
                Put(dom, row, schema, "iRAmt", ArapReq.Money(line.Amt));
                Put(dom, row, schema, "iRAmt_f", ArapReq.Money(line.AmtF));
                return;
            }
            Put(dom, row, schema, "iAmount", ArapReq.Money(line.Amt));
            Put(dom, row, schema, "iAmount_f", ArapReq.Money(line.AmtF));
            Put(dom, row, schema, "iRAmount", ArapReq.Money(line.Amt));
            Put(dom, row, schema, "iRAmount_f", ArapReq.Money(line.AmtF));
            decimal net = NoTax(line.Amt, line.TaxRate);
            decimal netF = NoTax(line.AmtF, line.TaxRate);
            Put(dom, row, schema, "iTaxRate", ArapReq.Plain(line.TaxRate));
            Put(dom, row, schema, "iNoTaxAmount", ArapReq.Money(net));
            Put(dom, row, schema, "iNoTaxAmount_f", ArapReq.Money(netF));
            Put(dom, row, schema, "iNatTax", ArapReq.Money(line.Amt - net));
            Put(dom, row, schema, "iTax", ArapReq.Money(line.AmtF - netF));
        }

        static decimal NoTax(decimal amount, decimal rate)
        {
            if (rate == 0m)
            {
                return amount;
            }
            return decimal.Round(amount / (1m + rate / 100m), 2, MidpointRounding.AwayFromZero);
        }

        // 行上没给才用表头的值；表头也没有就不写。
        static void Fallback(object dom, object row, List<string> schema, ArapLine line, string name, string value)
        {
            if (value.Length == 0 || line.Fields.ContainsKey(name.ToLowerInvariant()))
            {
                return;
            }
            Put(dom, row, schema, name, value);
        }

        // 调用方字段：白名单已过，这里再要求在该 DOM 的 schema 里。
        internal static void Generic(object dom, object row, List<string> schema, Dictionary<string, string> fields)
        {
            Generic(dom, row, schema, fields, null);
        }

        // at：field 前缀（head / lines），不知道时只给字段名。
        internal static void Generic(object dom, object row, List<string> schema, Dictionary<string, string> fields, string at)
        {
            foreach (KeyValuePair<string, string> pair in fields)
            {
                string actual = Find(schema, pair.Key);
                if (actual == null)
                {
                    throw BridgeException.BadField(FieldPath.Join(at, pair.Key), "未知字段 " + pair.Key);
                }
                DomRows.Set(dom, row, actual, pair.Value, schema);
            }
        }

        static void Put(object dom, object row, List<string> schema, string name, string value)
        {
            DomRows.Set(dom, row, name, value, schema);
        }

        internal static string Find(List<string> schema, string name)
        {
            for (int i = 0; i < schema.Count; i++)
            {
                if (string.Equals(schema[i], name, StringComparison.OrdinalIgnoreCase))
                {
                    return schema[i];
                }
            }
            return null;
        }

        static string HeadText(ArapInput input, string key)
        {
            string value;
            return input.Head.TryGetValue(key, out value) ? value : "";
        }
    }
}
