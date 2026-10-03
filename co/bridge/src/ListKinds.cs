using System;
using System.Collections.Generic;

namespace U8Co
{
    // 单据列表的一种类型。列和表达式都来自 ListKinds 的常量表，调用方的文本只进参数。
    internal sealed class ListKind
    {
        public const int Name = 0;
        public const int Head = 1;
        public const int Id = 2;
        public const int Code = 3;
        public const int Date = 4;
        public const int Cus = 5;
        public const int Ven = 6;
        public const int Wh = 7;
        public const int Dep = 8;
        public const int Person = 9;
        public const int Maker = 10;
        public const int Verifier = 11;
        public const int VerifiedAt = 12;
        public const int Closer = 13;
        public const int ClosedExpr = 14;
        public const int VerifiedExpr = 15;
        public const int Red = 16;
        public const int Ufts = 17;
        public const int Body = 18;
        public const int BodyFk = 19;
        public const int BodyUfts = 20;
        public const int Cond = 21;
        public const int Extra = 22;
        public const int Apply = 23;
        public const int Width = 24;

        readonly string[] _cols;
        public readonly string[] ExtraKeys;

        public ListKind(string[] cols)
        {
            if (cols == null || cols.Length != Width)
            {
                throw new BridgeException(500, "internal", "列表类型定义无效");
            }
            _cols = cols;
            ExtraKeys = Aliases(cols[Extra]);
        }

        public string this[int index]
        {
            get { return _cols[index]; }
        }

        public bool Has(int index)
        {
            return _cols[index] != null;
        }

        public bool HasBodyUfts
        {
            get { return _cols[Body] != null; }
        }

        // 已审核：有覆盖表达式用它，否则按审核人非空。
        public string VerifiedSql()
        {
            if (_cols[VerifiedExpr] != null)
            {
                return _cols[VerifiedExpr];
            }
            return NonBlank(_cols[Verifier]);
        }

        // 已关闭：有覆盖表达式用它，否则按关闭人非空；两者都没有返回 null。
        public string ClosedSql()
        {
            if (_cols[ClosedExpr] != null)
            {
                return _cols[ClosedExpr];
            }
            if (_cols[Closer] == null)
            {
                return null;
            }
            return NonBlank(_cols[Closer]);
        }

        static string NonBlank(string column)
        {
            return "CASE WHEN NULLIF(LTRIM(RTRIM(" + column + ")), N'') IS NOT NULL THEN 1 ELSE 0 END";
        }

        // 附加列写成 "h.x AS a, h.y AS b"，片段里不含逗号；取出别名作为响应的固定键。
        static string[] Aliases(string extra)
        {
            if (extra == null || extra.Length == 0)
            {
                return new string[0];
            }
            string[] parts = extra.Split(new string[] { ", " }, StringSplitOptions.None);
            string[] keys = new string[parts.Length];
            for (int i = 0; i < parts.Length; i++)
            {
                int at = parts[i].LastIndexOf(" AS ", StringComparison.Ordinal);
                if (at < 0)
                {
                    throw new BridgeException(500, "internal", "列表附加列缺少别名");
                }
                keys[i] = parts[i].Substring(at + 4);
            }
            return keys;
        }
    }

    // 各类型的列映射，已在测试账套上逐条跑过。下标见 ListKind 常量；null 表示没有这一列。
    // 顺序：name, head, id, code, date, cus, ven, wh, dep, person, maker, verifier, verified_at, closer,
    // closed 覆盖, verified 覆盖, red, ufts, 表体(有 rowversion 才填), 表体外键, 表体 rowversion, 类型条件, 附加列, APPLY 覆盖。
    internal static partial class ListKinds
    {
        static readonly Dictionary<string, ListKind> ByName = Build();

        public static ListKind Find(string name)
        {
            ListKind kind;
            if (name != null && ByName.TryGetValue(name, out kind))
            {
                return kind;
            }
            return null;
        }

        public static IEnumerable<string> Names()
        {
            return ByName.Keys;
        }

        static Dictionary<string, ListKind> Build()
        {
            Dictionary<string, ListKind> map = new Dictionary<string, ListKind>(StringComparer.Ordinal);
            AddAll(map, SaRows());
            AddAll(map, PuRows());
            AddAll(map, StRows());
            AddAll(map, MoQmRows());
            AddAll(map, ArRows());
            // 请购单（ListKindsPuApp.cs）。
            AddAll(map, PuAppRows());
            // 物料清单（ListKindsBom.cs）。
            AddAll(map, BomRows());
            // 形态转换单、调拨申请单、盘点单（ListKindsStMisc.cs）。
            AddAll(map, StMiscRows());
            // 应收 / 应付票据（ListKindsNotes.cs），只读。
            AddAll(map, NoteRows());
            // 其他报检单、其他检验单（ListKindsQmOther.cs），只读。
            AddAll(map, QmOtherRows());
            // 采购结算单（ListKindsPuSettle.cs），只读。
            AddAll(map, PuSettleRows());
            // 出入库调整单、存货调价单（ListKindsIaSa.cs），只读。
            AddAll(map, IaSaRows());
            // 退货申请单（ListKindsReturnsApply.cs），只读。
            AddAll(map, ReturnsApplyRows());
            return map;
        }

        static void AddAll(Dictionary<string, ListKind> map, string[][] rows)
        {
            for (int i = 0; i < rows.Length; i++)
            {
                map.Add(rows[i][ListKind.Name], new ListKind(rows[i]));
            }
        }

