"""Unit tests for //deeplearning/clgen/preprocessors/opencl.py."""
import subprocess
import sys

import pytest
from absl import app
from absl import flags
from absl import logging

import deeplearning.clgen
from deeplearning.clgen import errors, native
from deeplearning.clgen.preprocessors import opencl


FLAGS = flags.FLAGS


class MockProcess():
  """Mock class for subprocess.Popen() return."""

  def __init__(self, returncode):
    self.returncode = returncode

  def communicate(self, *args):
    return '', ''


# GetClangArgs() tests.

def test_GetClangArgs_no_shim():
  args = opencl.GetClangArgs(use_shim=False)
  assert native.SHIMFILE not in args


def test_GetClangArgs_with_shim():
  args = opencl.GetClangArgs(use_shim=True)
  assert native.SHIMFILE in args


# ClangPreprocess() tests.

def test_ClangPreprocess_small_program():
  """Test that a small program without preprocessor directives is unchanged."""
  assert opencl.ClangPreprocess("""
kernel void A(global int*a ) {}
""") == """
kernel void A(global int*a ) {}
"""


def test_ClangPreprocess_user_directives():
  """Test inlining of user-defined preprocessor directives."""
  assert opencl.ClangPreprocess("""
#define MY_TYPE int
kernel void A(global MY_TYPE* a) {}
#ifdef SOME_CONDITIONAL_THING
kernel void B() {}
#endif
""") == """

kernel void A(global int* a) {}
"""


def test_ClangPreprocess_undefined_macro():
  """Test that code with undefined macro is unchanged."""
  assert opencl.ClangPreprocess("""
kernel void A(global FLOAT_T* a) {}
""") == """
kernel void A(global FLOAT_T* a) {}
"""


# ClangPreprocessWithShim() tests.

def test_ClangPreprocessWithShim_compiler_args(mocker):
  """Test that shimfile is in comand which is run."""
  mock_Popen = mocker.patch('subprocess.Popen')
  mock_Popen.return_value = MockProcess(0)
  opencl.ClangPreprocessWithShim('')
  subprocess.Popen.assert_called_once()
  cmd = subprocess.Popen.call_args_list[0][0][0]
  assert native.SHIMFILE in cmd


def test_ClangPreprocessWithShim_shim_define():
  """Test that code which contains defs in opencl-shim can compile."""
  # FLOAT_T is defined in shim header. Preprocess will fail if FLOAT_T is
  # undefined.
  assert opencl.ClangPreprocessWithShim("""
kernel void A(global FLOAT_T* a) {}
""") == """
kernel void A(global float* a) {}
"""


# Compile() tests.

def test_Compile_empty_input():
  """Test that Compile accepts an empty input."""
  assert opencl.Compile('') == ''


def test_Compile_small_program():
  """Test that Compile accepts a small program."""
  assert opencl.Compile("""
kernel void A(global int*a ) {
  a[get_global_id(0)] = 0;
}
""") == """
kernel void A(global int*a ) {
  a[get_global_id(0)] = 0;
}
"""


def test_Compile_missing_shim_define():
  """Test that Compile rejects a program which depends on the shim header."""
  with pytest.raises(errors.ClangException):
    opencl.Compile("""
kernel void A(global FLOAT_T* a) {}
""")


def test_Compile_user_define():
  """Test that Compile accepts a program with a custom #define."""
  assert opencl.Compile("""
#define FLOAT_T float
kernel void A(global FLOAT_T* a) {}
""") == """
#define FLOAT_T float
kernel void A(global FLOAT_T* a) {}
"""


def test_Compile_syntax_error():
  """Test that Compile rejects a program with invalid syntax."""
  with pytest.raises(errors.ClangException) as e_info:
    opencl.Compile("kernel void A2@@1!!!#")
  assert 'error: ' in str(e_info.value)


