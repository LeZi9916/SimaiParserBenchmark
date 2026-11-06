using System.Diagnostics;
using System.Globalization;
using System.Text.Json;

namespace SimaiBench;

public static class ProbeProgram
{
    public static void Main(string[] args)
    {
        var options = args.Chunk(2).ToDictionary(x => x[0], x => x[1]);
        var corpus = new Corpus(options["--corpus"]);
        var output = options["--out"];
        var mode = options["--mode"];
        var rounds = int.Parse(options["--rounds"]);
        var warmups = int.Parse(options["--warmup"]);
        Adapter.Initialize(options["--tolerance"]);
        for (var i = 0; i < warmups; i++)
        {
            foreach (var sample in corpus.Samples)
            {
                Adapter.Parse(sample);
            }
        }
        var results = new FileResult[corpus.Samples.Length];
        for (var i = 0; i < results.Length; i++)
        {
            results[i] = new FileResult { Path = corpus.Samples[i].Path, Bytes = corpus.Samples[i].Bytes.Length,
                Nanoseconds = new double[mode == "timing" ? rounds : 0] };
        }
        GC.Collect();
        GC.WaitForPendingFinalizers();
        GC.Collect();
        var total = new double[mode == "timing" ? rounds : 0];
        if (mode == "timing")
        {
            for (var round = 0; round < rounds; round++)
            {
                var allStart = Stopwatch.GetTimestamp();
                for (var i = 0; i < results.Length; i++)
                {
                    var start = Stopwatch.GetTimestamp();
                    var result = Adapter.Parse(corpus.Samples[i]);
                    var elapsed = Stopwatch.GetTimestamp() - start;
                    results[i].Nanoseconds[round] = elapsed * (1e9 / Stopwatch.Frequency);
                    if (round == 0)
                    {
                        results[i].Success = result.Success;
                        results[i].Error = result.Error;
                        results[i].ChartAttempts = result.ChartAttempts;
                        results[i].FailedCharts = result.FailedCharts;
                    }
                    else if (results[i].Success != result.Success || results[i].ChartAttempts != result.ChartAttempts || results[i].FailedCharts != result.FailedCharts)
                    {
                        results[i].Inconsistent = true;
                    }
                }
                total[round] = (Stopwatch.GetTimestamp() - allStart) * (1e9 / Stopwatch.Frequency);
            }
        }
        else
        {
            for (var i = 0; i < results.Length; i++)
            {
                // Process-wide, precise: includes MajSimai's Parallel.For worker allocations.
                var before = GC.GetTotalAllocatedBytes(precise: true);
                var result = Adapter.Parse(corpus.Samples[i]);
                var after = GC.GetTotalAllocatedBytes(precise: true);
                results[i].ManagedAllocated = after - before;
                results[i].Success = result.Success;
                results[i].Error = result.Error;
                results[i].ChartAttempts = result.ChartAttempts;
                results[i].FailedCharts = result.FailedCharts;
            }
        }
        using var process = Process.GetCurrentProcess();
        process.Refresh();
        var metadata = new ProbeResult
        {
            Mode = mode, Rounds = rounds, Warmups = warmups, StopwatchFrequency = Stopwatch.Frequency,
            Preloaded = true, InputFiles = corpus.Samples.Length, InputBytes = corpus.Bytes, InputCharts = corpus.Charts, PreloadedBytes = corpus.PreloadedBytes,
            PeakWorkingSetBytes = process.PeakWorkingSet64,
            PeakPagedMemoryBytes = process.PeakPagedMemorySize64,
            InstrumentedTotalNanoseconds = total, Files = results
        };
        Directory.CreateDirectory(Path.GetDirectoryName(output)!);
        File.WriteAllText(output, JsonSerializer.Serialize(metadata, ProbeJsonContext.Default.ProbeResult));
    }
}

public sealed class FileResult
{
    public string Path { get; set; } = "";
    public int Bytes { get; set; }
    public bool Success { get; set; }
    public string? Error { get; set; }
    public bool Inconsistent { get; set; }
    public double[] Nanoseconds { get; set; } = [];
    public int ChartAttempts { get; set; } = -1;
    public int FailedCharts { get; set; } = -1;
    public long ManagedAllocated { get; set; }
}

// NativeAOT uses generated JSON metadata; no reflection serializer fallback.
[System.Text.Json.Serialization.JsonSerializable(typeof(FileResult[]))]
[System.Text.Json.Serialization.JsonSerializable(typeof(ProbeResult))]
internal partial class ProbeJsonContext : System.Text.Json.Serialization.JsonSerializerContext { }

public sealed class ProbeResult
{
    public bool Preloaded { get; set; }
    public int InputFiles { get; set; }
    public long InputBytes { get; set; }
    public int InputCharts { get; set; }
    public int PreloadedBytes { get; set; }
    public string Mode { get; set; } = "";
    public int Rounds { get; set; }
    public int Warmups { get; set; }
    public long StopwatchFrequency { get; set; }
    public long PeakWorkingSetBytes { get; set; }
    public long PeakPagedMemoryBytes { get; set; }
    public double[] InstrumentedTotalNanoseconds { get; set; } = [];
    public FileResult[] Files { get; set; } = [];
}
