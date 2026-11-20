using System;
using System.Collections;
using System.Collections.Generic;
using System.Data;

namespace U8Co
{
    // --selftest 的生产订单参照销售订单生成部分：只测纯逻辑（请求解析、按 U8 列表定行、保存参数的层次和键、
    // 逐行错误码、回读核对），不连库、不加载 U8 组件。
    internal static class MoGenSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckAttach();
            CheckSaveArgs();
            CheckCnt();
            CheckVerify();
        }

        static void CheckParse()
        {
            MoGenAsk ask = MoGenReq.Parse(9, Head("1"), new object[] { Line(11, 2), Line(12, 1.5) });
            Expect("gen parse", ask.SoId == 9, ask.MoType == "1", ask.Lines.Count == 2, ask.Remark == null,
                ask.Lines[1].Seq == 2, ask.Lines[1].Qty == 1.5m);
            Dictionary<string, object> extra = Head("1");
            extra["cmemo"] = "x";
            Refused("gen head key", delegate { MoGenReq.Parse(9, extra, new object[] { Line(11, 1) }); }, 400);
            Refused("gen no type", delegate { MoGenReq.Parse(9, new Dictionary<string, object>(), new object[] { Line(11, 1) }); }, 400);
            Refused("gen long type", delegate { MoGenReq.Parse(9, Head("123"), new object[] { Line(11, 1) }); }, 400);
            Refused("gen dup", delegate { MoGenReq.Parse(9, Head("1"), new object[] { Line(11, 1), Line(11, 2) }); }, 400);
            Dictionary<string, object> wh = Line(11, 1);
            wh["wh_code"] = "01";
            Refused("gen line key", delegate { MoGenReq.Parse(9, Head("1"), new object[] { wh }); }, 400);
            Refused("gen zero", delegate { MoGenReq.Parse(9, Head("1"), new object[] { Line(11, 0) }); }, 400);
        }

        static void CheckAttach()
        {
            DataTable t = Table();
            AddRow(t, 11, 5m, "0101");
            MoGenAsk ask = MoGenReq.Parse(9, Head("1"), new object[] { Line(11, 2) });
            MoGenArgs.Attach(ask, t);
            Expect("gen attach dept", ask.Lines[0].FinalDept == "0101", ask.Lines[0].U8Row != null);
            MoGenAsk over = MoGenReq.Parse(9, Head("1"), new object[] { Line(11, 6) });
            Refused("gen over", delegate { MoGenArgs.Attach(over, t); }, 409);
            over.OverPlan = true;
            MoGenArgs.Attach(over, t);
            Expect("gen over plan", over.Lines[0].U8Row != null);
            MoGenAsk missing = MoGenReq.Parse(9, Head("1"), new object[] { Line(12, 1) });
            Refused("gen missing", delegate { MoGenArgs.Attach(missing, t); }, 409);
            missing.DataAuth = true;
            Refused("gen missing auth", delegate { MoGenArgs.Attach(missing, t); }, 403);
            DataTable blank = Table();
            AddRow(blank, 11, 5m, "");
            MoGenAsk nodept = MoGenReq.Parse(9, Head("1"), new object[] { Line(11, 1) });
            Refused("gen no dept", delegate { MoGenArgs.Attach(nodept, blank); }, 400);
        }

        static void CheckSaveArgs()
        {
            DataTable t = Table();
            AddRow(t, 11, 5m, "0101");
            AddRow(t, 12, 4m, "0101");
            Dictionary<string, object> head = Head("1");
            head["mo_code"] = "MO001";
            MoGenAsk ask = MoGenReq.Parse(9, head, new object[] { Line(11, 2), Line(12, 4) });
            ask.AuxDigits = 2;
            MoGenArgs.Attach(ask, t);
            MoGenWho who = new MoGenWho();
            who.AccId = "801";
            who.UserId = "demo";
            Hashtable all = MoGenArgs.SaveArgs(ask, who);
            Hashtable group = all[0] as Hashtable;
            Expect("gen save shape", all.Count == 1, group != null);
            Expect("gen save lines", group.Count == 2);
            Hashtable first = (Hashtable)((Hashtable)group[0])[0];
            Hashtable second = (Hashtable)((Hashtable)group[1])[0];
            Expect("gen save keys", (string)first["MoCode"] == "MO001", (int)first["OrderDId"] == 11,
                (string)first["From"] == "SA", (int)first["v_procmodule"] == 2, !first.ContainsKey("Type"),
                (string)first["v_MDeptCode"] == "0101", (string)first["Remark"] == "SO memo",
                first.ContainsKey("v_Define37"), (decimal)first["Qty"] == 2m);
            // 数量改了按换算率重算件数（2 / 2 = 1）；等于列表缺省时用列表件数（4 → 列表 GenAuxQty 9）。
            Expect("gen save aux", (decimal)first["Num"] == 1m, (decimal)second["Num"] == 9m);
        }

        static void CheckCnt()
        {
            MoGenLine line = new MoGenLine();
            line.Seq = 3;
            line.FinalDept = "0101";
            BridgeException dept = MoGenCheck.Refused(line, 901027);
            BridgeException bom = MoGenCheck.Refused(line, 900022);
            Expect("gen cnt dept", dept.Status == 403, dept.Message.Contains("0101"));
            Expect("gen cnt bom", bom.Status == 409, bom.Message == "第 3 行：BOM 展开失败");
            Hashtable res = new Hashtable();
            Hashtable one = new Hashtable();
            one["Cnt"] = "900031";
            res[0] = one;
            List<MoGenHit> hits = MoGenCheck.Hits(res, 2);
            Expect("gen hits", hits.Count == 2, hits[0].Cnt == 900031, hits[0].Known, !hits[1].Known);
            Hashtable two = new Hashtable();
            two["Cnt"] = 0;
            two["MoDId"] = 77;
            res[1] = two;
            List<int> ids = MoGenCheck.Reported(MoGenCheck.Hits(res, 2));
            Expect("gen reported", ids.Count == 1, ids[0] == 77);
        }

        static void CheckVerify()
        {
            MoGenAsk ask = MoGenReq.Parse(9, Head("1"), new object[] { Line(11, 2) });
            ask.QtyDigits = 2;
            ask.MoTypeId = 1;
            ask.Lines[0].FinalDept = "0101";
            ask.Lines[0].MoQtyBefore = 3m;
            List<Dictionary<string, object>> ok = new List<Dictionary<string, object>>();
            ok.Add(Found("2.000000", "5.000000"));
            Expect("gen verify ok", MoGenCheck.Verify(ask, ok) == null);
            List<Dictionary<string, object>> qty = new List<Dictionary<string, object>>();
            qty.Add(Found("1.000000", "4.000000"));
            Expect("gen verify qty", MoGenCheck.Verify(ask, qty) != null);
            List<Dictionary<string, object>> back = new List<Dictionary<string, object>>();
            back.Add(Found("2.000000", "3.000000"));
            Expect("gen verify so", MoGenCheck.Verify(ask, back) != null);
        }

        static Dictionary<string, object> Found(string qty, string soMoQty)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["MoId"] = "100";
            row["MoCode"] = "MO001";
            row["MoDId"] = "200";
            row["OrderDId"] = "11";
            row["Qty"] = qty;
            row["MDeptCode"] = "0101";
            row["MoTypeId"] = "1";
            row["SoMoQty"] = soMoQty;
            return row;
        }

        static DataTable Table()
        {
            DataTable t = new DataTable(MoGenArgs.Table);
            t.Columns.Add("Id", typeof(int));
            t.Columns.Add("OrderId", typeof(int));
            t.Columns.Add("OrderDId", typeof(int));
            t.Columns.Add("OrderType", typeof(int));
            t.Columns.Add("CBomId", typeof(int));
            t.Columns.Add("InvCode", typeof(string));
            for (int i = 1; i <= 10; i++)
            {
                t.Columns.Add("InvFree_" + i, typeof(string));
            }
            t.Columns.Add("UnGenQty", typeof(decimal));
            t.Columns.Add("GenQty", typeof(decimal));
            t.Columns.Add("GenAuxQty", typeof(decimal));
            t.Columns.Add("GenChangeRate", typeof(decimal));
            t.Columns.Add("DueDate", typeof(DateTime));
            t.Columns.Add("MDeptCode", typeof(string));
            t.Columns.Add("Remark", typeof(string));
            t.Columns.Add("RequisitionFlag", typeof(bool));
            t.Columns.Add("MoClass", typeof(byte));
            t.Columns.Add("GroupType", typeof(short));
            t.Columns.Add("Ufts", typeof(byte[]));
            t.Columns.Add("DDefine_37", typeof(string));
            return t;
        }

        static void AddRow(DataTable t, int soLineId, decimal left, string dept)
        {
            DataRow r = t.NewRow();
            r["Id"] = t.Rows.Count + 1;
            r["OrderId"] = 9;
            r["OrderDId"] = soLineId;
            r["OrderType"] = 1;
            r["InvCode"] = "A01";
            r["UnGenQty"] = left;
            r["GenQty"] = left;
            r["GenAuxQty"] = 9m;
            r["GenChangeRate"] = 2m;
            r["DueDate"] = new DateTime(2026, 1, 31);
            r["MDeptCode"] = dept;
            r["Remark"] = "SO memo";
            r["RequisitionFlag"] = false;
            r["MoClass"] = (byte)1;
            r["GroupType"] = (short)1;
            r["Ufts"] = new byte[8];
            t.Rows.Add(r);
        }

        static Dictionary<string, object> Head(string moType)
        {
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["mo_type"] = moType;
            return head;
        }

        static Dictionary<string, object> Line(int id, double qty)
        {
            Dictionary<string, object> line = new Dictionary<string, object>();
            line["source_line_id"] = id;
            line["quantity"] = qty;
            return line;
        }

        static void Refused(string name, Action act, int status)
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
                throw new InvalidOperationException(name + ": " + ex.Status);
            }
            throw new InvalidOperationException(name + ": 没有拒绝");
        }

        // 各项用逗号分开传（不用 &&），免得测试函数的圈复杂度超限。
        static void Expect(string name, params bool[] checks)
        {
            for (int i = 0; i < checks.Length; i++)
            {
                if (!checks[i])
                {
                    throw new InvalidOperationException(name + " #" + i);
                }
            }
        }
    }
}
