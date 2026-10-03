using System;
using System.Collections.Generic;

namespace U8Co
{
    // EAI 档案写入前的校验：Transact 自己提交，能查的都在调用前查完，出错 400（请求不对）或 409（库里的状态不允许）。
    // 功能权限：PermRegistry 里登记了「write:archive:<档案>:<操作>」的档案（货位、收发类别、本单位开户银行、计量单位、计量单位组、结算方式等，见 PermRegistryMore.ArchiveWriteRules）
    // 由桥先查，没登记的交给 U8。
    internal static class ArcGuard
    {
        internal static string RuleKey(string archive, string op)
        {
            return "write:archive:" + archive + ":" + op;
        }

        public static void Permit(WorkContext ctx, ArcReq req)
        {
            PermRule rule = PermRegistry.ForKey(RuleKey(req.Kind.Name, req.Op));
            if (rule == null)
            {
                return;
            }
            PermContext p = PermCheck.Of(ctx);
            PermCheck.RequireRule(p, rule);
            // 客户存货对照另按客户、存货的数据权限判断编码的两段（与读取的 ArcPair.Obj1 / Obj2 一致）。
            ArcPair pair = ArcPair.Of(req.Kind);
            if (pair == null)
            {
                return;
            }
            string[] parts = pair.Split(req.Code, "code");
            if ((pair.Obj1 != null && !PermCheck.Allow(p, pair.Obj1, parts[0]))
                || (pair.Obj2 != null && !PermCheck.Allow(p, pair.Obj2, parts[1])))
            {
                throw new BridgeException(403, "no_permission", PermCheck.DeniedRow);
            }
        }

        // bag 是模板、当前行、调用方字段和缺省值合并后的整条记录；row 是修改前的当前行，新增时为 null。
        public static void Check(WorkContext ctx, ArcReq req, ArcBag bag, Dictionary<string, string> row)
        {
            object conn = ctx.Conn;
            ArcBank.Check(conn, req, bag);
            switch (req.Kind.Name)
            {
                case ArcPos.Name:
                    ArcPos.Check(conn, req, bag, row);
                    break;
                case ArcUnit.Name:
                    ArcUnit.Check(conn, req, bag, row);
                    break;
                case ArcPairWrite.Define:
                case ArcPairWrite.Contra:
                    ArcPairWrite.Check(conn, req);
                    break;
                default:
                    ArcMoreGuard.Check(conn, req, bag, row);
                    break;
            }
        }

        // 删除前查引用：货位（下级、存量、出入库记录、存货默认货位、存货货位对照）、计量单位（存货、同组辅计量单位）；
        // 计量单位组、结算方式、收发类别、采购类型、销售类型、地区分类、银行档案、原因码见 ArcRefs（下级、引用）。
        public static void Delete(object conn, ArcReq req, Dictionary<string, string> row)
        {
            if (req.Kind.Name == ArcPos.Name)
            {
                ArcPos.CheckDelete(conn, req.Code);
            }
            else if (req.Kind.Name == ArcUnit.Name)
            {
                ArcUnit.CheckDelete(conn, req.Code, row);
            }
            else
            {
                ArcRefs.CheckDelete(conn, req);
            }
        }

        internal static string Need(ArcBag bag, ArcReq req, string tag, string label)
        {
            string value = bag.Get(req.Map.Canon(tag));
            if (value == null || value.Trim().Length == 0)
            {
                throw ArcReq.Bad("缺少字段 " + label, FieldPath.Join("fields", tag));
            }
            return value.Trim();
        }

        // 调用方这一次改了 tag，且与当前行（列名取自 RsXml）的值不同（去空格、不分大小写）。
        internal static bool Changed(ArcReq req, Dictionary<string, string> row, string tag)
        {
            string canon = req.Map.Canon(tag);
            if (row == null || canon == null || !req.Fields.Has(canon))
            {
                return false;
            }
            string now;
            if (!row.TryGetValue(req.Map.Column(canon) ?? "", out now))
            {
                now = "";
            }
            string want = req.Fields.Get(canon) ?? "";
            return !string.Equals(now.Trim(), want.Trim(), StringComparison.OrdinalIgnoreCase);
        }

        // 布尔标签：1 / 0 / true / false（不分大小写）；没给返回 null，其他值 400。
        internal static string Flag(ArcBag bag, ArcReq req, string tag)
        {
            string value = bag.Get(req.Map.Canon(tag));
            if (value == null || value.Trim().Length == 0)
            {
                return null;
            }
            string text = value.Trim();
            if (text == "1" || string.Equals(text, "true", StringComparison.OrdinalIgnoreCase))
            {
                return "1";
            }
            if (text == "0" || string.Equals(text, "false", StringComparison.OrdinalIgnoreCase))
            {
                return "0";
            }
            throw ArcReq.Bad("字段 " + tag + " 必须是布尔或 0 / 1", FieldPath.Join("fields", tag));
        }

        internal static void Put(ArcBag bag, ArcReq req, string tag, string value)
        {
            string canon = req.Map.Canon(tag);
            if (canon != null)
            {
                bag.Put(canon, value);
            }
        }

        internal static BridgeException State(string message)
        {
            return new BridgeException(409, "state_mismatch", message);
        }
    }
}
