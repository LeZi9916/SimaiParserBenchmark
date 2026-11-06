# SimaiParserBenchmark

统一比较 cimai、MajSimai、SimaiSharp 和 maidata-rs 的可复现基准程序。完整输入为 `SimaiTestSamples/**/maidata.txt`，不包含磁盘 I/O 的计时。

测试集的谱面源自 [MajdataNET](https://majdata.net)，谱面作者保留所有权利。

## 环境

- Windows x64、Python 3.12+、Git、CMake 3.21+、Ninja。
- .NET SDK 10.0.401（global.json 固定）、Rust 1.99.1 和 Visual Studio C++ x64 构建工具、Windows SDK（NativeAOT 需要）。
- 首次构建需要访问 NuGet、Google Benchmark 仓库和 crates.io。缺少 Rust 时，runner 会在 `.work/toolchains` 安装固定版本的最小工具链，不修改系统 PATH。
- 项目路径和测试参数在 `benchmark-config.json` 配置；默认解析器仓库为 `modules/cimai`、`modules/MajSimai`、`modules/SimaiSharp` 和 `modules/maidata-rs`，相对路径均以项目根目录为基准。

## 运行

克隆本项目时使用 `git clone --recurse-submodules <本项目仓库地址>`。已有工作区先初始化解析器子模块：

```powershell
git submodule update --init --recursive
```

在本目录的 PowerShell 中执行：

```powershell
# 一次完成源码快照、全部构建、全量测试、报告
python tools/bench.py all --output artifacts/canonical-full

# 分步运行
python tools/bench.py prepare
python tools/bench.py build
python tools/bench.py run --output artifacts/canonical-full

# 快速验证：固定抽取 32 个文件，必含最大文件；不能当作全量结果
python tools/bench.py run --limit 32 --output artifacts/smoke

# 某组合失败或中断后继续；已完成组合跳过
python tools/bench.py run --output artifacts/canonical-full --resume

# 单独选择组合 / 单独重新生成报告
python tools/bench.py run --only 'maj-refactor.*Strict' --output artifacts/strict
python tools/bench.py report --output artifacts/canonical-full

# 输入、矩阵、统计回归测试
python -m unittest discover -s tests -v
dotnet run --project tests/ManagedTests.csproj -c Release
```

`build --only` 选择变体名，例如 `--only sharp-master`。`run --only` 对完整组合名匹配正则。构建与性能测试串行执行；正式运行时应避免并行构建和其他 CPU 密集任务。

项目随附 `sources.lock.json`，`prepare` 始终从其中的固定 commit 导出源码。子模块中的未提交改动不参与测试，也不切换子模块分支。生成或刷新锁文件时，主要测试版本读取各子模块的 HEAD；MajSimai 2.2.3 和 SimaiSharp master 分别从同一子模块的 tag 和分支导出。需要更新版本时，先执行 `git submodule update --remote` 更新子模块，再使用新的 `.work` 目录执行 `python tools/bench.py prepare --refresh-sources`，并一并提交子模块指针和锁文件；已有不同 commit 的快照不会被自动覆盖。MajSimai 原 `refactor/main-3730i8` 远端分支已不存在，当前仍保留其锁定 commit，后续更新跟踪 `master`。

## 测试矩阵

| 项目 | 版本 | 调用方式 | 编译产物 | 宽容度 |
|---|---|---|---|---|
| cimai | 首次运行的 HEAD，之后锁定 commit | C API，C++ 驱动 | MSVC 原生 | API 无宽容度参数 |
| MajSimai | 2.2.3 | C# 完整文件 API | JIT、NativeAOT | 固有 Lenient 行为 |
| MajSimai | refactor/main-3730i8 | C# 完整文件 API | JIT、NativeAOT | Lenient、Strict |
| MajSimai | refactor/main-3730i8 | CppGen wrapper 的 ParseAsView、Parse | C++ + NativeAOT shared library | Lenient、Strict |
| maidata-rs | 固定 HEAD commit | 解析指令 / 解析并 materialize | Rust Release | 诊断 errors 非空或 panic 计失败 |
| SimaiSharp | master、feat/one-pass-parser | 两分支相同文件容器扫描 + 所有非空 inote_1..7 的 Deserialize | JIT、NativeAOT | API 无宽容度参数 |

矩阵共有 23 行，其中 17 行可实际测试。MajSimai 2.2.3 没有 Strict API 或 CppGen，因此对应 6 行明确列为不支持，不模拟这些能力。两个 C++ 入口分别反映原生视图和复制为 C++ 对象的代价。

为了统一运行时，解析库源码在独立 SDK 项目中以 net10.0 编译；2.2.3 原项目的最高目标框架为 net9.0，因此报告明确记录这一实验设置。NativeAOT 编译器固定为配置中的版本。C/C++ 使用 Google Benchmark 1.9.5 固定 commit，C# 使用 BenchmarkDotNet 0.15.8，Rust 使用 Criterion 0.5.1。Criterion 至少使用 10 个样本，Flat 模式、1 秒框架预热、3 秒目标测量时间；其两种流程分别生成 AST 和带时间音符，报告分开列示。

## 输出与指标

`artifacts/canonical-full/report.md` 为中文完整报告，同时生成：

- `summary.csv`：列出固定参照 cimai 与成功率达到门槛的组合；`summary.json` 的 combinations 保存这些主表结果，all_combinations 保存完整诊断状态与排除原因；`all-combinations.csv` 保存全部结果。指标包括总耗时中位数、MiB/s、文件/s、单文件 P50/P99、最大文件耗时、最大单文件累计分配、RSS/commit 峰值、成功率、相对 cimai 的耗时倍率（`relative_to_baseline`），`baseline_id` 指明参照。
- `failures.csv`：每种组合失败的文件与错误。
- `common-success.txt`：达到门槛的组合共同接受且 cimai 完成调用的文件名单；报告额外比较这部分文件的诊断耗时，并以 cimai 在同一子集上的耗时作为 1.000× 参照，避免提前拒绝导致的排名误导。
- `raw/<组合>/`：框架原始结果、每文件多轮耗时与难度尝试计数、每文件分配以及完整命令与日志。
- `corpus.manifest.json`：输入顺序、字节数、每文件 SHA-256。
- `run.json` / `build-info.json` / `instrumentation.json`：环境、源码 commit、参数、内存专用插桩信息。

统计口径：

1. 总耗时与最大文件使用框架正式测量样本的中位数。C/C++、C# 默认预热 2 次，正式 5 次，整批一次调用处理所有文件，保留离群值。
2. 单文件诊断独立执行 5 轮；先对每文件取中位数，再对文件分布计算 P50 和 nearest-rank P99。
3. 吞吐使用全部尝试输入的字节数/文件数；成功率是 API 接受率。失败文件可能提前返回，需同时阅读成功率和交集结果。
4. cimai 的 void API 不返回验证状态，成功率记 N/A，完成率单列。MajSimai 2.2.3 吞掉的谱面异常通过计数日志检测，不能当作成功。
5. 新测量的默认成功率门槛为 **90%**，可用 `--min-success-rate 0.95` 调整。报告固定使用 **cimai = 1.000×**，倍率为该组合中位数 / cimai 在相同输入上的中位数；cimai 按用户指定作为耗时参照列入主表，成功率仍为 N/A，完成率不能替代解析成功率。其他低于门槛或成功率未知的组合不进入主结果表或 summary.csv，原始数据保留在诊断表和 all-combinations.csv。cimai 结果不可用时倍率留空，不自动替换参照。
6. 最大分配指一次完整文件解析的累计申请量最大值；C# 使用全线程 GC 分配计数，包括 Parallel.For；cimai 和 C++ 使用独立的原生分配诊断程序。它不是 RSS 或单次最大 malloc。
7. 性能程序使用原始解析源码；内存专用构建拦截 C malloc/realloc/free、C++ 普通 new/delete 和 MajSimai Marshal.AllocHGlobal/FreeHGlobal。诊断器自身和运行时内部原生保留区不在累计申请量中；RSS/commit 包含运行时和输入，另行报告。
8. 各库的模型、语法支持和哈希工作量不同，接受率不代表结果等价；最大文件被拒绝时会在耗时旁标记。

## 代码结构

- `tools/bench.py`：固定源码、预装输入、生成隔离项目、构建、串行调度与错误记录。
- `tools/report.py`：读取框架结果、校验输入一致性、统计、CSV/JSON/Markdown 报告。
- `src/managed/`：C# 适配器、BDN benchmarks、JIT/AOT 诊断入口、原生库诊断 exports。
- `src/native/`：Google Benchmark、原生逐文件诊断与内存跟踪器。
- `src/rust/main.rs`：Criterion、UTF-8 文件解析、materialize、逐文件诊断、独立 GlobalAlloc 分配计数。
- `rust-dependencies.lock`：Rust 基准程序及解析器的完整 Cargo 依赖锁。
- `CMakeLists.txt`：cimai、C++ wrapper 和 Google Benchmark 构建。
- `tests/test_benchmark.py`：输入、完整矩阵和统计口径的回归测试。

构建产物和下载/导出的依赖全部位于 `.work`，原始测试输出位于 `artifacts`。这两个目录与测试语料通过 `.gitignore` 排除。

## 已确认的上游问题

锁定的 SimaiSharp one-pass commit `6e5f2d80d978d6d96e3c3320e0aa43f38cf7c654` 在 `FindInsertIndexForTime` 访问空列表末尾元素后才检查 `Count == 0`。最小输入 `(120){4}1,2,3,4,E` 会抛出 `ArgumentOutOfRangeException`。基准保持该 commit 原样，因此其低耗时不能解释为完整解析速度；请结合成功率与失败清单阅读。

## 内存预加载与完整覆盖

参考 MajSimai 的 tools/MajSimai.ManagedBenchmark、tools/NativeBenchmark 和 benchmark-native.py，每个进程在预热前一次读取完整语料，关闭输入文件后持有一个连续缓冲区。C# 用 ReadOnlyMemory 和数组偏移创建 MemoryStream；C/C++ 用 string_view；Rust 用借用切片。解析和正式计时阶段只访问内存，保留解码、容器扫描、解析和结果释放成本。

默认 --limit 0 对目录中全部 maidata.txt 运行；--limit 仅用于显式冒烟测试。清单记录发现文件总数、实际运行文件数、所有有效非空 inote_1..7 字段数与逐文件哈希。每个进程记录预加载文件数和字节数，汇总时校验每个文件均被处理。SimaiSharp 每个难度单独捕获异常并继续其余难度，逐文件尝试数量必须等于清单字段数。完整文件 API 继续保留 Strict 等自身的拒绝规则，文件失败后仍处理下一个文件。

可以仅调整已有结果的排名门槛，无需重新测量；未传入 `--min-success-rate` 时保留已有报告的门槛，首次生成时采用测量配置。当前 canonical-full 报告的门槛为 **30%**：

~~~powershell
python tools/bench.py report --output artifacts/canonical-full --min-success-rate 0.95
~~~

编码覆盖：语料包含 10 个 UTF-16 文件。打包预加载时统一转成 UTF-8，并保留每个文件的原始编码、原始 SHA-256、原始大小以及解析输入的 SHA-256 和大小。转码不修改样本文件、不进入计时。全量有效非空难度字段数为 23,840；吞吐使用实际解析的 UTF-8 字节数。
