using System;
using System.Collections.Generic;

namespace U8Co
{
    // 一个操作员在一个账套、一个年度下的 U8 权限快照（功能权限 + 记录级数据权限 + 字段权限）。
    // 由 PermLoad 在登录（或登录缓存命中）之后用 ctx.Conn 读出，PermCache 按 60 秒缓存。
    // 只读：建好之后不再改，多个线程可以同时用同一个实例。
    internal sealed class PermContext
    {
        public string Acc;
        public string Operator;
        // 请求的 year（账套库年度）、登录日期的年份（只进缓存键，不参与年度窗口）、账套的建账年度（UFSYSTEM..UA_Account.iYear）。
        public int Year;
        public int DateYear;
        public int AcctYear;
        // 持有 admin / Admin（账套主管）：功能与数据权限都不受限。
        public bool Supervisor;
        // 总账选项「明细账查询权限控制到科目」（AccInformation GL bQryCtlSubj）。关着时科目（code）不过滤。
        public bool GlSubjCtl;
        public long LoadedTicks;

        internal readonly HashSet<string> Funcs = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        // 操作员所属角色（UFSYSTEM..UA_Role.cGroup_Id），建快照时解析一次；PermSql 的 AA_HoldAuth 条件直接用它。
        internal readonly List<string> Roles = new List<string>();
        // 数据权限控制已打开的业务对象（AA_BusObject_base.bAuthControl=1），键是小写的对象 id。
        internal readonly HashSet<string> On = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        // AA_holdBusobject.iAdmin=1：该对象的数据权限管理员，不受限。
        internal readonly HashSet<string> DataAdmin = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        // 受控对象 → 可查询的档案编码（去空格）；fitem 的键是「大类\u001f编码」。
        internal readonly Dictionary<string, HashSet<string>> Codes =
            new Dictionary<string, HashSet<string>>(StringComparer.OrdinalIgnoreCase);
        // 受控对象 → 编码实际取自 AA_HoldAuth 的哪个 cBusObId（人员可能落在 hr_hi_person 上）。
        internal readonly Dictionary<string, string> SourceOf =
            new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

        // 字段权限（AA_ColumnAuth，iAuthType=1 且开关打开的对象）：对象 id（cKey，去空格）→ 拒绝查看的字段（cFld，原样去空格）。
        // 只存拒绝（U8 是拒绝清单，没有行 = 可见）；用户与角色已按 U8 规则合并（PermLoad.MergeColumns）。主管为空。
        internal readonly Dictionary<string, HashSet<string>> Columns =
            new Dictionary<string, HashSet<string>>(StringComparer.OrdinalIgnoreCase);

        public const char PairSep = '\u001f';

        // 某个字段权限对象上拒绝查看的字段；没有时返回空集合。
        public HashSet<string> DeniedFields(string key)
        {
            HashSet<string> set;
            if (key != null && Columns.TryGetValue(key.Trim(), out set))
            {
                return set;
            }
            return new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        }

        // 功能权限：精确持有该 cAuth_Id。以 % 结尾的写法表示前缀（如 "GL%" = 任一总账功能）。
        // admin / Admin 只认 Supervisor（按最近一个有 admin 行的年度判定），不看 Funcs：Funcs 按请求年度和建账年度两年取，
        // 某年收回的主管仍会命中。
        public bool Has(string auth)
        {
            if (Supervisor)
            {
                return true;
            }
            if (string.IsNullOrEmpty(auth) || string.Equals(auth, "admin", StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }
            if (!auth.EndsWith("%", StringComparison.Ordinal))
            {
                return Funcs.Contains(auth);
            }
            string prefix = auth.Substring(0, auth.Length - 1);
            foreach (string held in Funcs)
            {
                if (held.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }

        // 多个可接受的 id 之间是「或」。空数组表示只要登录成功即可。
        public bool HasAny(string[] auths)
        {
            if (Supervisor || auths == null || auths.Length == 0)
            {
                return true;
            }
            for (int i = 0; i < auths.Length; i++)
            {
                if (Has(auths[i]))
                {
                    return true;
                }
            }
            return false;
        }

        // 该对象要不要按编码过滤：开关打开，且操作员既不是账套主管也不是该对象的数据权限管理员。
        // 科目另要总账选项 bQryCtlSubj 打开（U8 只在这时按科目控制明细账、余额表查询）。
        public bool Controls(string obj)
        {
            if (Supervisor || string.IsNullOrEmpty(obj))
            {
                return false;
            }
            if (!GlSubjCtl && string.Equals(obj, PermObj.Account, StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }
            return On.Contains(obj) && !DataAdmin.Contains(obj);
        }

        // 受控对象的可查询编码；不受控或没有授权时返回空集合（受控时空集合 = 一行都不给）。
        public HashSet<string> CodesOf(string obj)
        {
            HashSet<string> set;
            if (obj != null && Codes.TryGetValue(obj, out set))
            {
                return set;
            }
            return new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        }

        public string Source(string obj)
        {
            string source;
            if (obj != null && SourceOf.TryGetValue(obj, out source))
            {
                return source;
            }
            return obj ?? "";
        }

        // 单个编码能否查询。受控时空白编码不放行（与列表丢行、读取 403 一致）。
        public bool Allow(string obj, string code)
        {
            if (!Controls(obj))
            {
                return true;
            }
            string text = code == null ? "" : code.Trim();
            return text.Length > 0 && CodesOf(obj).Contains(text);
        }

        // 项目（fitem）按大类 + 编码一起判断。
        public bool AllowItem(string cls, string code)
        {
            if (!Controls(PermObj.Item))
            {
                return true;
            }
            string c = cls == null ? "" : cls.Trim();
            string k = code == null ? "" : code.Trim();
            return c.Length > 0 && k.Length > 0 && CodesOf(PermObj.Item).Contains(c + PairSep + k);
        }
    }
}
