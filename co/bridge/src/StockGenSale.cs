using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 销售出库参照发货单。
    internal static partial class StockGen
    {

        static void RefuseDispatch(object conn, int id)
        {
            Dictionary<string, object> row = Rows.One(conn, DispSql, new object[] { id });
            if (row == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (CoRows.Col(row, "cVouchType") != "05" || CoRows.FlagOf(row, "bReturnFlag"))
            {
                throw new BridgeException(400, "bad_request", "仅支持蓝字发货单");
            }
            if (CoRows.Col(row, "cVerifier").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            if (CoRows.Col(row, "cCloser").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            decimal left;
            if (!StockUnits.Dec(Rows.Scalar(conn, RemainSql, new object[] { id }), out left) || left <= 0m)
            {
                throw new BridgeException(409, "state_mismatch", "没有可出库数量");
            }
        }

        static void RefuseHead(Dictionary<string, object> head)
        {
            if (head == null)
            {
                return;
            }
            foreach (KeyValuePair<string, object> kv in head)
            {
                throw BridgeException.BadField(FieldPath.Join("head", kv.Key), "不能设置字段 " + kv.Key);
            }
        }

        static int MaxOut(object conn, VoucherKind kind, int dlid)
        {
            string sql = "select max(" + kind.IdColumn + ") from " + kind.HeadTable + " where cDLCode=?";
            return ReadId(conn, sql, dlid.ToString(CultureInfo.InvariantCulture));
        }

        // MakeOutVouch 之后只认比调用前 max(ID) 更大的行。跨仓库时一张发货单会出多张出库单。
        static List<int> Newer(WorkContext ctx, VoucherKind kind, int dlid, int floor)
        {
            try
            {
                string key = dlid.ToString(CultureInfo.InvariantCulture);
                List<int> ids = ListOut(ctx.Conn, kind, key, floor);
                if (ids.Count == 0)
                {
                    ids = ListOutFresh(ctx, kind, key, floor);
                }
                return ids;
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "SaleOut " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "已保存但未能确定单据标识");
            }
        }

        // 预演：MakeOutVouch 之后、提交前在请求连接上登记本次生成的出库单（比调用前 max(ID) 大的）和来源发货单。
        // 只为预览而读：读失败不让预演失败，记 generated_unknown 后照常走到提交钩子（回滚）。
        static StockCheck DrySaleOut(VoucherKind kind, int dlid, int floor)
        {
            return delegate(object conn)
            {
                DryRun.Touched(Kinds.Find("dispatch"), dlid);
                List<int> ids;
                try
                {
                    ids = ListOut(conn, kind, dlid.ToString(CultureInfo.InvariantCulture), floor);
                }
                catch (DryRunDone)
                {
                    throw;
                }
                catch (Exception)
                {
                    DryRun.Set("generated_unknown", true);
                    return;
                }
                for (int i = 0; i < ids.Count; i++)
                {
                    DryRun.Created(kind, ids[i]);
                }
            };
        }

        static List<int> ListOutFresh(WorkContext ctx, VoucherKind kind, string key, int floor)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                return ListOut(conn, kind, key, floor);
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static List<int> ListOut(object conn, VoucherKind kind, string key, int floor)
        {
            string sql = "select " + kind.IdColumn + " from " + kind.HeadTable
                + " where cDLCode=? and " + kind.IdColumn + ">? order by " + kind.IdColumn;
            List<Dictionary<string, object>> rows = Rows.Query(conn, sql, new object[] { key, floor }, 1000);
            List<int> ids = new List<int>();
            for (int i = 0; i < rows.Count; i++)
            {
                int id = FirstId(rows[i]);
                if (id > floor)
                {
                    ids.Add(id);
                }
            }
            return ids;
        }

        static int FirstId(Dictionary<string, object> row)
        {
            if (row == null)
            {
                return 0;
            }
            foreach (object value in row.Values)
            {
                return CoRows.AsId(value);
            }
            return 0;
        }

        static int[] CopyIds(List<int> ids)
        {
            int[] all = new int[ids.Count];
            for (int i = 0; i < ids.Count; i++)
            {
                all[i] = ids[i];
            }
            return all;
        }

    }
}
