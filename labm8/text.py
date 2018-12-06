"""Text utilities.
"""
from __future__ import division

import re


class Error(Exception):
  """
  Module-level error.
  """
  pass


class TruncateError(Error):
  """
  Thrown in case of truncation error.
  """
  pass


def get_substring_idxs(substr, string):
  """
  Return a list of indexes of substr. If substr not found, list is
  empty.

  Arguments:
      substr (str): Substring to match.
      string (str): String to match in.

  Returns:
      list of int: Start indices of substr.
  """
  return [match.start() for match in re.finditer(substr, string)]


def truncate(string, maxchar):
  """
  Truncate a string to a maximum number of characters.

  If the string is longer than maxchar, then remove excess
  characters and append an ellipses.

  Arguments:

      string (str): String to truncate.
      maxchar (int): Maximum length of string in characters. Must be >= 4.

  Returns:

      str: Of length <= maxchar.

  Raises:

      TruncateError: In case of an error.
  """
  if maxchar < 4:
    raise TruncateError("Maxchar must be > 3")

  if len(string) <= maxchar:
    return string
  else:
    return string[:maxchar - 3] + "..."


def levenshtein(s1, s2):
  """
  Return the Levenshtein distance between two strings.

  Implementation of Levenshtein distance, one of a family of edit
  distance metrics.

  Based on: https://en.wikibooks.org/wiki/Algorithm_Implementation/Strings/Levenshtein_distance#Python

  Examples:

      >>> text.levensthein("foo", "foo")
      0

      >>> text.levensthein("foo", "fooo")
      1

      >>> text.levensthein("foo", "")
      3

      >>> text.levensthein("1234", "1 34")
      1

  Arguments:

      s1 (str): Argument A.
      s2 (str): Argument B.

  Returns:

      int: Levenshtein distance between the two strings.
  """
  # Left string must be >= right string.
  if len(s1) < len(s2):
    return levenshtein(s2, s1)

  # Distance is length of s1 if s2 is empty.
  if len(s2) == 0:
    return len(s1)

  previous_row = range(len(s2) + 1)
  for i, c1 in enumerate(s1):
    current_row = [i + 1]
    for j, c2 in enumerate(s2):
      insertions = previous_row[j + 1] + 1
      deletions = current_row[j] + 1
      substitutions = previous_row[j] + (c1 != c2)
      current_row.append(min(insertions, deletions, substitutions))
    previous_row = current_row

  return previous_row[-1]


def diff(s1, s2):
  """
  Return a normalised Levenshtein distance between two strings.

  Distance is normalised by dividing the Levenshtein distance of the
  two strings by the max(len(s1), len(s2)).

  Examples:

      >>> text.diff("foo", "foo")
      0

      >>> text.diff("foo", "fooo")
      1

      >>> text.diff("foo", "")
      1

      >>> text.diff("1234", "1 34")
      1

  Arguments:

      s1 (str): Argument A.
      s2 (str): Argument B.

  Returns:

      float: Normalised distance between the two strings.
  """
  return levenshtein(s1, s2) / max(len(s1), len(s2))
