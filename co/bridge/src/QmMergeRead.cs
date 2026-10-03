using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // vouchers/load 读合并检验的产品检验单（QM04，BMERGECHECKFLAG=1）时另附 merge_sources：每个合并来源一项，
    // source_line_id 即参照它生成产成品入库时的 source_line_id（QMMergeCheckDetail.AUTOID）。非合并检验不带这个键。
    // 只读 SQL，读线程池可用。
    internal static class QmMergeRead
    {
        const int Cap = 500;
        const string Sql = "select convert(varchar(20), m.AUTOID) as AutoId, isnull(o.MoCode, isnull(m.SOURCECODE,'')) as MoCode,"
            + " convert(varchar(20), isnull(d.SortSeq,0)) as MoSeq, convert(varchar(20), isnull(m.SOURCEAUTOID,0)) as MoDId,"
            + " isnull(m.CINSPECTCODE,'') as InspectCode, convert(varchar(40), isnull(m.FREGQUANTITY,0)) as RegQty,"
            + " convert(varchar(40), isnull(m.FCONQUANTIY,0)) as ConQty, convert(varchar(40), isnull(m.FSUMQUANTITY,0)) as SumQty,"
            + " convert(varchar(5), isnull(m.BPROINFLAG,0)) as InDone"
            + " from QMMergeCheckDetail m left join mom_orderdetail d on d.MoDId=m.SOURCEAUTOID"
            + " left join mom_order o on o.MoId=d.MoId where m.ID=? order by m.AUTOID";

        public static void Attach(object conn, VoucherKind kind, int id, Dictionary<string, object> head, Dictionary<string, object> body)
        {
            if (kind == null || kind.Name != "qm_product_check" || !CoRows.FlagOf(head, "BMERGECHECKFLAG"))
            {
                return;
            }
            List<Dictionary<string, object>> rows = Rows.Query(conn, Sql, new object[] { id }, Cap);
            List<object> list = new List<object>();
            for (int i = 0; rows != null && i < rows.Count; i++)
            {
                list.Add(Source(rows[i]));
            }
            body["merge_sources"] = list;
        }

        static Dictionary<string, object> Source(Dictionary<string, object> row)
        {
            decimal reg = Dec(row, "RegQty");
            decimal con = Dec(row, "ConQty");
            decimal sum = Dec(row, "SumQty");
            decimal left = reg + con - sum;
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["source_line_id"] = CoRows.AsId(CoRows.Col(row, "AutoId"));
            item["mo_code"] = CoRows.Col(row, "MoCode");
            item["mo_seq"] = CoRows.AsId(CoRows.Col(row, "MoSeq"));
            item["mo_detail_id"] = CoRows.AsId(CoRows.Col(row, "MoDId"));
            item["inspect_code"] = CoRows.Col(row, "InspectCode");
            item["qualified"] = reg;
            item["concession"] = con;
            item["stocked"] = sum;
            item["remaining"] = left > 0m ? left : 0m;
            item["done"] = CoRows.Col(row, "InDone") == "1";
            return item;
        }

        static decimal Dec(Dictionary<string, object> row, string name)
        {
            decimal value;
            if (!decimal.TryParse(CoRows.Col(row, name), NumberStyles.Number, CultureInfo.InvariantCulture, out value))
            {
                return 0m;
            }
            return value;
        }
    }
}
