"""Utility code for working with sqlalchemy."""
import collections
import contextlib
import pathlib
import typing

import pandas as pd
import sqlalchemy as sql
from absl import flags
from absl import logging
from sqlalchemy import orm
from sqlalchemy.dialects import mysql
from sqlalchemy.ext import declarative

from labm8 import labdate
from labm8 import pbutil
from labm8 import text

FLAGS = flags.FLAGS

flags.DEFINE_bool(
    'sqlutil_echo', False,
    'If True, the Engine will log all statements as well as a repr() of their '
    'parameter lists to the engines logger, which defaults to sys.stdout.')

# The Query type is returned by Session.query(). This is a convenience for type
# annotations.
Query = orm.query.Query


class DatabaseNotFound(FileNotFoundError):
  """An error that is raised if the requested database cannot be found."""

  def __init__(self, url: str):
    self._url = url

  @property
  def url(self):
    return self._url

  def __repr__(self) -> str:
    return f"Database not found: '{self.url}'"

  def __str__(self) -> str:
    return repr(self)


def Base(*args, **kwargs) -> sql.ext.declarative.DeclarativeMeta:
  """Construct a base class for declarative class definitions."""
  return sql.ext.declarative.declarative_base(*args, **kwargs)


def GetOrAdd(session: sql.orm.session.Session,
             model,
             defaults: typing.Dict[str, object] = None,
             **kwargs):
  """Instantiate a mapped database object.

  If the object is not in the database,
  add it. Note that no change is written to disk until commit() is called on the
  session.

  Args:
    session: The database session.
    model: The database table class.
    defaults: Default values for mapped objects.
    kwargs: The values for the table row.

  Returns:
    An instance of the model class, with the values specified.
  """
  instance = session.query(model).filter_by(**kwargs).first()
  if not instance:
    params = {
        k: v
        for k, v in kwargs.items()
        if not isinstance(v, sql.sql.expression.ClauseElement)
    }
    params.update(defaults or {})
    instance = model(**params)
    session.add(instance)
    if logging.level_debug():
      logging.debug('New record: %s(%s)', model.__name__,
                    ', '.join([f'{k}={v}' for k, v in params.items()]))
  return instance


def Get(session: sql.orm.session.Session,
        model,
        defaults: typing.Dict[str, object] = None,
        **kwargs):
  """Determine if a database object exists.

  Args:
    session: The database session.
    model: The database table class.
    defaults: Default values for mapped objects.
    kwargs: The values for the table row.

  Returns:
    An instance of the model class with the values specified, or None if the
    object is not in the database.
  """
  del defaults
  return session.query(model).filter_by(**kwargs).first()