        const string SaExtra = "h.cSTCode AS sale_type, h.cBusType AS bus_type, h.iswfcontrolled AS wf, "
            + "h.dcreatesystime AS created_at, h.dmodifysystime AS modified_at, h.cLocker AS locker";
        const string DlExtra = "h.cSTCode AS sale_type, h.cBusType AS bus_type, h.cSOCode AS so_code, "
            + "h.iswfcontrolled AS wf, h.dcreatesystime AS created_at, h.dmodifysystime AS modified_at";
        const string SbvExtra = "h.cVouchType AS vouch_type, h.cSource AS source, h.cDLCode AS dl_ref, "
            + "h.cSOCode AS so_code, h.cVerifier AS ar_verifier, h.dcreatesystime AS created_at, "
            + "h.dmodifysystime AS modified_at";

        static string[][] SaRows()
        {
            return new string[][]
            {
                new string[] { "sale_order", "SO_SOMain", "ID", "h.cSOCode", "h.dDate",
                    "h.cCusCode", null, null, "h.cDepCode", "h.cPersonCode", "h.cMaker",
                    "h.cVerifier", "h.dverifydate", "h.cCloser", null, null, "h.bReturnFlag",
                    "ufts", "SO_SODetails", "ID", "dufts", null, SaExtra, null },
                new string[] { "dispatch", "DispatchList", "DLID", "h.cDLCode", "h.dDate",
                    "h.cCusCode", null, null, "h.cDepCode", "h.cPersonCode", "h.cMaker",
                    "h.cVerifier", "h.dverifydate", "h.cCloser", null, null, "h.bReturnFlag",
                    "ufts", null, null, null, "h.cVouchType = N'05'", DlExtra, null },
                new string[] { "sale_invoice", "SaleBillVouch", "SBVID", "h.cSBVCode", "h.dDate",
                    "h.cCusCode", null, null, "h.cDepCode", "h.cPersonCode", "h.cMaker",
                    "h.cChecker", "h.dverifydate", null, null, null, "h.bReturnFlag",
                    "ufts", null, null, null, null, SbvExtra, null },
                // 退货单：同发货单表，只列红字。
                new string[] { "sale_return", "DispatchList", "DLID", "h.cDLCode", "h.dDate",
                    "h.cCusCode", null, null, "h.cDepCode", "h.cPersonCode", "h.cMaker",
                    "h.cVerifier", "h.dverifydate", "h.cCloser", null, null, "h.bReturnFlag",
                    "ufts", null, null, null, "h.cVouchType = N'05' AND h.bReturnFlag = 1", DlExtra, null }
            };
        }

        const string PoExtra = "h.cState AS po_state, h.cPTCode AS pt_code, h.cBusType AS bus_type, "
            + "h.IsWfControlled AS wf, h.cmaketime AS created_at, h.cModifyTime AS modified_at, h.cLocker AS locker";
        const string PoClosed = "CASE WHEN h.cState = 2 OR NULLIF(LTRIM(RTRIM(h.cCloser)), N'') IS NOT NULL "
            + "THEN 1 ELSE 0 END";
        const string ArrExtra = "h.cPTCode AS pt_code, h.cBusType AS bus_type, h.cpocode AS po_code, "
            + "h.IsWfControlled AS wf, h.cMakeTime AS created_at, h.cModifyTime AS modified_at";
        const string PbvExtra = "h.cPBVBillType AS bill_type, h.cPTCode AS pt_code, h.cBusType AS bus_type, "
            + "h.cSource AS source, h.cmaketime AS created_at, h.cmodifytime AS modified_at, "
            // 采购复核（同 verifier / verified_at）与应付审核人分开给出。
            + "h.cVerifier AS reviewer, h.cAuditDate AS reviewed_at, h.cPBVVerifier AS ap_verifier";

        static string[][] PuRows()
        {
            return new string[][]
            {
                new string[] { "purchase_order", "PO_Pomain", "POID", "h.cPOID", "h.dPODate",
                    null, "h.cVenCode", null, "h.cDepCode", "h.cPersonCode", "h.cMaker",
                    "h.cVerifier", "h.cAuditDate", "h.cCloser", PoClosed, null, null,
                    "ufts", "PO_Podetails", "POID", "dUfts", null, PoExtra, null },
                new string[] { "arrival", "PU_ArrivalVouch", "ID", "h.cCode", "h.dDate",
                    null, "h.cVenCode", null, "h.cDepCode", "h.cPersonCode", "h.cMaker",
                    "h.cverifier", "h.cAuditDate", "h.ccloser", null, null, "h.iBillType",
                    "ufts", null, null, null, null, ArrExtra, null },
                new string[] { "purchase_invoice", "PurBillVouch", "PBVID", "h.cPBVCode", "h.dPBVDate",
                    null, "h.cVenCode", null, "h.cDepCode", "h.cPersonCode", "h.cPBVMaker",
                    "h.cVerifier", "h.cAuditDate", null, null, null, null,
                    "ufts", null, null, null, null, PbvExtra, null },
                // 采购退货单（红字到货单，PuRet）：同到货单的表，只列 iBillType=1。
                new string[] { "purchase_return", "PU_ArrivalVouch", "ID", "h.cCode", "h.dDate",
                    null, "h.cVenCode", null, "h.cDepCode", "h.cPersonCode", "h.cMaker",
                    "h.cverifier", "h.cAuditDate", "h.ccloser", null, null, "h.bNegative",
                    "ufts", null, null, null, "h.iBillType = 1", ArrExtra, null }
            };
        }
    }
}
