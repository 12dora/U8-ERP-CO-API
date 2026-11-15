using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的质量单据部分：只测纯逻辑（U8 错误原文的两种来路、合格数量与剩余数量、必输项核对、请求校验），不连库、不建 COM。
    internal static class QmSelfTest
    {
        const string Bag = "<?xml version=\"1.0\"?>\r\n<ErrBagList><ErrBag flag=\"false\" excepted=\"[预料外错误]\" number=\"-1\""
            + " source=\"a.[b]\" description=\"'检验部门        '不能为空！\"/><ErrBag flag=\"true\" number=\"-1\""
            + " source=\"b\" description=\"第二条\"/></ErrBagList>\r\n";

        public static void Run()
        {
            CheckBag();
            CheckList();
            CheckMath();
            CheckMissing();
            CheckRequest();
            // 其他报检单、其他检验单的读取。
            QmOtherSelfTest.Run();
            // 质量单据修改。
            QmEditSelfTest.Run();
            // 产品报检单单独弃审。
            QmInsUnverifySelfTest.Run();
        }

        static void CheckBag()
        {
            Expect("qm bag", "'检验部门'不能为空！", QmCo.BagText(Bag));
            Expect("qm bag broken", "抽检量不能为0", QmCo.BagText("x <ErrBagList><ErrBag description=\"抽检量不能为0\"/>"));
            Expect("qm bag none", "", QmCo.BagText("类型不匹配。"));
        }

        static void CheckList()
        {
            Expect("qm list one", "回写到货单失败:到货单到货数量必须大于等于累计报检数量!",
                QmCo.ListText("错误列表：\r\n 1、  [描述]:回写到货单失败:到货单到货数量必须大于等于累计报检数量!"));
            Expect("qm list two", "'报检人'不能为空！；表体行数必须大于0行！",
                QmCo.ListText("错误列表：\r\n 1、  [描述]:'报检人'不能为空！\r\n 2、  [描述]:表体行数必须大于0行！\r\n"));
            Expect("qm list plain", "别的错误", QmCo.ListText("  别的错误 "));
            Expect("qm list empty", "", QmCo.ListText(null));
        }

        static void CheckMath()
        {
            ExpectDec("qm reg default", 7m, QmMath.Qualified(10m, false, 0m, 2m, 1m));
            ExpectDec("qm reg given", 10m, QmMath.Qualified(10m, true, 10m, 0m, 0m));
            ExpectStatus("qm reg over", 400, delegate { QmMath.Qualified(10m, false, 0m, 6m, 5m); });
            ExpectStatus("qm reg sum", 400, delegate { QmMath.Qualified(10m, true, 5m, 1m, 1m); });
            ExpectDec("qm left", 40m, QmMath.Remaining(100m, 60m));
            ExpectDec("qm left over", -5m, QmMath.Remaining(100m, 105m));
            QmMath.RequireLeft(40m, 40m, "");
            ExpectStatus("qm left 409", 409, delegate { QmMath.RequireLeft(40.000001m, 40m, ""); });
            Expect("qm conclusion ok", QmReq.Qualified, QmMath.Conclusion(0m));
            Expect("qm conclusion bad", QmReq.Unqualified, QmMath.Conclusion(0.5m));
        }

        static void CheckMissing()
        {
            List<Dictionary<string, object>> fields = new List<Dictionary<string, object>>();
            fields.Add(Field("CCHECKCODE", "T"));
            fields.Add(Field("chdefine11", "T"));
            fields.Add(Field("CDEPNAME", "T"));
            fields.Add(Field("CINVCODE", "B"));
            List<Dictionary<string, object>> head = new List<Dictionary<string, object>>();
            head.Add(Row("CDEPNAME", "示例车间"));
            List<Dictionary<string, object>> body = new List<Dictionary<string, object>>();
            body.Add(Row("CINVCODE", "A01"));
            body.Add(Row("CINVCODE", ""));
            List<string> missing = QmTplReq.Missing(fields, head, body, "CCHECKCODE");
            Expect("qm missing", "chdefine11,CINVCODE", string.Join(",", missing.ToArray()));
        }

        static void CheckRequest()
        {
            QmSpec check = QmSpec.Find("qm_incoming_check");
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["cCheckPersonCode"] = "P01";
            head["chdefine11"] = "x";
            Dictionary<string, object> line = new Dictionary<string, object>();
            line["source_line_id"] = 7;
            line["quantity"] = 10;
            line["fDisQuantity"] = 2.5d;
            QmAsk ask = QmReq.Parse(check, head, new object[] { line });
            ExpectDec("qm ask reg", 7.5m, ask.Lines[0].Reg);
            Expect("qm ask person", "P01", ask.Get("ccheckpersoncode"));
            head.Remove("cCheckPersonCode");
            ExpectStatus("qm ask checker", 400, delegate { QmReq.Parse(check, head, new object[] { line }); });
            ExpectStatus("qm ask chdefine inspect", 400,
                delegate { QmReq.Parse(QmSpec.Find("qm_incoming_inspect"), head, new object[] { line }); });
        }

        static Dictionary<string, object> Field(string name, string section)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["FieldName"] = name;
            row["CardSection"] = section;
            return row;
        }

        static Dictionary<string, object> Row(string name, string value)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row[name] = value;
            return row;
        }

        static void ExpectStatus(string name, int status, Action act)
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
                throw new InvalidOperationException(name);
            }
        }
    }
}
