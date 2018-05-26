"""Command line interface to clgen."""
import argparse
import cProfile
import inspect
import os
import pathlib
import sys
import traceback
from argparse import ArgumentParser, FileType, RawDescriptionHelpFormatter
from pathlib import Path
from typing import BinaryIO, List, TextIO

from absl import logging

from deeplearning.clgen import atomizers
from deeplearning.clgen import cache
from deeplearning.clgen import dbutil
from deeplearning.clgen import errors
from deeplearning.clgen import explore
from deeplearning.clgen import features
from deeplearning.clgen import fetch
from deeplearning.clgen import languages
from deeplearning.clgen import samplers
from deeplearning.clgen.models import models
from deeplearning.clgen.preprocessors import preprocessors
from deeplearning.clgen.proto import model_pb2
from deeplearning.clgen.proto import sampler_pb2
from lib.labm8 import fs
from lib.labm8 import labtypes
from lib.labm8 import pbutil
from lib.labm8 import prof


__help_epilog__ = """
Copyright (C) 2016, 2017, 2018 Chris Cummins <chrisc.101@gmail.com>.
<http://chriscummins.cc/clgen>
"""


def getself(func):
  """ decorator to pass function as first argument to function """

  def wrapper(*args, **kwargs):
    return func(func, *args, **kwargs)

  return wrapper


class ReadableFilesOrDirectories(argparse.Action):
  """
  Adapted from @mgilson http://stackoverflow.com/a/11415816
  """

  def __call__(self, parser, namespace, values, option_string=None) -> None:
    for path in values:
      if not os.path.isdir(path) and not os.path.isfile(path):
        raise argparse.ArgumentTypeError(
            f"ReadableFilesOrDirectories:{path} not found")
      if not os.access(path, os.R_OK):
        raise argparse.ArgumentTypeError(
            f"ReadableFilesOrDirectories:{path} is not readable")

    setattr(namespace, self.dest, [Path(path) for path in values])


def run(method, *args, **kwargs):
  """
  Runs the given method as the main entrypoint to a program.

  If an exception is thrown, print error message and exit.

  If environmental variable DEBUG=1, then exception is not caught.

  Parameters
  ----------
  method : function
      Function to execute.
  *args
      Arguments for method.
  **kwargs
      Keyword arguments for method.

  Returns
  -------
  method(*args, **kwargs)
  """

  def _user_message(exception):
    logging.fatal("""\
{err} ({type})

Please report bugs at <https://github.com/ChrisCummins/clgen/issues>\
""".format(err=e, type=type(e).__name__))

  def _user_message_with_stacktrace(exception):
    # get limited stack trace
    def _msg(i, x):
      n = i + 1

      filename = fs.basename(x[0])
      lineno = x[1]
      fnname = x[2]

      loc = "{filename}:{lineno}".format(**vars())
      return "      #{n}  {loc: <18} {fnname}()".format(**vars())

    _, _, tb = sys.exc_info()
    NUM_ROWS = 5  # number of rows in traceback

    trace = reversed(traceback.extract_tb(tb, limit=NUM_ROWS + 1)[1:])
    message = "\n".join(_msg(*r) for r in enumerate(trace))

    logging.fatal("""\
{err} ({type})

  stacktrace:
{stack_trace}

Please report bugs at <https://github.com/ChrisCummins/clgen/issues>\
""".format(err=e, type=type(e).__name__, stack_trace=message))

  # if DEBUG var set, don't catch exceptions
  if os.environ.get("DEBUG", None):
    # verbose stack traces. see: https://pymotw.com/2/cgitb/
    import cgitb
    cgitb.enable(format='text')

    return method(*args, **kwargs)

  try:
    def runctx():
      return method(*args, **kwargs)

    if prof.is_enabled() and logging.get_verbosity() == logging.DEBUG:
      return cProfile.runctx('runctx()', None, locals(), sort='tottime')
    else:
      return runctx()
  except errors.UserError as err:
    logging.fatal("%s (%s)", err, type(err).__name__)
  except KeyboardInterrupt:
    sys.stdout.flush()
    sys.stderr.flush()
    print("\nkeyboard interrupt, terminating", file=sys.stderr)
    sys.exit(1)
  except errors.File404 as e:
    _user_message(e)
  except Exception as e:
    _user_message_with_stacktrace(e)


