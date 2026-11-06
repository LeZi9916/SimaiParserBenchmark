#pragma once
#include <stdlib.h>
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
void* simai_bench_malloc(size_t size);
void* simai_bench_calloc(size_t count, size_t size);
void* simai_bench_realloc(void* pointer, size_t size);
void simai_bench_free(void* pointer);
#ifdef __cplusplus
}
#endif
#define malloc simai_bench_malloc
#define calloc simai_bench_calloc
#define realloc simai_bench_realloc
#define free simai_bench_free
