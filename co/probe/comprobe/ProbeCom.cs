using System;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;

namespace U8Co
{
    // U8 与 ADO/MSXML 都走 IDispatch。探测程序不引用互操作程序集。
    internal static class ComUtil
    {
        const BindingFlags CallFlag = BindingFlags.InvokeMethod;
        const BindingFlags GetFlag = BindingFlags.GetProperty;
        const BindingFlags SetFlag = BindingFlags.SetProperty;

        public static object Create(string progId)
        {
            // 以 { 开头按 CLSID 创建（没有 ProgID 的类，例如 VB6 运行库的 Collection）。
            Type kind;
            if (progId.StartsWith("{", StringComparison.Ordinal))
            {
                Guid clsid;
                if (!Guid.TryParse(progId, out clsid))
                {
                    throw new BridgeException(400, "bad_request", "CLSID 无效");
                }
                kind = Type.GetTypeFromCLSID(clsid, false);
            }
            else
            {
                kind = Type.GetTypeFromProgID(progId, false);
            }
            if (kind == null)
            {
                return null;
            }
            try
            {
                return Activator.CreateInstance(kind);
            }
            catch (Exception ex)
            {
                if (Unavailable(ex))
                {
                    throw new BridgeException(503, "com_unavailable", "组件无法创建 " + progId);
                }
                throw;
            }
        }

        public static object Call(object target, string name, object[] args)
        {
            try
            {
                return target.GetType().InvokeMember(name, CallFlag, null, target, args);
            }
            catch (TargetInvocationException ex)
            {
                return Rethrow(ex);
            }
        }

        public static object CallRef(object target, string name, object[] args, int[] refIndexes)
        {
            if (refIndexes == null || refIndexes.Length == 0)
            {
                return Call(target, name, args);
            }
            if (args == null)
            {
                throw new BridgeException(500, "internal", "引用参数缺少实参");
            }
            ParameterModifier mod = Mod(args.Length, refIndexes);
            try
            {
                return target.GetType().InvokeMember(
                    name, CallFlag, null, target, args, new ParameterModifier[] { mod }, null, null);
            }
            catch (TargetInvocationException ex)
            {
                return Rethrow(ex);
            }
        }

        public static object Get(object target, string name)
        {
            try
            {
                return target.GetType().InvokeMember(name, GetFlag, null, target, null);
            }
            catch (TargetInvocationException ex)
            {
                return Rethrow(ex);
            }
        }

        public static void Set(object target, string name, object value)
        {
            try
            {
                target.GetType().InvokeMember(name, SetFlag, null, target, new object[] { value });
            }
            catch (TargetInvocationException ex)
            {
                Rethrow(ex);
            }
        }

        public static void Final(object target)
        {
            if (target == null || !Marshal.IsComObject(target))
            {
                return;
            }
            try
            {
                Marshal.FinalReleaseComObject(target);
            }
            catch (Exception)
            {
            }
        }

        public static void ReleaseOne(object target)
        {
            if (target == null || !Marshal.IsComObject(target))
            {
                return;
            }
            try
            {
                Marshal.ReleaseComObject(target);
            }
            catch (Exception)
            {
            }
        }

        static ParameterModifier Mod(int length, int[] refIndexes)
        {
            ParameterModifier mod = new ParameterModifier(length);
            for (int i = 0; i < refIndexes.Length; i++)
            {
                int index = refIndexes[i];
                if (index < 0 || index >= length)
                {
                    throw new BridgeException(400, "bad_request", "引用参数序号无效");
                }
                mod[index] = true;
            }
            return mod;
        }

        static object Rethrow(TargetInvocationException ex)
        {
            if (ex.InnerException != null)
            {
                throw ex.InnerException;
            }
            throw ex;
        }

        static bool Unavailable(Exception ex)
        {
            Exception cur = ex;
            while (cur != null)
            {
                if (cur is COMException || cur is FileNotFoundException || cur is BadImageFormatException)
                {
                    return true;
                }
                cur = cur.InnerException;
            }
            return false;
        }
    }
}
