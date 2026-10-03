using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    internal static class CoRows
    {
        public static Dictionary<string, object> HeadRow(object conn, VoucherKind kind, int id)
        {
            string sql = "select "
                + Ident(kind.VerifierColumn) + " as verifier, "
                + Ident(kind.VerifyDateColumn) + " as verify_date, "
                + Ident(kind.CodeColumn) + " as code, "
                + Extra(kind)
                + " from " + Ident(kind.HeadTable)
                + " where " + Ident(kind.IdColumn) + "=?";
            return Rows.One(conn, sql, new object[] { id });
        }

        static string Extra(VoucherKind kind)
        {
            if (kind.Name == "sale_order")
            {
                return "dverifysystime as verify_sys, cCloser as closer, iswfcontrolled as wf, iverifystate as vstate";
            }
            // 退货单（sale_return）与发货单同表。
            if (DispatchTable(kind.Name))
            {
                return "dverifysystime as verify_sys, cCloser as closer, iswfcontrolled as wf";
            }
            if (kind.Name == "purchase_order")
            {
                return "cCloser as closer, cState as cstate, IsWfControlled as wf";
            }
            // 采购退货单（purchase_return，PuRet）与到货单同表。
            if (ArrivalTable(kind.Name))
            {
                return "ccloser as closer, iBillType as bill_type, IsWfControlled as wf";
            }
            if (kind.Name == "sale_invoice")
            {
                return "cVerifier as ar_verifier, iswfcontrolled as wf, cVouchType as vouch_type, bReturnFlag as red";
            }
            if (kind.Name == "transfer")
            {
                return "iswfcontrolled as wf, cSource as source";
            }
            if (kind.Name == "purchase_invoice")
            {
                return "cPBVBillType as bill_type";
            }
            if (StockSource(kind.Name))
            {
                return "cSource as source, cBusType as bus_type";
            }
            return ExtraMore(kind);
        }

        static string ExtraMore(VoucherKind kind)
        {
            if (kind.Family == "ar")
            {
                // Ap_CloseBill / Ap_Vouch 同名列。按 flag / vouch_type 核对类型。
                return "dverifysystime as verify_sys, cFlag as flag, cVouchType as vouch_type, IsWfControlled as wf";
            }
            // 形态转换单、调拨申请单、盘点单（StockMisc）：修改后的回读（EditMsg.Saved）。
            if (StockMisc.Handles(kind))
            {
                return "iswfcontrolled as wf, csource as source";
            }
            throw new BridgeException(400, "bad_request", "该单据类型不支持");
        }

        static bool DispatchTable(string name)
        {
            return name == "dispatch" || name == "sale_return";
        }

        static bool ArrivalTable(string name)
        {
            return name == "arrival" || name == "purchase_return";
        }

        static bool StockSource(string name)
        {
            return name == "purchase_in" || name == "sale_out" || name == "other_in"
                || name == "other_out" || name == "product_in" || name == "material_out";
        }

        public static string Ident(string name)
        {
            if (name == null || name.Length == 0 || name.Length > 40 || !Letters(name))
            {
                throw new BridgeException(500, "internal", "单据类型配置无效");
            }
            return name;
        }

        static bool Letters(string name)
        {
            for (int i = 0; i < name.Length; i++)
            {
                if (!Letter(name[i]))
                {
                    return false;
                }
            }
            return true;
        }

        static bool Letter(char c)
        {
            if (c >= 'A' && c <= 'Z')
            {
                return true;
            }
            if (c >= 'a' && c <= 'z')
            {
                return true;
            }
            if (c >= '0' && c <= '9' || c == '_')
            {
                return true;
            }
            return false;
        }

        public static ApiResult Pack(VoucherKind kind, int id, Dictionary<string, object> snap, object[] doms)
        {
            Dictionary<string, object> head = RequireHead(doms[0], kind.IdColumn, id);
            bool truncated;
            List<Dictionary<string, object>> lines = TakeLines(Rows.FromDom(doms[1], 501), out truncated);
            string code = Col(snap, "code");
            if (code.Length == 0)
            {
                code = Col(head, kind.CodeColumn);
            }
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["id"] = id;
            body["code"] = code;
            body["head"] = Strip(head);
            body["lines"] = lines;
            body["state"] = StateOf(snap);
            if (truncated)
            {
                body["lines_truncated"] = true;
            }
            return ApiResult.Ok(body);
        }

        // 表头必须恰好一行，且 ID / POID / DLID（kind.IdColumn）等于请求的 id。属性名不区分大小写。
        public static Dictionary<string, object> RequireHead(object dom, string idColumn, int id)
        {
            List<Dictionary<string, object>> heads = Rows.FromDom(dom, 2);
            if (heads == null || heads.Count != 1)
            {
                throw new BridgeException(409, "state_mismatch", "U8 返回的表头行数不是 1");
            }
            if (!SameId(Col(heads[0], idColumn), id))
            {
                throw new BridgeException(409, "state_mismatch", "单据标识与请求不一致");
            }
            return heads[0];
        }

        static bool SameId(string text, int id)
        {
            int got;
            if (int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out got))
            {
                return got == id;
            }
            decimal num;
            if (!decimal.TryParse(text, NumberStyles.Number, CultureInfo.InvariantCulture, out num))
            {
                return false;
            }
            return num == id;
        }

        public static int AsId(object value)
        {
            if (value == null || value is DBNull || value == Type.Missing)
            {
                return 0;
            }
            int id;
            string text = Convert.ToString(value, CultureInfo.InvariantCulture).Trim();
            if (!int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out id) || id <= 0)
            {
                return 0;
            }
            return id;
        }

        public static Dictionary<string, object> StateOf(Dictionary<string, object> row)
        {
            string verifier = Col(row, "verifier");
            string date = Col(row, "verify_date");
            string at = Col(row, "verify_sys");
            if (at.Length == 0)
            {
                at = date;
            }
            bool verified = verifier.Length > 0 && date.Length > 0;
            Dictionary<string, object> state = new Dictionary<string, object>();
            state["verified"] = verified;
            state["verifier"] = verifier;
            state["verified_at"] = verified ? at : "";
            return state;
        }

        static List<Dictionary<string, object>> TakeLines(List<Dictionary<string, object>> rows, out bool truncated)
        {
            truncated = false;
            List<Dictionary<string, object>> list = new List<Dictionary<string, object>>();
            if (rows == null)
            {
                return list;
            }
            int n = rows.Count;
            if (n > 500)
            {
                truncated = true;
                n = 500;
            }
            for (int i = 0; i < n; i++)
            {
                list.Add(Strip(rows[i]));
            }
            return list;
        }

        static Dictionary<string, object> Strip(Dictionary<string, object> row)
        {
            Dictionary<string, object> copy = new Dictionary<string, object>();
            if (row == null)
            {
                return copy;
            }
            foreach (KeyValuePair<string, object> pair in row)
            {
                if (pair.Value == null || pair.Value is DBNull || Secret(pair.Key))
                {
                    continue;
                }
                copy[pair.Key] = Show(pair.Value);
            }
            return copy;
        }

        static bool Secret(string name)
        {
            string lower = name == null ? "" : name.ToLowerInvariant();
            if (lower.IndexOf("password", StringComparison.Ordinal) >= 0)
            {
                return true;
            }
            return lower.IndexOf("pwd", StringComparison.Ordinal) >= 0;
        }

        static object Show(object value)
        {
            if (value is string)
            {
                return value;
            }
            if (value is DateTime)
            {
                DateTime at = (DateTime)value;
                string pattern = at.TimeOfDay.Ticks == 0 ? "yyyy-MM-dd" : "yyyy-MM-dd HH:mm:ss";
                return at.ToString(pattern, CultureInfo.InvariantCulture);
            }
            IFormattable fmt = value as IFormattable;
            if (fmt != null)
            {
                return fmt.ToString(null, CultureInfo.InvariantCulture);
            }
            return Convert.ToString(value);
        }

        public static string Col(Dictionary<string, object> row, string name)
        {
            return Values.Text(Raw(row, name)).Trim();
        }

        public static bool FlagOf(Dictionary<string, object> row, string name)
        {
            return Values.Flag(Raw(row, name));
        }

        static object Raw(Dictionary<string, object> row, string name)
        {
            if (row == null || name == null)
            {
                return null;
            }
            object value;
            if (row.TryGetValue(name, out value))
            {
                return value;
            }
            foreach (KeyValuePair<string, object> pair in row)
            {
                if (string.Equals(pair.Key, name, StringComparison.OrdinalIgnoreCase))
                {
                    return pair.Value;
                }
            }
            return null;
        }

        // 审计补充只留末尾 300 字。库存、销售、采购都从这里追加。
        public static void Note(WorkItem item, string text)
        {
            if (item == null || text == null || text.Length == 0)
            {
                return;
            }
            string cur = item.Detail ?? "";
            string next = cur.Length == 0 ? text : cur + "; " + text;
            if (next.Length > 300)
            {
                next = next.Substring(next.Length - 300, 300);
            }
            item.Detail = next;
        }

        public static void Swap(object[] doms, int index, object updated)
        {
            if (updated == null || updated is DBNull || object.ReferenceEquals(doms[index], updated))
            {
                return;
            }
            ComUtil.Final(doms[index]);
            doms[index] = updated;
        }

        // by-ref 登录槽返回后调用：U8 换了登录对象时本次登录不放回缓存（ctx.DropLogin，LoginCache），再放掉换来的对象。
        public static void LoginBack(WorkContext ctx, object original, object returned)
        {
            if (returned != null && !(returned is DBNull) && !object.ReferenceEquals(original, returned))
            {
                ctx.DropLogin();
            }
            ReleaseIfNew(original, returned);
        }

        public static void ReleaseIfNew(object original, object updated)
        {
            if (updated == null || updated is DBNull || object.ReferenceEquals(original, updated))
            {
                return;
            }
            ComUtil.ReleaseOne(updated);
        }

        public static void CatchTran(object conn, WorkItem item, bool open)
        {
            if (item.TranAfter == null)
            {
                try
                {
                    item.TranAfter = CoTrans.Count(conn);
                }
                catch (Exception)
                {
                }
            }
            if (!open)
            {
                return;
            }
            try
            {
                CoTrans.Rollback(conn);
            }
            catch (Exception)
            {
            }
        }

        public static bool MissingTable(Exception ex)
        {
            string text = ex == null ? "" : ex.ToString();
            if (text.IndexOf("Invalid object name", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                return true;
            }
            return text.IndexOf("对象名", StringComparison.Ordinal) >= 0
                && text.IndexOf("无效", StringComparison.Ordinal) >= 0;
        }
    }
}
