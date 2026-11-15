using System;
using System.Collections.Generic;

namespace U8Co
{
    // 物料清单的 U8API 调用（U8ApiComBroker，AppType BOM，登录子系统 BO）。与生产订单同一套 Env / Broker，
    // API 自己开 TransactionScope 提交，不包 CoTrans。见 docs/u8-notes.md「物料清单」B1–B6。
    // 审核 / 弃审 / 删除（BomAuditing / BomUnauditing / BomDelete）：AssignNormalValue partid（bas_part.PartId，整数）、
    // bomtype（1）、versionoridencode（版本号的字符串）。
    // 新增 / 修改（BomAdd / BomUpdate）：Connect 之后 GetExtBoEntity("extbo")，NewItem 建表头、GetSubEntity("Bom_Component")
    // 建行，SetValue 填字段。修改带 UpdateByDiff=true 并送全部行：没送的行号会被删掉，不带 UpdateByDiff 则整表替换。
    // 调用前（建对象、Connect、填实体）出错照常报；InvokeApi 开始后的非桥异常和 IPC 错误返回给调用方按库确认。
    internal static class BomCom
    {
        internal const string AuditUrl = "U8API/BOM/BomAuditing";
        internal const string UnauditUrl = "U8API/BOM/BomUnauditing";
        internal const string DeleteUrl = "U8API/BOM/BomDelete";
        const string AddUrl = "U8API/BOM/BomAdd";
        const string UpdateUrl = "U8API/BOM/BomUpdate";

        delegate void Filler(Com com);

        internal static Exception Triple(WorkContext ctx, string url, int partId, string version)
        {
            BomDry.TripleInput(partId, version);
            return Run(ctx, url, delegate(Com com)
            {
                ComUtil.Call(com.Broker, "AssignNormalValue", new object[] { "partid", partId });
                ComUtil.Call(com.Broker, "AssignNormalValue", new object[] { "bomtype", 1 });
                ComUtil.Call(com.Broker, "AssignNormalValue", new object[] { "versionoridencode", version });
            });
        }

        internal static Exception Save(WorkContext ctx, BomAsk ask, bool update)
        {
            BomDry.SaveInput(ask, update);
            return Run(ctx, update ? UpdateUrl : AddUrl, delegate(Com com)
            {
                com.Ext = ComUtil.Call(com.Broker, "GetExtBoEntity", new object[] { "extbo" });
                Fill(com, ask, update);
            });
        }

