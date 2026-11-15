using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的红冲蓝字销售发票部分（SaleGenBlueRed*）：来源登记与分派、发票类型对应 VT、剩余数量与行计划、
    // 保存后的红字行核对、SQL 占位符个数。不连库、不建 COM。
    internal static class SaleBlueRedSelfTest
    {
        public static void Run()
        {
            CheckWiring();
            CheckVt();
            CheckPlan();
            CheckRefusals();
            CheckMismatch();
            CheckShape();
            CheckSql();
        }

        // RedShape(n, r, blue, disp, back)：红字发票删除 / 复核的分类。
        static void CheckShape()
        {
            Expect("br shape blue", SaleGen.RedShape(2m, 0m, 2m, 0m, 0m) == SaleGen.RedFromBlue);
            Expect("br shape blue u8 return", SaleGen.RedShape(2m, 2m, 2m, 0m, 1m) == SaleGen.RedFromBlue);
            Expect("br shape blue disp1 back", SaleGen.RedShape(1m, 1m, 1m, 1m, 1m) == SaleGen.RedFromBlue);
            Expect("br shape blue disp0", SaleGen.RedShape(1m, 1m, 1m, 0m, 0m) == SaleGen.RedFromBlue);
            Expect("br shape return", SaleGen.RedShape(2m, 2m, 0m, 1m, 0m) == SaleGen.RedFromReturn);
            Expect("br shape return wins", SaleGen.RedShape(2m, 2m, 2m, 1m, 0m) == SaleGen.RedFromReturn);
            Expect("br shape first bill", SaleGen.RedShape(2m, 2m, 0m, 1m, 1m) < 0);
            Expect("br shape mixed", SaleGen.RedShape(2m, 1m, 1m, 1m, 0m) < 0);
            Expect("br shape empty", SaleGen.RedShape(0m, 0m, 0m, 1m, 0m) < 0);
        }

        static void CheckWiring()
        {
            VoucherKind inv = Kinds.Find("sale_invoice");
            Expect("br source", inv != null && Array.IndexOf(inv.Sources, SaleGen.BlueSourceName) >= 0);
            Expect("br default source", inv.Sources[0] == "dispatch");
            WorkItem item = new WorkItem();
            item.Source = inv;
            Expect("br from blue", SaleGen.FromBlue(item) && !SaleGen.FromReturn(item));
            item.Source = Kinds.Find("dispatch");
            Expect("br not blue", !SaleGen.FromBlue(item) && !SaleGen.FromBlue(null));
            Expect("br meta", MetaGenerate.Of("sale_invoice", SaleGen.BlueSourceName) != null);
        }

        static void CheckVt()
        {
            Expect("br vt 26", SaleGen.BlueVt("", "26") == 1 && SaleGen.BlueVt("26", "26") == 1);
            Expect("br vt 27", SaleGen.BlueVt("", "27") == 3 && SaleGen.BlueVt("27", " 27 ") == 3);
            Expect("br vt mismatch", Refused(delegate { SaleGen.BlueVt("27", "26"); }, 400));
            Expect("br vt other", Refused(delegate { SaleGen.BlueVt("", "28"); }, 400));
        }

        // 11：5 张已红冲 2；12：3 张已全部红冲；13：0 数量调价行。
        static List<Dictionary<string, object>> Rows3()
        {
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            rows.Add(Row("11", "5", "2"));
            rows.Add(Row("12", "3", "3"));
            rows.Add(Row("13", "0", "0"));
            return rows;
        }

        static void CheckPlan()
        {
            List<SaleGen.BlueLine> whole = SaleGen.PlanBlueLines(Rows3(), new object[0]);
            Expect("br whole", whole.Count == 1 && whole[0].LineId == 11 && whole[0].Qty == 3m && whole[0].SoLine == 71);
            Expect("br whole null", SaleGen.PlanBlueLines(Rows3(), null).Count == 1);
            List<SaleGen.BlueLine> part = SaleGen.PlanBlueLines(Rows3(), new object[] { Line(11, 2) });
            Expect("br part", part.Count == 1 && part[0].LineId == 11 && part[0].Qty == 2m && part[0].DlLine == 61);
            Expect("br left", SaleGen.BlueLeft(Row("1", "2", "5")) == 0m && SaleGen.BlueLeft(Row("1", "4", "1.5")) == 2.5m);
        }

        static void CheckRefusals()
        {
            Expect("br over", Refused(delegate { SaleGen.PlanBlueLines(Rows3(), new object[] { Line(11, 4) }); }, 409));
            Expect("br done line", Refused(delegate { SaleGen.PlanBlueLines(Rows3(), new object[] { Line(12, 1) }); }, 409));
            Expect("br zero line", Refused(delegate { SaleGen.PlanBlueLines(Rows3(), new object[] { Line(13, 1) }); }, 409));
            Expect("br unknown", Refused(delegate { SaleGen.PlanBlueLines(Rows3(), new object[] { Line(99, 1) }); }, 400));
            Expect("br dup", Refused(delegate { SaleGen.PlanBlueLines(Rows3(), new object[] { Line(11, 1), Line(11, 1) }); }, 400));
            Expect("br bad qty", Refused(delegate { SaleGen.PlanBlueLines(Rows3(), new object[] { Line(11, 0) }); }, 400));
            List<Dictionary<string, object>> spent = new List<Dictionary<string, object>>();
            spent.Add(Row("12", "3", "3"));
            Expect("br all done", Refused(delegate { SaleGen.PlanBlueLines(spent, new object[0]); }, 409));
        }

        static void CheckMismatch()
        {
            List<SaleGen.BlueLine> plan = SaleGen.PlanBlueLines(Rows3(), new object[] { Line(11, 2) });
            Dictionary<int, decimal> linked = new Dictionary<int, decimal>();
            linked[11] = 2m;
            Dictionary<int, decimal> lost = new Dictionary<int, decimal>();
            lost[0] = 2m;
            Expect("br saved ok", SaleGen.BlueMismatch(plan, 1, 0, 2m, linked, true).Length == 0);
            Expect("br saved count", SaleGen.BlueMismatch(plan, 2, 0, 2m, linked, true).Length > 0);
            Expect("br saved sign", SaleGen.BlueMismatch(plan, 1, 1, 2m, linked, true).Length > 0);
            Expect("br saved total", SaleGen.BlueMismatch(plan, 1, 0, 1m, linked, true).Length > 0);
            Expect("br saved link", SaleGen.BlueMismatch(plan, 1, 0, 2m, lost, true).Length > 0);
            Expect("br saved nolink", SaleGen.BlueMismatch(plan, 1, 0, 2m, lost, false).Length == 0);
        }

        static void CheckSql()
        {
            Expect("br line sql", Count(SaleGen.BlueLineSql) == 1 && Count(SaleGen.BlueLineLockSql) == 1);
            Expect("br lock sql", SaleGen.BlueLineLockSql.Contains("from SaleBillVouchs b with (updlock, holdlock) where b.SBVID=?"));
            Expect("br link sql", SaleGen.BlueLineSql.Contains("r.iSBVID=b.AutoID"));
            Expect("br snap sql", Count(SaleGen.BlueSnapSql) == 3 && Count(SaleGen.BlueNewHeadSql) == 2);
        }

        static Dictionary<string, object> Row(string id, string q, string back)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["AutoID"] = id;
            row["iDLsID"] = "61";
            row["iSOsID"] = "71";
            row["q"] = q;
            row["back"] = back;
            return row;
        }

        static Dictionary<string, object> Line(int id, int qty)
        {
            Dictionary<string, object> line = new Dictionary<string, object>();
            line["source_line_id"] = id;
            line["quantity"] = qty;
            return line;
        }

        static bool Refused(Action act, int status)
        {
            try
            {
                act();
            }
            catch (BridgeException ex)
            {
                return ex.Status == status;
            }
            return false;
        }

        static int Count(string text)
        {
            int n = 0;
            for (int i = 0; i < text.Length; i++)
            {
                if (text[i] == '?')
                {
                    n++;
                }
            }
            return n;
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException(name);
            }
        }
    }
}
