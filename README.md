# SimaiParserBenchmark

针对 Simai / maidata 谱面解析器的跨语言可复现基准测试，覆盖 **cimai**（C/C++）、**MajSimai**（C#）、**SimaiSharp**（C#）与 **maidata-rs**（Rust）。

测试样本取自 [MajdataNET](https://majdata.net) 收集的 19,223 份真实谱面（共计 23,840 个难度字段）。为了消除磁盘 I/O 抖动和系统文件缓存对计时造成的干扰，测试进程会在预热前将全部样本一次性加载至连续内存，计时阶段只测量纯内存解析与对象构建开销。

---

## 测试报告（2026-11-05）

以下数据来自最近一次全量基准测试（对应构建结果位于 `artifacts/canonical-full/`）。
测量区间为 **2026-11-05 21:12:32 至 22:10:58（Asia/Shanghai）**，报告生成于 **22:23:48**。

### 核心结论

- **速度基准（cimai）**：C 原生编写的 `cimai` 解析 19,223 个文件仅耗时 1.27 秒（吞吐 ~66.3 MiB/s，单文件 P50 耗时 41.9 µs），在此次测试中速度最高。但需要指出，`cimai` 的对外 C API 为 void 签名，不返回解析状态或语法校验结果，因此报告将其作为纯执行速度的 1.000× 基准参照，而非语义正确性基准。
- **C# 综合表现（MajSimai 与 SimaiSharp）**：
  - `MajSimai` 重构版本表现均衡，在 NativeAOT 下整批耗时 ~4.48 秒（约为基准的 3.53×），Lenient 宽容模式成功率 100%；Strict 严格模式下耗时 ~4.69 秒，成功率 91.96%。
  - `SimaiSharp` master 分支紧随其后，NativeAOT 下耗时 ~4.92 秒（3.88×），成功率 95.95%。值得注意的是其内存效率：单文件最大累计分配仅 10.08 MiB，进程峰值 RSS 仅 256 MiB，显著低于 MajSimai（最大分配 45–51 MiB，峰值 RSS ~539 MiB）。
- **Rust 实现（maidata-rs）**：
  - `maidata-rs-parse`（仅生成 AST）耗时约 18.96 秒（14.94×），`materialize`（生成带时间戳音符）耗时约 21.79 秒（17.16×），成功率为 58.84%（语法报错或 panic 均计为失败）。
  - 该库的最大累计分配量较高（~932 MiB），这反映了解析过程中的临时堆申请总量；但其进程峰值 RSS 仅约 130 MiB，常驻内存控制良好。
- **关于 SimaiSharp one-pass 分支的异常数值**：
  - `sharp-onepass` 在数据表上跑出了 378 ms 的超低耗时，但成功率仅有 3.17%。经定位，锁定的 commit 在 `FindInsertIndexForTime` 中存在访问空列表末尾的边界 bug，绝大多数谱面在输入前几个音符时便直接抛出 `ArgumentOutOfRangeException` 提前中止，因此该数值反映的是快速失败而非有效解析，已被门槛机制剔除出主排名表。

### 测试环境与输入规格

- **测试平台**：Windows 10 x64 (10.0.19044) / Intel Core i9-7940X (14 核 28 线程 @ 3.10GHz) / 31.77 GiB RAM
- **编译环境**：.NET SDK 10.0.401 (net10.0, NativeAOT 10.0.12) / MSVC 14.51 (C++17 /O2 /Ob3 /GL /LTCG) / Rust 1.99.1 (Release thin-LTO)
- **样本规模**：共 19,223 个文件，23,840 个有效非空难度字段。原始文件总大小 88,372,396 字节；预加载时将 10 个 UTF-16 样本转为 UTF-8（原样本不变），送入解析器的实际 UTF-8 字节总数为 88,311,568 字节。最大单文件为 409,926 字节。
- **输入校验**：样本包全局 SHA-256 为 `c2d4acdbaf971d8a5409db91ba4e44609ba5c7e350a06d0b40869b6ae64d8861`。
- **测量轮次**：C/C++ 与 C# 均预热 2 轮、正式测量 5 轮；单文件诊断独立预热后执行 5 轮。Rust 采用 Criterion（10 个 Flat 样本，1 秒预热，3 秒测量）。

---

### 全样本结果

主表收录成功率达到排名门槛（本次报告设为 30.00%）的可测组合。所有倍率均为该组合全量中位数与 `cimai-native` 的比值。

| 组合 | 总中位数 ms | MiB/s | 文件/s | 单文件 P50 µs | 单文件 P99 µs | 最大文件 ms | 最大分配 MiB | 峰值 RSS MiB | 成功率 | 相对 cimai 耗时 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| cimai-native | 1,269.538 | 66.339 | 15,141.7 | 41.900 | 320.800 | 2.273（完成，未验证） | 16.879 | 102.289 | N/A（完成 100.00%） | 1.000× |
| maj-refactor-Lenient-NativeAOT | 4,479.012 | 18.803 | 4,291.8 | 202.300 | 635.400 | 6.772（接受） | 45.339 | 539.066 | 100.00% | 3.528× |
| maj-refactor-Lenient-JIT | 4,596.055 | 18.325 | 4,182.5 | 190.600 | 620.500 | 6.823（接受） | 33.089 | 532.406 | 100.00% | 3.620× |
| maj-refactor-Strict-NativeAOT | 4,685.585 | 17.974 | 4,102.6 | 213.100 | 640.100 | 6.866（接受） | 51.589 | 472.906 | 91.96% | 3.691× |
| maj-refactor-Strict-JIT | 4,811.477 | 17.504 | 3,995.2 | 204.300 | 618.400 | 7.389（接受） | 51.589 | 525.793 | 91.96% | 3.790× |
| sharp-master-NativeAOT | 4,924.339 | 17.103 | 3,903.7 | 233.200 | 824.300 | 12.159（接受） | 10.076 | 256.352 | 95.95% | 3.879× |
| sharp-master-JIT | 5,176.406 | 16.270 | 3,713.6 | 227.100 | 797.900 | 13.616（接受） | 10.076 | 270.039 | 95.95% | 4.077× |
| maj-2.2.3-Lenient-NativeAOT | 6,415.254 | 13.128 | 2,996.5 | 214.200 | 654.900 | 14.596（接受） | 36.078 | 556.434 | 99.93% | 5.053× |
| maj-refactor-cpp-view-Strict | 7,034.638 | 11.972 | 2,732.6 | 303.700 | 974.300 | 16.276（接受） | 79.699 | 473.547 | 91.96% | 5.541× |
| maj-refactor-cpp-view-Lenient | 7,053.019 | 11.941 | 2,725.5 | 297.200 | 1,016.200 | 15.463（接受） | 73.700 | 495.387 | 100.00% | 5.556× |
| maj-2.2.3-Lenient-JIT | 7,557.513 | 11.144 | 2,543.6 | 226.000 | 669.100 | 15.649（接受） | 35.951 | 561.645 | 99.93% | 5.953× |
| maj-refactor-cpp-model-Strict | 10,315.724 | 8.164 | 1,863.5 | 446.900 | 1,618.500 | 37.483（接受） | 100.879 | 451.270 | 91.96% | 8.126× |
| maj-refactor-cpp-model-Lenient | 10,633.095 | 7.921 | 1,807.8 | 446.300 | 1,712.800 | 40.586（接受） | 100.629 | 450.652 | 100.00% | 8.376× |
| maidata-rs-parse | 18,964.274 | 4.441 | 1,013.6 | 730.500 | 3,012.400 | 122.103（接受） | 932.054 | 130.105 | 58.84% | 14.938× |
| maidata-rs-materialize | 21,786.664 | 3.866 | 882.3 | 857.900 | 3,469.700 | 122.706（接受） | 934.250 | 131.238 | 58.84% | 17.161× |

---

### 排除的诊断结果

| 组合 | 总中位数 ms | MiB/s | 成功率 | 排除原因 |
| --- | ---: | ---: | ---: | --- |
| sharp-onepass-JIT | 443.328 | 189.973 | 3.17% | 成功率 3.17% 低于 30.00% 门槛。 |
| sharp-onepass-NativeAOT | 378.325 | 222.614 | 3.17% | 成功率 3.17% 低于 30.00% 门槛。 |

**关于 SimaiSharp one-pass 失败的说明**：
锁定的提交 `6e5f2d80d978d6d96e3c3320e0aa43f38cf7c654` 在 `FindInsertIndexForTime` 函数中先访问了 `timedList[^1]`，随后才检查列表是否为空。使用最小样本 `(120){4}1,2,3,4,E` 可稳定复现抛出 `ArgumentOutOfRangeException`。因此该提交的超低耗时系提前崩溃导致，不代表完整解析能力。所有失败记录详见 `artifacts/canonical-full/failures.csv`。

---

### 共同接受文件对比（消除提前拒绝偏差）

为了避免某些解析器因较早遇到不支持语法提前退出而产生“虚假加速”，我们提取了所有达标组合均成功解析且 `cimai` 完成调用的文件交集，共计 **11,208 个文件**（**47,218,434 字节**）。

此表利用独立单文件诊断中每轮记录的数据，仅累加交集文件在各轮的解析耗时，取轮次中位数计算对比倍率（`cimai-native` 固定为 1.000×）：

| 组合 | 共同文件诊断中位数 ms | MiB/s | 相对 cimai 耗时 |
| --- | ---: | ---: | ---: |
| cimai-native | 573.252 | 78.554 | 1.000× |
| maj-refactor-Lenient-JIT | 2,459.816 | 18.307 | 4.291× |
| maj-refactor-Lenient-NativeAOT | 2,573.018 | 17.501 | 4.488× |
| maj-refactor-Strict-JIT | 2,654.820 | 16.962 | 4.631× |
| maj-refactor-Strict-NativeAOT | 2,768.451 | 16.266 | 4.829× |
| sharp-master-JIT | 2,809.504 | 16.028 | 4.901× |
| sharp-master-NativeAOT | 2,908.420 | 15.483 | 5.074× |
| maj-2.2.3-Lenient-NativeAOT | 3,521.427 | 12.788 | 6.143× |
| maj-refactor-cpp-view-Lenient | 3,694.227 | 12.190 | 6.444× |
| maj-refactor-cpp-view-Strict | 3,888.004 | 11.582 | 6.782× |
| maj-2.2.3-Lenient-JIT | 3,976.127 | 11.325 | 6.936× |
| maj-refactor-cpp-model-Lenient | 5,567.717 | 8.088 | 9.713× |
| maj-refactor-cpp-model-Strict | 5,777.482 | 7.794 | 10.078× |
| maidata-rs-parse | 8,758.430 | 5.141 | 15.279× |
| maidata-rs-materialize | 10,292.147 | 4.375 | 17.954× |

交集对比印证了全样本主表的排名分布：在完全一致的成功样本上，MajSimai 重构版约需 4.3–4.8× 时间，SimaiSharp master 约需 4.9–5.1×，maidata-rs 约需 15–18×。

---

<details>
<summary>展开查看：输入覆盖、内存明细、不支持组合与源码版本锁定</summary>

### 输入覆盖与预加载验证

诊断进程逐文件核对了文件路径、原始字节大小以及输入清单。任意文件解析抛错后继续处理下一文件，不跳过后续样本。

| 组合 | 已预加载 | 完成文件数 | 输入谱面字段数 | 逐难度调用数 |
| --- | --- | ---: | ---: | ---: |
| cimai-native | 是 | 19,223 | 23840 | 完整文件 API |
| maj-2.2.3-Lenient-JIT | 是 | 19,223 | 23840 | 完整文件 API |
| maj-2.2.3-Lenient-NativeAOT | 是 | 19,223 | 23840 | 完整文件 API |
| maj-refactor-Lenient-JIT | 是 | 19,223 | 23840 | 完整文件 API |
| maj-refactor-Lenient-NativeAOT | 是 | 19,223 | 23840 | 完整文件 API |
| maj-refactor-Strict-JIT | 是 | 19,223 | 23840 | 完整文件 API |
| maj-refactor-Strict-NativeAOT | 是 | 19,223 | 23840 | 完整文件 API |
| sharp-master-JIT | 是 | 19,223 | 23840 | 23840 |
| sharp-master-NativeAOT | 是 | 19,223 | 23840 | 23840 |
| sharp-onepass-JIT | 是 | 19,223 | 23840 | 23840 |
| sharp-onepass-NativeAOT | 是 | 19,223 | 23840 | 23840 |
| maj-refactor-cpp-view-Lenient | 是 | 19,223 | 23840 | 完整文件 API |
| maj-refactor-cpp-view-Strict | 是 | 19,223 | 23840 | 完整文件 API |
| maj-refactor-cpp-model-Lenient | 是 | 19,223 | 23840 | 完整文件 API |
| maj-refactor-cpp-model-Strict | 是 | 19,223 | 23840 | 完整文件 API |
| maidata-rs-parse | 是 | 19,223 | 23840 | 完整文件 API |
| maidata-rs-materialize | 是 | 19,223 | 23840 | 完整文件 API |

### 主表组合的内存明细

- **最大托管分配**：单文件解析中产生的托管 GC 累计申请量最大值（通过 `GC.GetTotalAllocatedBytes(true)` 全线程跟踪，含工作线程）。
- **最大原生分配**：单文件解析中拦截到的原生堆申请累计最大值（拦截 C malloc/realloc/free、C++ operator new/delete、Marshal.AllocHGlobal）。
- **峰值 commit**：测试进程包含运行时和预载语料后的总体内存提交峰值。

| 组合 | 最大托管分配 MiB | 最大原生分配 MiB | 峰值 commit MiB | 最大分配文件 |
| --- | ---: | ---: | ---: | --- |
| cimai-native | 0.000 | 16.879 | 104.215 | 8b0a87ab-07b6-40c4-a985-11ba2725bce6/maidata.txt |
| maj-2.2.3-Lenient-JIT | 35.951 | 0.000 | 544.250 | adad6b4e-5e4a-4535-ac08-da0bba78bc0b/maidata.txt |
| maj-2.2.3-Lenient-NativeAOT | 36.078 | 0.000 | 552.238 | adad6b4e-5e4a-4535-ac08-da0bba78bc0b/maidata.txt |
| maj-refactor-Lenient-JIT | 33.089 | 0.000 | 589.164 | adad6b4e-5e4a-4535-ac08-da0bba78bc0b/maidata.txt |
| maj-refactor-Lenient-NativeAOT | 45.339 | 0.000 | 612.805 | adad6b4e-5e4a-4535-ac08-da0bba78bc0b/maidata.txt |
| maj-refactor-Strict-JIT | 51.589 | 0.000 | 567.488 | adad6b4e-5e4a-4535-ac08-da0bba78bc0b/maidata.txt |
| maj-refactor-Strict-NativeAOT | 51.589 | 0.000 | 540.633 | adad6b4e-5e4a-4535-ac08-da0bba78bc0b/maidata.txt |
| sharp-master-JIT | 10.076 | 0.000 | 253.203 | 8b0a87ab-07b6-40c4-a985-11ba2725bce6/maidata.txt |
| sharp-master-NativeAOT | 10.076 | 0.000 | 251.434 | 8b0a87ab-07b6-40c4-a985-11ba2725bce6/maidata.txt |
| maj-refactor-cpp-view-Lenient | 51.340 | 22.360 | 553.469 | adad6b4e-5e4a-4535-ac08-da0bba78bc0b/maidata.txt |
| maj-refactor-cpp-view-Strict | 57.339 | 22.360 | 546.223 | adad6b4e-5e4a-4535-ac08-da0bba78bc0b/maidata.txt |
| maj-refactor-cpp-model-Lenient | 45.090 | 55.538 | 494.004 | adad6b4e-5e4a-4535-ac08-da0bba78bc0b/maidata.txt |
| maj-refactor-cpp-model-Strict | 45.340 | 55.538 | 494.031 | adad6b4e-5e4a-4535-ac08-da0bba78bc0b/maidata.txt |
| maidata-rs-parse | 0.000 | 932.054 | 144.305 | 1b59c79b-a75f-4989-8755-5d256558f884/maidata.txt |
| maidata-rs-materialize | 0.000 | 934.250 | 145.430 | 1b59c79b-a75f-4989-8755-5d256558f884/maidata.txt |

### 不支持、失败或未运行组合

| 组合 | 状态 | 原因 |
| --- | --- | --- |
| maj-2.2.3-Strict-JIT | unsupported | 2.2.3 没有 SimaiParserOptions / Strict API。 |
| maj-2.2.3-Strict-NativeAOT | unsupported | 2.2.3 没有 SimaiParserOptions / Strict API。 |
| maj-2.2.3-cpp-view-Lenient | unsupported | 2.2.3 没有 CppGen；旧 ABI 不满足新生成器的类型注解与签名要求。 |
| maj-2.2.3-cpp-view-Strict | unsupported | 2.2.3 没有 CppGen；旧 ABI 不满足新生成器的类型注解与签名要求。 |
| maj-2.2.3-cpp-model-Lenient | unsupported | 2.2.3 没有 CppGen；旧 ABI 不满足新生成器的类型注解与签名要求。 |
| maj-2.2.3-cpp-model-Strict | unsupported | 2.2.3 没有 CppGen；旧 ABI 不满足新生成器的类型注解与签名要求。 |

### 源码版本锁定

为确保基准完全可复现，构建阶段通过 `git archive` 从各模块固定 commit 导出干净源码，不受本地未提交改动影响。版本信息由 `sources.lock.json` 固化：

| 变体 | 请求 ref | 完整 commit | commit 时间 |
| --- | --- | --- | --- |
| cimai | HEAD | 1f9fe149a3964121a7f5b74e731bf22a6c2258df | 2026-10-24T17:13:01+08:00 |
| maj-2.2.3 | 2.2.3 | fdb2a3e39d8997a0abbf8b4679062d854473cc77 | 2026-09-01T21:01:46+08:00 |
| maj-refactor | refactor/main-3730i8 | 2b3517895f0eb4c218d9a67c70ec026f5d296c0d | 2026-11-05T03:55:40-05:00 |
| sharp-master | master | 5ef06f91ffdcc77d494baba46d2df4c8d67ca1d6 | 2025-03-07T13:52:49+08:00 |
| sharp-onepass | feat/one-pass-parser | 6e5f2d80d978d6d96e3c3320e0aa43f38cf7c654 | 2026-10-27T17:41:17+08:00 |
| maidata-rs | HEAD | f89ca14d5e1325faef42bd0f995a732794b884e6 | 2026-07-16T04:47:07+08:00 |

</details>

---

## 运行方式

克隆仓库并初始化子模块：

```powershell
git clone --recurse-submodules <本项目仓库地址>
```

若已有工作区，需确保所有子模块已拉取：

```powershell
git submodule update --init --recursive
```

在项目根目录启动 PowerShell 执行基准脚本：

```powershell
# 全流程执行：导出源码快照 -> 编译各组合 -> 全量性能测试 -> 生成汇总报告
python tools/bench.py all --output artifacts/canonical-full

# 也可以按步骤分步执行：
python tools/bench.py prepare
python tools/bench.py build
python tools/bench.py run --output artifacts/canonical-full

# 快速验证（冒烟测试：仅抽样 32 个典型文件，必含最大文件）
python tools/bench.py run --limit 32 --output artifacts/smoke

# 中断恢复：继续未完成的测试，已测组合自动跳过
python tools/bench.py run --output artifacts/canonical-full --resume

# 仅运行指定正则匹配的组合
python tools/bench.py run --only 'maj-refactor.*Strict' --output artifacts/strict

# 单独重新生成报告并调整准入门槛（例如提高到 95%）
python tools/bench.py report --output artifacts/canonical-full --min-success-rate 0.95

# 运行自动化校验与回归测试
python -m unittest discover -s tests -v
dotnet run --project tests/ManagedTests.csproj -c Release
```

> **注意**：基准测试与构建均按串行调度执行。为了获得准确的测量数据，正式测试期间应尽量避免后台 CPU 密集型任务与并行构建。

---

## 环境与依赖要求

- **操作系统**：Windows 10 / 11 x64
- **基础工具**：Python 3.12+、Git、CMake 3.21+、Ninja
- **.NET 栈**：.NET SDK 10.0.401（由 `global.json` 锁定）、Visual Studio 2022 C++ x64 构建工具与 Windows SDK（NativeAOT 必需）
- **Rust 栈**：Rust 1.99.1。若宿主机未安装，`bench.py` 会在 `.work/toolchains` 中自动下载部署一套独立的最小工具链，不会污染系统的全局 `PATH`
- **网络访问**：初次构建时需能够连通 NuGet、crates.io 以及 GitHub（拉取 Google Benchmark 源码）

---

## 测试矩阵

| 项目 | 版本 | 调用方式 | 编译产物 | 宽容度行为 |
| --- | --- | --- | --- | --- |
| cimai | 首次运行的 HEAD，之后锁定 commit | C API，C++ 驱动 | MSVC 原生 | API 无宽容度参数 |
| MajSimai | 2.2.3 | C# 完整文件 API | JIT、NativeAOT | 固有 Lenient 行为 |
| MajSimai | refactor/main-3730i8 | C# 完整文件 API | JIT、NativeAOT | 支持 Lenient、Strict |
| MajSimai | refactor/main-3730i8 | CppGen 包装层的 ParseAsView 与 Parse | C++ + NativeAOT 动态链接库 | 支持 Lenient、Strict |
| maidata-rs | 固定 HEAD commit | 解析指令 AST / 解析并 materialize 音符 | Rust Release | 诊断 errors 非空或 panic 记为失败 |
| SimaiSharp | master、feat/one-pass-parser | 完整文件容器扫描 + 非空 inote_1..7 的 Deserialize | JIT、NativeAOT | API 无宽容度参数 |

- 完整矩阵定义了 23 种测试组合，其中 17 种具备代码支持并实际参与测试；MajSimai 2.2.3 缺少的 6 种组合标为 unsupported。
- `ParseAsView` 测试 C++ 直接读取 NativeAOT 暴露的原始内存视图的开销；`Parse` 则进一步包含了将其深度复制为 C++ `std::string`/`std::vector` 对象模型的耗时与析构代价。
- 托管解析库源码均置于独立生成的 SDK 工程中以 `net10.0` 统一编译（MajSimai 2.2.3 原始项目最高支持 net9.0，此处将其源码统一迁入 net10.0 以控制运行时变量）。

---

## 测试设计与统计口径

1. **预加载与无 I/O 计时**：参考 MajSimai 的测试架构，每个测试进程在预热前一次性读取全部谱面数据存入连续内存。C# 采用 `ReadOnlyMemory<byte>`，C/C++ 采用 `std::string_view`，Rust 采用切片引用。计时区间完全排除任何磁盘读写、目录枚举和转码过程，仅统计解码、扫描、解析与对象生命周期的耗时。
2. **多编码兼容**：样本集内包含 10 个 UTF-16 编码文件。打包阶段会在内存中将其统一规范化为 UTF-8，同时记录原始哈希与实际送入解析器的 UTF-8 哈希。吞吐量（MiB/s）以实际送入解析器的 UTF-8 字节数为基准换算。
3. **测量统计算法**：
   - 整批总耗时与最大文件耗时取框架多次迭代的中位数（wall clock）。
   - 单文件分布耗时（P50、P99）：独立执行 5 轮单文件诊断，先求得各文件自身的中位数，再对所有文件的中位数计算百分位数（P99 使用 nearest-rank 计算），反映的是谱面复杂度与大小的分布特征，而非瞬时抖动。
4. **成功率与错误判定**：
   - 成功率 = 接口正常完成且返回有效数据的文件数 / 总输入文件数。对于按谱面难度逐个解析的接口（如 SimaiSharp），任一难度抛出未捕获异常即判定该文件失败。
   - `cimai` 的 C 接口无状态返回值，其成功率记为 N/A，另外单列 100% 完成率。
   - `MajSimai` 2.2.3 内部向控制台输出并捕获的谱面异常通过自定义计数器追踪，不计入成功。
5. **内存累计分配追踪**：
   - 表格中的“最大分配”指解析单份文件过程中**累计申请的堆内存总量**。C# 使用 `GC.GetTotalAllocatedBytes(true)`，包含工作线程；C/C++ 通过替换自定义全局分配器拦截 `malloc`/`calloc`/`realloc`/`free` 以及 C++ `operator new`/`delete`；Rust 通过自定义 `GlobalAlloc` 统计。该指标反映的是算法的内存申请开销，而非进程常驻内存（RSS）。
   - 进程峰值 RSS 与 commit 由独立监控项记录，包含运行时自身和全量预载样本。

---

## 源码版本锁定机制

为确保每次测试的基准库代码完全可追溯且不可篡改，测试框架不直接使用各子模块当前工作树的代码，而是根据 `sources.lock.json` 中锁定的 commit 散列值，通过 `git archive` 提取出独立的临时工作源树。

若需升级上游解析器版本：

1. 运行 `git submodule update --remote` 拉取最新代码。
2. 在新的临时目录执行 `python tools/bench.py prepare --refresh-sources` 刷新快照与锁定文件。
3. 提交子模块指针更新与新的 `sources.lock.json`。

注：MajSimai 原 `refactor/main-3730i8` 远端分支已被上游删除，当前配置依然锁定其最后保留的 commit；后续更新将平滑迁移至 upstream `master`。

---

## 项目结构

```text
├── tools/
│   ├── bench.py               # 构建、测试编排、子进程隔离与调度运行器
│   └── report.py              # 数据收集、统计计算与 Markdown/CSV 报告生成器
├── src/
│   ├── managed/               # C# 驱动、BenchmarkDotNet 配置与 NativeAOT 原生导出适配
│   ├── native/                # Google Benchmark 驱动、原生内存拦截器与 C++ wrapper 测试
│   └── rust/                  # Criterion 驱动、maidata-rs 解析流程与独立分配器探针
├── tests/
│   └── test_benchmark.py      # 输入清单、测试矩阵与数据统计算法的单元回归测试
├── benchmark-config.json       # 基准测试运行时参数与库路径映射
├── sources.lock.json          # 各被测库的 Git Commit 锁定清单
└── rust-dependencies.lock     # Rust 测试工具链的 Cargo.lock 镜像
```
