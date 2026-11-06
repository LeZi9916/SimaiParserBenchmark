using System.Text;
using SimaiBench;

var checks = 0;
void Check(bool condition, string message)
{
    checks++;
    if (!condition)
    {
        throw new Exception(message);
    }
}
void Verify(string text, params string[] expected)
{
    var actual = ChartEntries.Read(Encoding.UTF8.GetBytes(text)).Select(x => Encoding.UTF8.GetString(x.Span));
    Check(actual.SequenceEqual(expected), "Chart container slicing mismatch.");
}
byte[] Pack(params (string Path, string Text)[] files)
{
    using var stream = new MemoryStream();
    using var writer = new BinaryWriter(stream, Encoding.UTF8, leaveOpen: true);
    writer.Write("SIMAI001"u8);
    writer.Write(files.Length);
    foreach (var file in files)
    {
        var name = Encoding.UTF8.GetBytes(file.Path);
        var content = Encoding.UTF8.GetBytes(file.Text);
        writer.Write(name.Length);
        writer.Write(name);
        writer.Write(content.Length);
        writer.Write(content);
    }
    writer.Flush();
    return stream.ToArray();
}
void Invalid(byte[] bytes)
{
    var rejected = false;
    try
    {
        _ = new Corpus(bytes);
    }
    catch (InvalidDataException)
    {
        rejected = true;
    }
    Check(rejected, "Malformed corpus was accepted.");
}
Verify("&title=A\n&inote_1=(120){4}1,E\n", "(120){4}1,E\n");
Verify("\ufeff&inote_7=A\r\n&inote_1=B\r\n&title=x", "B\r\n", "A\r\n");
Verify("&inote_1=old\n&inote_1=new\n", "new\n");
Verify("&title=A&B\n&inote_2=\n&lv_2=5", "\n");
Verify("&title=No charts");
Verify("&inote_3=中文\n", "中文\n");

var allCharts = string.Join("", Enumerable.Range(1, 7).Select(i => $"&inote_{i}=chart-{i}\n"));
var data = Pack(("中文/maidata.txt", allCharts), ("second/maidata.txt", "&inote_1=second\n"));
var temporary = Path.GetTempFileName();
Corpus corpus;
try
{
    File.WriteAllBytes(temporary, data);
    corpus = new Corpus(temporary);
}
finally
{
    File.Delete(temporary);
}
Check(!File.Exists(temporary), "The preload test must remove the source before parsing.");
Check(corpus.Samples.Length == 2 && corpus.Charts == 8, "The complete corpus/chart count was not retained.");
Check(corpus.PreloadedBytes == data.Length, "The entire packed buffer was not retained.");
Check(ReferenceEquals(corpus.Samples[0].Segment.Array, corpus.Samples[1].Segment.Array), "Files must share one input buffer.");
Check(corpus.Samples[0].Path == "中文/maidata.txt", "The corpus lost a Unicode path.");
var visited = new List<string>();
long checksum = 0;
var attempts = 0;
foreach (var sample in corpus.Samples)
{
    var outcome = Adapter.ParseCharts(sample.Bytes, entry =>
    {
        var chart = Encoding.UTF8.GetString(entry.Span).Trim();
        visited.Add(chart);
        if (chart == "chart-1")
        {
            throw new FormatException("Intentional first-chart failure.");
        }
        return 1;
    });
    attempts += outcome.ChartAttempts;
    checksum += outcome.Checksum;
    if (sample == corpus.Samples[0])
    {
        Check(!outcome.Success && outcome.FailedCharts == 1, "A per-chart exception was not recorded.");
    }
}
Check(attempts == 8 && visited.Count == 8 && checksum == 7, "An error skipped a remaining chart or file.");
Check(visited.Last() == "second", "The file after a rejected chart was not parsed.");
var direct = Adapter.Parse(corpus.Samples[0]);
Check(direct.ChartAttempts == 7, "The real adapter did not attempt all preloaded difficulties.");
var following = Adapter.Parse(corpus.Samples[1]);
Check(following.ChartAttempts == 1, "The real adapter skipped a following file after chart errors.");
Invalid(data[..^1]);
Invalid(data.Concat(new byte[] { 0 }).ToArray());
Invalid("SIMAI001\0\0\0\0"u8.ToArray());
var corrupt = data.ToArray();
corrupt.AsSpan(12, 4).Fill(0xff);
Invalid(corrupt);
Console.WriteLine($"{checks} managed preload/container/coverage checks passed.");
