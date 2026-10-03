using System;
using System.Text;

namespace U8Co
{
    internal sealed class HeadState
    {
        public string Verifier;
        public string VerifiedAt;
        public string VerifyDate;
        public string VouchType;
        public bool Workflow;
        public bool Red;
        public bool First;
    }

    internal static class AdoXml
    {
        const int AdInteger = 3;
        const int AdVarWChar = 202;
        const int AdParamInput = 1;
        const int AdUseClient = 3;
        const int AdOpenStatic = 3;
        const int AdLockReadOnly = 1;
        const int AdPersistXml = 1;

        public static object Open(string connectionString)
        {
            object conn = ComUtil.Create("ADODB.Connection");
            if (conn == null)
            {
                throw new BridgeException(503, "com_unavailable", "ADODB 未注册");
            }
            try
            {
                ComUtil.Set(conn, "CommandTimeout", 45);
                ComUtil.Call(conn, "Open", new object[] { connectionString });
                return conn;
            }
            catch
            {
                ComUtil.Final(conn);
                throw;
            }
        }

        public static void Close(object conn)
        {
            if (conn == null)
            {
                return;
            }
            try
            {
                ComUtil.Call(conn, "Close", new object[0]);
            }
            catch (Exception)
            {
            }
            ComUtil.Final(conn);
        }

        public static string ConnectionString(object login, BridgeConfig cfg)
        {
            string raw = Values.Text(ComUtil.Get(login, "UfDbName"));
            return AdoCreds.Resolve(raw, cfg.SqlUser, cfg.SqlPassword);
        }

        public static string VoucherSql(object conn, bool saleOrder)
        {
            string card = saleOrder ? "17" : "01";
            string fields;
            string join;
            ReadExtend(conn, card, out fields, out join);
            if (saleOrder)
            {
                return "select '' as editprop, saleorderq.* " + fields
                    + " from SaleOrderQ saleorderq with(nolock) " + join
                    + " where saleorderq.id=?";
            }
            return "select '' as editprop, sales_fhd_t.* " + fields
                + " from Sales_FHD_T sales_fhd_t with(nolock) " + join
                + " where sales_fhd_t.dlid=?";
        }

        public static object LoadDom(object conn, string sql, int id)
        {
            object cmd = null;
            object rs = null;
            object dom = null;
            try
            {
                rs = OpenQuery(conn, sql, Arg("id", id, AdInteger, 4), out cmd);
                if (Eof(rs))
                {
                    throw new BridgeException(404, "not_found", "单据不存在");
                }
                dom = ComUtil.Create("MSXML2.DOMDocument");
                if (dom == null)
                {
                    throw new BridgeException(503, "com_unavailable", "MSXML 未注册");
                }
                ComUtil.Set(dom, "async", false);
                ComUtil.Call(rs, "Save", new object[] { dom, AdPersistXml });
                object keep = dom;
                dom = null;
                return keep;
            }
            finally
            {
                CloseRs(rs);
                ComUtil.Final(dom);
                ComUtil.Final(cmd);
            }
        }

        public static HeadState ReadHead(object conn, bool saleOrder, int id)
        {
            string sql = saleOrder
                ? "select cVerifier, dverifysystime, dverifydate, iswfcontrolled from SO_SOMain where ID=?"
                : "select cVerifier, dverifysystime, dverifydate, iswfcontrolled, bReturnFlag, cVouchType, bFirst from DispatchList where DLID=?";
            object cmd = null;
            object rs = null;
            try
            {
                rs = OpenQuery(conn, sql, Arg("id", id, AdInteger, 4), out cmd);
                if (Eof(rs))
                {
                    return null;
                }
                return FillHead(rs, !saleOrder);
            }
            finally
            {
                CloseRs(rs);
                ComUtil.Final(cmd);
            }
        }

