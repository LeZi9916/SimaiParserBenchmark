using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using MajSimai.Logging;

namespace SimaiBench;

public static class NativeExports
{
    [UnmanagedCallersOnly(EntryPoint = "Benchmark_Init", CallConvs = [typeof(CallConvCdecl)])]
    public static void Initialize()
    {
        Console.SetOut(TextWriter.Null);
        SimaiLogger.Current = new SilentLogger();
        NativeHeap.Prepare();
    }
    [UnmanagedCallersOnly(EntryPoint = "Benchmark_ManagedAllocated", CallConvs = [typeof(CallConvCdecl)])]
    public static ulong Allocated() => (ulong)GC.GetTotalAllocatedBytes(precise: true);
    [UnmanagedCallersOnly(EntryPoint = "Benchmark_NativeBegin", CallConvs = [typeof(CallConvCdecl)])]
    public static void Begin() => NativeHeap.Begin();
    [UnmanagedCallersOnly(EntryPoint = "Benchmark_NativeAllocated", CallConvs = [typeof(CallConvCdecl)])]
    public static ulong NativeAllocated() => NativeHeap.Allocated;
    [UnmanagedCallersOnly(EntryPoint = "Benchmark_NativePeak", CallConvs = [typeof(CallConvCdecl)])]
    public static ulong NativePeak() => NativeHeap.Peak;
    [UnmanagedCallersOnly(EntryPoint = "Benchmark_NativeLive", CallConvs = [typeof(CallConvCdecl)])]
    public static ulong NativeLive() => NativeHeap.Live;
    private sealed class SilentLogger : ISimaiLogger
    {
        public bool IsEnabled(SimaiLogLevel level) => false;
        public void Log(SimaiLogLevel level, string message, Exception? exception) { }
    }
}

// Only the diagnostic library redirects Marshal calls. Timing uses pristine parser source.
public static class NativeHeap
{
    private static readonly Dictionary<nint, ulong> Sizes = new();
    private static bool Enabled;
    public static ulong Allocated { get; private set; }
    public static ulong Live { get; private set; }
    public static ulong Peak { get; private set; }
    public static void Prepare() => Sizes.EnsureCapacity(64);
    public static void Begin()
    {
        if (Live != 0)
        {
            throw new InvalidOperationException("Native output memory leaked between files.");
        }
        Allocated = Live = Peak = 0;
        Enabled = true;
    }
    public static nint AllocHGlobal(nint size)
    {
        var pointer = Marshal.AllocHGlobal(size);
        if (Enabled)
        {
            var bytes = checked((ulong)size);
            Sizes.Add(pointer, bytes);
            Allocated += bytes;
            Live += bytes;
            Peak = Math.Max(Peak, Live);
        }
        return pointer;
    }
    public static void FreeHGlobal(nint pointer)
    {
        if (Sizes.Remove(pointer, out var bytes))
        {
            Live -= bytes;
        }
        Marshal.FreeHGlobal(pointer);
    }
}
