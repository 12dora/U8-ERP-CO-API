using System;

namespace U8Co
{
    internal sealed class BridgeException : Exception
    {
        public int Status { get; private set; }
        public string Code { get; private set; }

        public BridgeException(int status, string code, string message)
            : base(message ?? "")
        {
            Status = status;
            Code = code;
        }
    }

    internal static class Values
    {
        public static string Text(object value)
        {
            if (value == null || value is DBNull)
            {
                return "";
            }
            return Convert.ToString(value);
        }

        public static bool Flag(object value)
        {
            if (value == null || value is DBNull)
            {
                return false;
            }
            if (value is bool)
            {
                return (bool)value;
            }
            string text = value as string;
            if (text != null)
            {
                return text == "1" || text.Equals("true", StringComparison.OrdinalIgnoreCase);
            }
            try
            {
                return Convert.ToInt64(value) != 0;
            }
            catch (Exception)
            {
                return false;
            }
        }
    }
}
