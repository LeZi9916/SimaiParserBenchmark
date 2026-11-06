using System.Text;
#if MAJ_REFACTOR
using MajSimai;
using MajSimai.Logging;
#endif

namespace SimaiBench;

public readonly record struct Outcome(bool Success, long Checksum, string? Error = null, int ChartAttempts = -1, int FailedCharts = -1);

public static class Adapter
{
    private static readonly CountingWriter Quiet = new();
    private static bool Strict;

    public static void Initialize(string tolerance)
    {
        Strict = tolerance == "Strict";
        Console.SetOut(Quiet);
#if MAJ_REFACTOR
        SimaiLogger.Current = new SilentLogger();
#endif
    }

    public static Outcome Parse(Sample sample)
    {
        Quiet.Reset();
        try
        {
#if MAJ_LEGACY || MAJ_REFACTOR
            var segment = sample.Segment;
            using var stream = new MemoryStream(segment.Array!, segment.Offset, segment.Count, writable: false);
#if MAJ_REFACTOR
            var file = SimaiParser.Parse(stream, Strict ? SimaiParserOptions.Strict : SimaiParserOptions.Lenient);
#else
            var file = MajSimai.SimaiParser.Parse(stream);
#endif
            long count = 0;
            foreach (var chart in file.Charts)
            {
                if (chart is not null)
                {
                    foreach (var timing in chart.NoteTimings)
                    {
                        count += timing.Notes.Length;
                    }
                }
            }
            GC.KeepAlive(file);
            // 2.2.3 catches chart errors and writes them to Console. Include those failures.
            return new Outcome(Quiet.Count == 0, count, Quiet.Count == 0 ? null : "SuppressedChartException");
#else
            return ParseCharts(sample.Bytes, ParseChart);
#endif
        }
        catch (Exception error)
        {
            return new Outcome(false, 0, error.GetType().FullName);
        }
    }


    /// <summary>Attempts every difficulty even if an earlier chart is rejected.</summary>
    public static Outcome ParseCharts(ReadOnlyMemory<byte> content, Func<ReadOnlyMemory<byte>, long> parseChart)
    {
        long count = 0;
        var attempts = 0;
        var failures = 0;
        string? firstError = null;
        foreach (var entry in ChartEntries.Read(content))
        {
            attempts++;
            try
            {
                count += parseChart(entry);
            }
            catch (Exception error)
            {
                failures++;
                firstError ??= error.GetType().FullName;
            }
        }
        return new Outcome(failures == 0, count, firstError, attempts, failures);
    }

#if SHARP_MASTER || SHARP_ONEPASS
    private static long ParseChart(ReadOnlyMemory<byte> entry)
    {
#if SHARP_MASTER
        var chart = SimaiSharp.SimaiConvert.Deserialize(Encoding.UTF8.GetString(entry.Span));
        var count = chart.NoteCollections.Length;
#else
        var chart = SimaiSharp.SimaiConvert.Deserialize(entry.Span);
        var count = chart.noteGroups.Count;
#endif
        GC.KeepAlive(chart);
        return count;
    }
#endif

    private sealed class CountingWriter : TextWriter
    {
        private long _count;
        public override Encoding Encoding => Encoding.UTF8;
        public long Count => Interlocked.Read(ref _count);
        public void Reset() => Interlocked.Exchange(ref _count, 0);
        public override void Write(char value) => Interlocked.Increment(ref _count);
        public override void Write(string? value)
        {
            if (!string.IsNullOrEmpty(value))
            {
                Interlocked.Increment(ref _count);
            }
        }
        public override void WriteLine(object? value) => Interlocked.Increment(ref _count);
    }
#if MAJ_REFACTOR
    private sealed class SilentLogger : ISimaiLogger
    {
        public bool IsEnabled(SimaiLogLevel level) => false;
        public void Log(SimaiLogLevel level, string message, Exception? exception) { }
    }
#endif
}