@getself
def _register_train_parser(self, parent: ArgumentParser) -> None:
  """Train a CLgen model."""

  def _main(model_file: TextIO) -> None:
    model_proto = pbutil.FromFile(pathlib.Path(model_file.name),
                                  model_pb2.Model())
    model_ = models.Model(model_proto)
    model_.Train()
    logging.info("done.")

  parser = parent.add_parser("train", aliases=["t", "tr"], help="train models",
                             description=inspect.getdoc(self),
                             epilog=__help_epilog__)
  parser.set_defaults(dispatch_func=_main)
  parser.add_argument("model_file", metavar="<model>", type=FileType("r"),
                      help="path to model specification file")


@getself
def _register_sample_parser(self, parent: ArgumentParser) -> None:
  """Sample a model."""

  def _main(model_file: TextIO, sampler_file: TextIO) -> None:
    model_proto = pbutil.FromFile(pathlib.Path(model_file.name),
                                  model_pb2.Model())
    model = models.Model(model_proto)
    sampler_proto = pbutil.FromFile(pathlib.Path(sampler_file.name),
                                    sampler_pb2.Sampler())
    sampler = samplers.Sampler(sampler_proto)
    model.Sampler(sampler)

  parser = parent.add_parser("sample", aliases=["s", "sa"],
                             help="train and sample models",
                             description=inspect.getdoc(self),
                             epilog=__help_epilog__)
  parser.set_defaults(dispatch_func=_main)
  parser.add_argument("model_file", metavar="<model>", type=FileType("r"),
                      help="path to model specification file")
  parser.add_argument("sampler_file", metavar="<sampler>", type=FileType("r"),
                      help="path to sampler specification file")


@getself
def _register_fetch_parser(self, parent: ArgumentParser) -> None:
  """
  Import OpenCL files into kernel datbase.

  The kernel database is used as a staging ground for input files, which are
  then preprocessed and assembled into corpuses. This program acts as the front
  end, assembling files from the file system into a database for preprocessing.
  """

  # FIXME(polyglot):

  @getself
  def _register_fs_parser(self, parent: ArgumentParser) -> None:
    """
    Fetch files from local filesystem.
    """

    def _main(db_file: BinaryIO, paths: List[Path]) -> None:
      fetch.fetch(db_file.name, paths)
      logging.info("done.")

    parser = parent.add_parser("fs", help="fetch from filesystem",
                               description=inspect.getdoc(self),
                               epilog=__help_epilog__)
    parser.set_defaults(dispatch_func=_main)
    parser.add_argument("db_file", metavar="<db>", type=FileType("rb"),
                        help="path to SQL dataset")
    parser.add_argument("paths", metavar="<path>", nargs="+",
                        action=ReadableFilesOrDirectories,
                        help="path to OpenCL files or directories")

  @getself
  def _register_github_parser(self, parent: ArgumentParser) -> None:
    """
    Mines OpenCL kernels from Github. Requires the following environment
    variables to be set:

         GITHUB_USERNAME   github username
         GITHUB_PW         github password
         GITHUB_TOKEN      github api token

    For instructions to generate an API token, see:

      <https://help.github.com/articles/creating-an-access-token-for-command
      -line-use/>

    This process issues thousands of GitHub API requests per minute. Please
    exercise restrained in minimizing your use of this program -- we don't
    want to upset the nice folks at GH :-)
    """

    def _main(db_file: BinaryIO) -> None:
      from os import environ
      from github import BadCredentialsException

      try:
        username = environ['GITHUB_USERNAME']
        password = environ['GITHUB_PW']
        token = environ['GITHUB_TOKEN']
      except KeyError as e:
        logging.fatal('environment variable {} not set'.format(e))

      try:
        fetch.fetch_github(db_file.name, username, password, token,
                           lang=languages.Language.OPENCL)
      except BadCredentialsException as e:
        logging.fatal("bad GitHub credentials")

    parser = parent.add_parser("github", help="mine OpenCL from GitHub",
                               description=inspect.getdoc(self),
                               epilog=__help_epilog__)
    parser.set_defaults(dispatch_func=_main)
    parser.add_argument("db_file", metavar="<db>", type=FileType('rb'),
                        help='path to SQL dataset')

  @getself
  def _register_repos_parser(self, parent: ArgumentParser) -> None:
    """
    Imports files from a directory of cloned git repositories.
    """

    def _main(db_file: BinaryIO, indir: Path) -> None:
      fetch.fetch_repos(db_file.name, indir, lang=languages.Language.GLSL)

    parser = parent.add_parser("repos",
                               help="import from directory of repositories",
                               description=inspect.getdoc(self),
                               epilog=__help_epilog__)
    parser.set_defaults(dispatch_func=_main)
    parser.add_argument("db_file", metavar="<db>", type=FileType('rb'),
                        help='path to SQL dataset')
    parser.add_argument("indir", metavar="<dir>",
                        help='directory containing repositories')

  fetch_ = parent.add_parser("fetch", aliases=["f", "fe"],
                             help="gather training data",
                             description=inspect.getdoc(self),
                             epilog=__help_epilog__)
  subparser = fetch_.add_subparsers(title="available commands")

  subparsers = [_register_fs_parser, _register_github_parser,
                _register_repos_parser, ]

  for register_fn in subparsers:
    register_fn(subparser)