def CreateEngine(url: str, must_exist: bool = False) -> sql.engine.Engine:
  """Create an sqlalchemy database engine.

  This is a convenience wrapper for creating an sqlalchemy engine, that also
  creates the database if required, and checks that the database exists. This
  means that it is less flexible than SqlAlchemy's create_engine() - only three
  combination of dialects and drivers are supported: sqlite, mysql, and
  postgresql.

  See https://docs.sqlalchemy.org/en/latest/core/engines.html for details.

  Additionally, this implements a custom 'file://' handler, which reads a URL
  from a local file, and returns a connection to the database addressed by the
  URL. Use this if you would like to keep sensitive information such as a MySQL
  database password out of your .bash_history.

  Examples:
    Create in-memory SQLite database:
    >>> engine = CreateEngine('sqlite://')

    Connect to an SQLite database at relative.db:
    >>> engine = CreateEngine('sqlite:///relative.db')

    Connect to an SQLite database at /absolute/path/to/db:
    >>> engine = CreateEngine('sqlite:////absolute/path/to/db')

    Connect to MySQL database:
    >>> engine = CreateEngine(
        'mysql://bob:password@localhost:1234/database?charset=utf8')

    Connect to PostgreSQL database:
    >>> engine.CreateEngine(
      'postgresql://bob:password@localhost:1234/database')

    Connect to a URL specified in the file /tmp/url.txt:
    >>> engine.CreateEngine('file:///tmp/url.txt')

    Connect to a URL specified in the file /tmp/url.txt, with the suffix
    '/database?charset=utf8':
    >>> engine.CreateEngine('file:///tmp/url.txt?/database?charset=utf8')

  Args:
    url: The URL of the database to connect to.
    must_exist: If True, raise DatabaseNotFound if it doesn't exist. Else,
        database is created if it doesn't exist.

  Returns:
    An SQLalchemy Engine instance.

  Raises:
    DatabaseNotFound: If the database does not exist and must_exist is set.
    ValueError: If the datastore backend is not supported.
  """
  if url.startswith('mysql://'):
    # Support for MySQL dialect.

    # We create a throwaway engine that we use to check if the requested
    # database exists.
    engine = sql.create_engine('/'.join(url.split('/')[:-1]))
    database = url.split('/')[-1].split('?')[0]
    query = engine.execute(
        sql.text('SELECT SCHEMA_NAME FROM '
                 'INFORMATION_SCHEMA.SCHEMATA WHERE '
                 'SCHEMA_NAME = :database'),
        database=database)
    if not query.first():
      if must_exist:
        raise DatabaseNotFound(url)
      else:
        # We can't use sql.text() escaping here because it uses single quotes
        # for escaping. MySQL only accepts backticks for quoting database
        # names.
        engine.execute(f'CREATE DATABASE `{database}`')
    engine.dispose()
  elif url.startswith('sqlite://'):
    # Support for SQLite dialect.

    # This project (phd) deliberately disallows relative paths due to Bazel
    # sandboxing.
    if url != 'sqlite://' and not url.startswith('sqlite:////'):
      raise ValueError("Relative path to SQLite database is not allowed")

    if url == 'sqlite://':
      if must_exist:
        raise ValueError(
            'must_exist=True not valid for in-memory SQLite database')
    else:
      path = pathlib.Path(url[len('sqlite:///'):])
      if must_exist:
        if not path.is_file():
          raise DatabaseNotFound(url)
      else:
        # Make the parent directory for SQLite database if creating a new
        # database.
        path.parent.mkdir(parents=True, exist_ok=True)
  elif url.startswith('postgresql://'):
    # Support for PostgreSQL dialect.

    engine = sql.create_engine('/'.join(url.split('/')[:-1] + ['postgres']))
    conn = engine.connect()
    database = url.split('/')[-1]
    query = conn.execute(
        sql.text('SELECT 1 FROM pg_database WHERE datname = :database'),
        database=database)
    if not query.first():
      if must_exist:
        raise DatabaseNotFound(url)
      else:
        # PostgreSQL does not let you create databases within a transaction, so
        # manually complete the transaction before creating the database.
        conn.execute(sql.text('COMMIT'))
        # PostgreSQL does not allow single quoting of database names.
        conn.execute(f'CREATE DATABASE {database}')
    conn.close()
    engine.dispose()
  elif url.startswith('file://'):
    # Read URL from a file.

    # Split the URL into the file path, and the optional suffix.
    components = url.split('?')
    path, suffix = components[0], '?'.join(components[1:])

    # Strip the file:// prefix from the path.
    path = pathlib.Path(path[len('file://'):])

    if not path.is_absolute():
      raise ValueError('Relative path to file:// is not allowed')

    if not path.is_file():
      raise FileNotFoundError(f"File '{path}' not found")

    # Read the contents of the file, ignoring lines starting with '#'.
    with open(path) as f:
      file_url = '\n'.join(x for x in f.read().split('\n')
                           if not x.lstrip().startswith('#')).strip()

    # Append the suffix.
    file_url += suffix

    # Recurse so that we can build the engine from the URL in the file.
    return CreateEngine(file_url, must_exist=must_exist)
  else:
    raise ValueError(f"Unsupported database URL='{url}'")

  # Create the engine.
  engine = sql.create_engine(url, encoding='utf-8', echo=FLAGS.sqlutil_echo)

  # Create and immediately close a connection. This is because SQLAlchemy engine
  # is lazily instantiated, so for connections such as SQLite, this line
  # actually creates the file.
  engine.connect().close()

  return engine


def ColumnNames(model) -> typing.List[str]:
  """Return the names of all columns in a mapped object.

  Args:
    model: A mapped class.

  Returns:
    A list of string column names in the order that they are declared.
  """
  try:
    inst = sql.inspect(model)
    return [c_attr.key for c_attr in inst.mapper.column_attrs]
  except sql.exc.NoInspectionAvailable as e:
    raise TypeError(str(e))


class Session(orm.session.Session):
  """A subclass of the default SQLAlchemy Session with added functionality.

  An instance of this class is returned by Database.Session().
  """

  def GetOrAdd(self, model, defaults: typing.Dict[str, object] = None,
               **kwargs):
    """Instantiate a mapped database object.

    If the object is not in the database, add it. Note that no change is written
    to disk until commit() is called on the session.

    Args:
      model: The database table class.
      defaults: Default values for mapped objects.
      kwargs: The values for the table row.

    Returns:
      An instance of the model class, with the values specified.
    """
    return GetOrAdd(self, model, defaults, **kwargs)


