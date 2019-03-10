# This is an implementation of the OpenCL benchmark driver from:
#
#     ﻿Cummins, C., Petoumenos, P., Zang, W., & Leather, H. (2017). Synthesizing
#     Benchmarks for Predictive Modeling. In CGO. IEEE.

py_binary(
    name = "generative_model",
    srcs = ["generative_model.py"],
    visibility = ["//visibility:public"],
    deps = [
        "//deeplearning/clgen",
        "//deeplearning/clgen/proto:clgen_py_pb2",
        "//deeplearning/clgen/proto:corpus_py_pb2",
        "//deeplearning/clgen/proto:model_py_pb2",
        "//deeplearning/clgen/proto:sampler_py_pb2",
        "//third_party/py/absl",
    ],
)

py_test(
    name = "generative_model_test",
    srcs = ["generative_model_test.py"],
    deps = [
        ":generative_model",
        "//labm8:test",
    ],
)

sh_test(
    name = "generative_model_smoke_test",
    srcs = ["generative_model_smoke_test.sh"],
    data = [":generative_model"],
)

py_library(
    name = "opencl_kernel_driver",
    srcs = ["opencl_kernel_driver.py"],
    visibility = ["//visibility:public"],
    deps = [
        "//compilers/llvm:clang",
        "//deeplearning/deepsmith/harnesses:cldrive",
        "//deeplearning/deepsmith/proto:deepsmith_py_pb2",
        "//gpu/cldrive/legacy:env",
        "//gpu/libcecl:libcecl_compile",
        "//gpu/libcecl:libcecl_rewriter",
        "//gpu/libcecl:libcecl_runtime",
        "//gpu/libcecl/proto:libcecl_py_pb2",
        "//third_party/py/absl",
    ],
)

py_test(
    name = "opencl_kernel_driver_test",
    srcs = ["opencl_kernel_driver_test.py"],
    deps = [
        ":opencl_kernel_driver",
        "//gpu/cldrive/legacy:env",
        "//labm8:test",
        "//third_party/py/pytest",
    ],
)