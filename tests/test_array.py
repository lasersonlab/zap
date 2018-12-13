import concurrent.futures
import logging
import pytest
import sys
import zappy.base as np  # zappy includes everything in numpy, with some overrides and new functions
import zappy.executor
import zappy.direct
import zappy.spark
import zarr

from numpy.testing import assert_allclose
from pyspark.sql import SparkSession

# add/change to "pywren_ndarray" to run the tests using Pywren (requires Pywren to be installed)
TESTS = [
    "direct_ndarray",
    "direct_zarr",
    "executor_ndarray",
    "executor_zarr",
    "spark_ndarray",
    "spark_zarr",
]

# only run Beam tests on Python 2, and don't run executor tests
if sys.version_info[0] == 2:
    import apache_beam as beam
    from apache_beam.options.pipeline_options import PipelineOptions
    import zappy.beam

    TESTS = [
        "direct_ndarray",
        "direct_zarr",
        "spark_ndarray",
        "spark_zarr",
        "beam_ndarray",
        "beam_zarr",
    ]


class TestZappyArray:
    @pytest.fixture()
    def x(self):
        return np.array(
            [
                [0.0, 1.0, 0.0, 3.0, 0.0],
                [2.0, 0.0, 3.0, 4.0, 5.0],
                [4.0, 0.0, 0.0, 6.0, 7.0],
            ]
        )

    @pytest.fixture()
    def chunks(self):
        return (2, 5)

    @pytest.fixture()
    def xz(self, x, chunks, tmpdir):
        input_file_zarr = str(tmpdir.join("x.zarr"))
        z = zarr.open(
            input_file_zarr, mode="w", shape=x.shape, dtype=x.dtype, chunks=chunks
        )
        z[:] = x.copy()  # write as zarr locally
        return input_file_zarr

    @pytest.fixture(scope="module")
    def sc(self):
        logger = logging.getLogger("py4j")
        logger.setLevel(logging.WARN)
        spark = (
            SparkSession.builder.master("local[2]")
            .appName("my-local-testing-pyspark-context")
            .getOrCreate()
        )
        yield spark.sparkContext
        spark.stop()

    @pytest.fixture(params=TESTS)
    def xd(self, sc, x, xz, chunks, request):
        if request.param == "direct_ndarray":
            yield zappy.direct.from_ndarray(x.copy(), chunks)
        elif request.param == "direct_zarr":
            yield zappy.direct.from_zarr(xz)
        elif request.param == "executor_ndarray":
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                yield zappy.executor.from_ndarray(executor, x.copy(), chunks)
        elif request.param == "executor_zarr":
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                yield zappy.executor.from_zarr(executor, xz)
        elif request.param == "spark_ndarray":
            yield zappy.spark.from_ndarray(sc, x.copy(), chunks)
        elif request.param == "spark_zarr":
            yield zappy.spark.from_zarr(sc, xz)
        elif request.param == "beam_ndarray":
            pipeline_options = PipelineOptions()
            pipeline = beam.Pipeline(options=pipeline_options)
            yield zappy.beam.from_ndarray(pipeline, x.copy(), chunks)
        elif request.param == "beam_zarr":
            pipeline_options = PipelineOptions()
            pipeline = beam.Pipeline(options=pipeline_options)
            yield zappy.beam.from_zarr(pipeline, xz)
        elif request.param == "pywren_ndarray":
            executor = zappy.executor.PywrenExecutor()
            yield zappy.executor.from_ndarray(executor, x.copy(), chunks)

    @pytest.fixture(params=TESTS)
    def xd_and_temp_store(self, sc, x, xz, chunks, request):
        if request.param == "direct_ndarray":
            yield zappy.direct.from_ndarray(x.copy(), chunks), zarr.TempStore()
        elif request.param == "direct_zarr":
            yield zappy.direct.from_zarr(xz), zarr.TempStore()
        elif request.param == "executor_ndarray":
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                yield zappy.executor.from_ndarray(
                    executor, x.copy(), chunks
                ), zarr.TempStore()
        elif request.param == "executor_zarr":
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                yield zappy.executor.from_zarr(executor, xz), zarr.TempStore()
        elif request.param == "spark_ndarray":
            yield zappy.spark.from_ndarray(sc, x.copy(), chunks), zarr.TempStore()
        elif request.param == "spark_zarr":
            yield zappy.spark.from_zarr(sc, xz), zarr.TempStore()
        elif request.param == "beam_ndarray":
            pipeline_options = PipelineOptions()
            pipeline = beam.Pipeline(options=pipeline_options)
            yield zappy.beam.from_ndarray(pipeline, x.copy(), chunks), zarr.TempStore()
        elif request.param == "beam_zarr":
            pipeline_options = PipelineOptions()
            pipeline = beam.Pipeline(options=pipeline_options)
            yield zappy.beam.from_zarr(pipeline, xz), zarr.TempStore()
        elif request.param == "pywren_ndarray":
            import s3fs.mapping

            def create_unique_bucket_name(prefix):
                import uuid

                return "%s-%s" % (prefix, str(uuid.uuid4()).replace("-", ""))

            s3 = s3fs.S3FileSystem()
            bucket = create_unique_bucket_name("zappy-test")
            s3.mkdir(bucket)
            path = "%s/%s" % (bucket, "test.zarr")
            s3store = s3fs.mapping.S3Map(path, s3=s3)
            executor = zappy.executor.PywrenExecutor()
            yield zappy.executor.from_ndarray(executor, x.copy(), chunks), s3store
            s3.rm(bucket, recursive=True)

    @pytest.fixture(params=["direct", "executor", "spark"])  # TODO: beam
    def zeros(self, sc, request):
        if request.param == "direct":
            yield zappy.direct.zeros((3, 5), chunks=(2, 5), dtype=int)
        elif request.param == "executor":
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                yield zappy.executor.zeros(executor, (3, 5), chunks=(2, 5), dtype=int)
        elif request.param == "spark":
            yield zappy.spark.zeros(sc, (3, 5), chunks=(2, 5), dtype=int)

    @pytest.fixture(params=["direct", "executor", "spark"])  # TODO: beam
    def ones(self, sc, request):
        if request.param == "direct":
            yield zappy.direct.ones((3, 5), chunks=(2, 5), dtype=int)
        elif request.param == "executor":
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                yield zappy.executor.ones(executor, (3, 5), chunks=(2, 5), dtype=int)
        elif request.param == "spark":
            yield zappy.spark.ones(sc, (3, 5), chunks=(2, 5), dtype=int)

    def test_identity(self, x, xd):
        assert_allclose(xd.asndarray(), x)

    def test_astype(self, x, xd):
        xd = xd.astype(int)
        x = x.astype(int)
        assert xd.dtype == x.dtype
        assert_allclose(xd.asndarray(), x)

    def test_astype_inplace(self, x, xd):
        original_id = id(xd)
        xd = xd.astype(int, copy=False)
        assert original_id == id(xd)
        x = x.astype(int, copy=False)
        assert xd.dtype == x.dtype
        assert_allclose(xd.asndarray(), x)

    def test_scalar_arithmetic(self, x, xd):
        xd = (((xd + 1) * 2) - 4) / 1.1
        x = (((x + 1) * 2) - 4) / 1.1
        assert_allclose(xd.asndarray(), x)

    def test_arithmetic(self, x, xd):
        xd = xd * 2 + xd
        x = x * 2 + x
        assert_allclose(xd.asndarray(), x)

    def test_broadcast_row(self, x, xd):
        a = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        xd = xd + a
        x = x + a
        assert_allclose(xd.asndarray(), x)

    def test_broadcast_col(self, x, xd):
        if sys.version_info[0] == 2 and isinstance(
            xd, zappy.beam.array.BeamZappyArray
        ):  # TODO: fix this
            return
        a = np.array([[1.0], [2.0], [3.0]])
        xd = xd + a
        x = x + a
        assert_allclose(xd.asndarray(), x)

    def test_eq(self, x, xd):
        xd = xd == 0.0
        x = x == 0.0
        assert xd.dtype == x.dtype
        assert_allclose(xd.asndarray(), x)

    def test_ne(self, x, xd):
        xd = xd != 0.0
        x = x != 0.0
        assert_allclose(xd.asndarray(), x)

    def test_invert(self, x, xd):
        xd = ~(xd == 0.0)
        x = ~(x == 0.0)
        assert_allclose(xd.asndarray(), x)

    def test_inplace(self, x, xd):
        original_id = id(xd)
        xd += 1
        assert original_id == id(xd)
        x += 1
        assert_allclose(xd.asndarray(), x)

    def test_simple_index(self, x, xd):
        xd = xd[0]
        x = x[0]
        assert_allclose(xd, x)

    def test_boolean_index(self, x, xd):
        xd = np.sum(xd, axis=1)  # sum rows
        xd = xd[xd > 5]
        x = np.sum(x, axis=1)  # sum rows
        x = x[x > 5]
        assert_allclose(xd.asndarray(), x)

    def test_slice_cols(self, x, xd):
        xd = xd[:, 1:3]
        x = x[:, 1:3]
        assert xd.shape == x.shape
        assert_allclose(xd.asndarray(), x)

    # TODO: implement row slicing
    # def test_slice_rows(self, x, xd):
    #     xd = xd[1:3, :]
    #     x = x[1:3, :]
    #     assert xd.shape == x.shape
    #     assert_allclose(xd.asndarray(), x)

    def test_subset_cols_boolean(self, x, xd):
        subset = np.array([True, False, True, False, True])
        xd = xd[:, subset]
        x = x[:, subset]
        assert xd.shape == x.shape
        assert_allclose(xd.asndarray(), x)

    def test_subset_rows_boolean(self, x, xd):
        subset = np.array([True, False, True])
        xd = xd[subset, :]
        x = x[subset, :]
        assert xd.shape == x.shape
        assert_allclose(xd.asndarray(), x)

    def test_subset_cols_int(self, x, xd):
        subset = np.array([1, 3])
        xd = xd[:, subset]
        x = x[:, subset]
        assert xd.shape == x.shape
        assert_allclose(xd.asndarray(), x)

    def test_subset_rows_int(self, x, xd):
        # TODO: this fails if changed to `subset = np.array([1, 2])`
        subset = np.array([0, 1, 2])
        xd = xd[subset, :]
        x = x[subset, :]
        assert xd.shape == x.shape
        assert_allclose(xd.asndarray(), x)

    def test_newaxis(self, x, xd):
        xd = np.sum(xd, axis=1)[:, np.newaxis]
        x = np.sum(x, axis=1)[:, np.newaxis]
        assert_allclose(xd.asndarray(), x)

    def test_log1p(self, x, xd):
        log1pnps = np.log1p(xd).asndarray()
        log1pnp = np.log1p(x)
        assert_allclose(log1pnps, log1pnp)

    def test_sum(self, x, xd):
        if sys.version_info[0] == 2 and isinstance(
            xd, zappy.beam.array.BeamZappyArray
        ):  # TODO: fix this
            return
        totald = np.sum(xd)
        total = np.sum(x)
        assert totald == pytest.approx(total)

    def test_sum_cols(self, x, xd):
        xd = np.sum(xd, axis=0)
        x = np.sum(x, axis=0)
        assert_allclose(xd.asndarray(), x)

    def test_sum_rows(self, x, xd):
        xd = np.sum(xd, axis=1)
        x = np.sum(x, axis=1)
        assert_allclose(xd.asndarray(), x)

    def test_mean(self, x, xd):
        if sys.version_info[0] == 2 and isinstance(
            xd, zappy.beam.array.BeamZappyArray
        ):  # TODO: fix this
            return
        meand = np.mean(xd)
        mean = np.mean(x)
        assert meand == pytest.approx(mean)

    def test_mean_cols(self, x, xd):
        xd = np.mean(xd, axis=0)
        x = np.mean(x, axis=0)
        assert_allclose(xd.asndarray(), x)

    def test_mean_rows(self, x, xd):
        xd = np.mean(xd, axis=1)
        x = np.mean(x, axis=1)
        assert_allclose(xd.asndarray(), x)

    def test_var(self, x, xd):
        def var(x):
            mean = x.mean(axis=0)
            mean_sq = np.multiply(x, x).mean(axis=0)
            return mean_sq - mean ** 2

        varnps = var(xd).asndarray()
        varnp = var(x)
        assert_allclose(varnps, varnp)

    def test_write_zarr(self, x, xd_and_temp_store):
        xd, temp_store = xd_and_temp_store
        xd.to_zarr(temp_store, xd.chunks)
        # read back as zarr directly and check it is the same as x
        z = zarr.open(temp_store, mode="r", shape=x.shape, dtype=x.dtype, chunks=(2, 5))
        arr = z[:]
        assert_allclose(arr, x)

    def test_write_zarr_ncopies(self, x, xd_and_temp_store):
        xd, temp_store = xd_and_temp_store
        if sys.version_info[0] == 2 and isinstance(
            xd, zappy.beam.array.BeamZappyArray
        ):  # TODO: fix this
            return
        xd = xd._repartition_chunks((3, 5))
        ncopies = 3
        xd.to_zarr(temp_store, xd.chunks, ncopies=ncopies)
        # read back as zarr directly and check it is the same as x
        z = zarr.open(
            temp_store,
            mode="r",
            shape=(x.shape[0] * ncopies, x.shape[1]),
            dtype=x.dtype,
            chunks=(1, 5),
        )
        arr = z[:]
        x_ncopies = np.vstack((x,) * ncopies)
        assert_allclose(arr, x_ncopies)

    def test_zeros(self, zeros):
        totals = np.sum(zeros, axis=0)
        x = np.array([0, 0, 0, 0, 0])
        assert_allclose(totals.asndarray(), x)

    def test_ones(self, ones):
        totals = np.sum(ones, axis=0)
        x = np.array([3, 3, 3, 3, 3])
        assert_allclose(totals.asndarray(), x)

    def test_asndarrays(self, x, xd):
        if not isinstance(xd, zappy.executor.array.ExecutorZappyArray):
            return
        xd1, xd2 = zappy.executor.asndarrays((xd + 1, xd + 2))
        assert_allclose(xd1, x + 1)
        assert_allclose(xd2, x + 2)
