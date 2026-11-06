use criterion::{Criterion, SamplingMode};
use serde::Serialize;
use std::collections::HashMap;
use std::fs::File;
use std::hint::black_box;
use std::io;
use std::path::Path;
use std::time::{Duration, Instant};

#[cfg(feature = "allocation-probe")]
mod allocation {
    use std::alloc::{GlobalAlloc, Layout, System};
    use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
    static ACTIVE: AtomicBool = AtomicBool::new(false);
    static ALLOCATED: AtomicU64 = AtomicU64::new(0);
    static LIVE: AtomicU64 = AtomicU64::new(0);
    static PEAK: AtomicU64 = AtomicU64::new(0);
    pub struct Tracked;
    fn add(size: usize) {
        if ACTIVE.load(Ordering::Relaxed) {
            ALLOCATED.fetch_add(size as u64, Ordering::Relaxed);
            let live = LIVE.fetch_add(size as u64, Ordering::Relaxed) + size as u64;
            PEAK.fetch_max(live, Ordering::Relaxed);
        }
    }
    unsafe impl GlobalAlloc for Tracked {
        unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
            let pointer = System.alloc(layout);
            if !pointer.is_null() { add(layout.size()); }
            pointer
        }
        unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
            let pointer = System.alloc_zeroed(layout);
            if !pointer.is_null() { add(layout.size()); }
            pointer
        }
        unsafe fn dealloc(&self, pointer: *mut u8, layout: Layout) {
            if ACTIVE.load(Ordering::Relaxed) { LIVE.fetch_sub(layout.size() as u64, Ordering::Relaxed); }
            System.dealloc(pointer, layout);
        }
        unsafe fn realloc(&self, pointer: *mut u8, layout: Layout, size: usize) -> *mut u8 {
            let result = System.realloc(pointer, layout, size);
            if !result.is_null() && ACTIVE.load(Ordering::Relaxed) {
                LIVE.fetch_sub(layout.size() as u64, Ordering::Relaxed);
                add(size);
            }
            result
        }
    }
    pub fn begin() {
        ALLOCATED.store(0, Ordering::Relaxed); LIVE.store(0, Ordering::Relaxed); PEAK.store(0, Ordering::Relaxed);
        ACTIVE.store(true, Ordering::Relaxed);
    }
    pub fn end() -> (u64, u64, u64) {
        ACTIVE.store(false, Ordering::Relaxed);
        (ALLOCATED.load(Ordering::Relaxed), PEAK.load(Ordering::Relaxed), LIVE.load(Ordering::Relaxed))
    }
}
#[cfg(feature = "allocation-probe")]
#[global_allocator]
static ALLOCATOR: allocation::Tracked = allocation::Tracked;