class Database(object):
  """A base class for implementing databases."""

  def __init__(self, url: str, declarative_base, must_exist: bool = False):
    """Instantiate a database object.

    Example:
      >>> db = Database('sqlite:////tmp/foo.db',
                        sqlalchemy.ext.declarative.declarative_base())

    Args:
      url: The URL of the database to connect to.
      declarative_base: The SQLAlchemy declarative base instance.
      must_exist: If True, raise DatabaseNotFound if it doesn't exist. Else,
        database is created if it doesn't exist.

    Raises:
      DatabaseNotFound: If the database does not exist and must_exist is set.
      ValueError: If the datastore backend is not supported.
    """
    self._url = url
    self.engine = CreateEngine(url, must_exist=must_exist)
    declarative_base.metadata.create_all(self.engine)
    declarative_base.metadata.bind = self.engine

    # Bind the Engine to a session maker, which instantiates our own Session
    # class, which is a subclass of the default SQLAlchemy Session with added
    # functionality.
    self.make_session = orm.sessionmaker(bind=self.engine, class_=Session)

  def Drop(self, are_you_sure_about_this_flag: bool = False):
    """Drop the database, irreverisbly destroying it.

    Be careful with this! After calling this method an a Database instance, no
    further operations can be made on it, and any Sessions should be discarded.

    Args:
      are_you_sure_about_this_flag: You should be sure.

    Raises:
      ValueError: In case you're not 100% sure.
    """
    if not are_you_sure_about_this_flag:
      raise ValueError("Let's take a minute to think things over")

    if self.url.startswith('mysql://'):
      engine = sql.create_engine('/'.join(self.url.split('/')[:-1]))
      database = self.url.split('/')[-1].split('?')[0]
      engine.execute(
          sql.text('DROP DATABASE IF EXISTS :database'), database=database)
    elif self.url.startswith('sqlite://'):
      path = pathlib.Path(self.url[len('sqlite:///'):])
      assert path.is_file()
      path.unlink()
    else:
      raise NotImplementedError(
          "Unsupported operation DROP for database: '{self.url}'")

  @property
  def url(self) -> str:
    """Return the URL of the database."""
    return self._url

  @contextlib.contextmanager
  def Session(self, commit: bool = False) -> Session:
    """Provide a transactional scope around a session.

    Args:
      commit: If true, commit session at the end of scope.

    Returns:
      A database session.
    """
    session = self.make_session()
    try:
      yield session
      if commit:
        session.commit()
    except:
      session.rollback()
      raise
    finally:
      session.close()

  def __repr__(self) -> str:
    return self.url


def QueryToDataFrame(session: Session, query: Query) -> pd.DataFrame:
  """Read query results to a Pandas DataFrame.

  Args:
    session: A database session.
    query: The query to run.

  Returns:
    A Pandas DataFrame.
  """
  return pd.read_sql(query.statement, session.bind)


def ModelToDataFrame(session: Session,
                     model,
                     columns: typing.Optional[typing.List[str]] = None,
                     query_identity=lambda q: q):
  """Construct and execute a query reads an object's fields to a dataframe.

  Args:
    session: A database session.
    model: A database mapped object.
    columns: A list of column names, where each element is a column mapped to
      the model. If not provided, all column names are used.
    query_identity: A function which takes the produced query and returns a
      query. Use this to implement filtering of the query results.

  Returns:
    A Pandas DataFrame with one column for each field.
  """
  columns = columns or ColumnNames(model)
  query = session.query(*[getattr(model, column) for column in columns])
  df = QueryToDataFrame(session, query_identity(query))
  df.columns = columns
  return df


class TablenameFromClassNameMixin(object):
  """A class mixin which derives __tablename__ from the class name.

  Add this mixin to a mapped table class to automatically set the set the
  __tablename__ property of a class to the lowercase name of the Python class.
  """

  @declarative.declared_attr
  def __tablename__(self):
    return self.__name__.lower()


class TablenameFromCamelCapsClassNameMixin(object):
  """A class mixin which derives __tablename__ from the class name.

  Add this mixin to a mapped table class to automatically set the set the
  __tablename__ property of a class to the name of the Python class with camel
  caps converted to underscores, e.g.

    class FooBar -> table "foo_bar".
  """

  @declarative.declared_attr
  def __tablename__(self):
    return text.CamelCapsToUnderscoreSeparated(self.__name__)


