using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using ComTypes = System.Runtime.InteropServices.ComTypes;

namespace U8Co
{
    // 类型库里的一个成员（方法或属性访问器）。参数不含 [retval] 和 [lcid]。
    internal sealed class SigFunc
    {
        public string Name;
        public int Invoke;
        public int Total;
        public int Required;
        public bool[] ByRef;
    }

    // 只读类型库：LoadTypeLibEx(REGKIND_NONE) 不写注册表，也不创建对象、不登录 U8。
    // 一次自检内按路径缓存已载入的库，按 CLSID 缓存成员列表；用完 Dispose 释放。
    internal sealed class SigTypeLib : IDisposable
    {
        const int RegKindNone = 2;
        const short VtPtr = 26;
        const short VtUserDefined = 29;
        const short VtByRef = 0x4000;
        const int MaxDepth = 4;

        [DllImport("oleaut32.dll", CharSet = CharSet.Unicode, PreserveSig = true)]
        static extern int LoadTypeLibEx(string file, int regKind, out ComTypes.ITypeLib lib);

        readonly Dictionary<string, ComTypes.ITypeLib> _libs =
            new Dictionary<string, ComTypes.ITypeLib>(StringComparer.OrdinalIgnoreCase);
        readonly Dictionary<Guid, List<SigFunc>> _members = new Dictionary<Guid, List<SigFunc>>();

        // 返回该类所有非事件接口上的成员；拿不到类型库时返回 null，原因写进 problem。
        public List<SigFunc> Members(SigComp comp, out string problem)
        {
            problem = null;
            List<SigFunc> cached;
            if (_members.TryGetValue(comp.Clsid, out cached))
            {
                return cached;
            }
            ComTypes.ITypeInfo coclass = FindClass(comp, out problem);
            if (coclass == null)
            {
                return null;
            }
            List<SigFunc> list = new List<SigFunc>();
            CollectClass(coclass, list);
            _members[comp.Clsid] = list;
            return list;
        }

        ComTypes.ITypeInfo FindClass(SigComp comp, out string problem)
        {
            problem = "没有登记类型库";
            string[] paths = new string[] { comp.TypeLibPath, comp.Managed ? "" : comp.Server };
            for (int i = 0; i < paths.Length; i++)
            {
                ComTypes.ITypeLib lib = Load(paths[i]);
                if (lib == null)
                {
                    continue;
                }
                ComTypes.ITypeInfo info = ClassIn(lib, comp.Clsid);
                if (info != null)
                {
                    problem = null;
                    return info;
                }
                problem = "类型库里没有该类";
            }
            return null;
        }

        ComTypes.ITypeLib Load(string path)
        {
            if (path == null || path.Length == 0)
            {
                return null;
            }
            ComTypes.ITypeLib lib;
            if (_libs.TryGetValue(path, out lib))
            {
                return lib;
            }
            lib = null;
            int hr = LoadTypeLibEx(path, RegKindNone, out lib);
            if (hr != 0)
            {
                lib = null;
            }
            _libs[path] = lib;
            return lib;
        }

        static ComTypes.ITypeInfo ClassIn(ComTypes.ITypeLib lib, Guid clsid)
        {
            try
            {
                ComTypes.ITypeInfo info;
                Guid id = clsid;
                lib.GetTypeInfoOfGuid(ref id, out info);
                return info;
            }
            catch (COMException)
            {
                return null;
            }
        }

        // 缺省接口在前，其余非事件接口在后：补丁把方法挪到非缺省接口时也能看见。
        void CollectClass(ComTypes.ITypeInfo coclass, List<SigFunc> list)
        {
            ComTypes.TYPEATTR attr = Attr(coclass);
            if (attr.typekind != ComTypes.TYPEKIND.TKIND_COCLASS)
            {
                CollectInterface(coclass, list, 0);
                return;
            }
            List<ComTypes.ITypeInfo> rest = new List<ComTypes.ITypeInfo>();
            for (int i = 0; i < attr.cImplTypes; i++)
            {
                ComTypes.IMPLTYPEFLAGS flags;
                coclass.GetImplTypeFlags(i, out flags);
                if ((flags & ComTypes.IMPLTYPEFLAGS.IMPLTYPEFLAG_FSOURCE) != 0)
                {
                    continue;
                }
                ComTypes.ITypeInfo iface = RefOfImpl(coclass, i);
                if ((flags & ComTypes.IMPLTYPEFLAGS.IMPLTYPEFLAG_FDEFAULT) != 0)
                {
                    rest.Insert(0, iface);
                }
                else
                {
                    rest.Add(iface);
                }
            }
            for (int i = 0; i < rest.Count; i++)
            {
                CollectInterface(rest[i], list, 0);
            }
        }

        static ComTypes.ITypeInfo RefOfImpl(ComTypes.ITypeInfo info, int index)
        {
            int href;
            info.GetRefTypeOfImplType(index, out href);
            ComTypes.ITypeInfo target;
            info.GetRefTypeInfo(href, out target);
            return target;
        }

