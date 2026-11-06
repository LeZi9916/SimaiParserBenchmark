#include <benchmark/benchmark.h>
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <string_view>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#define NOMINMAX
#include <windows.h>
#include <psapi.h>
#include "heap.hpp"
#ifdef PARSER_CIMAI
#include <cimai/cimai.h>
#else
#include <MajSimai/MajSimai.h>
extern "C" __declspec(dllimport) void Benchmark_Init();
extern "C" __declspec(dllimport) std::uint64_t Benchmark_ManagedAllocated();
extern "C" __declspec(dllimport) void Benchmark_NativeBegin();
extern "C" __declspec(dllimport) std::uint64_t Benchmark_NativeAllocated();
extern "C" __declspec(dllimport) std::uint64_t Benchmark_NativePeak();
extern "C" __declspec(dllimport) std::uint64_t Benchmark_NativeLive();
#endif

struct Sample { std::string path; std::string_view content; };
struct Outcome { bool success; std::uint64_t checksum; int error = 0; };
struct FileResult
{
    bool success = false;
    bool inconsistent = false;
    int error = 0;
    std::vector<double> ns;
    std::uint64_t managed = 0, native = 0, native_peak = 0, native_live = 0;
};
std::vector<char> corpus_buffer;
std::vector<Sample> samples;
std::size_t input_bytes = 0;
bool strict = false, owned = false;
std::uint32_t read_u32(std::size_t& offset)
{
    if (corpus_buffer.size() - offset < 4) { throw std::runtime_error("Truncated corpus length."); }
    const auto bytes = reinterpret_cast<const unsigned char*>(corpus_buffer.data() + offset);
    const auto value = std::uint32_t(bytes[0]) | (std::uint32_t(bytes[1]) << 8) |
        (std::uint32_t(bytes[2]) << 16) | (std::uint32_t(bytes[3]) << 24);
    offset += 4;
    return value;
}
std::string_view read_slice(std::size_t& offset)
{
    const auto length = read_u32(offset);
    if (length > corpus_buffer.size() - offset) { throw std::runtime_error("Truncated corpus contents."); }
    const auto value = std::string_view(corpus_buffer.data() + offset, length);
    offset += length;
    return value;
}
void load_corpus(const std::string& path)
{
    // Read and close the input once. Every parse receives a non-owning slice of this buffer.
    {
        std::ifstream stream(path, std::ios::binary | std::ios::ate);
        if (!stream || stream.tellg() < 12) { throw std::runtime_error("Invalid corpus."); }
        corpus_buffer.resize(static_cast<std::size_t>(stream.tellg()));
        stream.seekg(0);
        stream.read(corpus_buffer.data(), static_cast<std::streamsize>(corpus_buffer.size()));
        if (!stream) { throw std::runtime_error("Cannot preload the corpus."); }
    }
    if (std::memcmp(corpus_buffer.data(), "SIMAI001", 8) != 0) { throw std::runtime_error("Invalid corpus magic."); }
    std::size_t offset = 8;
    const auto count = read_u32(offset);
    if (!count || count > (corpus_buffer.size() - offset) / 8) { throw std::runtime_error("Invalid corpus file count."); }
    samples.reserve(count);
    for (std::uint32_t i = 0; i < count; ++i)
    {
        const auto name = read_slice(offset);
        const auto content = read_slice(offset);
        samples.push_back({std::string(name), content});
        input_bytes += content.size();
    }
    if (offset != corpus_buffer.size()) { throw std::runtime_error("Trailing corpus bytes."); }
    std::cerr << "Preloaded corpus: " << samples.size() << " files, " << input_bytes
        << " input bytes, " << corpus_buffer.size() << " buffer bytes\n";
}
Outcome parse(const Sample& sample)
{
#ifdef PARSER_CIMAI
    String_View input = sv_from_parts(sample.content.data(), sample.content.size());
    SimaiFile file = {};
    cimai_parse(&input, &file);
    std::uint64_t count = 0;
    for (auto chart : file.charts) { if (chart) { count += chart->notes.len; } }
    cimai_file_release(&file);
    return {true, count}; // cimai has no validation/error result API.
#else
    try
    {
        const auto tolerance = strict ? MajSimai::SimaiSyntaxTolerance::Strict : MajSimai::SimaiSyntaxTolerance::Lenient;
        std::uint64_t count = 0;
        if (owned)
        {
            auto file = MajSimai::SimaiParser::Parse(sample.content, tolerance);
            for (const auto& chart : file.charts)
            {
                for (const auto& timing : chart.noteTimings) { count += timing.notes.size(); }
            }
        }
        else
        {
            auto file = MajSimai::SimaiParser::ParseAsView(sample.content, tolerance);
            for (const auto chart : file.GetFile().GetCharts())
            {
                for (const auto timing : chart.GetNoteTimings()) { count += timing.GetNotes().Size(); }
            }
        }
        return {true, count};
    }
    catch (const MajSimai::SimaiParseException& error) { return {false, 0, static_cast<int>(error.GetCode())}; }
#endif
}
std::uint64_t run_all()
{
    std::uint64_t checksum = 0;
    for (const auto& sample : samples)
    {
        const auto result = parse(sample);
        checksum += result.checksum + result.success;
    }
    return checksum;
}
void quoted(std::ostream& stream, const std::string& text)
{
    stream << '"';
    for (const auto c : text)
    {
        if (c == '"' || c == '\\') { stream << '\\'; }
        if (static_cast<unsigned char>(c) < 32) { stream << '?'; } else { stream << c; }
    }
    stream << '"';
}
void profile(const std::string& path, const std::string& mode, int rounds)
{
    std::vector<FileResult> results(samples.size());
    for (auto& result : results) { result.ns.resize(mode == "timing" ? rounds : 0); }
    const auto repeats = mode == "timing" ? rounds : 1;
    for (int round = 0; round < repeats; ++round)
    {
        for (std::size_t i = 0; i < samples.size(); ++i)
        {
            auto& row = results[i];
            if (mode == "memory")
            {
                std::uint64_t before = 0;
#ifndef PARSER_CIMAI
                before = Benchmark_ManagedAllocated();
                Benchmark_NativeBegin();
#endif
                heap_begin();
                const auto result = parse(samples[i]);
                const auto memory = heap_end();
                row.native = memory.allocated;
                row.native_peak = memory.peak;
                row.native_live = memory.live;
#ifndef PARSER_CIMAI
                row.managed = Benchmark_ManagedAllocated() - before;
                row.native += Benchmark_NativeAllocated();
                row.native_peak += Benchmark_NativePeak();
                row.native_live += Benchmark_NativeLive();
#endif
                row.success = result.success; row.error = result.error;
            }
            else
            {
                const auto start = std::chrono::steady_clock::now();
                const auto result = parse(samples[i]);
                const auto end = std::chrono::steady_clock::now();
                row.ns[round] = std::chrono::duration<double, std::nano>(end - start).count();
                if (!round) { row.success = result.success; row.error = result.error; }
                else if (row.success != result.success) { row.inconsistent = true; }
            }
        }
    }
    PROCESS_MEMORY_COUNTERS_EX counters{};
    GetProcessMemoryInfo(GetCurrentProcess(), reinterpret_cast<PROCESS_MEMORY_COUNTERS*>(&counters), sizeof(counters));
    std::ofstream stream(path);
    stream << std::setprecision(17) << "{\"mode\":\"" << mode << "\",\"rounds\":" << rounds
        << ",\"preloaded\":true,\"input_files\":" << samples.size()
        << ",\"input_bytes\":" << input_bytes << ",\"preloaded_bytes\":" << corpus_buffer.size()
        << ",\"peak_working_set_bytes\":" << counters.PeakWorkingSetSize
        << ",\"peak_paged_memory_bytes\":" << counters.PeakPagefileUsage << ",\"files\":[";
    for (std::size_t i = 0; i < results.size(); ++i)
    {
        if (i) { stream << ','; }
        const auto& row = results[i];
        stream << "{\"Path\":"; quoted(stream, samples[i].path);
        stream << ",\"Bytes\":" << samples[i].content.size() << ",\"Success\":" << (row.success ? "true" : "false")
            << ",\"Inconsistent\":" << (row.inconsistent ? "true" : "false") << ",\"Error\":" << row.error
            << ",\"ManagedAllocated\":" << row.managed << ",\"NativeAllocated\":" << row.native
            << ",\"NativePeak\":" << row.native_peak << ",\"NativeLiveAfterFree\":" << row.native_live << ",\"Nanoseconds\":[";
        for (std::size_t j = 0; j < row.ns.size(); ++j) { if (j) { stream << ','; } stream << row.ns[j]; }
        stream << "]}";
    }
    stream << "]}";
    if (!stream) { throw std::runtime_error("Could not write profile."); }
}
int main(int argc, char** argv)
{
    try
    {
        std::string corpus, output, mode;
        int rounds = 5, warmup = 2, repetitions = 5;
        std::vector<char*> google_args{argv[0]};
        for (int i = 1; i < argc; ++i)
        {
            const std::string key = argv[i];
            if (key.rfind("--benchmark_", 0) == 0) { google_args.push_back(argv[i]); continue; }
            if (i + 1 >= argc) { throw std::runtime_error("Missing argument value."); }
            const std::string value = argv[++i];
            if (key == "--corpus") { corpus = value; }
            else if (key == "--out") { output = value; }
            else if (key == "--mode") { mode = value; }
            else if (key == "--tolerance") { strict = value == "Strict"; }
            else if (key == "--wrapper") { owned = value == "model"; }
            else if (key == "--rounds") { rounds = std::stoi(value); }
            else if (key == "--warmup") { warmup = std::stoi(value); }
            else if (key == "--repetitions") { repetitions = std::stoi(value); }
            else { throw std::runtime_error("Unknown argument: " + key); }
        }
        load_corpus(corpus);
#ifndef PARSER_CIMAI
        Benchmark_Init();
#endif
        for (int i = 0; i < warmup; ++i) { benchmark::DoNotOptimize(run_all()); }
        if (mode != "benchmark") { profile(output, mode, rounds); return 0; }
        const auto largest = static_cast<std::size_t>(std::max_element(samples.begin(), samples.end(),
            [](const Sample& a, const Sample& b) { return a.content.size() < b.content.size(); }) - samples.begin());
        benchmark::RegisterBenchmark("Corpus", [](benchmark::State& state)
        {
            for (auto iteration : state) { benchmark::DoNotOptimize(run_all()); }
        })->Iterations(1)->Repetitions(repetitions)->UseRealTime()->Unit(benchmark::kMillisecond);
        benchmark::RegisterBenchmark("LargestFile", [largest](benchmark::State& state)
        {
            for (auto iteration : state) { benchmark::DoNotOptimize(parse(samples[largest]).checksum); }
        })->Iterations(1)->Repetitions(repetitions)->UseRealTime()->Unit(benchmark::kMillisecond);
        auto google_argc = static_cast<int>(google_args.size());
        benchmark::Initialize(&google_argc, google_args.data());
        if (benchmark::ReportUnrecognizedArguments(google_argc, google_args.data())) { return 1; }
        benchmark::RunSpecifiedBenchmarks();
        benchmark::Shutdown();
        return 0;
    }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
