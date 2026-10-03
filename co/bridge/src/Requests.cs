using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    internal static partial class Requests
    {
        static readonly string[] CommonKeys = new string[]
        {
            "acc", "year", "operator", "password_enc", "date"
        };

        static readonly string[] VoucherKeys = new string[]
        {
            "acc", "year", "operator", "password_enc", "date", "id", "action"
        };

        static readonly string[][] RouteSpecs = new string[][]
        {
            new string[] { "/u8co/v1/vouchers/load", "type", "id" },
            new string[] { "/u8co/v1/vouchers/verify", "type", "id", "action" },
            new string[] { "/u8co/v1/vouchers/create", "type", "head", "lines" },
            new string[] { "/u8co/v1/vouchers/update", "type", "id", "head", "lines" },
            new string[] { "/u8co/v1/vouchers/delete", "type", "id" },
            new string[] { "/u8co/v1/vouchers/close", "type", "id", "action", "line_ids" },
            new string[] { "/u8co/v1/vouchers/generate", "type", "id", "source_type", "head", "lines" },
            new string[] { "/u8co/v1/workflow/state", "type", "id" },
            new string[] { "/u8co/v1/workflow/history", "type", "id" },
            new string[] { "/u8co/v1/workflow/tasks", "type" },
            new string[] { "/u8co/v1/workflow/submit", "type", "id" },
            new string[] { "/u8co/v1/workflow/withdraw", "type", "id" },
            new string[] { "/u8co/v1/workflow/approve", "type", "id", "opinion" },
            new string[] { "/u8co/v1/workflow/disagree", "type", "id", "opinion" },
            new string[] { "/u8co/v1/workflow/return", "type", "id", "opinion" },
            new string[] { "/u8co/v1/workflow/abandon", "type", "id", "opinion" },
            new string[] { "/u8co/v1/workflow/resubmit", "type", "id" },
            // 单据附件列表（VoucherAttach，只读）。
            new string[] { VoucherAttach.Path, "type", "id" },
            // 销售订单 / 采购订单锁定、解锁（RequestsLock、VoucherLock）。
            new string[] { LockPath, "type", "id", "action" },
            // 应收 / 应付核销（RequestsWriteoff、ArapWriteoff）。
            new string[] { WriteoffPath, "receipt", "items" },
            // 取消核销（RequestsWriteoff、ArapUnwriteoff）。
            new string[] { WriteoffCancelPath, "flag", "cancel_no" },
            // 自动核销（RequestsWriteoff、ArapAutoWriteoff）。
            new string[] { WriteoffAutoPath, "flag", "partner", "date_to", "receipt", "targets", "max_amount", "dry_run", "include_prepay" },
            // 应收 / 应付制单、取消制单（RequestsArapVoucher、ArapVoucher、ArapVoucherDrop）。
            new string[] { ArapVoucherPath, "flag", "type", "id", "ids", "sign", "voucher_date", "digest", "cash_items" },
            new string[] { ArapVoucherDropPath, "flag", "pz_id" },
            // 名称解析（ArcResolveReq）、幂等结果查询（IdemGet）：只读。
            new string[] { ArcResolve.Path, "items", "limit", "include_disabled" },
            new string[] { IdemGet.Path, "route", IdemReq.KeyField, IdemReq.CallerField }
        };

        public static WorkItem Login(BridgeConfig cfg, Dictionary<string, object> body, AuditDraft draft)
        {
            RejectUnknown(body, CommonKeys);
            return Build(cfg, body, draft, WorkItem.LoginKind, false);
        }

        public static WorkItem Sale(BridgeConfig cfg, Dictionary<string, object> body, AuditDraft draft)
        {
            return Legacy(cfg, body, draft, WorkItem.SaleKind, "/u8co/v1/sale-orders/verify");
        }

        public static WorkItem Dispatch(BridgeConfig cfg, Dictionary<string, object> body, AuditDraft draft)
        {
            return Legacy(cfg, body, draft, WorkItem.DispatchKind, "/u8co/v1/dispatches/verify");
        }

        // 旧版审核路由：不收 dry_run；同样收幂等键（IdemReq），先取走再按字段表校验。
        static WorkItem Legacy(BridgeConfig cfg, Dictionary<string, object> body, AuditDraft draft, string kind, string path)
        {
            DryRunReq.RefuseLegacy(body, draft);
            string caller = WriteClassGate.CallerOf(body, path);
            IdemAsk idem = IdemReq.Take(body, path);
            draft.Caller = caller;
            RejectUnknown(body, VoucherKeys);
            WorkItem item = Build(cfg, body, draft, kind, true);
            item.Idem = idem;
            return item;
        }

        public static WorkItem ForPath(BridgeConfig cfg, string path, byte[] raw, AuditDraft draft)
        {
            if (path == "/u8co/v1/login-check")
            {
                return Login(cfg, Json.Parse(raw), draft);
            }
            if (path == "/u8co/v1/sale-orders/verify")
            {
                return Sale(cfg, Json.Parse(raw), draft);
            }
            if (path == "/u8co/v1/dispatches/verify")
            {
                return Dispatch(cfg, Json.Parse(raw), draft);
            }
            string[] extra = ExtraKeys(path);
            if (extra == null)
            {
                return null;
            }
            return ParseNew(cfg, Json.Parse(raw), draft, path, extra);
        }

        static WorkItem Build(BridgeConfig cfg, Dictionary<string, object> body, AuditDraft draft, string kind, bool voucher)
        {
            string acc = Need(body, "acc");
            if (!Digits(acc, 3))
            {
                throw BridgeException.BadField("acc", "账套号必须是 3 位数字");
            }
            // 名单不过就不解密、不入队。正式账套不会走到 U8Login。
            if (!BridgeConfig.AllowsAccount(cfg, acc))
            {
                draft.Acc = acc;
                throw new BridgeException(403, "account_not_allowed", "账套不在允许列表内");
            }
            string year = Need(body, "year");
            if (!Digits(year, 4))
            {
                throw BridgeException.BadField("year", "年度必须是 4 位数字");
            }
            string user = CheckOperator(Need(body, "operator"));
            string date = Need(body, "date");
            CheckDate(date);
            draft.Acc = acc;
            draft.Year = year;
            draft.Operator = user;
            // 只读账套与写入策略：账套名单之后、解密之前。未配置策略时只分类、不拦。
            WriteRequestInfo write = WriteClassGate.Pre(cfg, body, draft, acc, user);
            string password = Decrypt(cfg, Need(body, "password_enc"));
            WorkItem item = new WorkItem();
            item.Config = cfg;
            item.Kind = kind;
            item.Acc = acc;
            item.Year = year;
            item.Operator = user;
            item.Password = password;
            item.Date = date;
            item.ClientIp = draft.Ip;
            item.Path = draft.Path;
            item.Route = draft.Path ?? "";
            item.Started = draft.Start;
            WriteClassGate.Stamp(item, draft, write);
            if (voucher)
            {
                item.HasId = true;
                item.Id = NeedId(body);
                item.Action = NeedAction(body);
                draft.HasId = true;
                draft.Id = item.Id;
                draft.Action = item.Action;
            }
            return item;
        }

        static string Decrypt(BridgeConfig cfg, string enc)
        {
            try
            {
                string password = Crypto.Decrypt(cfg.EncKey, enc);
                if (password.Length == 0)
                {
                    throw new FormatException("empty");
                }
                return password;
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception)
            {
                throw new BridgeException(400, "bad_request", "口令无法解密");
            }
        }

        static void RejectUnknown(Dictionary<string, object> body, string[] known)
        {
            foreach (string key in body.Keys)
            {
                if (!Listed(known, key))
                {
                    throw new BridgeException(400, "bad_request", "含未知字段", FieldPath.Clean(key), "该接口可用的字段见 /v1/openapi.json");
                }
            }
        }

        static bool Listed(string[] known, string key)
        {
            for (int i = 0; i < known.Length; i++)
            {
                if (known[i] == key)
                {
                    return true;
                }
            }
            return false;
        }

        static string Need(Dictionary<string, object> body, string key)
        {
            if (!body.ContainsKey(key) || !(body[key] is string))
            {
                throw BridgeException.BadField(key, "缺少字段 " + key);
            }
            return (string)body[key];
        }

        static int NeedId(Dictionary<string, object> body)
        {
            if (!body.ContainsKey("id"))
            {
                throw BridgeException.BadField("id", "缺少单据 id");
            }
            object value = body["id"];
            if (value is int)
            {
                return Positive((int)value);
            }
            if (value is long)
            {
                long number = (long)value;
                if (number < 1 || number > int.MaxValue)
                {
                    throw BridgeException.BadField("id", "单据 id 无效");
                }
                return (int)number;
            }
            throw BridgeException.BadField("id", "单据 id 必须是整数");
        }

        static int Positive(int id)
        {
            if (id < 1)
            {
                throw BridgeException.BadField("id", "单据 id 无效");
            }
            return id;
        }

        static string NeedAction(Dictionary<string, object> body)
        {
            string action = Need(body, "action");
            if (action != "verify" && action != "unverify")
            {
                throw BridgeException.BadField("action", "action 只能是 verify 或 unverify");
            }
            return action;
        }

        static string NeedClose(Dictionary<string, object> body)
        {
            string action = Need(body, "action");
            if (action != "close" && action != "open")
            {
                throw BridgeException.BadField("action", "action 只能是 close 或 open");
            }
            return action;
        }

        static string CheckOperator(string user)
        {
            if (user.Length == 0 || user.Length > 20)
            {
                throw BridgeException.BadField("operator", "操作员编码无效");
            }
            for (int i = 0; i < user.Length; i++)
            {
                if (user[i] <= ' ' || user[i] == '\'' || user[i] == ';')
                {
                    throw BridgeException.BadField("operator", "操作员编码无效");
                }
            }
            return user;
        }

        static void CheckDate(string date)
        {
            DateTime parsed;
            if (!DateTime.TryParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out parsed))
            {
                throw BridgeException.BadField("date", "日期必须是 yyyy-MM-dd");
            }
        }

        static bool Digits(string text, int size)
        {
            if (text == null || text.Length != size)
            {
                return false;
            }
            for (int i = 0; i < text.Length; i++)
            {
                if (text[i] < '0' || text[i] > '9')
                {
                    return false;
                }
            }
            return true;
        }

        static string[] ExtraKeys(string path)
        {
            string[] extra = SpecKeys(RouteSpecs, path);
            // 只读报表。
            if (extra == null && IsReport(path))
            {
                return SpecKeys(ReportSpecs, path);
            }
            return extra ?? SpecKeys(P4Specs, path);
        }

        static string[] SpecKeys(string[][] specs, string path)
        {
            for (int i = 0; i < specs.Length; i++)
            {
                if (specs[i][0] != path)
                {
                    continue;
                }
                string[] extra = new string[specs[i].Length - 1];
                Array.Copy(specs[i], 1, extra, 0, extra.Length);
                return extra;
            }
            return null;
        }

        static string[] JoinKeys(string[] extra)
        {
            string[] keys = new string[CommonKeys.Length + extra.Length];
            Array.Copy(CommonKeys, keys, CommonKeys.Length);
            Array.Copy(extra, 0, keys, CommonKeys.Length, extra.Length);
            return keys;
        }

        static WorkItem ParseNew(
            BridgeConfig cfg, Dictionary<string, object> body, AuditDraft draft, string path, string[] extra)
        {
            // 写预演：先取走写路由上的 dry_run（DryRunReq），预演不能带幂等键。
            bool dry = DryRunReq.Take(body, path);
            DryRunReq.Mark(draft, dry ? DryRunReq.Requested : null);
            // 幂等：先取走 idempotency_key、caller 并算业务内容摘要（IdemReq），再按原来的字段表校验。
            string caller = WriteClassGate.CallerOf(body, path);
            IdemAsk idem = IdemReq.Take(body, path);
            draft.Caller = caller;
            DryRunReq.RefuseIdem(dry, idem);
            RejectUnknown(body, JoinKeys(extra));
            WorkItem item = Build(cfg, body, draft, "", false);
            ApplyType(body, item, path, draft);
            draft.TypeName = item.Type == null ? "" : item.Type.Name ?? "";
            ApplyId(body, item, draft, path);
            ApplyAction(body, item, path);
            draft.Action = item.Action ?? "";
            ApplyOpinion(body, item, path);
            ApplyPayload(body, item, path);
            // 预演模式按路由、类型（档案）、action、生单来源查表，不支持的组合登录前 400。
            DryRunReq.Apply(item, body, dry);
            DryRunReq.Mark(draft, item.DryRunMode);
            // 权限评估：登录前就记下 subject，名单外的 403 审计行也带上（PermEvaluate.cs NoteSubject）。
            NoteSubject(draft, body, path);
            ApplyP4(body, item, path);
            item.Idem = idem;
            return item;
        }

        static void ApplyId(Dictionary<string, object> body, WorkItem item, AuditDraft draft, string path)
        {
            bool noId = path == "/u8co/v1/vouchers/create" || path == "/u8co/v1/workflow/tasks" || NoIdP4(path) || ArapVoucherMerge(body, path);
            if (noId)
            {
                return;
            }
            item.HasId = true;
            item.Id = NeedId(body);
            draft.HasId = true;
            draft.Id = item.Id;
        }

        static void ApplyAction(Dictionary<string, object> body, WorkItem item, string path)
        {
            if (path == "/u8co/v1/vouchers/verify")
            {
                // arap_verify / arap_unverify 只给采购发票、销售发票（RequestsArap）。
                item.Action = NeedVerifyAction(body, item);
                return;
            }
            if (path == "/u8co/v1/vouchers/close")
            {
                item.Action = NeedClose(body);
                return;
            }
            if (path == LockPath)
            {
                item.Action = NeedLock(body);
                return;
            }
            item.Action = ActionName(path);
        }

        static void ApplyPayload(Dictionary<string, object> body, WorkItem item, string path)
        {
            if (path == "/u8co/v1/vouchers/create")
            {
                FillCreate(body, item, path); // 移到 PuSettleManReq.cs（采购手工结算另有规则）
                return;
            }
            if (path == "/u8co/v1/vouchers/update")
            {
                FillUpdate(body, item);
                return;
            }
            if (path == "/u8co/v1/vouchers/close")
            {
                item.LineIds = Json.IdList(body, "line_ids");
                return;
            }
            if (path == "/u8co/v1/vouchers/generate")
            {
                FillGenerate(body, item);
            }
        }

        static void FillUpdate(Dictionary<string, object> body, WorkItem item)
        {
            item.Head = Json.OptionalHead(body, item.Type);
            item.Lines = Json.UpdateLines(body, item.Type);
            bool headEmpty = item.Head == null || item.Head.Count == 0;
            bool linesEmpty = item.Lines == null || item.Lines.Length == 0;
            if (headEmpty && linesEmpty)
            {
                throw new BridgeException(400, "bad_request", "没有要修改的内容");
            }
        }

        // 采购发票表头必须带发票号 cPBVCode（单号列），不走通用禁写名单，由 PuInvReq 按白名单校验。
        static void FillGenerate(Dictionary<string, object> body, WorkItem item)
        {
            bool invoice = item.Type != null && item.Type.Name == "purchase_invoice";
            item.Head = Json.OptionalHead(body, invoice ? null : item.Type);
            item.Source = SourceFor(item.Type, body);
            item.Lines = Json.GenerateLines(body, item.Type, WholeGenerate(body, item));
            PuSettleReq.Apply(item); // 采购结算：不收 lines，表头只收 settle_date（替换本次的 U8 登录日期）
        }

        static void ApplyType(Dictionary<string, object> body, WorkItem item, string path, AuditDraft draft)
        {
            if (Typeless(path) || ListOnlyType(path, body) || (path == "/u8co/v1/workflow/tasks" && !body.ContainsKey("type")))
            {
                return;
            }
            VoucherKind kind = Kinds.Find(Need(body, "type"));
            if (kind == null)
            {
                throw BridgeException.BadField("type", "单据类型无效").WithHint("可用的单据类型见 /v1/co/meta");
            }
            item.Type = kind;
            if (draft != null)
            {
                draft.TypeName = kind.Name ?? "";
            }
            GuardItem(path, item);
        }

        static void GuardKind(string path, VoucherKind kind)
        {
            if (path.StartsWith("/u8co/v1/workflow/", StringComparison.Ordinal) && !kind.Workflow)
            {
                throw new BridgeException(400, "bad_request", "该单据类型未接入审批流");
            }
            KindGuard guard;
            if (KindGuards.TryGetValue(path, out guard))
            {
                guard(kind);
            }
        }

        static bool Empty(string text)
        {
            return text == null || text.Length == 0;
        }

        static string ActionName(string path)
        {
            string p4 = ActionP4(path);
            if (p4 != null)
            {
                return p4;
            }
            if (path == "/u8co/v1/vouchers/create")
            {
                return "create";
            }
            if (path == "/u8co/v1/vouchers/delete")
            {
                return "delete";
            }
            if (path == "/u8co/v1/vouchers/update")
            {
                return "update";
            }
            if (path == "/u8co/v1/vouchers/generate")
            {
                return "generate";
            }
            if (path == "/u8co/v1/vouchers/close")
            {
                return "close";
            }
            int slash = path.LastIndexOf('/');
            if (slash < 0 || path.IndexOf("/workflow/", StringComparison.Ordinal) < 0)
            {
                return "";
            }
            return path.Substring(slash + 1);
        }

        static void ApplyOpinion(Dictionary<string, object> body, WorkItem item, string path)
        {
            bool required = path.EndsWith("/disagree", StringComparison.Ordinal)
                || path.EndsWith("/return", StringComparison.Ordinal);
            bool optional = path.EndsWith("/approve", StringComparison.Ordinal)
                || path.EndsWith("/abandon", StringComparison.Ordinal);
            if (!required && !optional)
            {
                return;
            }
            if (!body.ContainsKey("opinion"))
            {
                if (required)
                {
                    throw BridgeException.BadField("opinion", "必须填写意见");
                }
                return;
            }
            string text = Need(body, "opinion");
            if (text.Length > 500)
            {
                throw BridgeException.BadField("opinion", "意见不能超过 500 字");
            }
            if (required && text.Trim().Length == 0)
            {
                throw BridgeException.BadField("opinion", "必须填写意见");
            }
            item.Opinion = text;
        }
    }
}
