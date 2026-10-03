using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 不良品处理单表体要由桥补的部门与计量：U8 参照生单不带这些列（AddVoucherByRef 只填表头）。
    internal sealed class QmRejUnit
    {
        public string Inv = "";
        public string Dep = "";
        public string Unit = "";
        public decimal Rate;
        public int NumDigits = 6;
    }

    // 取法照 U8 客户端保存的 QM06：表体部门 CDEPCODE = 检验单所属报检单的部门
    // （QMINSPECTVOUCHER.CDEPCODE；报检单读不到时用检验单的报检部门 CINSPECTDEPCODE）；
    // 件数 FNUM = 数量 / 检验单换算率 FCHANGRATE（检验单有辅计量单位 CUNITID 时），按账套件数小数位 iNumDecDgt 四舍五入；
    // 降级行的处理后单位 CDIMUNITID = 处理后存货的库存单位（cSTComUnitCode，没有时取主计量单位），换算率取 ComputationUnit，
    // FDIMNUM = 处理后数量 / 换算率。处理后存货无换算（iGroupType=0）时这三列不写（与测试账套 U8 保存的一样为空）。
    // U8 同存货降级的行用的也是存货的库存单位而不是检验单的辅单位，所以不沿用检验单的单位。
    internal static class QmRejUnits
    {
        const string InsDepSql = "select isnull(CDEPCODE,'') from QMINSPECTVOUCHER where ID=?";
        const string DigitsSql = "select cValue from AccInformation where cSysID='AA' and cName='iNumDecDgt'";
        const string DimSql = "select isnull(nullif(i.cSTComUnitCode,''), i.cComUnitCode) as Unit,"
            + " convert(varchar(5), isnull(i.iGroupType,0)) as GroupType, convert(varchar(40), isnull(u.iChangRate,0)) as Rate"
            + " from Inventory i left join ComputationUnit u on u.cComunitCode=isnull(nullif(i.cSTComUnitCode,''), i.cComUnitCode)"
            + " where i.cInvCode=?";
        // 扩展自定义项按模板字段名的原样大小写写（如 VT 355 / 356 是小写 chdefine15）。
        const string FieldSql = "select top 1 FieldName from voucheritems where VT_ID=? and CardSection='T' and lower(FieldName)=?";
        const int DigitsMax = 6;

        public static void Fill(object conn, QmRejJob job)
        {
            Dictionary<string, object> c = job.Check;
            QmRejUnit u = job.Unit;
            u.Inv = CoRows.Col(c, "CINVCODE");
            int inspectId = CoRows.AsId(CoRows.Col(c, "INSPECTID"));
            u.Dep = inspectId > 0 ? QmSql.Scalar(conn, InsDepSql, inspectId) : "";
            if (u.Dep.Length == 0)
            {
                u.Dep = CoRows.Col(c, "CINSPECTDEPCODE");
            }
            u.Unit = CoRows.Col(c, "CUNITID");
            u.Rate = QmSql.Dec(CoRows.Col(c, "Rate"));
            u.NumDigits = Digits(QmSql.Scalar(conn, DigitsSql));
            foreach (string key in job.Ask.Head.Keys)
            {
                if (key.StartsWith("chdefine", StringComparison.Ordinal))
                {
                    string name = QmSql.Scalar(conn, FieldSql, job.Spec.Vt, key);
                    job.DefineNames[key] = name.Length > 0 ? name : key;
                }
            }
        }

        // 降级行的处理后单位与换算率；存货无换算时留空。
        public static void Dim(object conn, QmRejLine line)
        {
            if (line.Flow != QmRejSrc.DimFlow || line.DimInv.Length == 0)
            {
                return;
            }
            Dictionary<string, object> row = QmSql.One(conn, DimSql, line.DimInv);
            if (row == null || CoRows.Col(row, "GroupType") == "0")
            {
                return;
            }
            line.DimUnit = CoRows.Col(row, "Unit");
            line.DimRate = QmSql.Dec(CoRows.Col(row, "Rate"));
        }

        // 件数 = 数量 / 换算率，按件数小数位四舍五入；没有单位或换算率不大于 0 时返回空串（不写）。
        internal static string Pieces(decimal qty, string unit, decimal rate, int digits)
        {
            if (unit == null || unit.Length == 0 || rate <= 0m)
            {
                return "";
            }
            return QmSql.Num(decimal.Round(qty / rate, digits, MidpointRounding.AwayFromZero));
        }

        internal static int Digits(string text)
        {
            int n;
            if (!int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out n) || n < 0 || n > DigitsMax)
            {
                return DigitsMax;
            }
            return n;
        }
    }
}