class ProtoBackedMixin(object):
  """A database table backed by protocol buffers.

  This class provides the abstract interface for sqlalchemy table classes which
  support serialization to and from protocol buffers.

  This is only an interface - inheriting classes must still inherit from
  sqlalchemy.ext.declarative.declarative_base().
  """
  proto_t = None

  def SetProto(self, proto: pbutil.ProtocolBuffer) -> None:
    """Set the fields of a protocol buffer with the values from the instance.

    Args:
      proto: A protocol buffer.
    """
    raise NotImplementedError(
        f'{type(self).__name__}.SetProto() not implemented')

  def ToProto(self) -> pbutil.ProtocolBuffer:
    """Serialize the instance to protocol buffer.

    Returns:
      A protocol buffer.
    """
    proto = self.proto_t()
    self.SetProto(proto)
    return proto

  @classmethod
  def FromProto(cls,
                proto: pbutil.ProtocolBuffer) -> typing.Dict[str, typing.Any]:
    """Return a dictionary of instance constructor args from proto.

    Examples:
      Construct a table instance from proto:
      >>> table = Table(**Table.FromProto(proto))

      Construct a table instance and add to session:
      >>> session.GetOrAdd(Table, **Table.FromProto(proto))

    Args:
      proto: A protocol buffer.

    Returns:
      A dictionary of constructor arguments.
    """
    raise NotImplementedError(
        f'{type(self).__name__}.FromProto() not implemented')

  @classmethod
  def FromFile(cls, path: pathlib.Path) -> typing.Dict[str, typing.Any]:
    """Return a dictionary of instance constructor args from proto file.

    Examples:
      Construct a table instance from proto file:
      >>> table = Table(**Table.FromFile(path))

      Construct a table instance and add to session:
      >>> session.GetOrAdd(Table, **Table.FromFile(path))

    Args:
      path: Path to a proto file.

    Returns:
      An instance.
    """
    proto = pbutil.FromFile(path, cls.proto_t())
    return cls.FromProto(proto)


# The results of an offset-limit batched query. The batch num is the current
# batch number. The offset is the offset into the results set, the limit is the
# last row in the results set, max_rows is the total number of rows in the query
# (only set if compute_max_rows, else None), and rows it the results.
OffsetLimitQueryResultsBatch = collections.namedtuple(
    'QueryResultsBatch', ['batch_num', 'offset', 'limit', 'max_rows', 'rows'])


def OffsetLimitBatchedQuery(query: Query,
                            batch_size: int = 1000,
                            start_at: int = 0,
                            compute_max_rows: bool = False
                           ) -> typing.Iterator[OffsetLimitQueryResultsBatch]:
  """Split and return the rows resulting from a query in to batches.

  This iteratively runs the query `SELECT * FROM * OFFSET i LIMIT batch_size;`
  with `i` initialized to `start_at` and increasing by `batch_size` per
  iteration. Iteration terminates when the query returns no rows.

  This function is useful for returning row sets from enormous tables, where
  loading the full query results in to memory would take prohibitive time or
  resources.

  Args:
    query: The query to run.
    batch_size: The number of rows to return per batch.
    start_at: The initial offset into the table.
    compute_max_rows: If true

  Returns:
    A generator of OffsetLimitQueryResultsBatch tuples, where each tuple
    contains between 1 <= x <= `batch_size` rows.
  """
  max_rows = None
  if compute_max_rows:
    max_rows = query.count()

  batch_num = 0
  i = start_at
  while True:
    batch_num += 1
    batch = query.offset(i).limit(batch_size).all()
    if batch:
      yield OffsetLimitQueryResultsBatch(
          batch_num=batch_num,
          offset=i,
          limit=i + batch_size,
          max_rows=max_rows,
          rows=batch)
      i += len(batch)
    else:
      break


class ColumnTypes(object):
  """Abstract class containing methods for generating column types."""

  def __init__(self):
    raise TypeError('abstract class')

  @staticmethod
  def BinaryArray(length: int):
    """Return a fixed size binary array column type.

    Args:
      length: The length of the column.

    Returns:
      A column type.
    """
    return sql.Binary(length).with_variant(mysql.BINARY(length), 'mysql')

  @staticmethod
  def UnboundedUnicodeText():
    """Return an unbounded unicode text column type.

    This isn't truly unbounded, but 2^32 chars should be enough!

    Returns:
      A column type.
    """
    return sql.UnicodeText().with_variant(sql.UnicodeText(2**31), 'mysql')

  @staticmethod
  def MillisecondDatetime():
    """Return a datetime type with millisecond precision.

    Returns:
      A column type.
    """
    return sql.DateTime().with_variant(mysql.DATETIME(fsp=3), 'mysql')


class ColumnFactory(object):
  """Abstract class containing methods for generating columns."""

  @staticmethod
  def MillisecondDatetime(nullable: bool = False,
                          default=labdate.GetUtcMillisecondsNow):
    """Return a datetime column with millisecond precision.

    Returns:
      A column which defaults to UTC now.
    """
    return sql.Column(
        sql.DateTime().with_variant(mysql.DATETIME(fsp=3), 'mysql'),
        nullable=nullable,
        default=default)