        // 双接口的 dispatch 视图已含继承来的成员；纯 vtable 接口要沿基接口往上找。
        void CollectInterface(ComTypes.ITypeInfo info, List<SigFunc> list, int depth)
        {
            ComTypes.TYPEATTR attr = Attr(info);
            for (int i = 0; i < attr.cFuncs; i++)
            {
                list.Add(ReadFunc(info, i));
            }
            for (int i = 0; i < attr.cVars; i++)
            {
                AddVar(info, i, list);
            }
            if (attr.typekind == ComTypes.TYPEKIND.TKIND_INTERFACE && attr.cImplTypes > 0 && depth < MaxDepth)
            {
                CollectInterface(RefOfImpl(info, 0), list, depth + 1);
            }
        }

        static ComTypes.TYPEATTR Attr(ComTypes.ITypeInfo info)
        {
            IntPtr ptr;
            info.GetTypeAttr(out ptr);
            try
            {
                return (ComTypes.TYPEATTR)Marshal.PtrToStructure(ptr, typeof(ComTypes.TYPEATTR));
            }
            finally
            {
                info.ReleaseTypeAttr(ptr);
            }
        }

        static SigFunc ReadFunc(ComTypes.ITypeInfo info, int index)
        {
            IntPtr ptr;
            info.GetFuncDesc(index, out ptr);
            try
            {
                ComTypes.FUNCDESC desc = (ComTypes.FUNCDESC)Marshal.PtrToStructure(ptr, typeof(ComTypes.FUNCDESC));
                SigFunc f = new SigFunc();
                f.Name = NameOf(info, desc.memid);
                f.Invoke = (int)desc.invkind;
                ReadParams(desc, f);
                return f;
            }
            finally
            {
                info.ReleaseFuncDesc(ptr);
            }
        }

        static void ReadParams(ComTypes.FUNCDESC desc, SigFunc f)
        {
            int size = Marshal.SizeOf(typeof(ComTypes.ELEMDESC));
            List<bool> byRef = new List<bool>();
            int trailingOpt = 0;
            for (int i = 0; i < desc.cParams; i++)
            {
                IntPtr at = new IntPtr(desc.lprgelemdescParam.ToInt64() + (long)i * size);
                ComTypes.ELEMDESC elem = (ComTypes.ELEMDESC)Marshal.PtrToStructure(at, typeof(ComTypes.ELEMDESC));
                ComTypes.PARAMFLAG flags = elem.desc.paramdesc.wParamFlags;
                if ((flags & (ComTypes.PARAMFLAG.PARAMFLAG_FRETVAL | ComTypes.PARAMFLAG.PARAMFLAG_FLCID)) != 0)
                {
                    continue;
                }
                byRef.Add(IsByRef(elem.tdesc, flags));
                bool optional = (flags & ComTypes.PARAMFLAG.PARAMFLAG_FOPT) != 0;
                trailingOpt = optional ? trailingOpt + 1 : 0;
            }
            // cParamsOpt 是 vararg 式的可选个数（VB6 一般为 0），与逐个参数的 [optional] 取大。
            int opt = Math.Max(trailingOpt, Math.Max(0, (int)desc.cParamsOpt));
            f.ByRef = byRef.ToArray();
            f.Total = f.ByRef.Length;
            f.Required = Math.Max(0, f.Total - opt);
        }

        // [out] 或指向非接口类型的指针（VARIANT*、BSTR*、IFoo**）算按引用；IFoo* 本身是按值传的接口。
        static bool IsByRef(ComTypes.TYPEDESC type, ComTypes.PARAMFLAG flags)
        {
            if ((flags & ComTypes.PARAMFLAG.PARAMFLAG_FOUT) != 0 || (type.vt & VtByRef) != 0)
            {
                return true;
            }
            if (type.vt != VtPtr || type.lpValue == IntPtr.Zero)
            {
                return false;
            }
            ComTypes.TYPEDESC inner = (ComTypes.TYPEDESC)Marshal.PtrToStructure(type.lpValue, typeof(ComTypes.TYPEDESC));
            return inner.vt != VtUserDefined;
        }

        // dispinterface 的公开字段当作无参 get 和单参 put。
        static void AddVar(ComTypes.ITypeInfo info, int index, List<SigFunc> list)
        {
            IntPtr ptr;
            info.GetVarDesc(index, out ptr);
            try
            {
                ComTypes.VARDESC desc = (ComTypes.VARDESC)Marshal.PtrToStructure(ptr, typeof(ComTypes.VARDESC));
                string name = NameOf(info, desc.memid);
                list.Add(VarAccess(name, (int)ComTypes.INVOKEKIND.INVOKE_PROPERTYGET, 0));
                list.Add(VarAccess(name, (int)ComTypes.INVOKEKIND.INVOKE_PROPERTYPUT, 1));
            }
            finally
            {
                info.ReleaseVarDesc(ptr);
            }
        }

        static SigFunc VarAccess(string name, int invoke, int total)
        {
            SigFunc f = new SigFunc();
            f.Name = name;
            f.Invoke = invoke;
            f.Total = total;
            f.Required = total;
            f.ByRef = new bool[total];
            return f;
        }

        static string NameOf(ComTypes.ITypeInfo info, int memid)
        {
            string name;
            string doc;
            int ctx;
            string file;
            info.GetDocumentation(memid, out name, out doc, out ctx, out file);
            return name ?? "";
        }

        public void Dispose()
        {
            foreach (ComTypes.ITypeLib lib in _libs.Values)
            {
                if (lib == null)
                {
                    continue;
                }
                try
                {
                    Marshal.ReleaseComObject(lib);
                }
                catch (Exception)
                {
                }
            }
            _libs.Clear();
            _members.Clear();
        }
    }
}
