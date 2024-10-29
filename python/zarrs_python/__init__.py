from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
import numpy as np
import json
import threading
import asyncio

from zarr.abc.codec import (
    Codec,
    CodecPipeline,
)
from zarr.core.common import ChunkCoords
from zarr.core.config import config
from zarr.core.indexing import SelectorTuple, is_total_slice
from zarr.registry import register_pipeline

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from typing import Self

    import numpy as np

    from zarr.abc.store import ByteGetter, ByteSetter
    from zarr.core.array_spec import ArraySpec
    from zarr.core.buffer import Buffer, NDBuffer
    from zarr.core.chunk_grids import ChunkGrid

from ._internal import CodecPipelineImpl


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
        # NOTE: In dask, this is always called from a single thread with batch_info of length 1
        # print(
        #     "Reading batch of length",
        #     len(batch_info),
        #     "on thread",
        #     threading.get_ident(),
        # )

        out = out.as_ndarray_like()  # FIXME: Error if array is not in host memory

        chunks_desc = [None] * len(batch_info)
        for i, (byte_getter, chunk_spec, chunk_selection, out_selection) in enumerate(
            batch_info
        ):
            chunk_path = str(byte_getter)
            chunks_desc[i] = (
                chunk_path,
                chunk_spec.shape,
                str(chunk_spec.dtype),
                chunk_spec.fill_value.tobytes(),
                out_selection,
                chunk_selection,
            )

        return await asyncio.to_thread(self.impl.retrieve_chunks, chunks_desc, out)

    async def write(
        self,
        batch_info: Iterable[
            tuple[ByteSetter, ArraySpec, SelectorTuple, SelectorTuple]
        ],
        value: NDBuffer,
        drop_axes: tuple[int, ...] = (),
    ) -> None:
        # FIXME: use drop_axes
        value = value.as_ndarray_like() # FIXME: Error if array is not in host memory
        if not value.flags.c_contiguous:
            value = np.ascontiguousarray(value)

        chunks_desc = [None] * len(batch_info)
        for i, (byte_setter, chunk_spec, chunk_selection, out_selection) in enumerate(
            batch_info
        ):
            chunk_path = str(byte_setter)
            chunks_desc[i] = (
                chunk_path,
                chunk_spec.shape,
                str(chunk_spec.dtype),
                chunk_spec.fill_value.tobytes(),
                out_selection,
                chunk_selection,
            )

        return await asyncio.to_thread(self.impl.store_chunks, chunks_desc, value)

register_pipeline(ZarrsCodecPipeline)
