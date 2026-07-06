# `.loombit` v3

`loombit` is the canonical binary floor for Loom-era declarations.

It is not a replacement for `.loom` or `.oradio` authoring.
It is the compiled bitstream layer below them.

v3 adds the missing split:

- `.ldict` = shared external dictionary
- `.loombit` = string-free payload references when compiled against that dictionary

## Purpose

Given a `.loom` or `.oradio` object graph, `loombit` writes:

1. a compact binary artifact
2. a shared string dictionary
3. an opcode payload
4. a checksum

That gives us one canonical payload that can later be rendered into:

- raw binary
- glyph strings
- audio carriers
- quadrant-grid PNG / scatter carriers

## `.ldict` shape

```text
magic      4 bytes   "LDT1"
version    1 byte    1
entry_n    varint
raw_n      varint
packed_n   varint
packed     zlib(dictionary bytes)
checksum   4 bytes little-endian crc32 over raw dictionary bytes
```

## Current v3 `.loombit` shape

```text
magic      4 bytes   "LBIT"
version    1 byte    3
mode       1 byte    0=generic 1=loom 2=oradio
dict_mode  1 byte    0=embedded 1=external
dict_crc   4 bytes   little-endian crc32 of the dictionary bytes

if embedded:
  dict_n     varint
  dict_len   varint
  payload_n  varint
  packed_n   varint
  packed     zlib(dict_bytes + payload)

if external:
  dict_n     varint
  payload_n  varint
  packed_n   varint
  packed     zlib(payload)
checksum   4 bytes little-endian crc32 over all prior bytes
```

## Dictionary

The dictionary is the sorted set of all:

- map keys
- string values

Each entry is:

```text
len varint + utf8 bytes
```

## Payload opcodes

The payload is a canonical tree codec over the authored object graph.

```text
0x00 null
0x01 false
0x02 true
0x03 int      zigzag varint
0x04 float64  little-endian IEEE754
0x05 string   dictionary ref varint
0x06 list     item count varint, then items
0x07 map      pair count varint, then key-ref + value
```

Maps are encoded with keys sorted lexicographically.

That means `loombit` is:

- deterministic
- semantics-preserving
- not dependent on YAML formatting quirks

## Why this first

v1 preserves the full authored `.loom` / `.oradio` object graph immediately.
That is more important than premature event-specific bit packing.

This gives us a clean base for later narrower codecs:

- graph-specific opcodes
- delta/repeat codes
- event tapes
- domain dictionaries
- bit-packed path references

## Quadrant carrier derivation

The first visual carrier strategy derives directly from the actual `loombit` bytes.

For each byte:

```text
high nibble
low nibble
```

Then group nibbles in threes:

```text
NW = stream A
NE = stream B
SW = stream C
SE = ECC = A xor B xor C
```

This is a derived carrier, not a second semantic path.
The image decodes the same payload, not a cousin of it.

## Why v3 changed

Packing inline strings was better than raw UTF-8, but still not the real floor.

The real next step was:

- move strings into a shared binary dictionary
- compile `.loombit` to stable IDs against that dictionary
- let the payload body stay free of authored semantic strings

That is now possible through external-dictionary mode.

## Current status

- compiler/decoder: implemented in `loom/loombit.py`
- CLI: `tools/loombit.py`
- tests: `tests/test_loombit.py`

## Near-term next step

Keep `.loom` as the smallest human seed.
Keep `.oradio` as the richer declaration.
Use `.ldict` + `.loombit` as the canonical floor for multi-carrier experiments.

## Recursive index pattern

The knowledge tree can itself be expressed as a normal loombit payload with:

```text
kind: loombit_index
title
level
branching
entry_count
class_counts
entries[]
```

Each entry can point to another loombit:

```text
id
path
class
topic
summary
checksum
children
```

So the tree can recurse cleanly:

```text
root index loombit
  -> shard index loombits
    -> topic/entity loombits
      -> leaf payload loombits
```

## Colored-lens projection

The visual/lens idea is implemented as a deterministic projection over the canonical
artifact bytes, not as a second semantic artifact.

For each group of three bytes, derive:

```text
R = routing / index emphasis
G = semantic / dictionary emphasis
B = payload / detail emphasis
K = parity / alignment
```

This lets one artifact expose different deterministic slices:

- red lens: navigation / routing
- green lens: semantic neighborhood
- blue lens: payload detail
- black/parity lens: alignment / integrity
