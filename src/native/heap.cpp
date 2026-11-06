#include "heap.hpp"
#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <new>

namespace
{
    struct alignas(std::max_align_t) Header { std::size_t size; bool tracked; };
    thread_local HeapMetrics metrics;
    thread_local bool enabled = false;
    void add(std::size_t size)
    {
        metrics.allocated += size;
        metrics.live += size;
        metrics.peak = (std::max)(metrics.peak, metrics.live);
        ++metrics.allocations;
    }
}
void heap_begin() { metrics = {}; enabled = true; }
HeapMetrics heap_end() { enabled = false; return metrics; }
extern "C" void* simai_bench_malloc(std::size_t size)
{
    if (size > (std::numeric_limits<std::size_t>::max)() - sizeof(Header)) { return nullptr; }
    auto header = static_cast<Header*>(std::malloc(sizeof(Header) + (std::max)(size, std::size_t(1))));
    if (!header) { return nullptr; }
    header->size = size;
    header->tracked = enabled;
    if (enabled) { add(size); }
    return header + 1;
}
extern "C" void simai_bench_free(void* pointer)
{
    if (!pointer) { return; }
    auto header = static_cast<Header*>(pointer) - 1;
    if (header->tracked && enabled) { metrics.live -= header->size; }
    std::free(header);
}
extern "C" void* simai_bench_calloc(std::size_t count, std::size_t size)
{
    if (size && count > (std::numeric_limits<std::size_t>::max)() / size) { return nullptr; }
    auto pointer = simai_bench_malloc(count * size);
    if (pointer) { std::memset(pointer, 0, count * size); }
    return pointer;
}
extern "C" void* simai_bench_realloc(void* pointer, std::size_t size)
{
    if (!pointer) { return simai_bench_malloc(size); }
    if (!size) { simai_bench_free(pointer); return nullptr; }
    if (size > (std::numeric_limits<std::size_t>::max)() - sizeof(Header)) { return nullptr; }
    auto header = static_cast<Header*>(pointer) - 1;
    const auto old_size = header->size;
    const auto tracked = header->tracked;
    auto replacement = static_cast<Header*>(std::realloc(header, sizeof(Header) + size));
    if (!replacement) { return nullptr; }
    replacement->size = size;
    replacement->tracked = enabled;
    if (enabled)
    {
        if (tracked) { metrics.live -= old_size; }
        add(size);
    }
    return replacement + 1;
}
#ifdef TRACK_CPP_HEAP
void* operator new(std::size_t size)
{
    if (auto pointer = simai_bench_malloc(size)) { return pointer; }
    throw std::bad_alloc();
}
void* operator new[](std::size_t size) { return ::operator new(size); }
void operator delete(void* pointer) noexcept { simai_bench_free(pointer); }
void operator delete[](void* pointer) noexcept { simai_bench_free(pointer); }
void operator delete(void* pointer, std::size_t) noexcept { simai_bench_free(pointer); }
void operator delete[](void* pointer, std::size_t) noexcept { simai_bench_free(pointer); }
#endif