#[derive(Debug)]
struct Sample<'a> { path: &'a str, bytes: &'a [u8] }
#[derive(Clone, Copy)]
struct Outcome { success: bool, checksum: u64, error: i64 }
fn read_u32(buffer: &[u8], offset: &mut usize) -> io::Result<usize> {
    let end = offset.checked_add(4).ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "Length overflow"))?;
    let bytes = buffer.get(*offset..end).ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "Truncated length"))?;
    *offset = end;
    Ok(u32::from_le_bytes(bytes.try_into().unwrap()) as usize)
}
fn read_slice<'a>(buffer: &'a [u8], offset: &mut usize) -> io::Result<&'a [u8]> {
    let length = read_u32(buffer, offset)?;
    let end = offset.checked_add(length).ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "Length overflow"))?;
    let bytes = buffer.get(*offset..end).ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "Truncated contents"))?;
    *offset = end;
    Ok(bytes)
}
fn corpus(buffer: &[u8]) -> io::Result<Vec<Sample<'_>>> {
    if buffer.get(..8) != Some(b"SIMAI001") { return Err(io::Error::new(io::ErrorKind::InvalidData, "Invalid corpus")); }
    let mut offset = 8;
    let count = read_u32(buffer, &mut offset)?;
    if count == 0 || count > (buffer.len() - offset) / 8 {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "Invalid file count"));
    }
    let samples = (0..count).map(|_| {
        let path = std::str::from_utf8(read_slice(buffer, &mut offset)?).map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "Invalid path"))?;
        Ok(Sample { path, bytes: read_slice(buffer, &mut offset)? })
    }).collect::<io::Result<Vec<_>>>()?;
    if offset != buffer.len() { return Err(io::Error::new(io::ErrorKind::InvalidData, "Trailing corpus bytes")); }
    Ok(samples)
}
fn parse(sample: &Sample, materialize: bool) -> Outcome {
    match std::panic::catch_unwind(|| {
        let content = match std::str::from_utf8(sample.bytes) {
            Ok(value) => value,
            Err(_) => return Outcome { success: false, checksum: 0, error: -2 },
        };
        let (file, state) = maidata::container::lex_maidata(content);
        let mut checksum = 0;
        for chart in file.iter_difficulties() {
            if materialize {
                let mut context = maidata::materialize::MaterializationContext::with_offset(chart.offset().unwrap_or(0.0));
                let notes = context.materialize_insns(chart.iter_insns());
                checksum += notes.len() as u64;
                black_box(&notes);
            } else {
                checksum += chart.iter_insns().count() as u64;
            }
        }
        black_box(&file);
        Outcome { success: !state.has_errors(), checksum, error: state.errors.len() as i64 }
    }) {
        Ok(result) => result,
        Err(_) => Outcome { success: false, checksum: 0, error: -1 },
    }
}
fn run_all(samples: &[Sample], materialize: bool) -> u64 {
    samples.iter().map(|sample| {
        let result = parse(sample, materialize); result.checksum + u64::from(result.success)
    }).sum()
}
#[derive(Serialize)]
#[serde(rename_all = "PascalCase")]
struct FileResult<'a> {
    path: &'a str, bytes: usize, success: bool, inconsistent: bool, error: i64,
    nanoseconds: Vec<f64>, managed_allocated: u64, native_allocated: u64,
    native_peak: u64, native_live_after_free: u64,
}
#[derive(Serialize)]
struct Profile<'a> {
    mode: &'a str, rounds: usize, preloaded: bool, input_files: usize, input_bytes: usize, preloaded_bytes: usize,
    peak_working_set_bytes: usize, peak_paged_memory_bytes: usize,
    files: Vec<FileResult<'a>>,
}
#[repr(C)]
#[derive(Default)]
struct ProcessMemory {
    cb: u32, faults: u32, peak_working: usize, working: usize,
    peak_paged_pool: usize, paged_pool: usize, peak_nonpaged_pool: usize, nonpaged_pool: usize,
    pagefile: usize, peak_pagefile: usize, private: usize,
}
#[link(name = "psapi")]
extern "system" {
    fn GetProcessMemoryInfo(process: *mut std::ffi::c_void, counters: *mut ProcessMemory, size: u32) -> i32;
}
fn memory() -> ProcessMemory {
    let mut info = ProcessMemory { cb: std::mem::size_of::<ProcessMemory>() as u32, ..Default::default() };
    unsafe { GetProcessMemoryInfo(-1isize as *mut _, &mut info, std::mem::size_of::<ProcessMemory>() as u32); }
    info
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    std::panic::set_hook(Box::new(|_| {}));
    let args: Vec<String> = std::env::args().skip(1).collect();
    let options: HashMap<&str, &str> = args.chunks_exact(2).map(|pair| (pair[0].as_str(), pair[1].as_str())).collect();
    // The only input file read: complete and closed before warm-up and Criterion.
    let buffer = std::fs::read(options["--corpus"])?;
    let samples = corpus(&buffer)?;
    let input_bytes: usize = samples.iter().map(|sample| sample.bytes.len()).sum();
    eprintln!("Preloaded corpus: {} files, {} input bytes, {} buffer bytes", samples.len(), input_bytes, buffer.len());
    let materialize = options["--pipeline"] == "materialize";
    let warmups: usize = options["--warmup"].parse()?;
    for _ in 0..warmups { black_box(run_all(&samples, materialize)); }
    let mode = options["--mode"];
    let output = Path::new(options["--out"]);
    if mode == "benchmark" {
        let largest = samples.iter().max_by_key(|sample| sample.bytes.len()).unwrap();
        let mut criterion = Criterion::default().without_plots().sample_size(10)
            .warm_up_time(Duration::from_secs(1))
            .measurement_time(Duration::from_secs(3)).output_directory(output);
        let mut group = criterion.benchmark_group("Parser");
        group.sampling_mode(SamplingMode::Flat);
        group.bench_function("Corpus", |bencher| bencher.iter(|| black_box(run_all(&samples, materialize))));
        group.bench_function("LargestFile", |bencher| bencher.iter(|| black_box(parse(largest, materialize))));
        group.finish();
        criterion.final_summary();
        return Ok(());
    }
    let rounds: usize = options["--rounds"].parse()?;
    let mut results: Vec<_> = samples.iter().map(|sample| FileResult {
        path: sample.path, bytes: sample.bytes.len(), success: false, inconsistent: false, error: 0,
        nanoseconds: vec![0.0; if mode == "timing" { rounds } else { 0 }],
        managed_allocated: 0, native_allocated: 0, native_peak: 0, native_live_after_free: 0,
    }).collect();
    for round in 0..if mode == "timing" { rounds } else { 1 } {
        for (sample, row) in samples.iter().zip(results.iter_mut()) {
            let result;
            if mode == "timing" {
                let started = Instant::now();
                result = parse(sample, materialize);
                row.nanoseconds[round] = started.elapsed().as_nanos() as f64;
            } else {
                #[cfg(not(feature = "allocation-probe"))]
                return Err("Memory mode requires allocation-probe build".into());
                #[cfg(feature = "allocation-probe")]
                {
                    allocation::begin(); result = parse(sample, materialize);
                    let (allocated, peak, live) = allocation::end();
                    row.native_allocated = allocated; row.native_peak = peak; row.native_live_after_free = live;
                }
            }
            if round == 0 { row.success = result.success; row.error = result.error; }
            else if row.success != result.success { row.inconsistent = true; }
        }
    }
    let peak = memory();
    let report = Profile { mode, rounds, preloaded: true, input_files: samples.len(), input_bytes, preloaded_bytes: buffer.len(),
        peak_working_set_bytes: peak.peak_working,
        peak_paged_memory_bytes: peak.peak_pagefile, files: results };
    serde_json::to_writer(File::create(output)?, &report)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> Vec<u8> {
        let mut buffer = b"SIMAI001".to_vec();
        buffer.extend_from_slice(&2u32.to_le_bytes());
        for (name, content) in [("one/maidata.txt", "&inote_1=(120){4}1,E\n"), ("two/maidata.txt", "&inote_7=(150){4}2,E\n")] {
            buffer.extend_from_slice(&(name.len() as u32).to_le_bytes());
            buffer.extend_from_slice(name.as_bytes());
            buffer.extend_from_slice(&(content.len() as u32).to_le_bytes());
            buffer.extend_from_slice(content.as_bytes());
        }
        buffer
    }
    #[test]
    fn every_file_borrows_the_preloaded_buffer() {
        let buffer = fixture();
        let samples = corpus(&buffer).unwrap();
        assert_eq!(samples.len(), 2);
        for sample in &samples {
            let pointer = sample.bytes.as_ptr() as usize;
            let start = buffer.as_ptr() as usize;
            assert!(pointer >= start && pointer + sample.bytes.len() <= start + buffer.len());
            assert!(parse(sample, false).success);
        }
    }
    #[test]
    fn malformed_or_partial_corpus_is_rejected() {
        let mut buffer = fixture();
        assert!(corpus(&buffer[..buffer.len() - 1]).is_err());
        buffer.push(0);
        assert!(corpus(&buffer).is_err());
        assert!(corpus(b"SIMAI001\0\0\0\0").is_err());
    }

    #[test]
    fn valid_chart_is_accepted_by_both_pipelines() {
        let sample = Sample { path: "", bytes: b"&title=Test\n&inote_1=(120){4}1,2,3,4,E\n" };
        for materialize in [false, true] {
            let result = parse(&sample, materialize);
            assert!(result.success); assert!(result.checksum > 0);
        }
    }
    #[cfg(feature = "allocation-probe")]
    #[test]
    fn allocation_probe_counts_realloc_and_release() {
        use std::alloc::{alloc, dealloc, realloc, Layout};
        allocation::begin();
        unsafe {
            let layout = Layout::from_size_align(128, 8).unwrap();
            let pointer = alloc(layout);
            assert!(!pointer.is_null());
            let pointer = realloc(pointer, layout, 256);
            assert!(!pointer.is_null());
            dealloc(pointer, Layout::from_size_align(256, 8).unwrap());
        }
        let (allocated, peak, live) = allocation::end();
        assert_eq!(allocated, 384);
        assert_eq!(peak, 256);
        assert_eq!(live, 0);
    }
    #[test]
    fn invalid_utf8_is_a_failure() {
        let sample = Sample { path: "", bytes: &[0xff, 0xfe] };
        assert_eq!(parse(&sample, false).error, -2);
    }
}
