using System;
using System.Collections.Generic;

namespace U8Co
{
    // 成本核算期间数据 ca_inputs 里的一张表：请求里的名称、U8 表、成本对象的形状、取数的时间条件、要输出的列和读取要的功能 id。
    internal sealed class CaTable
    {
        // 形状：Object 表上有 cPPID（成本核算对象，经 Caq_CoReal 解析成本中心、存货、生产订单）；
        // Base 表上的 cPPID 是基础成本核算对象（Caq_CoBase.iBaseCOID，产品 × 成本中心，没有生产订单）；
        // Center 表上有 cDeptID（成本中心编码，名称取成本中心档案）；Item 在 Center 上再有 cAmoID（成本项目，名称取成本项目定义）。
        public const string Object = "object";
        public const string Base = "base";
        public const string Center = "center";
        public const string Item = "item";

        // 时间条件：Period 是 iPeriod = YYYYMM；Date 是 dDate 落在该月；Year 是 iYear = 会计年度（与期间无关）。
        public const string Period = "period";
        public const string Date = "date";
        public const string Year = "year";

        public readonly string Name;
        public readonly string Title;
        public readonly string Table;
        public readonly string Shape;
        public readonly string Time;
        public readonly string[] Auths;
        // 要输出的数值列；null 表示除键列外的全部数值列。
        public string[] Measures;
        // 要输出的文本列（空串不输出）。
        public string[] Texts = new string[0];
        // 表上区分行的类型列（工时类型），输出为 type；没有为 null。
        public string TypeCol;
        public string Order;

        public CaTable(string name, string title, string table, string shape, string time, string[] auths)
        {
            Name = name;
            Title = title;
            Table = table;
            Shape = shape;
            Time = time;
            Auths = auths;
        }

        // 表上有 cPPID（成本核算对象或基础对象），行上解析存货、按存货数据权限过滤。
        public bool HasObject
        {
            get { return Shape == Object || Shape == Base; }
        }

        public bool IsBase
        {
            get { return Shape == Base; }
        }

        public bool HasCenter
        {
            get { return Shape == Center || Shape == Item; }
        }
    }

    // ca_inputs 能读的成本核算表。名称与 U8 的表标题一致（CA_DirMA 是人工费用，CA_ManCt 是制造费用，CA_DepCs 是折旧费用）。
    // 功能 id 取自成本核算的录入窗体菜单（CBGL caconfig.xml 的 ca0302…ca0315，人工费用 / 折旧费用是 Uscbhs 的窗体）：
    // 人工费用 CA0303、折旧费用 CA0304、制造费用 CA0305、工时日报 CA0308、完工产量日报 CA0309、在产品约当 CA0314、自定义分配项目 CA0315。
    internal static class ReportsCaInputsDef
    {
        internal static readonly CaTable[] All = Build();

        static CaTable[] Build()
        {
            return new CaTable[]
            {
                new CaTable("work_hours", "工时统计", "CA_TimSt", CaTable.Object, CaTable.Period, A("CA0308"))
                {
                    Measures = new string[] { "iRealWkt", "iOnpWkt" }, TypeCol = "cRealWktType",
                    Order = "t.cPPID, t.cBatch, t.cRealWktType"
                },
                new CaTable("daily_hours", "工时日报", "CA_DayTiS", CaTable.Object, CaTable.Date, A("CA0308"))
                {
                    Measures = new string[] { "iRealWkt", "iOnpWkt" }, TypeCol = "cRealWktType",
                    Order = "t.dDate, t.cPPID, t.cBatch, t.cRealWktType"
                },
                new CaTable("labor", "人工费用", "CA_DirMA", CaTable.Center, CaTable.Period, A("CA0303"))
                {
                    Measures = new string[] { "iDirectPay", "iInDirectPay" }, Order = "t.cDeptID"
                },
                new CaTable("overhead", "制造费用", "CA_ManCt", CaTable.Item, CaTable.Period, A("CA0305"))
                {
                    Measures = new string[] { "imanAmo" }, Order = "t.cDeptID, " + ReportsCaInputsSql.NumericOrder("t.cAmoID") + ", t.cAmoID"
                },
                new CaTable("depreciation", "折旧费用", "CA_DepCs", CaTable.Center, CaTable.Period, A("CA0304"))
                {
                    Measures = new string[] { "iDepreciation" }, Texts = new string[] { "cFrom" }, Order = "t.cDeptID"
                },
                new CaTable("wip_fixed", "在产品固定约当", "CA_OnPQu", CaTable.Base, CaTable.Year, A("CA0314"))
                {
                    Measures = new string[] { "iQuot", "iQuotMat", "iQuotDirm", "iQuotAux", "iQuotMau", "iQuotOth" },
                    Order = "t.cPPID"
                },
                new CaTable("wip_variable", "在产品变动约当", "CA_OnPCE", CaTable.Object, CaTable.Period, A("CA0314"))
                {
                    Measures = new string[] { "iOnPApp", "iOnPAppMat", "iOnPAppDirm", "iOnPAppAux", "iOnPAppMau", "iOnPAppOth" },
                    Order = "t.cPPID, t.cBatch"
                },
                new CaTable("distr_input", "自定义分配项目", "CA_DistrInput", CaTable.Object, CaTable.Period, A("CA0315"))
                {
                    Order = "t.cPPID, t.cBatch"
                },
                new CaTable("product_qty", "完工产品产量统计", "CA_ComPS", CaTable.Object, CaTable.Period, A("CA0309"))
                {
                    Measures = new string[] { "iQua", "iWasteQua" }, Order = "t.cPPID, t.cBatch"
                }
            };
        }

        static string[] A(params string[] ids)
        {
            return ids;
        }

        internal static CaTable Find(string name)
        {
            for (int i = 0; i < All.Length; i++)
            {
                if (All[i].Name == name)
                {
                    return All[i];
                }
            }
            return null;
        }

        // 请求里的表名（已校验过、已去重）→ 表定义，按名单顺序；没给表名就是全部。
        internal static List<CaTable> Select(string[] names)
        {
            List<CaTable> list = new List<CaTable>();
            for (int i = 0; i < All.Length; i++)
            {
                if (names.Length == 0 || Array.IndexOf(names, All[i].Name) >= 0)
                {
                    list.Add(All[i]);
                }
            }
            return list;
        }

        // 各表功能 id 的并集：report:ca_inputs 的路由规则，至少要能读其中一张。
        internal static string[] AllAuths()
        {
            List<string> ids = new List<string>();
            for (int i = 0; i < All.Length; i++)
            {
                for (int k = 0; k < All[i].Auths.Length; k++)
                {
                    if (!ids.Contains(All[i].Auths[k]))
                    {
                        ids.Add(All[i].Auths[k]);
                    }
                }
            }
            return ids.ToArray();
        }
    }
}
