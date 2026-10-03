using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的其他报检单（QM11）新增、其他检验单（QM15）生单 / 审核 / 删除部分：组件与功能 id、类型登记、预演模式、
    // 写规则、请求校验与拒绝路径、DOM 字段映射（属性名大小写、不写来源列）。不连库、不建 COM。
    internal static class QmOthWriteSelfTest
    {
        public static void Run()
        {
            CheckSpec();
            CheckWiring();
            CheckParse();
            CheckRefuse();
            CheckInspectFields();
            CheckCheckFields();
            CheckGates();
        }

        static void CheckSpec()
        {
            QmOthSpec i = QmOthSpec.Find("qm_other_inspect");
            QmOthSpec c = QmOthSpec.Find("qm_other_check");
            Expect("oth spec inspect", "QM11 361 UFQMCo.clsOtherInspectVoucherCO QM_QOthInspectB QM_QOthInspectT CINSPECTCODE",
                i.VouchType + " " + i.Vt + " " + i.ProgId + " " + i.HeadView + " " + i.BodyView + " " + i.CodeColumn);
            Expect("oth spec check", "QM15 365 UFQMCo.clsOtherCheckVoucherCO QM_QOthCheckB QM_QOthCheckT CCHECKCODE",
                c.VouchType + " " + c.Vt + " " + c.ProgId + " " + c.HeadView + " " + c.BodyView + " " + c.CodeColumn);
            Expect("oth auth inspect", "QM02060103 QM02060104 QM02060105 QM02060107",
                i.AddAuth + " " + i.DeleteAuth + " " + i.VerifyAuth + " " + i.UnverifyAuth);
            Expect("oth auth check", "QM02060203 QM02060204 QM02060205 QM02060207",
                c.AddAuth + " " + c.DeleteAuth + " " + c.VerifyAuth + " " + c.UnverifyAuth);
            QmSpec ask = QmOthSpec.CheckAsk;
            True("oth check ask", ask.VouchType == "QM15" && ask.Vt == 365 && !ask.Inspect && ask.CheckType == "OTH"
                && ask.SourceText.Length == 0 && QmSpec.Find("qm_other_check") == null);
        }

        static void CheckKinds()
        {
            VoucherKind i = Kinds.Find("qm_other_inspect");
            VoucherKind c = Kinds.Find("qm_other_check");
            True("oth inspect ops", i.Creatable && i.Deletable && i.Verifiable && i.Updatable && i.VerifySub == QmCo.LoginSub);
            True("oth inspect no source", i.Sources == null || i.Sources.Length == 0);
            True("oth check ops", !c.Creatable && c.Deletable && c.Verifiable && !c.Workflow);
            True("oth check source", c.VerifySub == QmCo.LoginSub && c.GenerateFrom == "qm_other_inspect");
        }

        static void CheckWiring()
        {
            CheckKinds();
            VoucherKind i = Kinds.Find("qm_other_inspect");
            VoucherKind c = Kinds.Find("qm_other_check");
            Expect("oth dry create", "validate", DryRunModes.Lookup("vouchers/create", "qm_other_inspect", "create", ""));
            Expect("oth dry delete", "validate", DryRunModes.Lookup("vouchers/delete", "qm_other_inspect", "delete", ""));
            Expect("oth dry generate", "validate",
                DryRunModes.Lookup("vouchers/generate", "qm_other_check", "generate", "qm_other_inspect"));
            Expect("oth dry verify", "validate", DryRunModes.Lookup("vouchers/verify", "qm_other_check", "unverify", ""));
            foreach (string key in new string[] { "write:qm_other_inspect:create", "write:qm_other_inspect:delete",
                "write:qm_other_check:create", "write:qm_other_check:delete", "write:qm_other_check:verify",
                "write:qm_other_check:unverify" })
            {
                True("oth rule " + key, PermRegistry.ForKey(key) != null);
            }
            True("oth inspect verify rule", PermRegistry.ForKey("write:qm_other_inspect:verify") != null
                && PermRegistry.ForKey("write:qm_other_inspect:unverify") != null);
            Expect("oth dry inspect verify", "validate", DryRunModes.Lookup("vouchers/verify", "qm_other_inspect", "verify", ""));
            True("oth items list", QmReq.ListField(c, "items", new ArrayList()) && !QmReq.ListField(i, "items", new ArrayList()));
            Expect("oth tpl name", "cInspectDepCode", QmRejTpl.RequestName("CINSPECTDEPCODE", QmOthReq.TplNames));
            Expect("oth tpl shared", "", QmRejTpl.RequestName("CINVCODE") ?? "");
            Expect("oth meta required", "cinvcode,quantity", string.Join(",", QmOthReq.CreateRequired(i)));
        }

        static void CheckParse()
        {
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["dDate"] = "2026-10-03";
            head["cDefine10"] = "LOT001";
            head["chDefine16"] = "原料";
            head["cInspectDepCode"] = "D01";
            QmOthAsk ask = QmOthReq.Parse(head, new object[] { Line("A01", 100), Line("A02", 2.5d) });
            Expect("oth ask head", "2026-10-03 LOT001 原料 D01",
                ask.Date + " " + ask.Get("cdefine10") + " " + ask.Get("chdefine16") + " " + ask.Get("cinspectdepcode"));
            Expect("oth ask lines", "2 A02 -1", ask.Lines.Count + " " + ask.Lines[1].Inv + " " + ask.Lines[1].TestStyle);
            Dictionary<string, object> styled = Line("A01", 1);
            styled["iTestStyle"] = 2;
            Expect("oth ask style", "2", QmOthReq.Parse(null, new object[] { styled }).Lines[0].TestStyle.ToString());
            Dictionary<string, object> check = new Dictionary<string, object>();
            check["cCheckPersonCode"] = "P01";
            QmAsk chk = QmReq.Parse(QmOthSpec.CheckAsk, check, new object[] { CheckLine(1002, 100) });
            Expect("oth check ask", "P01 1002", chk.Get("ccheckpersoncode") + " " + chk.Lines[0].SourceLineId);
        }

        static void CheckRefuse()
        {
            Dictionary<string, object> bad = new Dictionary<string, object>();
            bad["chdefine10"] = "x";
            Field("oth chdefine10", "head.chdefine10", delegate { QmOthReq.Parse(bad, new object[] { Line("A01", 1) }); });
            Dictionary<string, object> src = Line("A01", 1);
            src["source_line_id"] = 5;
            Field("oth source", "lines.0.source_line_id", delegate { QmOthReq.Parse(null, new object[] { src }); });
            Dictionary<string, object> noInv = Line("A01", 1);
            noInv.Remove("cinvcode");
            Field("oth no inv", "lines.0.cinvcode", delegate { QmOthReq.Parse(null, new object[] { noInv }); });
            Dictionary<string, object> style = Line("A01", 1);
            style["iteststyle"] = 4;
            Field("oth style", "lines.0.iteststyle", delegate { QmOthReq.Parse(null, new object[] { style }); });
            Field("oth no lines", "lines", delegate { QmOthReq.Parse(null, new object[0]); });
            Field("oth check person", "head.ccheckpersoncode",
                delegate { QmReq.Parse(QmOthSpec.CheckAsk, null, new object[] { CheckLine(1, 1) }); });
            Dictionary<string, object> person = new Dictionary<string, object>();
            person["ccheckpersoncode"] = "P01";
            Field("oth check one line", "lines",
                delegate { QmReq.Parse(QmOthSpec.CheckAsk, person, new object[] { CheckLine(1, 1), CheckLine(2, 1) }); });
        }

        static void CheckInspectFields()
        {
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["cdefine10"] = "LOT001";
            head["chdefine16"] = "原料";
            QmOthInsJob job = new QmOthInsJob();
            job.Spec = QmOthSpec.Find("qm_other_inspect");
            job.Ask = QmOthReq.Parse(head, new object[] { Line("A01", 100) });
            job.Date = "2026-10-03";
            job.Dep = "D01";
            job.DefineNames = new Dictionary<string, string>(StringComparer.Ordinal);
            job.DefineNames["chdefine16"] = "chdefine16";
            Dictionary<string, string> h = Map(QmOthIns.HeadFields(job));
            Expect("oth ins head", "A QM11 361 OTH 2026-10-03 D01 LOT001 原料",
                Join(h, "editprop", "CVOUCHTYPE", "IVTID", "CCHECKTYPECODE", "DDATE", "CINSPECTDEPCODE", "CDEFINE10", "chdefine16"));
            Expect("oth ins no source", "", Get(h, "CSOURCE") + Get(h, "CSOURCEID") + Get(h, "CSOURCECODE") + Get(h, "cdefine10"));
            Dictionary<string, object> inv = new Dictionary<string, object>();
            inv["cInvCode"] = "A01";
            inv["TestStyle"] = "3";
            inv["Unit"] = "U25";
            inv["GroupType"] = "1";
            inv["Rate"] = "25";
            Dictionary<string, string> b = Map(QmOthIns.LineFields(job.Ask.Lines[0], inv, 4));
            Expect("oth ins line", "A A01 100 3 U25 25 4",
                Join(b, "editprop", "CINVCODE", "FQUANTITY", "ITESTSTYLE", "CUNITID", "FCHANGRATE", "FNUM"));
            True("oth ins no source line", !b.ContainsKey("SOURCEAUTOID") && Get(b, "CWHCODE").Length == 0);
            inv["GroupType"] = "0";
            Expect("oth ins no unit", "", Get(Map(QmOthIns.LineFields(job.Ask.Lines[0], inv, 4)), "FNUM"));
        }

        static void CheckCheckFields()
        {
            QmChkJob job = new QmChkJob();
            job.Spec = QmOthSpec.CheckAsk;
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["ccheckpersoncode"] = "P01";
            head["chdefine11"] = "x";
            job.Ask = QmReq.Parse(QmOthSpec.CheckAsk, head, new object[] { CheckLine(1002, 100) });
            job.LineAsk = job.Ask.Lines[0];
            job.InspectId = 1001;
            job.Head = Row("CINSPECTCODE", "0000900001", "CCHECKTYPECODE", "OTH", "CINSPECTDEPCODE", "D01", "CMAKER", "张三");
            job.Line = Row("CINVCODE", "A01", "TestStyle", "3", "cComUnitCode", "KG");
            job.Project = Row("ID", "55", "CPROJECTCODE", "P55");
            job.DepCode = "Q01";
            job.Dt = 1m;
            Dictionary<string, string> names = new Dictionary<string, string>(StringComparer.Ordinal);
            names["chdefine11"] = "chdefine11";
            Dictionary<string, string> h = Map(QmOthChkDom.Head(job, names));
            Expect("oth chk head", "QM15 365 1001 0000900001 1002 OTH Q01 D01 张三 P01 A01 100 100 55 x",
                Join(h, "CVOUCHTYPE", "IVTID", "INSPECTID", "CINSPECTCODE", "INSPECTAUTOID", "CCHECKTYPECODE", "CDEPCODE",
                    "CINSPECTDEPCODE", "CINSPECTPERSON", "CCHECKPERSONCODE", "CINVCODE", "FQUANTITY", "FREGQUANTITY", "PROJECTID",
                    "chdefine11"));
            // 属性名照模板大写（同不良品处理单）；没有来源列。
            Expect("oth chk wf", "0 0 0", Join(h, "ISWFCONTROLLED", "IVERIFYSTATE", "IVERIFYSTATENEW"));
            Expect("oth chk no mixed case", "", Get(h, "IsWfControlled") + Get(h, "CSOURCE") + Get(h, "SOURCEID")
                + Get(h, "SOURCEAUTOID") + Get(h, "SOURCECODE") + Get(h, "CVENCODE"));
        }

        static void CheckGates()
        {
            QmOthChk.RequireFree(false, "0", 1);
            QmOthChk.RequireFree(false, "", 1);
            Status("oth used", 409, delegate { QmOthChk.RequireFree(true, "1", 1); });
            Status("oth flag", 409, delegate { QmOthChk.RequireFree(false, "1", 1); });
            Dictionary<string, object> doc = Row("wf", "0", "vs", "0");
            QmOthOps.RequireWfFree(doc);
            doc["vs"] = "1";
            Status("oth wf", 409, delegate { QmOthOps.RequireWfFree(doc); });
            QmOthInsJob job = new QmOthInsJob();
            job.Ask = QmOthReq.Parse(null, new object[] { Line("a01", 2), Line("B02", 1.5d) });
            job.Invs.Add(Row("cInvCode", "A01"));
            job.Invs.Add(Row("cInvCode", "B02"));
            List<Dictionary<string, object>> saved = new List<Dictionary<string, object>>();
            saved.Add(Row("CINVCODE", "B02", "qty", "1.500000"));
            saved.Add(Row("CINVCODE", "A01", "qty", "2"));
            True("oth covers", QmOthSaved.Covers(job, saved));
            saved[0]["qty"] = "1";
            True("oth covers qty", !QmOthSaved.Covers(job, saved));
        }

        static Dictionary<string, object> Line(string inv, object qty)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["cinvcode"] = inv;
            row["quantity"] = qty;
            return row;
        }

        static Dictionary<string, object> CheckLine(int src, object qty)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["source_line_id"] = src;
            row["quantity"] = qty;
            return row;
        }

        static Dictionary<string, object> Row(params string[] pairs)
        {
            Dictionary<string, object> row = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i + 1 < pairs.Length; i += 2)
            {
                row[pairs[i]] = pairs[i + 1];
            }
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

        static string Join(Dictionary<string, string> map, params string[] names)
        {
            List<string> parts = new List<string>();
            for (int i = 0; i < names.Length; i++)
            {
                parts.Add(Get(map, names[i]));
            }
            return string.Join(" ", parts.ToArray());
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

        static void Status(string name, int status, Action act)
        {
            try
            {
                act();
            }
            catch (BridgeException ex)
            {
                if (ex.Status == status)
                {
                    return;
                }
                throw new InvalidOperationException(name + " " + ex.Status);
            }
            throw new InvalidOperationException(name);
        }

        static void True(string name, bool ok)
        {
            if (!ok)
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
