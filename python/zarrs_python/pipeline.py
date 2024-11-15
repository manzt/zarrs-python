from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
from zarr.abc.codec import (
    Codec,
    CodecPipeline,
)
from zarr.core.config import config

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from typing import Self

    from zarr.abc.store import ByteGetter, ByteSetter
    from zarr.core.array_spec import ArraySpec
    from zarr.core.buffer import Buffer, NDBuffer
    from zarr.core.chunk_grids import ChunkGrid
    from zarr.core.common import ChunkCoords
    from zarr.core.indexing import SelectorTuple

from ._internal import CodecPipelineImpl
from .utils import (
    CollapsedDimensionError,
    DiscontiguousArrayError,
    get_max_threads,
    make_chunk_info_for_rust,
    make_chunk_info_for_rust_with_indices,
)


@dataclass(frozen=True)
class ZarrsCodecPipeline(CodecPipeline):
    codecs: tuple[Codec, ...]
    impl: CodecPipelineImpl

    def evolve_from_array_spec(self, array_spec: ArraySpec) -> Self:
        raise NotImplementedError("evolve_from_array_spec")

    @classmethod
    def from_codecs(cls, codecs: Iterable[Codec]) -> Self:
        codec_metadata = [codec.to_dict() for codec in codecs]
        codec_metadata_json = json.dumps(codec_metadata)
        return cls(
            codecs=tuple(codecs),
            impl=CodecPipelineImpl(
                codec_metadata_json,
                config.get("codec_pipeline.validate_checksums", None),
                config.get("codec_pipeline.store_empty_chunks", None),
                config.get("codec_pipeline.concurrent_target", None),
            ),
        )

    @property
    def supports_partial_decode(self) -> bool:
        return False

    @property
    def supports_partial_encode(self) -> bool:
        return False

    def __iter__(self) -> Iterator[Codec]:
        yield from self.codecs

    def validate(
        self, *, shape: ChunkCoords, dtype: np.dtype[Any], chunk_grid: ChunkGrid
    ) -> None:
        raise NotImplementedError("validate")

    def compute_encoded_size(self, byte_length: int, array_spec: ArraySpec) -> int:
        raise NotImplementedError("compute_encoded_size")

    async def decode(
        self,
        chunk_bytes_and_specs: Iterable[tuple[Buffer | None, ArraySpec]],
    ) -> Iterable[NDBuffer | None]:
        raise NotImplementedError("decode")

    async def encode(
        self,
        chunk_arrays_and_specs: Iterable[tuple[NDBuffer | None, ArraySpec]],
    ) -> Iterable[Buffer | None]:
        raise NotImplementedError("encode")

    async def read(
        self,
        batch_info: Iterable[
            tuple[ByteGetter, ArraySpec, SelectorTuple, SelectorTuple]
        ],
        out: NDBuffer,
        drop_axes: tuple[int, ...] = (),  # FIXME: unused
    ) -> None:
        chunk_concurrent_limit = (
            config.get("threading.max_workers") or get_max_threads()
        )
        out = out.as_ndarray_like()  # FIXME: Error if array is not in host memory
        if not out.dtype.isnative:
            raise RuntimeError("Non-native byte order not supported")
        try:
            chunks_desc = make_chunk_info_for_rust_with_indices(batch_info, drop_axes)
            index_in_rust = True
        except (DiscontiguousArrayError, CollapsedDimensionError):
            chunks_desc = make_chunk_info_for_rust(batch_info)
            index_in_rust = False
        if index_in_rust:
            await asyncio.to_thread(
                self.impl.retrieve_chunks_and_apply_index,
                chunks_desc,
                out,
                chunk_concurrent_limit,
            )
            return None
        chunks = await asyncio.to_thread(
            self.impl.retrieve_chunks, chunks_desc, chunk_concurrent_limit
        )
        for chunk, (_, spec, selection, out_selection) in zip(chunks, batch_info):
            chunk_reshaped = chunk.view(spec.dtype).reshape(spec.shape)
            chunk_selected = chunk_reshaped[selection]
            if drop_axes:
                chunk_selected = np.squeeze(chunk_selected, axis=drop_axes)
            out[out_selection] = chunk_selected

    async def write(
        self,
        batch_info: Iterable[
            tuple[ByteSetter, ArraySpec, SelectorTuple, SelectorTuple]
        ],
        value: NDBuffer,
        drop_axes: tuple[int, ...] = (),
    ) -> None:
        chunk_concurrent_limit = (
            config.get("threading.max_workers") or get_max_threads()
        )
        value = value.as_ndarray_like()  # FIXME: Error if array is not in host memory
        if not value.dtype.isnative:
            value = np.ascontiguousarray(value, dtype=value.dtype.newbyteorder("="))
        elif not value.flags.c_contiguous:
            value = np.ascontiguousarray(value)
        chunks_desc = make_chunk_info_for_rust_with_indices(batch_info, drop_axes)
        await asyncio.to_thread(
            self.impl.store_chunks_with_indices,
            chunks_desc,
            value,
            chunk_concurrent_limit,
        )
        return None