        static Exception Run(WorkContext ctx, string url, Filler fill)
        {
            if (ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "没有 U8 登录");
            }
            // 预演（校验模式）：U8API 自己提交，调用方的闸门都已查完，这里停（在 try 之外，DryRunDone 不会被下面改写）。
            DryRun.Stop(ctx, "U8API " + url.Substring(url.LastIndexOf('/') + 1));
            Com com = new Com();
            try
            {
                com.Env = MoApi.Need(ComUtil.Create(MoApi.EnvProgId));
                ComUtil.Set(com.Env, "U8Login", ctx.Session.Login);
                com.Broker = MoApi.Need(ComUtil.Create(MoApi.BrokerProgId));
                ComUtil.Call(com.Broker, "Connect", new object[] { url, com.Env });
                com.Connected = true;
                fill(com);
                com.Invoked = true;
                bool ok = Values.Flag(ComUtil.Call(com.Broker, "InvokeApi", new object[0]));
                string err = Values.Text(ComUtil.Call(com.Broker, "GetLastError", new object[0])).Trim();
                return Settle(ctx, ok, err);
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, ex.GetType().Name + " " + MoApi.FirstLine(ex.Message));
                if (!com.Invoked)
                {
                    throw MoApi.Failed(ex);
                }
                return ex;
            }
            finally
            {
                com.Release();
            }
        }

        // InvokeApi 返回 false：IPC 错误（U8MPool 没起）交给调用方按库确认，其余是 U8 拒绝（409 u8_rejected，去掉堆栈的原文）。
        static Exception Settle(WorkContext ctx, bool ok, string err)
        {
            if (err.Length > 0)
            {
                CoRows.Note(ctx.Item, MoApi.FirstLine(err));
            }
            if (!ok && MoApi.IsIpc(err))
            {
                return new InvalidOperationException(err);
            }
            if (!ok)
            {
                throw MoApi.Refused(err);
            }
            return null;
        }

        static void Fill(Com com, BomAsk ask, bool update)
        {
            object head = com.Keep(ComUtil.Call(com.Ext, "NewItem", new object[0]));
            Put(head, "BomType", 1);
            Put(head, "InvCode", ask.InvCode);
            Put(head, "Version", ask.Version);
            Put(head, "VersionDesc", ask.VersionDesc);
            Put(head, "VersionEffDate", ask.EffDate);
            Put(head, "ParentScrap", ask.ParentScrap);
            if (update)
            {
                Put(head, "UpdateByDiff", true);
            }
            object body = com.Keep(ComUtil.Call(head, "GetSubEntity", new object[] { "Bom_Component" }));
            for (int i = 0; i < ask.Rows.Count; i++)
            {
                object item = com.Keep(ComUtil.Call(body, "NewItem", new object[0]));
                FillRow(item, ask.Rows[i]);
            }
        }

        static void FillRow(object item, BomRow row)
        {
            Put(item, "DSortSeq", row.Seq);
            Put(item, "DOpSeq", row.OpSeq);
            Put(item, "DInvCode", row.InvCode);
            Put(item, "DBaseQtyN", row.QtyN);
            Put(item, "DBaseQtyD", row.QtyD);
            Put(item, "DCompScrap", row.Scrap);
            Put(item, "DWIPType", row.Wip);
            if (row.Wh.Length > 0)
            {
                Put(item, "DWhCode", row.Wh);
            }
            if (row.Remark.Length > 0)
            {
                Put(item, "DRemark", row.Remark);
            }
            Put(item, "DEffBegDate", row.EffBeg);
            Put(item, "DEffEndDate", row.EffEnd);
            FillFlags(item, row);
        }

        // 不送这些标志 SaveBOM 报 CheckStructureIntegrity（实测 B2）。分段损耗（DCompScrapFlag）桥不支持，恒为 0。
        // 名字照 U8 BomBase._AddBomDetails 的拼写（DCostWIPRel）。
        static void FillFlags(object item, BomRow row)
        {
            Put(item, "DFVFlag", row.FvFlag);
            Put(item, "DOffset", row.Offset);
            Put(item, "DPlanRate", row.PlanRate);
            Put(item, "DByproductFlag", row.Byproduct);
            Put(item, "DAccuCostFlag", row.AccuCost);
            Put(item, "DOptionalFlag", row.Optional);
            Put(item, "DMutexRule", row.Mutex);
            Put(item, "DProductType", row.ProductType);
            Put(item, "DCompScrapFlag", 0);
            Put(item, "DCostWIPRel", row.CostWip);
        }

        static void Put(object item, string name, object value)
        {
            ComUtil.Call(item, "SetValue", new object[] { name, value });
        }

        // 本次调用建的 COM 对象：扩展实体的子对象只 ReleaseOne，自己 Create 的 Env / Broker 在 Disconnect 之后 Final。
        sealed class Com
        {
            public object Env;
            public object Broker;
            public object Ext;
            public bool Connected;
            public bool Invoked;
            readonly List<object> _items = new List<object>();

            public object Keep(object item)
            {
                _items.Add(item);
                return item;
            }

            public void Release()
            {
                for (int i = _items.Count - 1; i >= 0; i--)
                {
                    ComUtil.ReleaseOne(_items[i]);
                }
                ComUtil.ReleaseOne(Ext);
                MoApi.Disconnect(Broker, Connected);
                ComUtil.Final(Broker);
                ComUtil.Final(Env);
            }
        }
    }
}
