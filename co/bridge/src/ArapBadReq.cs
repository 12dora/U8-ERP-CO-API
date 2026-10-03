using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 坏账发生的一项：单据类型、单号、发票行（0 表示没给 line_id，按行主键从小到大分摊）、金额（原币）。
    internal sealed class ArapBadLine
    {
        public string Type;
        public string Id;
        public int LineId;
        public decimal Amount;
    }

    // arap/bad_debt 的请求（只有应收）。Date 是登录日期，也是坏账处理的登记日期；DryRun 由分派（ArapBad）按任务填。
    // 坏账发生（occur）：Customer、Currency（空为本位币）、Digest（空用缺省摘要）、Dept、Person、Lines。
    // 坏账收回（recover）：Customer、Receipt（收款单号）、Amount（原币，须等于收款单金额）、Currency、Digest。
    // 计提坏账准备（provision）：只有 Date（ArapBadProvision）。
    internal sealed class ArapBadAsk
    {
        public string Action;
        public DateTime Date;
        public bool DryRun;
        public string Customer = "";
        public string Currency = "";
        public string Digest = "";
        public string Dept = "";
        public string Person = "";
        public List<ArapBadLine> Lines = new List<ArapBadLine>();
        public string Receipt = "";
        public decimal Amount;
    }

    // 坏账处理（arap/bad_debt，U8 处理方式 9G 坏账发生 / 9H 坏账收回 / 9F 计提坏账准备，处理号 HZAR…）在登录前的校验（400）。
    // 只对配置为测试账套的账套开放（TestAccountGate，登录前 403）；登录子系统 AR。处理在 ArapBad（分派）。
    internal static class ArapBadReq
    {
        public const string Path = "/u8co/v1/arap/bad_debt";
        public const string Route = "arap/bad_debt";
        public const string AuditAction = "bad_debt";
        public const string Occur = "occur";
        public const string Recover = "recover";
        public const string Provision = "provision";
        public const string TestOnly = "坏账处理只对配置为测试账套的账套开放";
        public const int MaxLines = 50;
        const int CodeMax = 30;
        const int PartnerMax = 20;
        const int CurrencyMax = 20;
        const int DeptMax = 12;
        const int PersonMax = 20;
        const decimal AmountMax = 1000000000000m;

        // 登录前的字段名单（RequestsP4 的 P4Specs）：dry_run、幂等键在字段校验前已取走。
        internal static readonly string[] Spec = new string[]
        {
            Path, "action", "flag", "customer", "currency", "digest", "dept", "person", "lines", "receipt", "amount"
        };
        // 按动作取舍的字段（不在本动作名单里的给了就 400）。
        static readonly string[] ActionKeys = new string[]
        {
            "customer", "currency", "digest", "dept", "person", "lines", "receipt", "amount"
        };
        static readonly string[] OccurKeys = new string[] { "customer", "currency", "digest", "dept", "person", "lines" };
        static readonly string[] RecoverKeys = new string[] { "customer", "currency", "digest", "receipt", "amount" };
        static readonly string[] LineKeys = new string[] { "type", "id", "line_id", "amount" };

        public static bool IsPath(string path)
        {
            return path == Path;
        }

        public static ArapBadAsk Parse(Dictionary<string, object> body)
        {
            ArapBadAsk ask = new ArapBadAsk();
            ask.Action = Requests.Field(body, "action") as string;
            if (ask.Action != Occur && ask.Action != Recover && ask.Action != Provision)
            {
                throw Bad("action 只能是 occur（坏账发生）、recover（坏账收回）或 provision（计提坏账准备）", "action");
            }
            object flag = Requests.Field(body, "flag");
            if (flag != null && !"AR".Equals(flag))
            {
                throw Bad("坏账处理只有应收：flag 可省略，给了只能是 AR", "flag");
            }
            ask.Date = Day(body);
            OnlyFor(body, ask.Action);
            if (ask.Action == Provision)
            {
                return ask;
            }
            ask.Customer = Code(Requests.Field(body, "customer"), "customer", "客户编码", PartnerMax, true);
            ask.Currency = Code(Requests.Field(body, "currency"), "currency", "币种", CurrencyMax, false);
            ask.Digest = Digest(Requests.Field(body, "digest"));
            if (ask.Action == Recover)
            {
                ask.Receipt = Code(Requests.Field(body, "receipt"), "receipt", "收款单号", CodeMax, true);
                ask.Amount = Amount(Requests.Field(body, "amount"), "amount");
                return ask;
            }
            ask.Dept = Code(Requests.Field(body, "dept"), "dept", "部门编码", DeptMax, false);
            ask.Person = Code(Requests.Field(body, "person"), "person", "业务员编码", PersonMax, false);
            ask.Lines = Lines(Requests.Field(body, "lines"));
            return ask;
        }

        // 登录日期 yyyy-MM-dd。year 是账套库的年度（一个库含多个会计年度），不和 date 比；会计期间由登录后的 UA_Period 判定。
        static DateTime Day(Dictionary<string, object> body)
        {
            string text = Requests.Field(body, "date") as string;
            DateTime day;
            if (!DateTime.TryParseExact(text ?? "", "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out day))
            {
                throw Bad("date 必须是 yyyy-MM-dd", "date");
            }
            return day;
        }

        // 只收本动作的字段；别的动作的字段给了（不是 null）就 400。
        static void OnlyFor(Dictionary<string, object> body, string action)
        {
            string[] own = action == Occur ? OccurKeys : action == Recover ? RecoverKeys : new string[0];
            foreach (string key in ActionKeys)
            {
                if (Requests.Field(body, key) != null && Array.IndexOf(own, key) < 0)
                {
                    throw Bad("action=" + action + " 不接受字段 " + key, key);
                }
            }
        }

        static string Code(object raw, string field, string title, int max, bool required)
        {
            string text = raw as string;
            if (raw != null && text == null)
            {
                throw Bad(field + " 必须是字符串", field);
            }
            text = text == null ? "" : text.Trim();
            if (text.Length == 0 && required)
            {
                throw Bad("缺少 " + field + "（" + title + "）", field);
            }
            if (text.Length > max || HasControl(text))
            {
                throw Bad(field + " 无效", field);
            }
            return text;
        }

        static string Digest(object raw)
        {
            if (raw == null)
            {
                return "";
            }
            string text = raw as string;
            if (text == null || HasControl(text))
            {
                throw Bad("digest 必须是不含控制字符的字符串", "digest");
            }
            text = text.Trim();
            if (text.Length > ArapVoucherReq.DigestMax)
            {
                throw Bad("digest 最多 " + ArapVoucherReq.DigestMax.ToString(CultureInfo.InvariantCulture) + " 字", "digest");
            }
            return text;
        }

        static List<ArapBadLine> Lines(object raw)
        {
            IList list = raw as IList;
            if (list == null || list.Count == 0 || list.Count > MaxLines)
            {
                throw Bad("lines 必须是 1 到 " + MaxLines.ToString(CultureInfo.InvariantCulture) + " 项的数组", "lines");
            }
            List<ArapBadLine> lines = new List<ArapBadLine>();
            // 单号按原样比较（与 API 层一致）；大小写不同而库里是同一张单据时，写后余额核对不符会回滚。
            HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
            for (int i = 0; i < list.Count; i++)
            {
                ArapBadLine line;
                try
                {
                    line = Line(list[i]);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Under(ex, FieldPath.Item("lines", i));
                }
                if (!Fresh(seen, line))
                {
                    throw Bad("lines 里有重复的单据行（同一单据不能既按行又按整单）", FieldPath.Item("lines", i));
                }
                lines.Add(line);
            }
            return lines;
        }

        // 同一（类型、单号、行）只出现一次；同一单据不能既有不带 line_id 的一项、又有带 line_id 的项（分摊会重叠）。
        internal static bool Fresh(HashSet<string> seen, ArapBadLine line)
        {
            string doc = line.Type + "|" + line.Id;
            string key = doc + "|" + line.LineId.ToString(CultureInfo.InvariantCulture);
            bool overlap = line.LineId == 0 ? seen.Contains(doc + "|line") : seen.Contains(doc + "|0");
            if (overlap || !seen.Add(key))
            {
                return false;
            }
            if (line.LineId != 0)
            {
                seen.Add(doc + "|line");
            }
            return true;
        }

        static ArapBadLine Line(object raw)
        {
            Dictionary<string, object> map = ArapWriteoffReq.Obj(raw, "lines[]");
            ArapWriteoffReq.Only(map, LineKeys, "");
            ArapBadLine line = new ArapBadLine();
            line.Type = ArapWriteoffReq.Text(map, "type");
            if (!ArapBadRule.Pickable(line.Type))
            {
                throw Bad("type 只能是 26、27、28、29（销售发票）或 R0 到 R9（应收单）", "type");
            }
            line.Id = Code(Requests.Field(map, "id"), "id", "单据号", CodeMax, true);
            line.LineId = ArapWriteoffReq.Id(Requests.Field(map, "line_id"), "line_id", false);
            if (line.LineId != 0 && ArapBadRule.WholeDoc(line.Type))
            {
                throw Bad("应收单按整单处理，不能带 line_id", "line_id");
            }
            line.Amount = Amount(Requests.Field(map, "amount"), "amount");
            return line;
        }

        // 金额（原币）大于 0、不超过 1000000000000、最多两位小数。
        static decimal Amount(object raw, string field)
        {
            decimal value;
            if (!ArapWriteoffReq.TryNum(raw, out value) || value <= 0 || value > AmountMax || decimal.Round(value, 2) != value)
            {
                throw Bad(field + " 必须大于 0、不超过 1000000000000、最多两位小数", field);
            }
            return value;
        }

        static bool HasControl(string text)
        {
            foreach (char c in text)
            {
                if (char.IsControl(c))
                {
                    return true;
                }
            }
            return false;
        }

        // 中文说明（审计备注、日志）。
        public static string Title(ArapBadAsk ask)
        {
            string day = ask.Date.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
            if (ask.Action == Occur)
            {
                return "坏账发生 客户 " + ask.Customer + " " + ask.Lines.Count.ToString(CultureInfo.InvariantCulture) + " 项 " + day;
            }
            if (ask.Action == Recover)
            {
                return "坏账收回 客户 " + ask.Customer + " 收款单 " + ask.Receipt + " " + day;
            }
            return "计提坏账准备 " + day;
        }

        // 审计 action：bad_debt_occur / bad_debt_recover / bad_debt_provision。
        public static string AuditOf(ArapBadAsk ask)
        {
            return AuditAction + "_" + ask.Action;
        }

        // 锁键："arap:writeoff:AR"（与应收的核销、转账等串行：都改同一批单据的余额）和 "arap:bad:AR"（坏账准备余额、HZ 编号）。
        // 请求体不合法时（登录前已 400）返回空。
        public static string[] LockKeys(Dictionary<string, object> body)
        {
            try
            {
                Parse(body);
                return new string[] { "arap:writeoff:AR", "arap:bad:AR" };
            }
            catch (BridgeException)
            {
                return new string[0];
            }
        }

        static BridgeException Bad(string message, string field)
        {
            return new BridgeException(400, "bad_request", message, field);
        }
    }

    // arap/bad_debt 在登录前的处理：不带 type、id；先校验字段（400，ArapBadReq），再查测试账套名单（403 test_account_only），
    // 都在登录 U8 之前。登录子系统 AR；审计 action 按动作改成 bad_debt_<action>。
    internal static partial class Requests
    {
        // 不是坏账处理路由返回 false，不动任务。
        static bool ApplyBadDebt(Dictionary<string, object> body, WorkItem item, string path)
        {
            if (!ArapBadReq.IsPath(path))
            {
                return false;
            }
            ArapBadAsk ask = ArapBadReq.Parse(body);
            TestAccountGate.Require(item, ArapBadReq.TestOnly);
            item.SubId = "AR";
            item.Action = ArapBadReq.AuditOf(ask);
            CoRows.Note(item, ArapBadReq.Title(ask));
            return true;
        }
    }

    // 坏账处理的功能权限（已按 U8 授权目录核对）：坏账发生 AR050602、坏账收回 AR050603、计提坏账准备 AR050601
    // （U8 授权目录里三个界面的功能 id），上级「坏账处理」AR0506。
    // 数据权限：坏账发生按每张单据的表头、坏账收回按收款单表头（往来单位、部门、业务员，同核销）；计提坏账准备不按往来单位。
    internal static partial class PermRegistry
    {
        public static string BadDebtKey(string action)
        {
            return "write:arap:bad_debt:" + action;
        }

        static PermRule[] BadDebtRules()
        {
            return new PermRule[]
            {
                R(BadDebtKey(ArapBadReq.Occur), "坏账发生", A("AR050602", "AR0506"), WriteoffObjs(PermObj.Customer)),
                R(BadDebtKey(ArapBadReq.Recover), "坏账收回", A("AR050603", "AR0506"), WriteoffObjs(PermObj.Customer)),
                R(BadDebtKey(ArapBadReq.Provision), "计提坏账准备", A("AR050601", "AR0506"))
            };
        }
    }
}
