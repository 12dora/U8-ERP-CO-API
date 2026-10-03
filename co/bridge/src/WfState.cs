using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    internal sealed class WfSnap
    {
        public string Code;
        public bool Controlled;
        public int VerifyState;
        public int VerifyNew;
        public int ReturnCount;
        public bool Running;
        public int AbandonCount;
        public List<Dictionary<string, object>> Pending;
        public Dictionary<string, object> Wf;
    }

    internal static class WfState
    {
        public static Dictionary<string, object> Describe(WorkContext ctx, VoucherKind kind, int id)
        {
            WfSnap snap = Load(ctx, ctx.Conn, kind, id);
            if (snap == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            return snap.Wf;
        }

        internal static WfSnap Load(WorkContext ctx, object conn, VoucherKind kind, int id)
        {
            Dictionary<string, object> head = Rows.One(conn, WfSql.HeadSql(kind), new object[] { id, kind.BizObjectId });
            if (head == null) return null;
            WfSnap snap = new WfSnap();
            snap.Code = WfText.Cell(head, "code").Trim();
            snap.Controlled = WfText.On(head, "controlled");
            snap.VerifyState = WfText.Num(head, "verify_state");
            snap.VerifyNew = WfText.Num(head, "verify_state_new");
            snap.ReturnCount = WfText.Num(head, "return_count");
            snap.Pending = Pending(conn, kind, id);
            Dictionary<string, object> instance = Instance(conn, kind, id, out snap.Running);
            snap.AbandonCount = AbandonCount(conn, kind.BizObjectId, id, Person(ctx));
            snap.Wf = Shape(snap, head, instance);
            return snap;
        }

        internal static WfSnap LoadFresh(WorkContext ctx, VoucherKind kind, int id)
        {
            object conn = ctx.OpenFresh();
            try
            {
                return Load(ctx, conn, kind, id);
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        internal static bool CanAbandon(object conn, VoucherKind kind, int id, string person)
        {
            if (person == null || person.Trim().Length == 0) return false;
            Dictionary<string, object> row = Rows.One(conn, WfSql.AgreeSql(), new object[]
            {
                kind.BizObjectId, id, kind.BizObjectId, id, person
            });
            return row != null;
        }

        internal static List<Dictionary<string, object>> History(object conn, VoucherKind kind, int id)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, WfSql.HistorySql(), new object[] { kind.BizObjectId, id }, 1000);
            List<Dictionary<string, object>> list = new List<Dictionary<string, object>>();
            if (rows == null) return list;
            for (int i = 0; i < rows.Count; i++)
            {
                list.Add(HistoryRow(rows[i]));
            }
            return list;
        }

        internal static List<Dictionary<string, object>> Tasks(object conn, string person, string bizFilter, out int other)
        {
            if (person == null || person.Trim().Length == 0)
            {
                other = 0;
                return new List<Dictionary<string, object>>();
            }
            List<Dictionary<string, object>> rows = Rows.Query(conn, WfSql.TaskSql(), new object[] { person }, 2000);
            return MapTasks(rows, bizFilter, out other);
        }

        static Dictionary<string, object> Shape(WfSnap snap, Dictionary<string, object> head, Dictionary<string, object> instance)
        {
            Dictionary<string, object> wf = new Dictionary<string, object>();
            wf["controlled"] = snap.Controlled;
            wf["status"] = Status(snap);
            wf["verify_state"] = snap.VerifyState;
            wf["verify_state_new"] = snap.VerifyNew;
            wf["return_count"] = snap.ReturnCount;
            wf["current_auditor"] = WfText.Cell(head, "current_auditor").Trim();
            wf["verifier"] = WfText.Cell(head, "verifier").Trim();
            wf["verified_at"] = WfText.Cell(head, "verified_at").Trim();
            wf["instance"] = instance;
            wf["pending"] = snap.Pending;
            return wf;
        }

        // 进行中且待办全是退回后的重新提交（cTaskType=5）时，状态是 returned。
        static string Status(WfSnap snap)
        {
            if (!snap.Controlled) return "not_controlled";
            if (snap.Running && AllReturned(snap.Pending)) return "returned";
            if (snap.VerifyNew == 1) return "in_approval";
            if (snap.VerifyNew == 2) return "approved";
            if (snap.VerifyNew == -1) return "not_approved";
            return "not_submitted";
        }

        static bool AllReturned(List<Dictionary<string, object>> pending)
        {
            if (pending == null || pending.Count == 0) return false;
            for (int i = 0; i < pending.Count; i++)
            {
                if (WfText.Num(pending[i], "task_type") != 5) return false;
            }
            return true;
        }

        static Dictionary<string, object> Instance(object conn, VoucherKind kind, int id, out bool running)
        {
            running = false;
            Dictionary<string, object> row = Rows.One(conn, WfSql.InstanceSql(), new object[] { kind.BizObjectId, id });
            if (row == null) return null;
            running = WfText.Num(row, "flag_code") == 0;
            Dictionary<string, object> inst = new Dictionary<string, object>();
            inst["piid"] = WfText.Cell(row, "piid").Trim();
            inst["running"] = running;
            inst["started_by"] = WfText.Cell(row, "started_by").Trim();
            inst["started_at"] = WfText.Cell(row, "started_at").Trim();
            return inst;
        }

        static List<Dictionary<string, object>> Pending(object conn, VoucherKind kind, int id)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, WfSql.PendingSql(), new object[] { id, kind.BizObjectId }, 200);
            List<Dictionary<string, object>> list = new List<Dictionary<string, object>>();
            if (rows == null) return list;
            for (int i = 0; i < rows.Count; i++)
            {
                list.Add(PendingRow(rows[i]));
            }
            return list;
        }

        static Dictionary<string, object> PendingRow(Dictionary<string, object> row)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["task_id"] = WfText.Cell(row, "task_id").Trim();
            item["activity_id"] = WfText.Cell(row, "activity_id").Trim();
            item["person"] = WfText.Cell(row, "person").Trim();
            item["operator"] = WfText.Cell(row, "operator").Trim();
            item["task_type"] = WfText.Num(row, "task_type");
            return item;
        }

        static int AbandonCount(object conn, string biz, int id, string person)
        {
            if (person == null || person.Length == 0) return 0;
            string text = Rows.Scalar(conn, WfSql.AbandonSql(), new object[] { biz, id, person });
            return WfText.Num(text);
        }

        static Dictionary<string, object> HistoryRow(Dictionary<string, object> row)
        {
            int action = WfText.Num(row, "action");
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["action"] = action;
            item["action_name"] = ActionName(action);
            item["task"] = WfText.Cell(row, "task").Trim();
            item["opinion"] = WfText.Cell(row, "opinion").Trim();
            item["person"] = WfText.Cell(row, "person").Trim();
            item["operator"] = WfText.Cell(row, "operator").Trim();
            item["name"] = WfText.Cell(row, "name").Trim();
            item["at"] = WfText.Cell(row, "at").Trim();
            return item;
        }

        static string ActionName(int action)
        {
            switch (action)
            {
                case 0: return "submit";
                case 1: return "agree";
                case 2: return "disagree";
                case 4: return "reject";
                case 5: return "withdraw";
                case 6: return "return";
                case 7: return "abandon";
                case 8: return "resubmit";
                default: return "other";
            }
        }

        static List<Dictionary<string, object>> MapTasks(List<Dictionary<string, object>> rows, string bizFilter, out int other)
        {
            other = 0;
            List<Dictionary<string, object>> list = new List<Dictionary<string, object>>();
            if (rows == null) return list;
            Dictionary<string, string> names = BizNames();
            for (int i = 0; i < rows.Count; i++)
            {
                Dictionary<string, object> mapped = MapTask(rows[i], names, bizFilter, ref other);
                if (mapped != null) list.Add(mapped);
            }
            return list;
        }

        static Dictionary<string, object> MapTask(Dictionary<string, object> row, Dictionary<string, string> names, string bizFilter, ref int other)
        {
            string biz = WfText.Cell(row, "biz").Trim();
            string name;
            if (!names.TryGetValue(biz, out name))
            {
                other++;
                return null;
            }
            if (bizFilter != null && bizFilter.Length > 0
                && !string.Equals(biz, bizFilter, StringComparison.OrdinalIgnoreCase))
            {
                return null;
            }
            int vid = WfText.Num(WfText.Cell(row, "voucher_id"));
            if (vid <= 0) return null;
            return TaskItem(row, name, biz, vid);
        }

        static Dictionary<string, object> TaskItem(Dictionary<string, object> row, string name, string biz, int vid)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["task_id"] = WfText.Cell(row, "task_id").Trim();
            item["type"] = name;
            item["biz"] = biz;
            item["id"] = vid;
            item["code"] = TaskCode(row);
            item["task_type"] = WfText.Num(row, "task_type");
            item["activity_id"] = WfText.Cell(row, "activity_id").Trim();
            item["from"] = WfText.Cell(row, "from_name").Trim();
            item["created_at"] = WfText.Cell(row, "created_at").Trim();
            // 待办标题（U8 消息中心显示的那一句）和流程实例，上层在即时通讯或待办系统建待办时直接用。
            item["title"] = WfText.Cell(row, "title").Trim();
            item["piid"] = WfText.Cell(row, "piid").Trim();
            return item;
        }

        static string TaskCode(Dictionary<string, object> row)
        {
            string check = WfText.Cell(row, "check_code").Trim();
            if (check.Length > 0) return check;
            string reject = WfText.Cell(row, "reject_code").Trim();
            if (reject.Length > 0) return reject;
            return WfText.Cell(row, "extend_code").Trim();
        }

        static Dictionary<string, string> BizNames()
        {
            Dictionary<string, string> map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (VoucherKind kind in Kinds.All())
            {
                if (!kind.Workflow || kind.BizObjectId == null || kind.BizObjectId.Length == 0) continue;
                map[kind.BizObjectId] = kind.Name;
            }
            return map;
        }

        static string Person(WorkContext ctx)
        {
            if (ctx == null || ctx.Session == null || ctx.Session.EmployeeId == null) return "";
            return ctx.Session.EmployeeId.Trim();
        }
    }

    internal static class WfText
    {
        public static string Cell(Dictionary<string, object> row, string key)
        {
            object value = Pick(row, key);
            if (value == null || value is DBNull) return "";
            if (value is DateTime)
            {
                return ((DateTime)value).ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture);
            }
            return Convert.ToString(value, CultureInfo.InvariantCulture);
        }

        public static int Num(Dictionary<string, object> row, string key)
        {
            return Num(Cell(row, key));
        }

        public static int Num(string text)
        {
            if (text == null) return 0;
            int n;
            if (int.TryParse(text.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out n))
            {
                return n;
            }
            return 0;
        }

        public static bool On(Dictionary<string, object> row, string key)
        {
            return Values.Flag(Pick(row, key));
        }

        public static object Pick(Dictionary<string, object> row, string key)
        {
            if (row == null || key == null) return null;
            object found;
            if (row.TryGetValue(key, out found)) return found;
            foreach (KeyValuePair<string, object> pair in row)
            {
                if (string.Equals(pair.Key, key, StringComparison.OrdinalIgnoreCase)) return pair.Value;
            }
            return null;
        }
    }
}
