using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的生产订单部分：只测纯逻辑（U8 原文截堆栈、数量小数位、拿不到 MoId 时的严格比对），不连库、不建 COM。
    internal static class MoCreateSelfTest
    {
        public static void Run()
        {
            CheckCutStack();
            CheckQtyDigits();
            CheckStrict();
        }

        static void CheckCutStack()
        {
            Expect("mo stack same line", "审核或关闭的生产订单不可删除!",
                MoApi.FirstLine("审核或关闭的生产订单不可删除!   在 UFSoft.U8.MO.BE.Order.Delete(String code)"));
            Expect("mo stack next line", "审核或关闭的生产订单不可删除!",
                MoApi.FirstLine("审核或关闭的生产订单不可删除!\r\n   在 UFSoft.U8.MO.BE.Order.Delete(String code)"));
            Expect("mo stack inner", "外层消息",
                MoApi.FirstLine("System.Exception: 外层消息 ---> 内层消息"));
            Expect("mo plain 在", "存货 A01 在 仓库 01 没有现存量",
                MoApi.FirstLine("存货 A01 在 仓库 01 没有现存量"));
            Expect("mo plain 在 ascii", "单据 在 U8 客户端已打开",
                MoApi.FirstLine("单据 在 U8 客户端已打开"));
        }

        static void CheckQtyDigits()
        {
            MoLine line = Line(12.5m);
            MoCreateSql.CheckQty(line, 2);
            bool refused = false;
            try
            {
                MoCreateSql.CheckQty(Line(1.125m), 2);
            }
            catch (BridgeException ex)
            {
                refused = ex.Status == 400;
            }
            if (!refused)
            {
                throw new InvalidOperationException("mo qty digits");
            }
        }

        static void CheckStrict()
        {
            MoCreateAsk ask = new MoCreateAsk();
            ask.QtyDigits = 2;
            ask.Lines.Add(Line(12.5m));
            if (!MoCreateLost.Strict(Many(Row("1", "12.500000", "")), ask))
            {
                throw new InvalidOperationException("mo lost strict");
            }
            if (MoCreateLost.Strict(Many(Row("0", "12.500000", "")), ask))
            {
                throw new InvalidOperationException("mo lost stale");
            }
            if (MoCreateLost.Strict(Many(Row("1", "12.510000", "")), ask))
            {
                throw new InvalidOperationException("mo lost qty");
            }
            // 没送仓库时不比仓库（U8 可能按存货默认仓库补上）；送了就必须一致。
            if (!MoCreateLost.Strict(Many(Row("1", "12.500000", "13")), ask))
            {
                throw new InvalidOperationException("mo lost wh default");
            }
            MoCreateAsk withWh = new MoCreateAsk();
            withWh.QtyDigits = 2;
            MoLine wh = Line(12.5m);
            wh.Wh = "12";
            withWh.Lines.Add(wh);
            if (MoCreateLost.Strict(Many(Row("1", "12.500000", "13")), withWh))
            {
                throw new InvalidOperationException("mo lost wh");
            }
            if (MoCreateLost.Strict(Many(Row("1", "12.500000", ""), Row("1", "1", "")), ask))
            {
                throw new InvalidOperationException("mo lost count");
            }
        }

        static MoLine Line(decimal qty)
        {
            MoLine line = new MoLine();
            line.Seq = 1;
            line.InvCode = "P001";
            line.Qty = qty;
            line.Start = "2026-09-28";
            line.Due = "2026-10-05";
            line.MoType = "1";
            line.Dept = "D01";
            line.Wh = "";
            line.Remark = "备注";
            return line;
        }

        static Dictionary<string, object> Row(string fresh, string qty, string wh)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["MoId"] = "1000000031";
            row["MoCode"] = "MO0001";
            row["fresh"] = fresh;
            row["SortSeq"] = "1";
            row["InvCode"] = "p001";
            row["Qty"] = qty;
            row["MDeptCode"] = "D01";
            row["MotypeCode"] = "1";
            row["WhCode"] = wh;
            row["Remark"] = "备注";
            row["StartDate"] = "2026-09-28";
            row["DueDate"] = "2026-10-05";
            return row;
        }

        static List<Dictionary<string, object>> Many(params Dictionary<string, object>[] rows)
        {
            return new List<Dictionary<string, object>>(rows);
        }

        static void Expect(string name, string want, string got)
        {
            if (want != got)
            {
                throw new InvalidOperationException(name + ": " + got);
            }
        }
    }
}
