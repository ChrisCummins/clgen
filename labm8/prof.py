"""Profiling API for timing critical paths in code.
"""
from __future__ import absolute_import
from __future__ import print_function

import inspect
import os
import sys
from time import time

from labm8 import labtypes


__TIMERS = {}


def is_enabled():
  return os.environ.get("PROFILE") is not None


def enable():
  os.environ["PROFILE"] = "1"


def disable():
  os.environ.pop("PROFILE", None)


def isrunning(name):
  """
  Check if a timer is running.

  Arguments:

      name (str, optional): The name of the timer to check.

  Returns:

      bool: True if timer is running, else False.
  """
  return name in _timers


def start(name):
  """
  Start a new profiling timer.

  Arguments:

      name (str, optional): The name of the timer to create. If no
        name is given, the resulting timer is anonymous. There can
        only be one anonymous timer.
      unique (bool, optional): If true, then ensure that timer name
        is unique. This is to prevent accidentally resetting an
        existing timer.

  Returns:

      bool: Whether or not profiling is enabled.
  """
  if is_enabled():
    __TIMERS[name] = time()
  return is_enabled()


def stop(name, file=sys.stderr):
  """
  Stop a profiling timer.

  Arguments:

      name (str): The name of the timer to stop. If no name is given, stop
          the global anonymous timer.

  Returns:

      bool: Whether or not profiling is enabled.

  Raises:

      KeyError: If the named timer does not exist.
  """
  if is_enabled():
    elapsed = (time() - __TIMERS[name])
    if elapsed > 60:
      elapsed_str = '{:.1f} m'.format(elapsed / 60)
    elif elapsed > 1:
      elapsed_str = '{:.1f} s'.format(elapsed)
    else:
      elapsed_str = '{:.1f} ms'.format(elapsed * 1000)

    del __TIMERS[name]
    print("[prof]", name, elapsed_str, file=file)
  return is_enabled()


def profile(fun, *args, **kwargs):
  """
  Profile a function.
  """
  timer_name = kwargs.pop("prof_name", None)

  if not timer_name:
    module = inspect.getmodule(fun)
    c = [module.__name__]
    parentclass = labtypes.get_class_that_defined_method(fun)
    if parentclass:
      c.append(parentclass.__name__)
    c.append(fun.__name__)
    timer_name = ".".join(c)

  start(timer_name)
  ret = fun(*args, **kwargs)
  stop(timer_name)
  return ret


def timers():
  """
  Iterate over all timers.

  Returns:
      Iterable[str]: An iterator over all time names.
  """
  for name in __TIMERS:
    yield name
