using System;
using System.Collections;
using System.Data;
using System.Globalization;

namespace U8Co
{
    // 生产订单参照销售订单生成：照 U8 界面 MO05001 拼 MOMExecutor 的两个 Hashtable（纯数据，不连库、不碰 U8 组件）。
    // 列表参数同 MO05001._LoadData（只取销售订单、单号起止都是这张订单、日期范围用 U8 的系统最小 / 最大日期）；
    // 保存参数同 MO05001._Save：三层 Hashtable（单号组 → 行组 → 行），全部行放进同一个单号组，每行自成一个行组，
    // BF（BFBatchProcess_R.SaveMOGenQtyData）给组内第一行 Type 4、其余 Type 5，同一个 MoCode。
    internal static class MoGenArgs
    {
        public const string Table = "MOGenBaseData";
        // EnumMoGenQtyType.Hand（界面缺省，不按批量拆行）、EnumOrdAutoSelType.Doc（按单据）。
        const int GenQtyHand = 1;
        const int AutoSelDoc = 1;

        public static Hashtable ListArgs(MoGenAsk ask, DateTime min, DateTime max)
        {
            Hashtable h = new Hashtable();
            // OrderType 是 int[]，DAC 取 [0] 给 @OrderType_1、[1] 给 @OrderType_3；第二个 0 = 不取出口订单。
            h.Add("OrderType", new int[] { 1, 0, 0, 0 });
            h.Add("SOrderCode", ask.SoCode);
            h.Add("EOrderCode", ask.SoCode);
            h.Add("SExCode", "");
            h.Add("EExCode", "");
            h.Add("SDate", min);
            h.Add("EDate", max);
            h.Add("SCustCode", "");
            h.Add("ECustCode", "");
            h.Add("SInvCode", "");
            h.Add("EInvCode", "");
            h.Add("SInvFree", Blank10());
            h.Add("EInvFree", Blank10());
            h.Add("SSendDate", min);
            h.Add("ESendDate", max);
            h.Add("SDueDate", min);
            h.Add("EDueDate", max);
            h.Add("GenQtyType", GenQtyHand);
            // 「显示已完成」：已全部下达（可生单数量 ≤ 0）的行也列出，允许超计划下达时可再生单，不允许时由 Attach 按可生单数量拒绝。
            h.Add("IsShow", 1);
            h.Add("SDeCode", "");
            h.Add("EDeCode", "");
            h.Add("MoTypeCode", ask.MoType);
            h.Add("AddWhere", "1=1");
            h.Add("AutoSelType", AutoSelDoc);
            // MoPrev 只用于列表里的临时单号（%1 …）；送 U8 的单号在保存参数里另给。
            h.Add("MoPrev", "");
            return h;
        }

        static string[] Blank10()
        {
            string[] a = new string[10];
            for (int i = 0; i < a.Length; i++)
            {
                a[i] = "";
            }
            return a;
        }

        // 在 U8 列表里找到每一行（OrderType=1、OrderDId=iSOsID），可生单数量、缺省生产部门都取列表的值。
        public static void Attach(MoGenAsk ask, DataTable table)
        {
            for (int i = 0; i < ask.Lines.Count; i++)
            {
                MoGenLine line = ask.Lines[i];
                line.U8Row = Find(table, line.SoLineId);
                if (line.U8Row == null)
                {
                    throw Missing(ask, line);
                }
                // 允许超计划下达时 U8 界面和 BF、Usp_MO_GenMoFromSo 都不设上限（只要数量大于 0）。
                decimal left = Dec(line.U8Row["UnGenQty"]);
                if (!ask.OverPlan && line.Qty > left)
                {
                    throw MoGenSql.Mismatch(line, "超过可生单数量 " + left.ToString("0.######", CultureInfo.InvariantCulture));
                }
                line.FinalDept = line.Dept.Length > 0 ? line.Dept : Values.Text(line.U8Row["MDeptCode"]).Trim();
                if (line.FinalDept.Length == 0)
                {
                    throw MoGenSql.Bad(line, "必须指定生产部门");
                }
            }
        }

        // 其余列表条件桥都先查过（MoGenSql），剩下的只有 U8 列表按操作员的销售订单部门、生产订单类别、工厂数据权限过滤：
        // 受数据权限控制的操作员 403；不受控制（账套主管）时原因不明，409。
        static BridgeException Missing(MoGenAsk ask, MoGenLine line)
        {
            if (ask.DataAuth)
            {
                return new BridgeException(403, "no_permission", "第 " + line.SeqText
                    + " 行：不在 U8 生产订单生成列表中（销售订单部门 / 生产订单类别 / 工厂的数据权限）");
            }
            return MoGenSql.Mismatch(line, "不在 U8 生产订单生成列表中");
        }

        static DataRow Find(DataTable table, int soLineId)
        {
            if (table == null || !table.Columns.Contains("OrderDId") || !table.Columns.Contains("OrderType"))
            {
                return null;
            }
            foreach (DataRow row in table.Rows)
            {
                if (CoRows.AsId(row["OrderType"]) == 1 && CoRows.AsId(row["OrderDId"]) == soLineId)
                {
                    return row;
                }
            }
            return null;
        }

