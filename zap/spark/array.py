import builtins
import numpy as np
import zarr

from zap.base import *  # include everything in zap.base and hence base numpy
from zap.zarr_util import calculate_partition_boundaries, extract_partial_chunks


def from_ndarray(sc, arr, chunks):
    return ndarray_rdd.from_ndarray(sc, arr, chunks)


def from_zarr(sc, zarr_file):
    return ndarray_rdd.from_zarr(sc, zarr_file)


# ndarray in Spark


class ndarray_rdd(ndarray_dist):
    """A numpy.ndarray backed by a Spark RDD"""

    def __init__(self, sc, rdd, shape, chunks, dtype, partition_row_counts=None):
        ndarray_dist.__init__(self, shape, chunks, dtype, partition_row_counts)
        self.sc = sc
        self.rdd = rdd

    # methods to convert to/from regular ndarray - mainly for testing
    @classmethod
    def from_ndarray(cls, sc, arr, chunks):
        func, chunk_indices = ndarray_dist._read_chunks(arr, chunks)
        rdd = sc.parallelize(chunk_indices, len(chunk_indices)).map(func)
        return cls(sc, rdd, arr.shape, chunks, arr.dtype)

    @classmethod
    def from_zarr(cls, sc, zarr_file):
        """
        Read a Zarr file as an ndarray_rdd object.
        """
        arr = zarr.open(zarr_file, mode="r")
        return cls.from_ndarray(sc, arr, arr.chunks)

    def _compute(self):
        return self.rdd.collect()

    def _repartition_chunks(self, chunks):
        c = chunks[0]  # the chunk size for rows

        partition_row_ranges, total_rows, new_num_partitions = calculate_partition_boundaries(
            chunks, self.partition_row_counts
        )

        def extract(iterator):
            # iterator has just a single element for map partitions
            return extract_partial_chunks(list(iterator)[0], chunks)

        def identity_partition_func(key):
            return key

        def combine_partial_chunks(pair):
            """
            Combine multiple non-overlapping parts of a new chunk into a single chunk.
            """
            new_index = pair[0]
            # last chunk has fewer than c rows
            if new_index == new_num_partitions - 1 and total_rows % c != 0:
                last_chunk_rows = total_rows % c
                arr = np.zeros((last_chunk_rows, chunks[1]))
            else:
                arr = np.zeros(chunks)
            for ((new_start_offset, new_end_offset), partial_chunk) in pair[1]:
                arr[new_start_offset:new_end_offset] = partial_chunk
            return arr

        partitioned_rdd = (
            self.sc.parallelize(partition_row_ranges, len(partition_row_ranges))
            .zip(self.rdd)
            .mapPartitions(extract)
            .groupByKey(new_num_partitions, identity_partition_func)
            .map(combine_partial_chunks)
        )

        partition_row_counts = [chunks[0]] * (self.shape[0] // chunks[0])
        remaining = self.shape[0] % chunks[0]
        if remaining != 0:
            partition_row_counts.append(remaining)
        return self._new(
            rdd=partitioned_rdd,
            chunks=chunks,
            partition_row_counts=partition_row_counts,
        )

    def _write_zarr(self, store, chunks, write_chunk_fn):
        zarr.open(store, mode="w", shape=self.shape, chunks=chunks, dtype=self.dtype)

        def index_partitions(index, iterator):
            values = list(iterator)
            assert len(values) == 1  # 1 numpy array per partition
            return [(index, values[0])]

        self.rdd.mapPartitionsWithIndex(index_partitions).foreach(write_chunk_fn)

    # Calculation methods (https://docs.scipy.org/doc/numpy-1.14.0/reference/arrays.ndarray.html#calculation)

    def mean(self, axis=None):
        if axis == 0:  # mean of each column
            result = self.rdd.map(lambda x: (x.shape[0], np.sum(x, axis=0))).collect()
            total_count = builtins.sum([res[0] for res in result])
            mean = np.sum([res[1] for res in result], axis=0) / total_count
            rdd = self.rdd.ctx.parallelize([mean])
            return self._new(
                rdd=rdd,
                shape=mean.shape,
                chunks=mean.shape,
                partition_row_counts=mean.shape,
            )
        elif axis == 1:
            return self._calc_func_axis_rowwise(np.mean, axis)
        return NotImplemented

    def _calc_func_axis_rowwise(self, func, axis):
        return self._new(
            rdd=self.rdd.map(lambda x: func(x, axis=axis)),
            shape=(self.shape[0],),
            chunks=(self.chunks[0],),
        )

    def _calc_func_axis_distributive(self, func, axis):
        if axis == 0:  # column-wise
            per_chunk_result = self.rdd.map(lambda x: func(x, axis=0)).collect()
            result = func(per_chunk_result, axis=0)
            rdd = self.rdd.ctx.parallelize([result])
            return self._new(
                rdd=rdd,
                shape=result.shape,
                chunks=result.shape,
                partition_row_counts=result.shape,
            )
        return NotImplemented

    # Distributed ufunc internal implementation

    def _unary_ufunc(self, func, dtype=None, copy=True):
        new_rdd = self.rdd.map(lambda x: func(x))
        return self._new(rdd=new_rdd, dtype=dtype, copy=copy)

    def _binary_ufunc_self(self, func, dtype=None, copy=True):
        new_rdd = self.rdd.map(lambda x: func(x, x))
        return self._new(rdd=new_rdd, dtype=dtype, copy=copy)

    def _binary_ufunc_broadcast_single_row_or_value(
        self, func, other, dtype=None, copy=True
    ):
        other = asarray(other)  # materialize
        # TODO: should send 'other' as a Spark broadcast
        new_rdd = self.rdd.map(lambda x: func(x, other))
        return self._new(rdd=new_rdd, dtype=dtype, copy=copy)

    def _binary_ufunc_broadcast_single_column(self, func, other, dtype=None, copy=True):
        other = asarray(other)  # materialize
        partition_row_subsets = self._copartition(other, self.partition_row_counts)
        repartitioned_other_rdd = self.sc.parallelize(
            partition_row_subsets, len(partition_row_subsets)
        )
        new_rdd = self.rdd.zip(repartitioned_other_rdd).map(lambda p: func(p[0], p[1]))
        return self._new(rdd=new_rdd, dtype=dtype, copy=copy)

    def _binary_ufunc_same_shape(self, func, other, dtype=None, copy=True):
        if self.partition_row_counts == other.partition_row_counts:
            new_rdd = self.rdd.zip(other.rdd).map(lambda p: func(p[0], p[1]))
            return self._new(rdd=new_rdd, dtype=dtype, copy=copy)
        elif other.shape[1] == 1:
            partition_row_subsets = self._copartition(
                other.asndarray(), self.partition_row_counts
            )
            repartitioned_other_rdd = self.sc.parallelize(
                partition_row_subsets, len(partition_row_subsets)
            )
            new_rdd = self.rdd.zip(repartitioned_other_rdd).map(
                lambda p: func(p[0], p[1])
            )
            return self._new(rdd=new_rdd, dtype=dtype, copy=copy)
        return NotImplemented

    # Slicing

    def _boolean_array_index_dist(self, item):
        subset = asarray(item)  # materialize
        partition_row_subsets = self._copartition(subset, self.partition_row_counts)
        new_partition_row_counts = self._partition_row_counts(partition_row_subsets)
        new_shape = (builtins.sum(new_partition_row_counts),)
        subset_rdd = self.sc.parallelize(
            partition_row_subsets, len(partition_row_subsets)
        )
        return self._new(
            rdd=self.rdd.zip(subset_rdd).map(lambda p: p[0][p[1]]),
            shape=new_shape,
            partition_row_counts=new_partition_row_counts,
        )

    def _column_subset(self, item):
        if item[1] is np.newaxis:  # add new col axis
            new_num_cols = 1
            new_shape = (self.shape[0], new_num_cols)
            new_chunks = (self.chunks[0], new_num_cols)
            return self._new(
                rdd=self.rdd.map(lambda x: x[:, np.newaxis]),
                shape=new_shape,
                chunks=new_chunks,
            )
        subset = asarray(item[1])  # materialize
        new_num_cols = builtins.sum(subset)
        new_shape = (self.shape[0], new_num_cols)
        new_chunks = (self.chunks[0], new_num_cols)
        return self._new(
            rdd=self.rdd.map(lambda x: x[item]), shape=new_shape, chunks=new_chunks
        )

    def _row_subset(self, item):
        subset = asarray(item[0])  # materialize
        partition_row_subsets = self._copartition(subset, self.partition_row_counts)
        new_partition_row_counts = self._partition_row_counts(partition_row_subsets)
        new_shape = (builtins.sum(new_partition_row_counts), self.shape[1])
        subset_rdd = self.sc.parallelize(
            partition_row_subsets, len(partition_row_subsets)
        )
        return self._new(
            rdd=self.rdd.zip(subset_rdd).map(lambda p: p[0][p[1], :]),
            shape=new_shape,
            partition_row_counts=new_partition_row_counts,
        )
