using System;
using System.Collections.Generic;

namespace U8Co
{
    internal static class QmRead
    {
        const int LineCap = 500;

        public static ApiResult Load(WorkContext ctx, VoucherKind kind, int id)
        {
            Require(kind);
            // 报检单（QM01 / QM02）只读、没有审批流，列取固定集合（QmInspect）；检验单、不良品处理单照旧。
            if (QmInspect.Handles(kind))
            {
                return QmInspect.Load(ctx, kind, id);
            }
            Dictionary<string, object> head = ReadHead(ctx, kind, id);
            bool truncated;
            List<Dictionary<string, object>> lines = ReadLines(ctx, kind, id, out truncated);
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["id"] = id;
            body["code"] = WfText.Cell(head, kind.CodeColumn).Trim();
            body["head"] = head;
            body["lines"] = lines;
            body["state"] = StateOf(head, kind);
            if (truncated) body["lines_truncated"] = true;
            // 合并检验的产品检验单另附 merge_sources（参照它生成产成品入库用）。
            QmMergeRead.Attach(ctx.Conn, kind, id, head, body);
            // 其他检验单（QM15）不接审批流，不附 wf。
            if (kind.Workflow)
            {
                body["wf"] = WfState.Describe(ctx, kind, id);
            }
            return ApiResult.Ok(body);
        }

        static void Require(VoucherKind kind)
        {
            if (kind == null || !string.Equals(kind.Family, "qm", StringComparison.OrdinalIgnoreCase))
            {
                throw new BridgeException(400, "bad_request", "该单据类型不是检验单");
            }
        }

        static Dictionary<string, object> ReadHead(WorkContext ctx, VoucherKind kind, int id)
        {
            string sql = "SELECT * FROM " + kind.HeadTable
                + " WHERE " + kind.IdColumn + "=? AND CVOUCHTYPE=?";
            Dictionary<string, object> head = Rows.One(ctx.Conn, sql, new object[] { id, kind.BizObjectId });
            if (head == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            Strip(head);
            return head;
        }

        static List<Dictionary<string, object>> ReadLines(WorkContext ctx, VoucherKind kind, int id, out bool truncated)
        {
            string sql = "SELECT * FROM " + kind.BodyTable + " WHERE " + kind.BodyFk + "=? ORDER BY AUTOID";
            List<Dictionary<string, object>> lines = Rows.Query(ctx.Conn, sql, new object[] { id }, LineCap + 1);
            if (lines == null) lines = new List<Dictionary<string, object>>();
            truncated = lines.Count > LineCap;
            while (lines.Count > LineCap)
            {
                lines.RemoveAt(lines.Count - 1);
            }
            for (int i = 0; i < lines.Count; i++)
            {
                Strip(lines[i]);
            }
            return lines;
        }

        static Dictionary<string, object> StateOf(Dictionary<string, object> head, VoucherKind kind)
        {
            string verifier = WfText.Cell(head, kind.VerifierColumn).Trim();
            string at = WfText.Cell(head, kind.VerifyDateColumn).Trim();
            Dictionary<string, object> state = new Dictionary<string, object>();
            state["verified"] = verifier.Length > 0 && at.Length > 0;
            state["verifier"] = verifier;
            state["verified_at"] = at;
            return state;
        }

        static void Strip(Dictionary<string, object> row)
        {
            if (row == null) return;
            List<string> drop = new List<string>();
            foreach (string key in row.Keys)
            {
                string low = key.ToLowerInvariant();
                if (low.IndexOf("password", StringComparison.Ordinal) >= 0
                    || low.IndexOf("pwd", StringComparison.Ordinal) >= 0)
                {
                    drop.Add(key);
                }
            }
            for (int i = 0; i < drop.Count; i++)
            {
                row.Remove(drop[i]);
            }
        }
    }
}
