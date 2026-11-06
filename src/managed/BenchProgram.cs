using BenchmarkDotNet.Attributes;
using BenchmarkDotNet.Columns;
using BenchmarkDotNet.Configs;
using BenchmarkDotNet.Diagnosers;
using BenchmarkDotNet.Environments;
using BenchmarkDotNet.Exporters.Json;
using BenchmarkDotNet.Jobs;
using BenchmarkDotNet.Running;
using BenchmarkDotNet.Toolchains.NativeAot;
using Perfolizer.Mathematics.OutlierDetection;

namespace SimaiBench;

public static class BenchProgram
{
    public static void Main()
    {
        var job = Job.Default
            .WithId(Environment.GetEnvironmentVariable("SIMAI_RUNTIME") ?? "JIT")
            .WithRuntime(Environment.GetEnvironmentVariable("SIMAI_RUNTIME") == "NativeAOT"
                ? NativeAotRuntime.Net10_0 : CoreRuntime.Core10_0)
            .DontEnforcePowerPlan()
            .WithGcForce(false)
            .WithLaunchCount(1)
            .WithWarmupCount(int.Parse(Environment.GetEnvironmentVariable("SIMAI_WARMUP") ?? "2"))
            .WithIterationCount(int.Parse(Environment.GetEnvironmentVariable("SIMAI_ITERATIONS") ?? "5"))
            .WithInvocationCount(1)
            .WithUnrollFactor(1)
            .WithOutlierMode(OutlierMode.DontRemove)
            .WithGcServer(false)
            .WithGcConcurrent(false)
            .WithEnvironmentVariable("DOTNET_TieredCompilation", "0")
            .WithEnvironmentVariable("DOTNET_TieredPGO", "0")
            .WithEnvironmentVariable("DOTNET_ReadyToRun", "0")
            .WithEnvironmentVariable("SIMAI_CORPUS", Environment.GetEnvironmentVariable("SIMAI_CORPUS")!)
            .WithEnvironmentVariable("SIMAI_TOLERANCE", Environment.GetEnvironmentVariable("SIMAI_TOLERANCE") ?? "Lenient");
        if (Environment.GetEnvironmentVariable("SIMAI_RUNTIME") == "NativeAOT")
        {
            var builder = NativeAotToolchain.CreateBuilder();
            builder.UseNuGet(Environment.GetEnvironmentVariable("SIMAI_AOT_VERSION") ?? "10.0.12");
            builder.TargetFrameworkMoniker("net10.0");
            builder.IlcOptimizationPreference("Speed");
            builder.IlcInstructionSet("");
            job = job.WithToolchain(builder.ToToolchain());
        }
        var config = ManualConfig.Create(DefaultConfig.Instance)
            .AddJob(job)
            .AddColumn(StatisticColumn.Median)
            .AddDiagnoser(MemoryDiagnoser.Default)
            .AddExporter(JsonExporter.Full)
            .WithArtifactsPath(Environment.GetEnvironmentVariable("SIMAI_ARTIFACTS")!);
        var summary = BenchmarkRunner.Run<ParserBenchmarks>(config);
        if (summary.Reports.Any(x => !x.Success))
        {
            Environment.ExitCode = 1;
        }
    }
}

public class ParserBenchmarks
{
    private Corpus _corpus = null!;

    [GlobalSetup]
    public void Setup()
    {
        _corpus = new Corpus(Environment.GetEnvironmentVariable("SIMAI_CORPUS")!);
        Console.WriteLine($"// Preloaded corpus: {_corpus.Samples.Length} files, {_corpus.Bytes} input bytes, {_corpus.Charts} chart fields, {_corpus.PreloadedBytes} buffer bytes");
        Adapter.Initialize(Environment.GetEnvironmentVariable("SIMAI_TOLERANCE") ?? "Lenient");
    }

    [Benchmark]
    public long Corpus()
    {
        long value = 0;
        foreach (var sample in _corpus.Samples)
        {
            var outcome = Adapter.Parse(sample);
            value += outcome.Checksum + (outcome.Success ? 1 : 0);
        }
        return value;
    }

    [Benchmark]
    public long LargestFile()
    {
        var outcome = Adapter.Parse(_corpus.Largest);
        return outcome.Checksum + (outcome.Success ? 1 : 0);
    }
}
