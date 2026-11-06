#pragma once
#include <cstddef>
#include <cstdint>
struct HeapMetrics
{
    std::uint64_t allocated = 0;
    std::uint64_t peak = 0;
    std::uint64_t live = 0;
    std::uint64_t allocations = 0;
};
void heap_begin();
HeapMetrics heap_end();
