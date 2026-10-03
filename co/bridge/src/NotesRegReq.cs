using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // notes/create（票据登记）的请求。可选文本没给时为空串。
    internal sealed class NoteRegAsk
    {
        public string Flag;
        public string NoteNo;
        public string SettleCode;
        public decimal Amount;
        public string SignDate;
        public string ReceiptDate;
        public string ExpireDate;
        // 往来单位：应收票据是交票客户（customer），应付票据是收票供应商（vendor）；写进票据的 cEndorser、收付款单的往来单位。
        public string Partner;
        public string Drawer;
        public string DrawerBank;
        public string Dept;
        public string Person;
        public string Receiver;
        public string ReceiveBank;
        public string ReceiveAccount;
        public string Km;
        // 票据科目（note_km）：省略取基本科目 pjkm；应付票据在 pjkm 没有设置时必填。
        public string NoteKm;
        public string Digest;
        // 分包票据的子票区间（NoteSplit.Parse）；非分包为 0。
        public long SubStart;
        public long SubEnd;

        public bool Split
        {
            get { return SubStart > 0; }
        }
    }

    // notes/delete（删除票据）的请求：票据号 NoteNo 或主键 NoteId（AP_Note.Auto_ID）二选一。
    internal sealed class NoteDelAsk
    {
        public string Flag;
        public string NoteNo;
        public int NoteId;
    }

    // 票据登记、删除在登录前的校验（400）。登录子系统就是 flag；与登录日期有关的检查在 NotesRegGate。
    // 应付票据（flag AP）：往来单位字段是 vendor（应收是 customer，另一个不收），登记生成付款单（49）。
    // 写法按应收票据对称推断、未经实测，属第二级写入：只对测试账套开放（TestAccountGate，登录前和入队后各查一次）。处理在 NotesReg / NotesRegDel。
    internal static class NotesRegReq
    {
        public const string CreatePath = "/u8co/v1/notes/create";
        public const string DeletePath = "/u8co/v1/notes/delete";
        public const string CreateAction = "notes_create";
        public const string DeleteAction = "notes_delete";
        public const string ApTestOnly = "应付票据只对配置为测试账套的账套开放（应付票据写入只在测试账套上验证过）";
        const decimal AmountMax = 1000000000000m;
        // 路由的字段表（RequestsP4.P4Specs）：dry_run、幂等键在字段校验前已取走。
        internal static readonly string[] CreateSpec = new string[]
        {
            CreatePath, "flag", "note_no", "settle_code", "amount", "sign_date", "receipt_date", "expire_date", "customer",
            "drawer", "drawer_bank", "dept", "person", "receiver", "receive_bank", "receive_account", "km", "note_km", "digest", "sub_start", "sub_end", "vendor"
        };
        internal static readonly string[] DeleteSpec = new string[] { DeletePath, "flag", "note_no", "id" };

        public static bool IsPath(string path)
        {
            return path == CreatePath || path == DeletePath;
        }

        public static NoteRegAsk ParseCreate(Dictionary<string, object> body)
        {
            NoteRegAsk ask = new NoteRegAsk();
            ask.Flag = FlagOf(Requests.Field(body, "flag"));
            ask.NoteNo = Text(body, "note_no", "票据号", 60, true);
            ask.SettleCode = Text(body, "settle_code", "结算方式（票据类型）", 3, true);
            ask.Amount = Money(Requests.Field(body, "amount"));
            ask.SignDate = Day(body, "sign_date", "签发日期");
            ask.ReceiptDate = Day(body, "receipt_date", "收票日期");
            ask.ExpireDate = Day(body, "expire_date", "到期日");
            if (string.CompareOrdinal(ask.ExpireDate, ask.SignDate) < 0)
            {
                throw Bad("到期日不能早于签发日期", "expire_date");
            }
            if (string.CompareOrdinal(ask.ReceiptDate, ask.SignDate) < 0)
            {
                throw Bad("收票日期不能早于签发日期", "receipt_date");
            }
            ask.Partner = PartnerOf(body, ask.Flag);
            ask.Drawer = Text(body, "drawer", "出票人", 60, false);
            ask.DrawerBank = Text(body, "drawer_bank", "出票人开户银行", 100, false);
            ask.Dept = Text(body, "dept", "部门编码", 12, true);
            ask.Person = Text(body, "person", "业务员编码", 20, false);
            ask.Receiver = Text(body, "receiver", "收款人", 50, true);
            ask.ReceiveBank = Text(body, "receive_bank", "收款人开户银行", 100, false);
            ask.ReceiveAccount = Text(body, "receive_account", "收款人账号", 50, false);
            ask.Km = Text(body, "km", ask.Flag == "AP" ? "付款单表体科目" : "收款单表体科目", 40, false);
            ask.NoteKm = Text(body, "note_km", "票据科目", 40, false);
            ask.Digest = Text(body, "digest", "摘要", 120, false);
            NoteSplit.Parse(ask, body);
            return ask;
        }

        public static NoteDelAsk ParseDelete(Dictionary<string, object> body)
        {
            NoteDelAsk ask = new NoteDelAsk();
            ask.Flag = FlagOf(Requests.Field(body, "flag"));
            object id = Requests.Field(body, "id");
            ask.NoteNo = Text(body, "note_no", "票据号", 60, false);
            if ((id == null) == (ask.NoteNo.Length == 0))
            {
                throw Bad("note_no 与 id 必须给且只给一个", "note_no");
            }
            if (id != null)
            {
                ask.NoteId = ArapWriteoffReq.Id(id, "id", true);
            }
            return ask;
        }

        static string FlagOf(object raw)
        {
            string flag = raw as string;
            if (flag != "AR" && flag != "AP")
            {
                throw Bad("flag 只能是 AR（应收票据）或 AP（应付票据）", "flag");
            }
            return flag;
        }

        // 往来单位：应收票据收 customer（交票客户），应付票据收 vendor（收票供应商）；另一个字段给了 400。
        static string PartnerOf(Dictionary<string, object> body, string flag)
        {
            bool ap = flag == "AP";
            string other = ap ? "customer" : "vendor";
            if (Requests.Field(body, other) != null)
            {
                throw Bad(other + " 只用于" + (ap ? "应收票据（flag AR）" : "应付票据（flag AP）"), other);
            }
            return ap ? Text(body, "vendor", "供应商编码", 20, true) : Text(body, "customer", "客户编码", 20, true);
        }

        // 金额：大于 0、不超过 1000000000000、最多两位小数。
        static decimal Money(object raw)
        {
            decimal value;
            if (!ArapWriteoffReq.TryNum(raw, out value) || value <= 0m || value > AmountMax || decimal.Round(value, 2) != value)
            {
                throw Bad("amount 必须大于 0、不超过 1000000000000、最多两位小数", "amount");
            }
            return value;
        }

        static string Day(Dictionary<string, object> body, string field, string title)
        {
            string text = Text(body, field, title, 10, true);
            DateTime parsed;
            if (!DateTime.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out parsed))
            {
                throw Bad(field + " 必须是 yyyy-MM-dd", field);
            }
            return text;
        }

        // 字符串字段：去掉首尾空白；不能含控制字符；required 为 false 时没给返回空串。
        static string Text(Dictionary<string, object> body, string field, string title, int max, bool required)
        {
            object raw = Requests.Field(body, field);
            if (raw != null && !(raw is string))
            {
                throw Bad(field + " 必须是字符串", field);
            }
            string text = raw == null ? "" : ((string)raw).Trim();
            if (text.Length == 0)
            {
                if (required)
                {
                    throw Bad("缺少 " + field + "（" + title + "）", field);
                }
                return "";
            }
            if (text.Length > max || HasControl(text))
            {
                throw Bad(field + " 无效：最多 " + max.ToString(CultureInfo.InvariantCulture) + " 个字符，不能含控制字符", field);
            }
            return text;
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

        // 锁键：登记、删除都持 "note:<flag>"（票据登记与删除串行，同一票据号不会同时写）；
        // 登记另持 "new:ar_receipt" / "new:ap_payment"（收付款单编号，同 vouchers/create），删除另持 "arap:writeoff:<flag>"（与票据处理、核销串行）。
        // 请求体不合法时（登录前已 400）返回空。不是本路由返回 null。
        public static string[] LockKeysOf(string path, Dictionary<string, object> body)
        {
            if (!IsPath(path))
            {
                return null;
            }
            try
            {
                if (path == CreatePath)
                {
                    NoteRegAsk ask = ParseCreate(body);
                    return new string[] { "note:" + ask.Flag, "new:" + CloseKind(ask.Flag) };
                }
                NoteDelAsk del = ParseDelete(body);
                return new string[] { "note:" + del.Flag, "arap:writeoff:" + del.Flag };
            }
            catch (BridgeException)
            {
                return new string[0];
            }
        }

        // 登记生成的收付款单类型：应收票据收款单 ar_receipt（48），应付票据付款单 ap_payment（49）。
        public static string CloseKind(string flag)
        {
            return flag == "AP" ? "ap_payment" : "ar_receipt";
        }

        // 应付票据是第二级写入：账套不在测试账套名单里 403 test_account_only。应收票据什么也不做。
        public static void TestGate(WorkItem item, string flag)
        {
            if (flag == "AP")
            {
                TestAccountGate.Require(item, ApTestOnly);
            }
        }

        public static string ActionOf(string path)
        {
            return path == DeletePath ? DeleteAction : CreateAction;
        }

        // 数据权限用的行：往来单位（票据的 cEndorser，即交票客户 / 收票供应商）、部门、业务员。
        internal static Dictionary<string, object> PermRow(string partner, string dept, string person)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["cDwCode"] = partner ?? "";
            row["cDeptCode"] = dept ?? "";
            row["cPerson"] = person ?? "";
            return row;
        }

        static BridgeException Bad(string message, string field)
        {
            return new BridgeException(400, "bad_request", message, field);
        }
    }

    // notes/create、notes/delete 接到请求分派（Requests：挂在核销一组，不带 type、id）上的部分。
    internal static partial class Requests
    {
        static bool IsNotesReg(string path)
        {
            return NotesRegReq.IsPath(path);
        }

        // 是票据登记 / 删除路由时校验并定登录子系统（= flag），返回 true；否则返回 false。
        static bool ApplyNotesReg(Dictionary<string, object> body, WorkItem item)
        {
            if (item.Path == NotesRegReq.CreatePath)
            {
                NoteRegAsk ask = NotesRegReq.ParseCreate(body);
                NotesRegReq.TestGate(item, ask.Flag);
                item.SubId = ask.Flag;
                CoRows.Note(item, "票据登记 " + ask.NoteNo);
                return true;
            }
            if (item.Path == NotesRegReq.DeletePath)
            {
                NoteDelAsk del = NotesRegReq.ParseDelete(body);
                NotesRegReq.TestGate(item, del.Flag);
                item.SubId = del.Flag;
                CoRows.Note(item, "删除票据 " + (del.NoteNo.Length > 0 ? del.NoteNo : del.NoteId.ToString(CultureInfo.InvariantCulture)));
                return true;
            }
            return false;
        }
    }

    // 票据登记、删除。功能权限已按 U8 授权目录核对：「票据录入」AR0504 / AP0504，「票据删除」AR2403 / AP2403。
    // 数据权限：交票客户 / 收票供应商（cEndorser）、部门、业务员。
    internal static partial class PermRegistry
    {
        public static string NotesRegKey(bool delete, string flag)
        {
            return (delete ? "write:notes:delete:" : "write:notes:create:") + (flag == "AP" ? "ap" : "ar");
        }

        static PermRule[] NotesRegRules()
        {
            return new PermRule[]
            {
                R(NotesRegKey(false, "AR"), "应收票据登记", A("AR0504"), WriteoffObjs(PermObj.Customer)),
                R(NotesRegKey(true, "AR"), "应收票据删除", A("AR2403"), WriteoffObjs(PermObj.Customer)),
                R(NotesRegKey(false, "AP"), "应付票据登记", A("AP0504"), WriteoffObjs(PermObj.Vendor)),
                R(NotesRegKey(true, "AP"), "应付票据删除", A("AP2403"), WriteoffObjs(PermObj.Vendor))
            };
        }
    }
}
