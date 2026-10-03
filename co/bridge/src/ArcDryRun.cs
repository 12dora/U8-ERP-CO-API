using System;
using System.Collections.Generic;

namespace U8Co
{
    // 档案写入的预演。EAI / U8PzInsert / U8CRMEAINew 的 Transact 自己提交：检查做完后在调用前停下（validate）。
    // 桥自己的 SQL（项目、客户供应商银行、币种和凭证类别的修改删除）照常执行，提交钩子回滚（rollback），这里只交 detail。
    internal static class ArcDryRun
    {
        // validate：调用自提交组件之前。what 写组件名，如 U8SrvTrans.IClsCommon.Transact。
        public static void Stop(WorkContext ctx, string archive, string code, string op, string what)
        {
            if (!DryRun.Active)
            {
                return;
            }
            DryRun.Set("archive", Head(archive, code, op));
            DryRun.Stop(ctx, what);
        }

        // rollback：提交之前交出事务内读到的行（after 可为 null；删除时传 null 并记 deleted）。
        public static void Set(string archive, string code, string op, object after)
        {
            if (!DryRun.Active)
            {
                return;
            }
            Dictionary<string, object> head = Head(archive, code, op);
            if (op == "delete")
            {
                head["deleted"] = true;
            }
            else if (after != null)
            {
                head["after"] = after;
            }
            DryRun.Set("archive", head);
        }

        // 请求连接上（事务内）读一行档案，读不到或出错都回 null：预演的附加信息，不影响写入。
        public static object Row(object conn, ArcKind kind, string code)
        {
            if (!DryRun.Active)
            {
                return null;
            }
            try
            {
                return ArcRead.Row(conn, kind, code);
            }
            catch (DryRunDone)
            {
                throw;
            }
            catch (Exception)
            {
                // 只是预演的附加信息：读失败不影响预演，detail 里不带 after，另记 preview_error。
                DryRun.Set("preview_error", true);
                return null;
            }
        }

        // 项目档案（fitemss<大类>）：事务内读到的行交 detail。
        public static void Project(object conn, ArcReq req, string table, string item)
        {
            if (!DryRun.Active)
            {
                return;
            }
            object after = null;
            if (req.Op != "delete")
            {
                try
                {
                    after = ArcGl.Texts(ArcProjectSql.Read(conn, table, item));
                }
                catch (DryRunDone)
                {
                    throw;
                }
                catch (Exception)
                {
                    DryRun.Set("preview_error", true);
                    after = null;
                }
            }
            Set(req.Kind.Name, req.Code, req.Op, after);
        }

        static Dictionary<string, object> Head(string archive, string code, string op)
        {
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["archive"] = archive;
            head["code"] = code;
            head["op"] = op;
            return head;
        }
    }
}
