using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 与请求无关的事件行（签名自检结果、U8 程序集加载），写进同一份审计日志。
    // 行形如 {"ts":…,"event":"signature_check",…}，没有 path/outcome 等请求字段。启动前（目录未设）静默丢弃。
    internal static class AuditEvent
    {
        static volatile string _dir;

        public static void SetDir(string dir)
        {
            _dir = dir;
        }

        public static void Write(string name, Dictionary<string, object> fields)
        {
            string dir = _dir;
            if (dir == null || dir.Length == 0)
            {
                return;
            }
            try
            {
                Dictionary<string, object> row = new Dictionary<string, object>();
                row["ts"] = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture);
                row["event"] = name;
                if (fields != null)
                {
                    foreach (KeyValuePair<string, object> pair in fields)
                    {
                        row[pair.Key] = pair.Value;
                    }
                }
                AuditLog.Append(dir, row);
            }
            catch (Exception)
            {
                // 事件行只是诊断，写不进去不影响服务。
            }
        }
    }
}