@getself
def _register_ls_parser(self, parent: ArgumentParser) -> None:
  @getself
  def _register_files_parser(self, parent: ArgumentParser) -> None:
    """
    Import OpenCL files into kernel datbase.

    The kernel database is used as a staging ground for input files, which
    are then preprocessed and assembled into corpuses. This program acts as
    the front end, assembling files from the file system into a database for
    preprocessing.
    """

    def _main(model_file: TextIO, sampler_file: TextIO) -> None:
      model_proto = pbutil.FromFile(pathlib.Path(model_file.name),
                                    model_pb2.Model())
      model = models.Model(model_proto)
      cache_paths = [model.corpus.cache.path, model.cache.path]

      if sampler_file:
        sampler_proto = pbutil.FromFile(pathlib.Path(sampler_file.name),
                                        sampler_pb2.Sampler())
        sampler = samplers.Sampler(sampler_proto)
        cache_paths.append(model.SamplerCache(sampler))

      files = labtypes.flatten(
          fs.ls(c, abspaths=True, recursive=True) for c in cache_paths if
          c.is_dir())
      print('\n'.join(sorted(files)))

    parser = parent.add_parser("files", help="list cached files",
                               description=inspect.getdoc(self),
                               epilog=__help_epilog__)
    parser.set_defaults(dispatch_func=_main)
    parser.add_argument("model_file", metavar="<model>", type=FileType("r"),
                        help="path to model specification file")
    parser.add_argument("sampler_file", metavar="<sampler>", nargs="?",
                        type=FileType("r"),
                        help="path to sampler specification file")

  @getself
  def _register_models_parser(self, parent: ArgumentParser) -> None:
    """
    List all locally cached models.
    """

    def _main() -> None:
      print(models.ModelsToTable(*models.GetAllModels()))

    parser = parent.add_parser("models", help="list cached models",
                               description=inspect.getdoc(self),
                               epilog=__help_epilog__)
    parser.set_defaults(dispatch_func=_main)

  @getself
  def _register_samplers_parser(self, parent: ArgumentParser) -> None:
    """
    List all locally cached samplers.
    """

    def _main() -> None:
      logging.warning("not implemented")

    parser = parent.add_parser("samplers", help="list cached samplers",
                               description=inspect.getdoc(self),
                               epilog=__help_epilog__)
    parser.set_defaults(dispatch_func=_main)

  parser = parent.add_parser("ls", help="list files",
                             description=inspect.getdoc(self),
                             epilog=__help_epilog__)
  subparser = parser.add_subparsers(title="available commands")

  _register_files_parser(subparser)
  _register_models_parser(subparser)
  _register_samplers_parser(subparser)