def test_Compile_undefined_variable():
  """Test that Compile rejects a program with an undefined variable."""
  with pytest.raises(errors.ClangException) as e_info:
    opencl.Compile("""
kernel void A(global int* a) {
  undefined_variable;
}
""")
  assert 'use of undeclared identifier' in str(e_info.value)


def test_Compile_undefined_function():
  """Test that Compile rejects a program with an undefined function."""
  with pytest.raises(errors.ClangException) as e_info:
    opencl.Compile("""
kernel void A(global int* a) {
  undefined_function(a);
}
""")
  assert 'implicit declaration of function' in str(e_info.value)


# SanitizeKernelPrototype() tests.

def test_SanitizeKernelPrototype_empty_input():
  """Test SanitizeKernelPrototype on an empty input."""
  assert opencl.SanitizeKernelPrototype('') == ''


def test_SanitizeKernelPrototype_leading_whitespace():
  """Test that SanitizeKernelPrototype strips leading whitespace."""
  assert opencl.SanitizeKernelPrototype("""
kernel void A(global float* a) {}
""") == """\
kernel void A(global float* a) {}
"""


def test_SanitizeKernelPrototype_multiple_spaces():
  """Test that SanitizeKernelPrototype removes double whitespace."""
  assert opencl.SanitizeKernelPrototype("""\
  kernel  void   A(global    float*  a) {}
""") == """\
kernel void A(global float* a) {}
"""


# StripDoubleUnderscorePrefixes() tests.

def test_StripDoubleUnderscorePrefixes_empty_input():
  assert opencl.StripDoubleUnderscorePrefixes('') == ''


def test_StripDoubleUnderscorePrefixes_simple_kernel():
  assert opencl.StripDoubleUnderscorePrefixes("""
__kernel void A(__global int* a) {
  __private int b;
}
""") == """
kernel void A(global int* a) {
  private int b;
}
"""


# Benchmarks.

HELLO_WORLD_CL = """
__kernel void A(__global int* a) {
  a[get_global_id(0)] = 0;
}
"""


def test_benchmark_ClangPreprocess_hello_world(benchmark):
  """Benchmark ClangPreprocess on a "hello world" OpenCL program."""
  benchmark(opencl.ClangPreprocess, HELLO_WORLD_CL)


def test_benchmark_ClangPreprocessWithShim_hello_world(benchmark):
  """Benchmark ClangPreprocessWithShim on a "hello world" OpenCL program."""
  benchmark(opencl.ClangPreprocessWithShim, HELLO_WORLD_CL)


def test_benchmark_Compile_hello_world(benchmark):
  """Benchmark Compile on a "hello world" OpenCL program."""
  benchmark(opencl.Compile, HELLO_WORLD_CL)


def test_benchmark_StripDoubleUnderscorePrefixes_hello_world(benchmark):
  """Benchmark StripDoubleUnderscorePrefixes on a "hello world" program."""
  benchmark(opencl.StripDoubleUnderscorePrefixes, HELLO_WORLD_CL)


@pytest.mark.skip(reason='TODO(cec): Re-enable GPUVerify support.')
def test_GpuVerify():
  code = """\
__kernel void A(__global float* a) {
  int b = get_global_id(0);
  a[b] *= 2.0f;
}"""
  assert opencl.GpuVerify(code, ["--local_size=64", "--num_groups=128"]) == code


@pytest.mark.skip(reason='TODO(cec): Re-enable GPUVerify support.')
def test_GpuVerify_data_race():
  code = """\
__kernel void A(__global float* a) {
  a[0] +=  1.0f;
}"""
  with pytest.raises(deeplearning.clgen.errors.GPUVerifyException):
    opencl.GpuVerify(code, ["--local_size=64", "--num_groups=128"])


def main(argv):
  """Main entry point."""
  if len(argv) > 1:
    raise app.UsageError('Unrecognized command line flags.')
  logging.set_verbosity(logging.DEBUG)
  sys.exit(pytest.main([__file__, '-vv']))


if __name__ == '__main__':
  app.run(main)