        public static bool HasBizObject(object conn, string srcTable)
        {
            string sql = "select top 1 cBizObjectId from AuditBizObjects with(nolock) where srcTable=?";
            return RowOrUnknown(conn, sql, srcTable);
        }

        public static bool WorkflowReleased(object conn, string srcTable)
        {
            // 业务对象 ID 不写死，按 AuditBizObjects.srcTable 对到发布表。事件是对象 ID 加 .Submit。
            string sql = "select top 1 r.cBizObjectId from Table_WorkFlowRelease r with(nolock) "
                + "inner join AuditBizObjects o with(nolock) on o.cBizObjectId = r.cBizObjectId "
                + "where r.Status = 0 and o.srcTable = ? and r.cBizEventId = o.cBizObjectId + '.Submit'";
            return RowOrUnknown(conn, sql, srcTable);
        }

        public static int QueryInt(object conn, string sql)
        {
            object rs = null;
            object fields = null;
            object field = null;
            try
            {
                rs = ComUtil.Call(conn, "Execute", new object[] { sql });
                if (Eof(rs))
                {
                    throw new BridgeException(500, "internal", "查询没有结果");
                }
                fields = ComUtil.Get(rs, "Fields");
                field = ComUtil.Call(fields, "Item", new object[] { 0 });
                return Convert.ToInt32(ComUtil.Get(field, "Value"));
            }
            finally
            {
                ComUtil.ReleaseOne(field);
                ComUtil.ReleaseOne(fields);
                CloseRs(rs);
            }
        }

        static bool RowOrUnknown(object conn, string sql, string srcTable)
        {
            try
            {
                return HasRow(conn, sql, "src", srcTable, AdVarWChar, 128);
            }
            catch (Exception ex)
            {
                if (MissingTable(ex))
                {
                    throw new BridgeException(409, "workflow_unknown", "审批流表不可用");
                }
                throw;
            }
        }

        static HeadState FillHead(object rs, bool dispatch)
        {
            HeadState head = new HeadState();
            head.Verifier = Values.Text(Field(rs, "cVerifier")).Trim();
            head.VerifyDate = Values.Time(Field(rs, "dverifydate")).Trim();
            string at = Values.Time(Field(rs, "dverifysystime"));
            if (at.Length == 0)
            {
                at = head.VerifyDate;
            }
            head.VerifiedAt = at;
            head.Workflow = Values.Flag(Field(rs, "iswfcontrolled"));
            if (!dispatch)
            {
                return head;
            }
            head.Red = Values.Flag(Field(rs, "bReturnFlag"));
            head.VouchType = Values.Text(Field(rs, "cVouchType")).Trim();
            head.First = Values.Flag(Field(rs, "bFirst"));
            return head;
        }

        static void ReadExtend(object conn, string card, out string fields, out string join)
        {
            object cmd = null;
            object rs = null;
            StringBuilder fieldBuf = new StringBuilder();
            StringBuilder joinBuf = new StringBuilder();
            string sql = "select cextendfield, cextendjoin from voucherextendinfo with(nolock) "
                + "where cardnumber=? and cextendtype='T' and bextend='2'";
            try
            {
                rs = OpenQuery(conn, sql, Arg("card", card, AdVarWChar, 10), out cmd);
                while (!Eof(rs))
                {
                    AppendPart(fieldBuf, Safe(Values.Text(Field(rs, "cextendfield"))), true);
                    AppendPart(joinBuf, Safe(Values.Text(Field(rs, "cextendjoin"))), false);
                    ComUtil.Call(rs, "MoveNext", new object[0]);
                }
            }
            finally
            {
                CloseRs(rs);
                ComUtil.Final(cmd);
            }
            fields = fieldBuf.ToString();
            join = joinBuf.ToString();
        }

        static void AppendPart(StringBuilder buf, string text, bool field)
        {
            if (text.Length == 0)
            {
                return;
            }
            if (field && text[0] != ',')
            {
                text = ", " + text;
            }
            if (buf.Length > 0)
            {
                buf.Append(' ');
            }
            buf.Append(text);
        }