@getself
def _register_db_parser(self, parent: ArgumentParser) -> None:
  """
  Utilities for managing content databases.
  """

  @getself
  def _register_init_parser(self, parent: ArgumentParser) -> None:
    """
    Utilities for managing content databases.
    """

    def _main(db_file: Path, github: bool) -> None:
      """
      Create an empty OpenCL kernel database.
      """
      dbutil.create_db(db_file, github)
      print(fs.abspath(db_file))

    parser = parent.add_parser("init", help="create a database",
                               description=inspect.getdoc(self),
                               epilog=__help_epilog__)
    parser.set_defaults(dispatch_func=_main)
    parser.add_argument('db_file', metavar="<db>", type=Path,
                        help='path to SQL dataset')
    parser.add_argument('-g', '--github', action='store_true',
                        help='generate dataset with GitHub metadata')

  @getself
  def _register_explore_parser(self, parent: ArgumentParser) -> None:
    """
    Exploratory analysis of preprocessed dataset.

    Provides an overview of the contents of an OpenCL kernel database.
    """

    def _main(db_file: BinaryIO):
      explore.explore(db_file.name)

    parser = parent.add_parser("explore", help="show database stats",
                               description=inspect.getdoc(self),
                               epilog=__help_epilog__)
    parser.set_defaults(dispatch_func=_main)
    parser.add_argument('db_file', metavar="<db>", type=FileType("rb"),
                        help='path to SQL dataset')

  @getself
  def _register_merge_parser(self, parent: ArgumentParser) -> None:
    """
    Merge kernel datasets.
    """

    def _main(db_out: BinaryIO, inputs: List[BinaryIO]):
      dbutil.merge(db_out.name, [db.name for db in inputs])

    parser = parent.add_parser("merge", help="merge databases",
                               description=inspect.getdoc(_main),
                               epilog=__help_epilog__)
    parser.set_defaults(dispatch_func=_main)
    parser.add_argument("db_out", metavar="<db>", type=FileType("wb"),
                        help="path to output database")
    parser.add_argument("inputs", metavar="<db>", nargs='+',
                        type=FileType("rb"),
                        help="path to database(s) to merge")

  @getself
  def _register_dump_parser(self, parent: ArgumentParser) -> None:
    """
    Dump kernel dataset to file(s).
    """

    def _main(db_file: BinaryIO, outpath: Path, dir: bool, eof: bool,
              file_sep: bool, input_samples: bool, reverse: bool,
              status: int) -> None:
      dbutil.dump_db(db_file.name, outpath, dir=dir, eof=eof, fileid=file_sep,
                     input_samples=input_samples)

    parser = parent.add_parser("dump", help="export database contents",
                               description=inspect.getdoc(_main),
                               epilog=__help_epilog__)
    parser.set_defaults(dispatch_func=_main)
    parser.add_argument('db_file', metavar="<db>", type=FileType("rb"),
                        help='path to kernels database')
    parser.add_argument('outpath', metavar="<path>", type=Path,
                        help='path to output file or directory')
    parser.add_argument("-d", "--dir", action='store_true',
                        help='output to directory (overrides -i, --eof, -r)')
    parser.add_argument("-i", "--file-sep", action='store_true',
                        help='include file separators')
    parser.add_argument('--input-samples', action='store_true',
                        help='use input contents, not preprocessed')
    parser.add_argument('--eof', action='store_true', help='print end of file')
    parser.add_argument('-r', '--reverse', action='store_true',
                        help='use reverse order')
    parser.add_argument('-s', '--status', type=int, default=0,
                        help='status code to use')

  parser = parent.add_parser("db", help="manage databases",
                             description=inspect.getdoc(self),
                             epilog=__help_epilog__)
  subparser = parser.add_subparsers(title="available commands")

  subparsers = [_register_init_parser, _register_explore_parser,
                _register_merge_parser, _register_dump_parser, ]

  for register_fn in subparsers:
    register_fn(subparser)


@getself
def _register_preprocess_parser(self, parent: ArgumentParser) -> None:
  """
  Process OpenCL files for machine learning.

  This is a three step process. First, the OpenCL kernels are compiled to
  bytecode, then the source files are preprocessed, before being rewritten.

  Preprocessing is computationally demanding and highly paralellised.
  Expect high resource contention during preprocessing.
  """

  # FIXME(polyglot):

  def _main(inputs: List[TextIO], inputs_are_files: bool, inplace: bool,
            gpuverify: bool, remove_bad_preprocessed: bool,
            remove_preprocessed: bool) -> None:
    input_paths = [infile.name for infile in inputs]

    for path in input_paths:
      if remove_bad_preprocessed:
        dbutil.remove_bad_preprocessed(path)
      elif remove_preprocessed:
        dbutil.remove_preprocessed(path)
        print("done.")
      else:
        if preprocessors.PreprocessDatabase(pathlib.Path(path),
                                            languages.Language.OPENCL, []):
          print("done.")
        else:
          print("nothing to be done.")

  parser = parent.add_parser("preprocess", aliases=["p", "pp"],
                             help="preprocess files for training",
                             description=inspect.getdoc(self),
                             epilog=__help_epilog__)
  parser.set_defaults(dispatch_func=_main)
  parser.add_argument('inputs', metavar="<path>", nargs='+', type=FileType("r"),
                      help='path to input(s)')
  parser.add_argument('-f', '--file', dest="inputs_are_files",
                      action='store_true', help='treat input as file')
  parser.add_argument('-i', '--inplace', action='store_true',
                      help='inplace file rewrite')
  parser.add_argument('-G', '--gpuverify', action='store_true',
                      help='run GPUVerify on kernels')
  group = parser.add_mutually_exclusive_group()
  group.add_argument('--remove-bad-preprocessed', action='store_true', help="""\
delete the contents of all bad or ugly preprocessed files,
but keep the entries in the table""")
  group.add_argument("--remove-preprocessed", action="store_true",
                     help="remove all preprocessed files from database")


