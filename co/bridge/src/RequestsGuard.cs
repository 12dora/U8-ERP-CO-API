using System.Collections.Generic;

namespace U8Co
{
    internal static partial class Requests
    {
        delegate void KindGuard(VoucherKind kind);

        static readonly Dictionary<string, KindGuard> KindGuards = BuildKindGuards();

        static Dictionary<string, KindGuard> BuildKindGuards()
        {
            Dictionary<string, KindGuard> map = new Dictionary<string, KindGuard>();
            map.Add("/u8co/v1/vouchers/verify", GuardVerify);
            map.Add("/u8co/v1/vouchers/create", GuardCreate);
            map.Add("/u8co/v1/vouchers/delete", GuardDelete);
            map.Add("/u8co/v1/vouchers/update", GuardUpdate);
            map.Add("/u8co/v1/vouchers/close", GuardClose);
            map.Add("/u8co/v1/vouchers/generate", GuardGenerate);
            map.Add(LockPath, GuardLock);
            return map;
        }

        // 登录前的类型闸门：期初结存单先过 StockOpening.PreLogin（修改 400、写入只对测试账套开放），再走通用的 GuardKind。
        static void GuardItem(string path, WorkItem item)
        {
            StockOpening.PreLogin(item, path);
            // 货位调整单：修改 400（新增、审核、弃审、删除为第一级写入）。
            PositionAdjust.PreLogin(item, path);
            GuardKind(path, item.Type);
        }

        static void GuardVerify(VoucherKind kind)
        {
            // 盘点单审核暂不支持（StockMisc.NoCheckVerify，U8 生成盘盈盘亏单时报类型不匹配）。
            if (kind.Name == StockMisc.CheckKind)
            {
                throw new BridgeException(400, "bad_request", StockMisc.NoCheckVerify);
            }
            if (!kind.Verifiable)
            {
                // 报检单另给原因（QmVerify.RefuseText）。
                throw new BridgeException(400, "bad_request", QmVerify.RefuseText(kind));
            }
        }

        static void GuardCreate(VoucherKind kind)
        {
            if (!kind.Creatable)
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持新增");
            }
        }

        static void GuardDelete(VoucherKind kind)
        {
            if (!kind.Deletable)
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持删除");
            }
        }

        static void GuardUpdate(VoucherKind kind)
        {
            if (!kind.Updatable)
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持修改");
            }
        }

        static void GuardClose(VoucherKind kind)
        {
            if (!kind.Closable)
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持关闭");
            }
        }

        static void GuardGenerate(VoucherKind kind)
        {
            if (Empty(kind.GenerateFrom))
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持生单");
            }
        }

        // source_type 可省，缺省用该类型的第一个来源（GenerateFrom）；只认 Kinds 里列出的来源。
        static VoucherKind SourceFor(VoucherKind kind, Dictionary<string, object> body)
        {
            if (kind == null || Empty(kind.GenerateFrom))
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持生单");
            }
            string name = kind.GenerateFrom;
            if (body != null && body.ContainsKey("source_type"))
            {
                name = body["source_type"] as string;
                if (name == null || kind.Sources == null || !Listed(kind.Sources, name))
                {
                    throw new BridgeException(400, "bad_request", "该单据类型不能参照此来源类型生单");
                }
            }
            VoucherKind source = Kinds.Find(name);
            if (source == null)
            {
                throw new BridgeException(500, "internal", "来源单据类型无效");
            }
            return source;
        }
    }
}
