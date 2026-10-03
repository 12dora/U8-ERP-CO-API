using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

namespace U8Co
{
    internal sealed class AuditDraft
    {
        public DateTime Start;
        public string Ip;
        public string Path;
        public string Acc;
        public string Year;
        public string Operator;
        public bool HasId;
        public int Id;
        public string Action;
        public bool Audited;
        public bool Responded;
        public string TranBefore;
        public string TranAfter;
        public string TypeName;
        public string Route;
        public string Detail;
        // 写预演的模式 rollback / validate；不是预演为空，审计行不写 dry_run。与 detail 分开，不会被截断或覆盖。
        public string DryRun;
        // 请求体的 caller（调用方）、写入分类的 op（WriteClass）、写入策略的判定（allow 或拒绝的错误码；未配置策略为空）。
        public string Caller;
        public string Op;
        public string Policy;
    }

    internal static class AuditLog
    {
        const int MessageLimit = 300;
        static readonly object Gate = new object();

        public static void Write(
            BridgeConfig cfg,
            AuditDraft draft,
            string outcome,
            string message,
            string secret)
        {
            if (cfg == null || draft == null || draft.Audited)
            {
                return;
            }
            draft.Audited = true;
            int duration = (int)(DateTime.UtcNow - draft.Start).TotalMilliseconds;
            if (duration < 0)
            {
                duration = 0;
            }
            Append(cfg.AuditLog, Row(cfg, draft, outcome, message, secret, duration));
        }

        static Dictionary<string, object> Row(
            BridgeConfig cfg,
            AuditDraft draft,
            string outcome,
            string message,
            string secret,
            int duration)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["ts"] = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture);
            row["ip"] = Text(draft.Ip);
            row["path"] = Text(draft.Path);
            row["acc"] = Text(draft.Acc);
            row["year"] = Text(draft.Year);
            row["operator"] = Text(draft.Operator);
            row["id"] = draft.HasId ? (object)draft.Id : null;
            row["action"] = Text(draft.Action);
            row["outcome"] = Text(outcome);
            row["message"] = Scrub(message, secret, cfg.SqlPassword);
            row["duration_ms"] = duration;
            row["trancount_before"] = Text(draft.TranBefore);
            row["trancount_after"] = Text(draft.TranAfter);
            row["type"] = Text(draft.TypeName);
            row["route"] = Text(draft.Route);
            row["detail"] = Scrub(draft.Detail, secret, cfg.SqlPassword);
            row["caller"] = Scrub(draft.Caller, secret, cfg.SqlPassword);
            row["op"] = Text(draft.Op);
            row["policy"] = Text(draft.Policy);
            if (!string.IsNullOrEmpty(draft.DryRun))
            {
                row["dry_run"] = draft.DryRun;
            }
            return row;
        }

        static string Text(string value)
        {
            return value ?? "";
        }

        public static void Fail(BridgeConfig cfg, AuditDraft draft, Exception ex, string secret)
        {
            BridgeException bridge = ex as BridgeException;
            if (bridge == null && ex != null)
            {
                bridge = ex.InnerException as BridgeException;
            }
            if (bridge != null)
            {
                Write(cfg, draft, bridge.Code, bridge.Message, secret);
                return;
            }
            string detail = ex == null ? "" : ex.Message;
            Write(cfg, draft, "internal", detail, secret);
        }

        internal static void Append(string dir, Dictionary<string, object> row)
        {
            if (dir == null || dir.Length == 0)
            {
                return;
            }
            try
            {
                string full = Paths.UnderRoot(dir);
                Directory.CreateDirectory(full);
                string name = "u8co-" + DateTime.Now.ToString("yyyyMMdd", CultureInfo.InvariantCulture) + ".log";
                string path = Paths.UnderRoot(Path.Combine(full, name));
                string line = new JavaScriptSerializer().Serialize(row) + "\n";
                byte[] buf = Encoding.UTF8.GetBytes(line);
                lock (Gate)
                {
                    bool fresh = !File.Exists(path);
                    using (FileStream fs = new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.Read))
                    {
                        if (fresh)
                        {
                            byte[] bom = new byte[] { 0xEF, 0xBB, 0xBF };
                            fs.Write(bom, 0, bom.Length);
                        }
                        fs.Write(buf, 0, buf.Length);
                    }
                }
            }
            catch (Exception)
            {
                // 审计写失败不能把已经完成的 U8 操作变成服务崩溃。
            }
        }

        // 口令和签名不在字段里。这里再擦一遍，防止 OLE DB 把连接串写进异常文本。
        static string Scrub(string text, string secret, string sqlPassword)
        {
            if (text == null)
            {
                return "";
            }
            if (secret != null && secret.Length > 0)
            {
                text = text.Replace(secret, "***");
            }
            if (sqlPassword != null && sqlPassword.Length > 0)
            {
                text = text.Replace(sqlPassword, "***");
            }
            text = Regex.Replace(text, "(?i)(Password|Pwd)\\s*=\\s*[^;\\r\\n]*", "$1=***");
            if (text.Length > MessageLimit)
            {
                return text.Substring(0, MessageLimit);
            }
            return text;
        }
    }
}
