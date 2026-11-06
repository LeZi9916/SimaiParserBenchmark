using System.Buffers.Binary;
using System.Runtime.InteropServices;
using System.Text;

namespace SimaiBench;

/// <summary>A file slice that keeps the preloaded corpus buffer alive.</summary>
public sealed record Sample(string Path, ReadOnlyMemory<byte> Bytes)
{
    public ArraySegment<byte> Segment
    {
        get
        {
            if (!MemoryMarshal.TryGetArray(Bytes, out var segment))
            {
                throw new InvalidOperationException("The sample must belong to the corpus byte array.");
            }
            return segment;
        }
    }
}

/// <summary>Loads every input into one owned array before any parser or timer is invoked.</summary>
public sealed class Corpus
{
    private readonly byte[] _buffer;
    public Sample[] Samples { get; }
    public Sample Largest { get; }
    public long Bytes { get; }
    public int Charts { get; }
    public int PreloadedBytes => _buffer.Length;

    public Corpus(string path) : this(File.ReadAllBytes(path))
    {
    }

    public Corpus(byte[] buffer)
    {
        _buffer = buffer;
        if (buffer.Length < 12 || !buffer.AsSpan(0, 8).SequenceEqual("SIMAI001"u8))
        {
            throw new InvalidDataException("Invalid corpus magic.");
        }
        var offset = 8;
        var count = ReadLength(buffer, ref offset);
        if (count <= 0 || count > (buffer.Length - offset) / 8)
        {
            throw new InvalidDataException("Invalid corpus file count.");
        }
        Samples = new Sample[count];
        for (var i = 0; i < count; i++)
        {
            var nameBytes = ReadSlice(buffer, ref offset);
            var name = Encoding.UTF8.GetString(nameBytes.Span);
            var bytes = ReadSlice(buffer, ref offset);
            Samples[i] = new Sample(name, bytes);
            Bytes += bytes.Length;
            Charts += ChartEntries.Read(bytes).Count;
        }
        if (offset != buffer.Length)
        {
            throw new InvalidDataException("Unexpected trailing bytes in the corpus.");
        }
        Largest = Samples.MaxBy(x => x.Bytes.Length)!;
    }

    private static int ReadLength(byte[] buffer, ref int offset)
    {
        if (buffer.Length - offset < sizeof(int))
        {
            throw new InvalidDataException("Truncated corpus length.");
        }
        var value = BinaryPrimitives.ReadInt32LittleEndian(buffer.AsSpan(offset, sizeof(int)));
        offset += sizeof(int);
        if (value < 0)
        {
            throw new InvalidDataException("Negative corpus length.");
        }
        return value;
    }

    private static ReadOnlyMemory<byte> ReadSlice(byte[] buffer, ref int offset)
    {
        var length = ReadLength(buffer, ref offset);
        if (length > buffer.Length - offset)
        {
            throw new InvalidDataException("Truncated corpus contents.");
        }
        var bytes = buffer.AsMemory(offset, length);
        offset += length;
        return bytes;
    }
}

// Both SimaiSharp versions expose a chart API. Scan the complete in-memory maidata
// inside the timed operation; the last duplicate inote field wins.
public static class ChartEntries
{
    public static List<ReadOnlyMemory<byte>> Read(ReadOnlyMemory<byte> bytes)
    {
        var ranges = new ReadOnlyMemory<byte>[7];
        var span = bytes.Span;
        var keyStart = -1;
        var valueStart = -1;
        var difficulty = -1;
        for (var i = 0; i <= span.Length; i++)
        {
            var entry = i < span.Length && span[i] == '&' &&
                (i == 0 || span[i - 1] is (byte)'\n' or (byte)'\r' ||
                 (i == 3 && span[0] == 0xef && span[1] == 0xbb && span[2] == 0xbf));
            if (i == span.Length || entry)
            {
                if (difficulty >= 0 && valueStart >= 0)
                {
                    ranges[difficulty] = bytes.Slice(valueStart, i - valueStart);
                }
                keyStart = i + 1;
                valueStart = -1;
                difficulty = -1;
            }
            else if (keyStart >= 0 && valueStart < 0 && span[i] == '=')
            {
                var key = span[keyStart..i].Trim((byte)' ');
                if (key.Length == 7 && key[..6].SequenceEqual("inote_"u8) && key[6] is >= (byte)'1' and <= (byte)'7')
                {
                    difficulty = key[6] - '1';
                }
                valueStart = i + 1;
            }
        }
        return ranges.Where(x => !x.IsEmpty).ToList();
    }
}