@getself
def _register_features_parser(self, parent: ArgumentParser) -> None:
  """
  Extract static OpenCL kernel features.

  This extracts the static compile-time features of the paper:

      Grewe, D., Wang, Z., & O'Boyle, M. F. P. M. (2013). Portable Mapping of
      Data Parallel Programs to OpenCL for Heterogeneous Systems. In CGO. IEEE.
  """

  # FIXME(polyglot):

  def _main(infiles: List[TextIO], dir_mode: bool, summarise: bool,
            fatal_errors: bool, use_shim: bool, quiet: bool,
            no_header: bool) -> None:
    input_paths = [infile.name for infile in infiles]

    def features_dir(csv_path):
      return fs.basename(fs.dirname(csv_path))

    if summarise:
      stats = [features.summarize(f) for f in input_paths]

      print('dataset', *list(stats[0].keys()), sep=',')
      for path, stat in zip(input_paths, stats):
        print(features_dir(path), *list(stat.values()), sep=',')
      return

    if dir_mode:
      trees = [fs.ls(d, abspaths=True, recursive=True) for d in input_paths]
      paths = [item for sublist in trees for item in sublist]
    else:
      paths = [fs.path(f) for f in input_paths]

    features.files(paths, fatal_errors=fatal_errors, quiet=quiet,
                   use_shim=use_shim, header=not no_header)

  parser = parent.add_parser("features", help="extract OpenCL kernel features",
                             description=inspect.getdoc(self),
                             epilog=__help_epilog__)
  parser.set_defaults(dispatch_func=_main)
  parser.add_argument("infiles", metavar="<path>", nargs="+",
                      type=FileType("r"), help="input path(s)")
  parser.add_argument("-d", "--dir", action="store_true",
                      help="treat inputs as directories")
  parser.add_argument("-s", "--stats", dest="summarize", action="store_true",
                      help="summarize features files")
  parser.add_argument("-e", "--fatal-errors", action="store_true",
                      help="quit on compiler error")
  parser.add_argument("--shim", dest="use_shim", action="store_true",
                      help="include shim header")
  parser.add_argument("-q", "--quiet", action="store_true",
                      help="minimal error output")
  parser.add_argument("-H", "--no-header", action="store_true",
                      help="no features header")


@getself
def _register_atomize_parser(self, parent: ArgumentParser) -> None:
  """
  Extract and print corpus vocabulary.
  """

  # FIXME(polyglot):

  def _main(infile: TextIO, vocab: str, size: bool) -> None:
    atoms = atomizers.atomize(infile.read(), vocab=vocab)

    if size:
      logging.info("size:", len(atoms))
    else:
      logging.info('\n'.join(atoms))

  parser = parent.add_parser("atomize", help="atomize files",
                             description=inspect.getdoc(self),
                             epilog=__help_epilog__)
  parser.set_defaults(dispatch_func=_main)
  parser.add_argument('infile', metavar="<path>", type=FileType("r"),
                      help='path to input text file')
  parser.add_argument('-t', '--type', type=str, dest="vocab", default='char',
                      help='vocabulary type')
  parser.add_argument('-s', '--size', action="store_true",
                      help="print vocabulary size")


@getself
def _register_cache_parser(self, parent: ArgumentParser) -> None:
  """
  Manage filesystem cache.
  """

  @getself
  def _register_migrate_parser(self, parent: ArgumentParser) -> None:
    """
    Refresh the cached model, corpus, and sampler IDs.
    """

    def _main() -> None:
      cache_ = cache.cachepath()

      logging.warning("Not Implemented: refresh corpuses")

      if fs.isdir(cache_, "model"):
        cached_modeldirs = fs.ls(fs.path(cache_, "model"), abspaths=True)
        for cached_modeldir in cached_modeldirs:
          cached_model_id = fs.basename(cached_modeldir)
          cached_proto = pbutil.FromFile(
              pathlib.Path(fs.path(cached_modeldir, "META")))
          model_ = models.Model(cached_proto)
          if cached_model_id != model_.hash:
            logging.info(cached_model_id, '->', model_.hash)

            if fs.isdir(model_.cache.path):
              logging.fatal("cache_ conflict", file=sys.stderr)

            fs.mv(cached_modeldir, model_.cache.path)

      logging.warning("Not Implemented: refresh samplers")

    parser = parent.add_parser("migrate", help="migrate the cache_",
                               description=inspect.getdoc(self),
                               epilog=__help_epilog__)
    parser.set_defaults(dispatch_func=_main)

  parser = parent.add_parser("cache", help="manage filesystem cache_",
                             description=inspect.getdoc(self),
                             epilog=__help_epilog__)

  subparser = parser.add_subparsers(title="available commands")

  subparsers = [_register_migrate_parser, ]

  for register_fn in subparsers:
    register_fn(subparser)