        // 扩展片段来自账套表，不是请求文本，但仍拒绝注释和分号，避免被拼进审核 SQL。
        static string Safe(string text)
        {
            string trimmed = text == null ? "" : text.Trim();
            if (trimmed.Length == 0)
            {
                return "";
            }
            if (trimmed.IndexOf(';') >= 0 || trimmed.IndexOf("--", StringComparison.Ordinal) >= 0
                || trimmed.IndexOf("/*", StringComparison.Ordinal) >= 0)
            {
                throw new BridgeException(500, "internal", "扩展字段含不允许的字符");
            }
            return trimmed;
        }

        static bool HasRow(object conn, string sql, string name, object value, int adoType, int size)
        {
            object cmd = null;
            object rs = null;
            try
            {
                rs = OpenQuery(conn, sql, Arg(name, value, adoType, size), out cmd);
                return !Eof(rs);
            }
            finally
            {
                CloseRs(rs);
                ComUtil.Final(cmd);
            }
        }

        sealed class QueryArg
        {
            public string Name;
            public object Value;
            public int AdoType;
            public int Size;
        }

        static QueryArg Arg(string name, object value, int adoType, int size)
        {
            QueryArg arg = new QueryArg();
            arg.Name = name;
            arg.Value = value;
            arg.AdoType = adoType;
            arg.Size = size;
            return arg;
        }

        static object OpenQuery(object conn, string sql, QueryArg arg, out object cmd)
        {
            cmd = null;
            cmd = ComUtil.Create("ADODB.Command");
            if (cmd == null)
            {
                throw new BridgeException(503, "com_unavailable", "ADODB 未注册");
            }
            object rs = null;
            try
            {
                ComUtil.Set(cmd, "ActiveConnection", conn);
                ComUtil.Set(cmd, "CommandText", sql);
                ComUtil.Set(cmd, "CommandTimeout", 45);
                AppendParam(cmd, arg.Name, arg.AdoType, arg.Size, arg.Value);
                rs = ComUtil.Create("ADODB.Recordset");
                if (rs == null)
                {
                    throw new BridgeException(503, "com_unavailable", "ADODB 未注册");
                }
                ComUtil.Set(rs, "CursorLocation", AdUseClient);
                ComUtil.Call(rs, "Open", new object[] { cmd, Type.Missing, AdOpenStatic, AdLockReadOnly, -1 });
                return rs;
            }
            catch
            {
                ComUtil.Final(rs);
                ComUtil.Final(cmd);
                cmd = null;
                throw;
            }
        }

        static void AppendParam(object cmd, string name, int adoType, int size, object value)
        {
            object param = ComUtil.Call(cmd, "CreateParameter", new object[] { name, adoType, AdParamInput, size, value });
            object parameters = null;
            try
            {
                parameters = ComUtil.Get(cmd, "Parameters");
                ComUtil.Call(parameters, "Append", new object[] { param });
            }
            finally
            {
                ComUtil.ReleaseOne(parameters);
                ComUtil.ReleaseOne(param);
            }
        }

        static object Field(object rs, string name)
        {
            object fields = null;
            object field = null;
            try
            {
                fields = ComUtil.Get(rs, "Fields");
                field = ComUtil.Call(fields, "Item", new object[] { name });
                return ComUtil.Get(field, "Value");
            }
            finally
            {
                ComUtil.ReleaseOne(field);
                ComUtil.ReleaseOne(fields);
            }
        }

        static bool Eof(object rs)
        {
            return Values.Flag(ComUtil.Get(rs, "EOF"));
        }

        static void CloseRs(object rs)
        {
            if (rs == null)
            {
                return;
            }
            try
            {
                ComUtil.Call(rs, "Close", new object[0]);
            }
            catch (Exception)
            {
            }
            ComUtil.Final(rs);
        }

        static bool MissingTable(Exception ex)
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
