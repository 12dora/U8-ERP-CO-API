using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;

namespace U8Co
{
    // 一种可取消的应收应付处理（按处理号前缀识别）。Ledgers 是这类处理写往来明细的账（9I / 9J 两边都写，并账只写本侧）；
    // Restore 为真时取消要把余额加回（9I / 9J：收付款单行、应收应付单、发票累计核销），并账成对 ± 行各单据净额为 0，只删行；
    // FlagDelete 为真时删行条件同 U8 并账（带 cflag 或代理进口），否则按处理方式和处理号删两边。
    internal sealed class ProcCancelKind
    {
        public string Prefix;
        public string Style;
        public string Flag;
        public string Name;
        public string Title;
        public string[] Ledgers;
        public bool Restore;
        public bool FlagDelete;
        // 红票对冲（9N）：余额加回的符号与 9I / 9J 相反（处理行的借+贷取负，见 ArapRedRule），发票累计按处理行原样加回
        // （ArapUnwriteoffBill 按处理方式选 ArapRedSql 的取数）；收付款单（48 / 49）的 9N 不支持（规则未经实测）。
        public bool Negate;
        // 票据处理（9A 结算 / 9D 贴现 / 9E 背书 / 9C 退回，PJJ / PJT / PJB / CL）：另走 NotesUndo（票据处理行 AP_Note_Sub、
        // 票据余额与分包可用区间、9C 生成的应收单）。Ledgers 的第一个是票据所在的账，9E 的第二个是被背书的对方账。
        public bool Notes;
        // 坏账处理（计提 9F / 发生 9G / 收回 9H，同一个 HZAR 编号）：Style 占位为 HZ，实际处理方式在事务里按库识别，
        // 另走 ArapProcCancelBad（第二级写入，只对测试账套开放）。
        public bool Bad;

        public bool Both
        {
            get { return Ledgers.Length == 2; }
        }
    }

    // arap/process/cancel 的请求：flag（登录子系统）和一个处理号。
    internal sealed class ProcCancelAsk
    {
        public string Flag;
        public string CancelNo;
        public ProcCancelKind Kind;
    }

    // arap/process/cancel（取消应收冲应付 9I、应付冲应收 9J、并账 BZ）在登录前的校验（400）。
    // 处理号由 U8 的 Ap_Proc_CancelNo 编出：应收冲应付 YCF+AP、应付冲应收 FCY+AR、并账 BZ+AR|AP。
    // 应收冲应付在应收系统里做和取消（flag=AR），应付冲应收在应付系统（flag=AP），并账跟号里的 AR / AP。
    // 另有红票对冲（9N，HRAR / HPAP，Kind.Negate）和票据处理（9A / 9D / 9E / 9C，Kind.Notes，处理在 NotesUndo）。处理在 ArapProcCancel。
    internal static class ArapProcCancelReq
    {
        public const string Path = "/u8co/v1/arap/process/cancel";
        public const string Action = "arap_process_cancel";
        // Requests 的字段表：路由，然后是 flag、cancel_no（dry_run、幂等键在字段校验前已取走）。
        internal static readonly string[] Spec = new string[] { Path, "flag", "cancel_no" };
        static readonly Regex CancelNo = new Regex("^(YCFAP|FCYAR|BZAR|BZAP|HRAR|HPAP|HZAR|PJJAR|PJTAR|PJBAR|CLAR|PJJAP|PJTAP|PJBAP|CLAP)[0-9]{1,20}\\z", RegexOptions.CultureInvariant);

