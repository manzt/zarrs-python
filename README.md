# `zarrs-python`

This project serves a python bridge between [`zarrs`](https://docs.rs/zarrs/latest/zarrs/) and `python` via [`PyO3`](https://pyo3.rs/v0.22.3/).  The main goal of the project is to speed up reading of zarr datasets as many biological datasets are write-once, read-many (unlike, say, satellite data where the "entire" dataset of Earth is re-written everyday, and it is quite large).

Currently, the API should be identical to that of `zarr` at that last merge-commit: https://github.com/ilan-gold/zarr-python/tree/681f2866343963f8fff3123f2ac8e18e2bc4b3ad.  That is, the python API for this package is identical to that of `zarr` at this commit.  The reason for this similarity is that the python API for this package is a minimally modified fork of the `zarr-python` implementation.   To see under what circumstances the rust bridge is used instead of the normal `zarr` implementation, see [these lines](https://github.com/ilan-gold/zarr-python/blob/ig/rust/core/array.py#L458-L475). In short:

1. The store must be backed by a file, not by something in memory
2. The `out_selection`s (i.e., indexing into the out-buffers) are all tuples (not so sure about this one TBH)
3. The `out_selection` tuples contain either slices, ints, or arrays that can be converted to slices. See https://github.com/LDeakin/zarrs/issues/52 for what would be required to allow integer-array indexing.
4. Any slice selection, out or in, needs to have a step size of 1.

To benchmark this implementation, there [is a fork of `zarrs_tools`](https://github.com/ilan-gold/zarrs_tools/tree/ig/zarrs_python) with some slight changes to allow for benchmarking this implementation on a mac.  To benchmark on linux, you would need to change the `time` command and the cache dropping e.g., [these lines](https://github.com/ilan-gold/zarrs_tools/blob/c60fcf43e11032f81531464986500433e9c9dffb/scripts/run_benchmark_read_chunks.py#L8-L17).  When creating the python environment for that repo to do benchmarking, simply install `-e ../zarrs-python` this project as well.  Then follow the directions there for running the various files in `./scripts`.

## `Rust` specifics overview

At the moment, parallelization occurs at the chunk level.  That is, every fetch-index-out operation (get data, index it, put it in an out-buffer) is its own thread in a similar way as `zarrs` (the code should look quite similar: https://github.com/LDeakin/zarrs/blob/5edbc502b05c98fbe2ab434948208473ef573f66/zarrs/src/array/array_sync_readable.rs#L733-L767); one can think of each chunk-fetch (and subsequent write into the returned buffer) as its own thread.  The output buffer is then wrapped as a [`dlpack`](https://dmlc.github.io/dlpack/latest/) object which should (in theory) enable a zero-copy return to the calling python function.  See also: https://github.com/zarr-developers/zarr-python/issues/2199 for more info on how this `dlpack` usage could be upstreamed in many ways.

This may *not* be the best way to achieve parallelization.  No profiling has been done on this matter.

## Testing

At the moment, we rely on the python tests for correctness, so `pytest zarrs_python/tests/v3` should run the relevant tests within `zarr-python` that hit the rust-based codepaths.  These tests should all pass, although some have been xfailed for good reasons (like object equality).


## `ld/codec_pipeline` branch
```
cargo run -q --bin stub_gen
maturin develop -r
./examples/simple.py
```