@getself
def main(self, args: List[str] = sys.argv[1:]):
  """
  A deep learning program generator for the OpenCL programming language.

  The core operations of CLgen are:

     1. OpenCL files are collected from a model specification file.
     2. These files are preprocessed into an OpenCL kernel database.
     3. A training corpus is generated from the input files.
     4. A machine learning model is trained on the corpus of files.
     5. The trained model is sampled for new kernels.
     6. The samples are tested for compilability.

  This program automates the execution of all six stages of the pipeline.
  The pipeline can be interrupted and resumed at any time. Results are cached
  across runs. If installed with CUDA support, NVIDIA GPUs will be used to
  improve performance where possible.
  """
  parser = ArgumentParser(prog="clgen", description=inspect.getdoc(self),
                          epilog="""
For information about a specific command, run `clgen <command> --help`.

""" + __help_epilog__, formatter_class=RawDescriptionHelpFormatter)

  # TODO:
  # parser.add_argument(
  #     "-l", "--lang", metavar="<language>",
  #     help="programming language (default: OpenCL)")
  parser.add_argument("-v", "--verbose", action="store_true",
                      help="increase output verbosity")
  parser.add_argument("--version", action="store_true",
                      help="show version information and exit")
  parser.add_argument("--debug", action="store_true",
                      help="in case of error, print debugging information")
  parser.add_argument("--profile", action="store_true", help=(
    "enable internal API profiling. When combined with --verbose, "
    "prints a complete profiling trace"))

  parser.add_argument("--corpus-dir", metavar="<corpus>", type=FileType("r"),
                      help="print path to corpus cache")
  parser.add_argument("--model-dir", metavar="<model>", type=FileType("r"),
                      help="print path to model cache")
  parser.add_argument("--sampler-dir", metavar=("<model>", "<sampler>"),
                      type=FileType("r"), nargs=2,
                      help="print path to sampler cache")

  subparser = parser.add_subparsers(title="available commands")

  subparsers = [_register_train_parser, _register_sample_parser,
                _register_db_parser, _register_fetch_parser,
                _register_ls_parser, _register_preprocess_parser,
                _register_features_parser, _register_atomize_parser,
                _register_cache_parser, ]

  for register_fn in subparsers:
    register_fn(subparser)

  args = parser.parse_args(args)

  # set log level
  logging.set_verbosity(logging.DEBUG if args.verbose else logging.INFO)

  # set debug option
  if args.debug:
    os.environ["DEBUG"] = "1"

  # set profile option
  if args.profile:
    prof.enable()

  # options whch override the normal argument parsing process.
  if args.version:
    print("clgen made with \033[1;31m♥\033[0;0m by Chris Cummins "
          "<chrisc.101@gmail.com>.")
  elif args.corpus_dir:
    model = models.Model(
        pbutil.FromFile(pathlib.Path(args.corpus_dir.name), model_pb2.Model()))
    print(model.corpus.cache.path)
  elif args.model_dir:
    model = models.Model(
        pbutil.FromFile(pathlib.Path(args.model_dir.name), model_pb2.Model()))
    print(model.cache.path)
  elif args.sampler_dir:
    model = models.Model(
        pbutil.FromFile(pathlib.Path(args.sampler_dir[0].name),
                        model_pb2.Model()))
    sampler = samplers.Sampler(
        pbutil.FromFile(pathlib.Path(args.sampler_dir[1].name),
                        sampler_pb2.Sampler()))
    print(model.SamplerCache(sampler))
  else:
    # strip the arguments from the top-level parser
    dispatch_func = args.dispatch_func
    opts = vars(args)
    del opts["version"]
    del opts["verbose"]
    del opts["debug"]
    del opts["profile"]
    del opts["corpus_dir"]
    del opts["model_dir"]
    del opts["sampler_dir"]
    del opts["dispatch_func"]

    run(dispatch_func, **opts)


if __name__ == '__main__':
  main()
