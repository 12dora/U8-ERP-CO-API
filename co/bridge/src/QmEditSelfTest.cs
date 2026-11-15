using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的质量单据修改部分：类型登记、预演模式、写规则与功能 id、请求校验、数量核对、回读比较。不连库、不建 COM。
    internal static class QmEditSelfTest
    {
        public static void Run()
        {
            CheckWiring();
            CheckParse();
            CheckRefuse();
            CheckSplit();
            CheckYield();
            CheckSame();
        }

        // 让步接收核准人（QmYield）：让步数量大于 0 时要有编码（请求或单据上已有）；其他检验单不要求；日期格式。
        static void CheckYield()
        {
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["cYielderCode"] = "P01";
            head["dYieldDate"] = "2026-09-30";
            QmEditAsk ask = QmEditReq.Parse(Kinds.Find("qm_product_check"), head, null);
            Expect("edit yield parse", "P01|2026-09-30", ask.Get(QmYield.CodeKey) + "|" + ask.Get(QmYield.DateKey));
            Bad("edit yield date", Kinds.Find("qm_product_check"), One("dyielddate", "2026/9/30"), null, "head.dyielddate");
            Bad("edit yield empty", Kinds.Find("qm_product_check"), One("cyieldercode", ""), null, "head.cyieldercode");
            Bad("edit yield inspect", Kinds.Find("qm_other_inspect"), One("cyieldercode", "P01"), null, "head.cyieldercode");
            Field("edit yield need", delegate { QmYield.Require("", "", 1m, true, "head.cyieldercode"); }, "head.cyieldercode");
            QmYield.Require("", "P01", 1m, true, "head.cyieldercode");
            QmYield.Require("P02", "", 1m, true, "head.cyieldercode");
            QmYield.Require("", "", 0m, true, "head.cyieldercode");
            QmYield.Require("", "", 1m, false, "head.cyieldercode");
            Dictionary<string, object> gen = new Dictionary<string, object>();
            gen["ccheckpersoncode"] = "P01";
            gen["cyieldercode"] = "P02";
            gen["dyielddate"] = "2026-09-30";
            Dictionary<string, object> line = new Dictionary<string, object>();
            line["source_line_id"] = 5;
            line["quantity"] = 2;
            line["fconquantiy"] = 1;
            QmAsk made = QmReq.Parse(QmSpec.Find("qm_product_check"), gen, new object[] { line });
            Expect("gen yield parse", "P02", made.Get(QmYield.CodeKey));
        }

        static void CheckWiring()
        {
            string[] kinds = new string[] { "qm_incoming_check", "qm_product_check", "qm_other_check", "qm_other_inspect" };
            for (int i = 0; i < kinds.Length; i++)
            {
                VoucherKind k = Kinds.Find(kinds[i]);
                True("edit kind " + kinds[i], k != null && k.Updatable && QmEditReq.Handles(k));
                Expect("edit dry " + kinds[i], "validate", DryRunModes.Lookup("vouchers/update", kinds[i], "update", ""));
                True("edit rule " + kinds[i], PermRegistry.ForKey("write:" + kinds[i] + ":update") != null);
            }
            string[] fixedKinds = new string[] { "qm_incoming_inspect", "qm_product_inspect", "qm_incoming_reject" };
            for (int i = 0; i < fixedKinds.Length; i++)
            {
                VoucherKind k = Kinds.Find(fixedKinds[i]);
                True("edit not " + fixedKinds[i], !k.Updatable && !QmEditReq.Handles(k));
            }
            Expect("edit auth", "QM02010203 QM02020203 QM02060203 QM02060103",
                QmEditReq.Auth("qm_incoming_check") + " " + QmEditReq.Auth("qm_product_check") + " "
                + QmEditReq.Auth("qm_other_check") + " " + QmEditReq.Auth("qm_other_inspect"));
            True("edit check flags", QmEditReq.IsCheck(Kinds.Find("qm_other_check")) && !QmEditReq.IsCheck(Kinds.Find("qm_other_inspect")));
        }

        static void CheckParse()
        {
            VoucherKind check = Kinds.Find("qm_product_check");
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["fDisQuantity"] = 2;
            head["cReasonCode"] = "03";
            head["chDefine11"] = "001";
            head["cDefine10"] = "B-1";
            head["items"] = new ArrayList { Item("A01", "G01", "合格") };
            QmEditAsk ask = QmEditReq.Parse(check, head, null);
            True("edit parse check", ask.Check && ask.Split && ask.Dis.Value == 2m && !ask.Reg.HasValue && ask.Items.Count == 1);
            Expect("edit parse text", "03|001|B-1", ask.Get("creasoncode") + "|" + ask.Get("chdefine11") + "|" + ask.Get("cdefine10"));
            Dictionary<string, object> ins = new Dictionary<string, object>();
            ins["cInspectDepCode"] = "D01";
            ins["dDate"] = "2026-09-30";
            QmEditAsk other = QmEditReq.Parse(Kinds.Find("qm_other_inspect"), ins, new object[0]);
            True("edit parse inspect", !other.Check && !other.Split && other.Items == null && other.Get("cinspectdepcode") == "D01");
            CheckKeys();
        }

        static void CheckKeys()
        {
            True("edit head keys", QmEditReq.HeadKey(true, "creasoncode") && !QmEditReq.HeadKey(false, "creasoncode"));
            True("edit head defines", QmEditReq.HeadKey(false, "chdefine16") && !QmEditReq.HeadKey(true, "cdefine01"));
            True("edit head fixed", !QmEditReq.HeadKey(true, "fquantity") && !QmEditReq.HeadKey(true, "cinvcode"));
        }

        static void CheckRefuse()
        {
            VoucherKind check = Kinds.Find("qm_incoming_check");
            VoucherKind inspect = Kinds.Find("qm_other_inspect");
            Bad("edit no head", check, new Dictionary<string, object>(), null, "head");
            Bad("edit lines", check, One("cchkconclusion", "合格"), new object[] { new Dictionary<string, object>() }, "lines");
            Bad("edit fquantity", check, One("fquantity", 5), null, "head.fquantity");
            Bad("edit cwhcode", check, One("cwhcode", "01"), null, "head.cwhcode");
            Bad("edit project", check, One("project_code", "P1"), null, "head.project_code");
            Bad("edit inspect items", inspect, One("items", new ArrayList { Item("A", "G", null) }), null, "head.items");
            Bad("edit inspect reason", inspect, One("creasoncode", "03"), null, "head.creasoncode");
            Bad("edit bad date", check, One("ddate", "2026/9/30"), null, "head.ddate");
            Bad("edit empty person", check, One("ccheckpersoncode", " "), null, "head.ccheckpersoncode");
            Bad("edit negative", check, One("fdisquantity", -1), null, "head.fdisquantity");
        }

        static void CheckSplit()
        {
            VoucherKind check = Kinds.Find("qm_product_check");
            QmEditAsk dis = QmEditReq.Parse(check, One("fdisquantity", 3), null);
            decimal[] split = QmEditReq.Resolve(dis, 10m);
            Expect("edit split", "7|0|3", QmSql.Num(split[0]) + "|" + QmSql.Num(split[1]) + "|" + QmSql.Num(split[2]));
            // 只有不良数量不要求原因；让步数量大于 0 时要求（请求或单据上已有）。
            QmEditReq.RequireReason(dis, true, split[1], "", false);
            QmEditAsk con = QmEditReq.Parse(check, One("fconquantiy", 2), null);
            decimal[] conSplit = QmEditReq.Resolve(con, 10m);
            Field("edit reason need", delegate { QmEditReq.RequireReason(con, true, conSplit[1], "", false); }, "head.creasoncode");
            QmEditReq.RequireReason(con, true, conSplit[1], "03", false);
            // 其他检验单：没有让步数量时不收原因。
            QmEditAsk other = QmEditReq.Parse(Kinds.Find("qm_other_check"), One("creasoncode", "03"), null);
            Field("edit reason other", delegate { QmEditReq.RequireReason(other, false, 0m, "", true); }, "head.creasoncode");
            QmEditReq.RequireReason(other, false, 1m, "", true);
            QmEditAsk over = QmEditReq.Parse(check, One("fconquantiy", 11), null);
            Field("edit split over", delegate { QmEditReq.Resolve(over, 10m); }, "fconquantiy");
            Dictionary<string, object> sum = new Dictionary<string, object>();
            sum["fregquantity"] = 5;
            sum["fdisquantity"] = 4;
            QmEditAsk wrong = QmEditReq.Parse(check, sum, null);
            Field("edit split sum", delegate { QmEditReq.Resolve(wrong, 10m); }, "fregquantity");
            QmEditAsk all = QmEditReq.Parse(check, One("fregquantity", 10), null);
            QmEditReq.RequireReason(all, true, QmEditReq.Resolve(all, 10m)[1], "", true);
        }

        static void CheckSame()
        {
            True("edit same text", QmEditSaved.Same(" 合格 ", "合格") && !QmEditSaved.Same("合格", "不合格"));
            True("edit same num", QmEditSaved.Same("3", "3.000000") && !QmEditSaved.Same("3", "3.1"));
            True("edit same date", QmEditSaved.Same("2026-09-30", "2026-09-30 00:00:00") && !QmEditSaved.Same("2026-09-30", "2026-09-29"));
        }

        static Dictionary<string, object> Item(string code, string guide, string judge)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["cchkitemcode"] = code;
            item["cchkguidecode"] = guide;
            if (judge != null)
            {
                item["ctargetqjug"] = judge;
            }
            return item;
        }

        static Dictionary<string, object> One(string key, object value)
        {
            Dictionary<string, object> head = new Dictionary<string, object>();
            head[key] = value;
            return head;
        }

        static void Bad(string name, VoucherKind kind, Dictionary<string, object> head, object[] lines, string field)
        {
            Field(name, delegate { QmEditReq.Parse(kind, head, lines); }, field);
        }

        static void Field(string name, Action act, string field)
        {
            try
            {
                act();
            }
            catch (BridgeException ex)
            {
                if (ex.Status != 400 || ex.Field != field)
                {
                    throw new InvalidOperationException(name + ": " + ex.Status + " " + ex.Field);
                }
                return;
            }
            throw new InvalidOperationException(name + ": no error");
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
