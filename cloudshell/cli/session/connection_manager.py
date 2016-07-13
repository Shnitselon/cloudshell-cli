import traceback
from weakref import WeakKeyDictionary
from Queue import Queue
from threading import Lock, currentThread

import cloudshell.configuration.cloudshell_cli_configuration as package_config
from cloudshell.shell.core.config_utils import get_config_attribute_or_none, call_if_callable
from cloudshell.configuration.cloudshell_shell_core_binding_keys import CONFIG, LOGGER
from cloudshell.configuration.cloudshell_cli_binding_keys import CONNECTION_MANAGER
import inject


class ConnectionManager(object):
    """Class implements Object Pool pattern for sessions, creates and pool sessions for specific types"""

    CREATE_SESSION_LOCK = Lock()
    SESSION_CONTAINER = WeakKeyDictionary()

    @inject.params(config=CONFIG, logger=LOGGER)
    def __init__(self, config, logger):
        if not config:
            raise Exception(self.__class__.__name__, 'Failed to read config for cloudshell-cli package')
        self._config = config
        self._connection_map = get_config_attribute_or_none('CONNECTION_MAP',
                                                            self._config) or package_config.CONNECTION_MAP

        self._max_connections = call_if_callable(get_config_attribute_or_none('SESSION_POOL_SIZE',
                                                                              self._config) or package_config.SESSION_POOL_SIZE)
        if not self._max_connections:
            self._max_connections = package_config.DEFAULT_SESSION_POOL_SIZE

        self._session_pool = Queue(maxsize=self._max_connections)
        self._existing_sessions = 0

        self._prompt = get_config_attribute_or_none('DEFAULT_PROMPT', self._config) or package_config.DEFAULT_PROMPT
        self._connection_type_auto = get_config_attribute_or_none('CONNECTION_TYPE_AUTO',
                                                                  self._config) or package_config.CONNECTION_TYPE_AUTO
        self._connection_type = get_config_attribute_or_none('CONNECTION_TYPE',
                                                             self._config) or package_config.CONNECTION_TYPE
        self._default_connection_type = get_config_attribute_or_none('DEFAULT_CONNECTION_TYPE',
                                                                     self._config) or package_config.DEFAULT_CONNECTION_TYPE

        self._pool_timeout = get_config_attribute_or_none('POOL_TIMEOUT', self._config) or package_config.POOL_TIMEOUT
        if logger:
            logger.debug('Connection manager created')

    @inject.params(logger=LOGGER)
    def _new_session(self, connection_type, logger=None):
        """Creates new session
        :param str connection_type:
        :rtype: Session
        :raises: Exception
        """
        if connection_type in self._connection_map:
            try:
                session_object = self._connection_map[connection_type].create_session()
                session_object.connect(re_string=self._prompt)
            except Exception as exception:
                logger.error('Cannot create session, Exception: {}'.format(exception.message))
                raise Exception(self.__class__.__name__, 'Failed to open connection, see logs for details')
            logger.debug('Created new session')
        else:
            err_msg = 'Unknown connection type \'{0}\''.format(connection_type)
            logger.error(err_msg)
            raise Exception('ConnectionManager', err_msg)

        return session_object

    def _create_session_by_connection_type(self, logger):
        """Creates session object for connection type
        :rtype: Session
        :raises: Exception
        """

        if self._connection_type and callable(self._connection_type):
            connection_type = self._connection_type()
        elif self._connection_type and isinstance(self._connection_type, str):
            connection_type = self._connection_type
        else:
            err_msg = 'Unknown connection type {0}'.format(self._connection_type)
            logger.error(err_msg)
            raise Exception('_create_session_by_connection_type', err_msg)

        if not connection_type:
            connection_type = self._default_connection_type

        connection_type = connection_type.lower()

        if not self._prompt or len(self._prompt) == 0:
            logger.warning('Provided Prompt for the session is empty!')

        logger.info('\n-------------------------------------------------------------')
        logger.info('Connection - {0}'.format(connection_type))

        session_object = None
        if connection_type != self._connection_type_auto:
            session_object = self._new_session(connection_type, logger=logger)
        else:
            for key in self._connection_map:
                logger.info('\n--------------------------------------')
                logger.info('Trying to open {0} connection ...'.format(key))
                try:
                    session_object = self._new_session(key, logger=logger)
                    if session_object:
                        break
                except Exception as error_object:
                    logger.error(traceback.format_exc())
                    logger.error('{0} connection failed with error msg: {1}'.format(key.upper(), error_object.message))

        if session_object is None:
            err_msg = 'Failed to open connection to device.'
            logger.error(err_msg)
            raise Exception('ConnectionManager', err_msg)

        return session_object

    @inject.params(logger=LOGGER)
    def _get_session_from_pool(self, logger=None):
        """Take session from pool

        :param logger:
        :rtype: Session
        :raises: Exception
        """

        logger.debug(
            'Session pool size: {0}, Sessions in the pool: {1}'.format(self._max_connections, self._existing_sessions))
        try:
            session_object = self._session_pool.get(True, self._pool_timeout)
            logger.info('Trying to get session from pool')
        except Exception as error_object:
            err_msg = "Failed to get available session from pull."
            logger.error(traceback.format_exc())
            logger.error(err_msg)
            raise Exception('ConnectionManager', err_msg)

        return session_object

    def return_session_to_pool(self, session, time_out=None):
        """Creates session object for connection type
        :param: Session session:
        """

        if not time_out:
            time_out = self._pool_timeout
        self._session_pool.put(session, True, time_out)

    def decrement_sessions_count(self):
        if self._existing_sessions > 0:
            self._existing_sessions -= 1

    def increment_sessions_count(self):
        self._existing_sessions += 1

    @inject.params(logger=LOGGER)
    def get_session_instance(self, logger):
        """Return session object, takes it from pool or create new session
        :rtype: Session
        """

        with ConnectionManager.CREATE_SESSION_LOCK:
            if self._session_pool.empty() and self._existing_sessions < int(self._max_connections):
                session = self._create_session_by_connection_type(logger)
                self.increment_sessions_count()
                self.return_session_to_pool(session)
        return self._get_session_from_pool()

    @staticmethod
    @inject.params(connection_manager=CONNECTION_MANAGER)
    def get_session(connection_manager):
        """Get session
        :rtype: Session
        """

        return connection_manager.get_session_instance()

    @staticmethod
    def get_thread_session():
        """Return same session for thread
        """

        if not currentThread() in ConnectionManager.SESSION_CONTAINER:
            ConnectionManager.SESSION_CONTAINER[currentThread()] = ConnectionManager.get_session()
        return ConnectionManager.SESSION_CONTAINER[currentThread()]

    @staticmethod
    def destroy_thread_session(session):
        """Destroy threaded session

        :param session:
        :return:
        """
        session.set_invalid()
        if currentThread() in ConnectionManager.SESSION_CONTAINER:
            del (ConnectionManager.SESSION_CONTAINER[currentThread()])