        static readonly ProcCancelKind[] Kinds = new ProcCancelKind[]
        {
            Kind("YCFAP 9I AR transfer 应收冲应付", "AR,AP", true, false),
            Kind("FCYAR 9J AP transfer 应付冲应收", "AR,AP", true, false),
            Kind("BZAR BZ AR merge 并账", "AR", false, true),
            Kind("BZAP BZ AP merge 并账", "AP", false, true),
            // 红票对冲（9N）：只写本侧，删除条件同 U8 取消 9N（带 cflag 或代理进口，实测）。
            Red(Kind("HRAR 9N AR red_offset 红票对冲", "AR", true, true)),
            Red(Kind("HPAP 9N AP red_offset 红票对冲", "AP", true, true)),
            // 票据处理：结算 9A、贴现 9D、退回 9C 只写票据所在的账；背书 9E 另写被背书供应商的应付明细（余额照 9I 加回）。
            // 应付票据（PJJAP / PJTAP / PJBAP / CLAP）按同一套原子推断、未经实测：结算、退回（PJJAP / CLAP）作为第二级写入
            // 只对测试账套开放（ApplyArapProc 登录前、NotesUndo 入队后各查一次）；贴现、背书（PJTAP / PJBAP）不开放（Parse 400，同 notes/process）。
            Note(Kind("PJJAR 9A AR note_settle 票据结算", "AR", false, false)),
            Note(Kind("PJTAR 9D AR note_discount 票据贴现", "AR", false, false)),
            Note(Kind("PJBAR 9E AR note_endorse 票据背书", "AR,AP", true, false)),
            Note(Kind("CLAR 9C AR note_return 票据退回", "AR", false, false)),
            Note(Kind("PJJAP 9A AP note_settle 票据结算", "AP", false, false)),
            Note(Kind("PJTAP 9D AP note_discount 票据贴现", "AP", false, false)),
            Note(Kind("PJBAP 9E AP note_endorse 票据背书", "AP,AR", true, false)),
            Note(Kind("CLAP 9C AP note_return 票据退回", "AP", false, false)),
            // 坏账处理：9F / 9G / 9H 共用 HZ+AR 编号，只在应收；处理在 ArapProcCancelBad。
            BadKind(Kind("HZAR HZ AR bad_debt 坏账处理", "AR", false, false))
        };

        static ProcCancelKind BadKind(ProcCancelKind k)
        {
            k.Bad = true;
            return k;
        }

        static ProcCancelKind Note(ProcCancelKind k)
        {
            k.Notes = true;
            return k;
        }

        static ProcCancelKind Red(ProcCancelKind k)
        {
            k.Negate = true;
            return k;
        }

        // names：前缀 处理方式 flag 种类名 中文名（空格分隔）；ledgers：涉及的账（逗号分隔）。
        static ProcCancelKind Kind(string names, string ledgers, bool restore, bool flagDelete)
        {
            string[] n = names.Split(' ');
            ProcCancelKind k = new ProcCancelKind();
            k.Prefix = n[0];
            k.Style = n[1];
            k.Flag = n[2];
            k.Name = n[3];
            k.Title = n[4];
            k.Ledgers = ledgers.Split(',');
            k.Restore = restore;
            k.FlagDelete = flagDelete;
            return k;
        }

        public static bool IsPath(string path)
        {
            return path == Path;
        }

        // 处理号对应的处理种类；不认识返回 null。
        internal static ProcCancelKind KindOf(string cancelNo)
        {
            if (cancelNo == null)
            {
                return null;
            }
            foreach (ProcCancelKind k in Kinds)
            {
                if (cancelNo.StartsWith(k.Prefix, StringComparison.Ordinal))
                {
                    return k;
                }
            }
            return null;
        }

        public static ProcCancelAsk Parse(Dictionary<string, object> body)
        {
            string flag = Requests.Field(body, "flag") as string;
            if (flag != "AR" && flag != "AP")
            {
                throw Bad("flag 只能是 AR 或 AP", "flag");
            }
            string no = Requests.Field(body, "cancel_no") as string;
            if (no == null || !CancelNo.IsMatch(no))
            {
                throw Bad("cancel_no 必须是 YCFAP（应收冲应付）、FCYAR（应付冲应收）、BZAR / BZAP（并账）、HRAR / HPAP（红票对冲）、"
                    + "HZAR（坏账计提、发生、收回）、PJJ / PJT / PJB / CL 加 AR 或 AP（票据结算、贴现、背书、退回）后接数字的处理号", "cancel_no");
            }
            ProcCancelKind kind = KindOf(no);
            if (kind.Notes && kind.Flag == "AP" && kind.Style != "9A" && kind.Style != "9C")
            {
                throw Bad(NotesProcReq.ApNotesOps, "cancel_no");
            }
            if (kind.Flag != flag)
            {
                throw Bad("cancel_no 与 flag 不一致（YCFAP、BZAR、HRAR、HZAR、PJJAR 等用 AR；FCYAR、BZAP、HPAP、PJJAP 等用 AP）", "cancel_no");
            }
            ProcCancelAsk ask = new ProcCancelAsk();
            ask.Flag = flag;
            ask.CancelNo = no;
            ask.Kind = kind;
            return ask;
        }