        public static Hashtable SaveArgs(MoGenAsk ask, MoGenWho who)
        {
            Hashtable group = new Hashtable();
            for (int i = 0; i < ask.Lines.Count; i++)
            {
                Hashtable lot = new Hashtable();
                lot.Add(0, Leaf(ask, ask.Lines[i], who));
                group.Add(i, lot);
            }
            Hashtable all = new Hashtable();
            all.Add(0, group);
            return all;
        }

        // 一行的键与 MO05001._Save 相同（不含 Type：BF 自己加）。MoCode 给了按手工编号（BF 用 ByHand），空串由 BF 按 MO21 自动编号。
        static Hashtable Leaf(MoGenAsk ask, MoGenLine line, MoGenWho who)
        {
            DataRow r = line.U8Row;
            Hashtable h = new Hashtable();
            h.Add("Id", CoRows.AsId(r["Id"]));
            h.Add("Acc_Id", who.AccId);
            h.Add("MoCode", ask.Code);
            h.Add("OrderDId", line.SoLineId);
            h.Add("OrderId", CoRows.AsId(r["OrderId"]));
            h.Add("CBomId", CoRows.AsId(r["CBomId"]));
            h.Add("InvCode", Values.Text(r["InvCode"]));
            for (int i = 1; i <= 10; i++)
            {
                h.Add("InvFree" + i.ToString(CultureInfo.InvariantCulture),
                    Values.Text(r["InvFree_" + i.ToString(CultureInfo.InvariantCulture)]));
            }
            h.Add("Qty", line.Qty);
            h.Add("DueDate", r["DueDate"]);
            h.Add("CreateUser", who.UserId);
            h.Add("CreateDate", who.OperDate);
            h.Add("Remark", ask.Remark ?? Values.Text(r["Remark"]));
            h.Add("OrderType", r["OrderType"]);
            // MParameters.STProxyWhFlag：U8 读完生产制造参数后恒为 false。
            h.Add("STProxyWhFlag", 0);
            h.Add("MoTypeCode", ask.MoType);
            // 类型照数据集列：RequisitionFlag bool、MoClass byte、GroupType short（列表 SQL 里 MoClass 恒为 1）。
            h.Add("RequisitionFlag", Or(r["RequisitionFlag"], false));
            h.Add("MoClass", Or(r["MoClass"], (byte)1));
            h.Add("From", "SA");
            h.Add("GroupType", Or(r["GroupType"], (short)0));
            AddAux(h, ask, line);
            h.Add("KeepQty", 0m);
            h.Add("KeepNum", 0m);
            object ufts = r["Ufts"];
            h.Add("Ufts", ufts is byte[] ? ufts : new byte[8]);
            h.Add("v_procmodule", 2);
            h.Add("v_MDeptCode", line.FinalDept);
            AddDefines(h, r);
            h.Add("GenQtyType", GenQtyHand);
            return h;
        }

        // 件数、换算率：数量等于列表缺省（可生单数量）时用列表的件数；改了数量按界面 __detailGenQty 的固定换算重算
        // 件数 = 数量 / 换算率（按件数小数位），没有换算率时件数为 0。
        static void AddAux(Hashtable h, MoGenAsk ask, MoGenLine line)
        {
            DataRow r = line.U8Row;
            object rate = r["GenChangeRate"];
            object aux = r["GenAuxQty"];
            if (line.Qty != Dec(r["GenQty"]))
            {
                decimal div = Dec(rate);
                aux = rate is DBNull || div == 0m ? (object)0m : decimal.Round(line.Qty / div, ask.AuxDigits);
            }
            decimal num = Dec(aux);
            h.Add("Num", num);
            h.Add("InvExchRate", Dec(rate));
        }

        static void AddDefines(Hashtable h, DataRow r)
        {
            for (int j = 1; j <= 16; j++)
            {
                h.Add("v_Define" + j.ToString(CultureInfo.InvariantCulture),
                    Values.Text(Cell(r, "Define_" + j.ToString(CultureInfo.InvariantCulture))));
            }
            for (int k = 22; k <= 37; k++)
            {
                h.Add("v_Define" + k.ToString(CultureInfo.InvariantCulture),
                    Values.Text(Cell(r, "DDefine_" + k.ToString(CultureInfo.InvariantCulture))));
            }
        }

        static object Cell(DataRow r, string column)
        {
            return r.Table.Columns.Contains(column) ? r[column] : null;
        }

        static object Or(object value, object fallback)
        {
            return value == null || value is DBNull ? fallback : value;
        }

        // 数据集的 decimal 列直接取；其他（字符串、空值）按 MoCreateSql.Dec 的不变区域解析。
        internal static decimal Dec(object value)
        {
            return value is decimal ? (decimal)value : MoCreateSql.Dec(Values.Text(value));
        }
    }

    // 送给 U8 的制单信息，取自 SetLoginInfo 之后的 UserContextGetter.U8LoginInfo（与 MO05001._Save 同源）。
    internal sealed class MoGenWho
    {
        public string AccId = "";
        public string UserId = "";
        public DateTime OperDate;
    }
}
