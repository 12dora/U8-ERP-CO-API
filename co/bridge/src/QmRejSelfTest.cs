using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的不良品处理单部分：请求校验、数量合计、降级存货、DOM 字段映射（属性名大小写）、功能 id。不连库、不建 COM。
    internal static class QmRejSelfTest
    {
        public static void Run()
        {
            CheckSpec();
            CheckParse();
            CheckRefuse();
            CheckRules();
            CheckFields();
        }

        static void CheckSpec()
        {
            QmRejSpec a = QmRejSpec.Find("qm_incoming_reject");
            QmRejSpec p = QmRejSpec.Find("qm_product_reject");
            Expect("rej spec arr", "QM05 355 qm_incoming_check QM03 QM_QARRCHECKB",
                a.VouchType + " " + a.Vt + " " + a.CheckKind + " " + a.CheckType + " " + a.CheckView);
            Expect("rej spec pro", "QM06 356 qm_product_check QM04 QM_QPROCHECKB",
                p.VouchType + " " + p.Vt + " " + p.CheckKind + " " + p.CheckType + " " + p.CheckView);
            Expect("rej auth arr", "QM02010303 QM02010304 QM02010305 QM02010307",
                a.AddAuth + " " + a.DeleteAuth + " " + a.VerifyAuth + " " + a.UnverifyAuth);
            Expect("rej auth pro", "QM02020303 QM02020304 QM02020305 QM02020307",
                p.AddAuth + " " + p.DeleteAuth + " " + p.VerifyAuth + " " + p.UnverifyAuth);
            Expect("rej rule key", "write:qm_product_reject:unverify", p.RuleKey("unverify"));
        }

        static void CheckParse()
        {
            QmRejSpec spec = QmRejSpec.Find("qm_incoming_reject");
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["dDate"] = "2026-10-02";
            head["cDefine1"] = "x";
            QmRejAsk ask = QmRejReq.Parse(spec, 5, head, new object[] { Line(5, 1, "Sys01"), Line(5, 2.5d, "Sys03") });
            Expect("rej ask date", "2026-10-02", ask.Date);
            Expect("rej ask define", "x", ask.Head["cdefine1"]);
            Expect("rej ask lines", "2", ask.Lines.Count.ToString());
            ExpectDec("rej ask total", 3.5m, ask.Total());
            Expect("rej ask dispose", "Sys03", ask.Lines[1].Dispose);
        }

        static void CheckRefuse()
        {
            QmRejSpec spec = QmRejSpec.Find("qm_product_reject");
            Field("rej other check", "lines.0.source_line_id",
                delegate { QmRejReq.Parse(spec, 5, null, new object[] { Line(6, 1, "Sys01") }); });
            Dictionary<string, object> noReason = Line(5, 1, "Sys01");
            noReason.Remove("creasoncode");
            Field("rej no reason", "lines.0.creasoncode", delegate { QmRejReq.Parse(spec, 5, null, new object[] { noReason }); });
            Dictionary<string, object> memo = Line(5, 1, "Sys01");
            memo["cbmemo"] = "m";
            Field("rej line memo", "lines.0.cbmemo", delegate { QmRejReq.Parse(spec, 5, null, new object[] { memo }); });
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["cMemo"] = "m";
            Field("rej head memo", "head.cMemo", delegate { QmRejReq.Parse(spec, 5, head, new object[] { Line(5, 1, "Sys01") }); });
            head.Clear();
            head["ddate"] = "2026/10/02";
            Field("rej head date", "head.ddate", delegate { QmRejReq.Parse(spec, 5, head, new object[] { Line(5, 1, "Sys01") }); });
            Field("rej no lines", "lines", delegate { QmRejReq.Parse(spec, 5, null, new object[0]); });
        }

        static void CheckRules()
        {
            QmRejSrc.RequireTotal(3.5m, 3.5m);
            Field("rej total", "lines", delegate { QmRejSrc.RequireTotal(3m, 3.5m); });
            QmRejLine dim = new QmRejLine();
            dim.Flow = QmRejSrc.DimFlow;
            Field("rej dim missing", "cdiminvcode", delegate { QmRejSrc.RequireDim(dim); });
            dim.DimInv = "B01";
            QmRejSrc.RequireDim(dim);
            dim.Flow = 0;
            Field("rej dim not allowed", "cdiminvcode", delegate { QmRejSrc.RequireDim(dim); });
        }

        static void CheckFields()
        {
            QmRejSpec spec = QmRejSpec.Find("qm_product_reject");
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["cdefine3"] = "y";
            head["chDefine15"] = "z";
            head["chdefine11"] = "w";
            QmRejAsk ask = QmRejReq.Parse(spec, 5, head, new object[] { Line(5, 1, "Sys01") });
            Dictionary<string, string> names = new Dictionary<string, string>(StringComparer.Ordinal);
            names["chdefine15"] = "chdefine15";
            Dictionary<string, string> h = Map(QmRejDom.HeadFields(spec, ask, true, names));
            Expect("rej head wf", "1", Get(h, "ISWFCONTROLLED"));
            Expect("rej head wf case", "", Get(h, "IsWfControlled"));
            Expect("rej head vt", "356", Get(h, "IVTID"));
            Expect("rej head define", "y", Get(h, "CDEFINE3"));
            // U8 只认模板字段名的原样大小写（chdefine15，小写）；大写的 CHDEFINE15 被忽略。
            Expect("rej head chdefine template case", "z", Get(h, "chdefine15"));
            Expect("rej head chdefine no upper", "", Get(h, "CHDEFINE15"));
            Expect("rej head chdefine default case", "w", Get(h, "chdefine11"));
            Expect("rej head no date", "", Get(h, "DDATE"));
            Expect("rej head wf off", "0", Get(Map(QmRejDom.HeadFields(spec, ask, false, null)), "ISWFCONTROLLED"));
            Field("rej chdefine10", "head.chdefine10", delegate
            {
                Dictionary<string, object> bad = new Dictionary<string, object>();
                bad["chdefine10"] = "x";
                QmRejReq.Parse(spec, 5, bad, new object[] { Line(5, 1, "Sys01") });
            });
            CheckBody();
            CheckTemplate();
        }

        static void CheckTemplate()
        {
            Expect("rej tpl chdefine", "chDefine15", QmRejTpl.RequestName("chdefine15"));
            Expect("rej tpl reason name", "cReasonCode", QmRejTpl.RequestName("CREASONNAME"));
            Expect("rej tpl bridge field", "", QmRejTpl.RequestName("DCHECKDATE") ?? "");
            List<Dictionary<string, object>> fields = new List<Dictionary<string, object>>();
            fields.Add(Tpl("DCHECKDATE", "T", "检验日期"));
            fields.Add(Tpl("chdefine15", "T", "处理类型"));
            List<string> missing = new List<string>();
            missing.Add("DCHECKDATE");
            missing.Add("chdefine15");
            BridgeException ex = QmRejTpl.Refuse(fields, missing);
            Expect("rej tpl message", "缺少必输字段 DCHECKDATE（检验日期）、chDefine15（处理类型）（U8 单据模板设置为必输）", ex.Message);
            Expect("rej tpl field", "head.chDefine15", ex.Field);
        }

        static Dictionary<string, object> Tpl(string name, string section, string caption)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["FieldName"] = name;
            row["CardSection"] = section;
            row["Caption"] = caption;
            return row;
        }

        // 样例：数量 1250、换算率 25 → 件数 50；部门取报检单部门；降级存货的库存单位与换算率另算。
        static void CheckBody()
        {
            QmRejUnit unit = new QmRejUnit();
            unit.Inv = "A01";
            unit.Dep = "D01";
            unit.Unit = "U25";
            unit.Rate = 25m;
            unit.NumDigits = 4;
            QmRejLine line = new QmRejLine();
            line.Qty = 1250m;
            line.Flow = QmRejSrc.DimFlow;
            line.Dispose = "Sys03";
            line.DisposeName = "降级";
            line.Reason = "01";
            line.ReasonName = "检测指标不合格";
            line.DimInv = "B01";
            line.DimUnit = "U500";
            line.DimRate = 500m;
            Dictionary<string, string> b = Map(QmRejDom.LineFields(line, unit));
            Expect("rej body", "A 1250 50 D01 2 Sys03 降级 01 检测指标不合格 A01 B01 1250 U500 500 2.5",
                Join(b, "editprop", "FQUANTITY", "FNUM", "CDEPCODE", "IDISPOSEFLOW", "CSCRAPDISCODE", "CSCRAPDISNAME",
                    "CREASONCODE", "CREASONNAME", "CINVCODE", "CDIMINVCODE", "FDIMQUANTITY", "CDIMUNITID", "FDIMCHANGRATE", "FDIMNUM"));
            line.Flow = 0;
            line.DimInv = "";
            unit.Unit = "";
            Dictionary<string, string> plain = Map(QmRejDom.LineFields(line, unit));
            Expect("rej body no dim", "", Get(plain, "FDIMQUANTITY") + Get(plain, "CDIMUNITID") + Get(plain, "FNUM"));
            Expect("rej pieces round", "0.3333", QmRejUnits.Pieces(1m, "U3", 3m, 4));
            Expect("rej pieces no rate", "", QmRejUnits.Pieces(1m, "U3", 0m, 4));
            Expect("rej digits default", "6", QmRejUnits.Digits("x").ToString());
            Expect("rej digits", "4", QmRejUnits.Digits("4").ToString());
        }

        static string Join(Dictionary<string, string> map, params string[] names)
        {
            List<string> parts = new List<string>();
            for (int i = 0; i < names.Length; i++)
            {
                parts.Add(Get(map, names[i]));
            }
            return string.Join(" ", parts.ToArray());
        }

        static Dictionary<string, object> Line(int src, object qty, string dispose)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["source_line_id"] = src;
            row["quantity"] = qty;
            row["cScrapDisCode"] = dispose;
            row["creasoncode"] = "01";
            return row;
        }

        // 属性名区分大小写（与 U8 一致）。
        static Dictionary<string, string> Map(List<string[]> fields)
        {
            Dictionary<string, string> map = new Dictionary<string, string>(StringComparer.Ordinal);
            for (int i = 0; i < fields.Count; i++)
            {
                map[fields[i][0]] = fields[i][1];
            }
            return map;
        }

        static string Get(Dictionary<string, string> map, string name)
        {
            string value;
            return map.TryGetValue(name, out value) ? value : "";
        }

        static void Field(string name, string field, Action act)
        {
            try
            {
                act();
            }
            catch (BridgeException ex)
            {
                if (ex.Status == 400 && ex.Field == field)
                {
                    return;
                }
                throw new InvalidOperationException(name + " " + ex.Field);
            }
            throw new InvalidOperationException(name);
        }

        static void ExpectDec(string name, decimal want, decimal got)
        {
            if (want != got)
            {
                throw new InvalidOperationException(name);
            }
        }

        static void Expect(string name, string want, string got)
        {
            if (!string.Equals(want, got, StringComparison.Ordinal))
            {
                throw new InvalidOperationException(name + ": " + got);
            }
        }
    }
}