        // 锁键：涉及的单据入队前查不到，按账串行：每个涉及的账一个 "arap:writeoff:<AR|AP>"（与核销、取消核销、自动核销、
        // 应收冲应付、并账共用：它们改同样的余额）；另加 "arap:proc:<处理号>"（与该批的处理制单串行）。
        // 请求体不合法时（登录前已 400）返回空。
        public static string[] LockKeys(Dictionary<string, object> body)
        {
            ProcCancelAsk ask;
            try
            {
                ask = Parse(body);
            }
            catch (BridgeException)
            {
                return new string[0];
            }
            List<string> keys = new List<string>();
            foreach (string ledger in ask.Kind.Ledgers)
            {
                keys.Add("arap:writeoff:" + ledger);
            }
            keys.Add("arap:proc:" + ask.CancelNo);
            // 坏账处理：与坏账发生、收回、计提（arap/bad_debt）共用 "arap:bad:AR"（它们改同一行坏账准备参数）。
            if (ask.Kind.Bad)
            {
                keys.Add("arap:bad:AR");
            }
            return keys.ToArray();
        }

        static BridgeException Bad(string message, string field)
        {
            return new BridgeException(400, "bad_request", message, field);
        }
    }

    // arap/process/cancel 接到请求分派（Requests）和权限登记（PermRegistry）上的部分。
    internal static partial class Requests
    {
        // 审计 action（BatchAction 的最后一档）：arap_process_cancel；其余返回 null。
        static string ArapProcAction(string path)
        {
            return ArapProcCancelReq.IsPath(path) ? ArapProcCancelReq.Action : null;
        }

        // 不是 arap/process/cancel 返回 false，不动任务。登录子系统就是 flag。
        static bool ApplyArapProc(Dictionary<string, object> body, WorkItem item, string path)
        {
            if (!ArapProcCancelReq.IsPath(path))
            {
                return false;
            }
            ProcCancelAsk ask = ArapProcCancelReq.Parse(body);
            if (ask.Kind.Bad)
            {
                TestAccountGate.Require(item, ArapProcCancelBad.TestOnly);
            }
            if (ask.Kind.Notes)
            {
                NotesProcReq.TestGate(item, ask.Kind.Flag);
            }
            item.SubId = ask.Flag;
            CoRows.Note(item, "取消" + ask.Kind.Title + " " + ask.CancelNo);
            return true;
        }
    }

    // 取消应收冲应付 / 应付冲应收 / 并账等：U8「其他处理 → 取消操作」AR0807 / AP0807（所有处理共用「取消操作」一个界面，
    // 与取消核销同一个 id；按 U8 授权目录更正，原先的 AR050203 / AR0502 是收款单删除 / 录入）。数据权限按每条处理行的往来单位、部门、业务员
    // （应收一侧按客户、应付一侧按供应商；并账的原单位和新单位都要放行）。
    internal static partial class PermRegistry
    {
        public static string ProcCancelKey(string flag)
        {
            return "write:arap:process_cancel:" + (flag == "AP" ? "ap" : "ar");
        }

        static PermRule[] ProcCancelRules()
        {
            return new PermRule[]
            {
                R(ProcCancelKey("AR"), "应收取消操作", A("AR0807"), WriteoffObjs(PermObj.Customer)),
                R(ProcCancelKey("AP"), "应付取消操作", A("AP0807"), WriteoffObjs(PermObj.Vendor))
            };
        }
    }
}
