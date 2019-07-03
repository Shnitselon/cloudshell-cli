import re
import time
from abc import ABCMeta, abstractmethod

from cloudshell.cli.service.action_map import ActionMap
from cloudshell.cli.service.error_map import ErrorMap
from cloudshell.cli.service.action_map import ActionLoopDetector
from cloudshell.cli.session.helper.normalize_buffer import normalize_buffer
from cloudshell.cli.session.session import Session
from cloudshell.cli.session.session_exceptions import SessionLoopLimitException, \
    ExpectedSessionException, CommandExecutionException, SessionReadTimeout, SessionReadEmptyData


class ExpectSession(Session, metaclass=ABCMeta):
    """Help to handle additional actions during send command"""

    SESSION_TYPE = 'EXPECT'
    MAX_LOOP_RETRIES = 20
    READ_TIMEOUT = 30
    EMPTY_LOOP_TIMEOUT = 0.5
    CLEAR_BUFFER_TIMEOUT = 0.1
    LOOP_DETECTOR_MAX_ACTION_LOOPS = 3
    LOOP_DETECTOR_MAX_COMBINATION_LENGTH = 4
    RECONNECT_TIMEOUT = 30

    def __init__(self, timeout=READ_TIMEOUT, new_line='\r', max_loop_retries=MAX_LOOP_RETRIES,
                 empty_loop_timeout=EMPTY_LOOP_TIMEOUT, loop_detector_max_action_loops=LOOP_DETECTOR_MAX_ACTION_LOOPS,
                 loop_detector_max_combination_length=LOOP_DETECTOR_MAX_COMBINATION_LENGTH,
                 clear_buffer_timeout=CLEAR_BUFFER_TIMEOUT, reconnect_timeout=RECONNECT_TIMEOUT):
        """

        :param int timeout:
        :param str new_line:
        :param int max_loop_retries:
        :param float empty_loop_timeout:
        :param int loop_detector_max_action_loops:
        :param int loop_detector_max_combination_length:
        :param float clear_buffer_timeout:
        :return:
        """
        self._new_line = new_line
        self._timeout = timeout
        self._max_loop_retries = max_loop_retries
        self._empty_loop_timeout = empty_loop_timeout

        self._loop_detector_max_action_loops = loop_detector_max_action_loops
        self._loop_detector_max_combination_length = loop_detector_max_combination_length
        self._clear_buffer_timeout = clear_buffer_timeout
        self._reconnect_timeout = reconnect_timeout

        self._active = False
        self._command_patterns = {}

    @property
    def session_type(self):
        return self.SESSION_TYPE

    @abstractmethod
    def _connect_actions(self, prompt, logger):
        """Read out buffer and run on_session_start actions

        :param str prompt: expected string in output
        :param logging.Logger logger: logger
        """
        pass

    @abstractmethod
    def _initialize_session(self, prompt, logger):
        """Create handler and initialize session

        :param str prompt: expected string in output
        :param logging.Logger logger: logger
        """
        pass

    def set_active(self, state):
        """

        :param bool state:
        :return:
        """
        self._active = state

    def active(self):
        """

        :rtype: bool
        """
        return self._active

    def _clear_buffer(self, timeout, logger):
        """Clear buffer

        :param int|float timeout:
        :param logging.Logger logger:
        :rtype: str
        """
        out = ''
        while True:
            try:
                read_buffer = self._receive(timeout, logger)
            except (SessionReadTimeout, SessionReadEmptyData):
                read_buffer = None
            if read_buffer:
                out += read_buffer
            else:
                break
        return out

    def connect(self, prompt, logger):
        """Connect to device

        :param str prompt: expected string in output
        :param logging.Logger logger: logger
        """
        try:
            self._initialize_session(prompt, logger)
            self._connect_actions(prompt, logger)
            self.set_active(True)
        except:
            self.disconnect()
            raise

    def send_line(self, command, logger):
        """Add new line to the end of command string and send

        :param str command:
        :param logging.Logger logger:
        :return:
        """
        self._send(command + self._new_line, logger)

    def _receive_all(self, timeout, logger):
        """Read as much as possible before catch SessionTimeoutException

        :param int timeout:
        :param logging.Logger logger:
        :rtype: str
        """
        timeout = timeout or self._timeout
        start_time = time.time()
        read_buffer = ''

        while True:
            try:
                read_buffer += self._receive(0.1, logger)
            except (SessionReadTimeout, SessionReadEmptyData):
                if read_buffer:
                    return read_buffer
                elif time.time() - start_time > timeout:
                    raise ExpectedSessionException('Socket closed by timeout')

    def _generate_command_pattern(self, command):
        """Generate command_pattern

        :param str command:
        :return:
        """
        if command not in self._command_patterns:
            self._command_patterns[command] = '\s*' + re.sub(r'\\\s+', '\s+', re.escape(command)) + '\s*'

        return self._command_patterns[command]

    def probe_for_prompt(self, expected_string, logger):
        """Matched string for regexp

        :param str expected_string:
        :param logging.Logger logger:
        :return:
        """
        return self.hardware_expect('', expected_string, logger)

    def match_prompt(self, prompt, match_string, logger):
        """Main verification for the prompt match

        :param str prompt: expected string, string or regular expression
        :param str match_string: Match string
        :param logging.Logger logger:
        :rtype: bool
        """
        return bool(re.search(prompt, match_string, re.DOTALL))

    def hardware_expect(self, command, expected_string, logger, action_map=None, error_map=None,
                        timeout=None, retries=None, check_action_loop_detector=True, empty_loop_timeout=None,
                        remove_command_from_output=True, **optional_args):
        """Get response form the device and compare it to action_map, error_map and expected_string patterns,

        perform actions specified in action_map if any, and return output.
        Raise Exception if receive empty response from device within a minute
        :param str command: command to send
        :param str expected_string: expected string
        :param logging.Logger logger: logger
        :param cloudshell.cli.service.action_map.ActionMap action_map:
        :param cloudshell.cli.service.error_map.ErrorMap error_map:
        :param int timeout: session timeout
        :param int retries: maximal retries count
        :param bool check_action_loop_detector:
        :param bool empty_loop_timeout:
        :param bool remove_command_from_output: In some switches the output string includes the command which was
            called. The flag used to verify whether the the command string removed from the output string.
        :rtype: str
        """
        action_map = action_map or ActionMap()
        error_map = error_map or  ErrorMap()
        retries = retries or self._max_loop_retries
        empty_loop_timeout = empty_loop_timeout or self._empty_loop_timeout

        if command is not None:
            self._clear_buffer(self._clear_buffer_timeout, logger)
            logger.debug(f'Command: {command}')
            self.send_line(command, logger)

        if not expected_string:
            raise ExpectedSessionException('List of expected messages can\'t be empty!')

        # Loop until one of the expressions is matched or MAX_RETRIES
        # nothing is expected (usually used for exit)
        output_list = list()
        output_str = ''
        retries_count = 0
        is_correct_exit = False

        action_loop_detector = ActionLoopDetector(self._loop_detector_max_action_loops,
                                                  self._loop_detector_max_combination_length)

        while retries == 0 or retries_count < retries:

            # try:
            # read_buffer = self._receive(timeout, logger)
            # read all data from buffer
            read_buffer = self._receive_all(timeout, logger)
            # except socket.timeout:
            #     read_buffer = None

            if read_buffer:
                read_buffer = normalize_buffer(read_buffer)
                logger.debug(read_buffer)
                output_str += read_buffer
                # if option remove_command_from_output is set to True, look for command in output buffer,
                #  remove it in case of found
                if command and remove_command_from_output:
                    command_pattern = self._generate_command_pattern(command)
                    if re.search(command_pattern, output_str, flags=re.MULTILINE):
                        output_str = re.sub(command_pattern, '', output_str, count=1, flags=re.MULTILINE)
                        remove_command_from_output = False
                retries_count = 0
            else:
                retries_count += 1
                time.sleep(empty_loop_timeout)
                continue

            if self.match_prompt(expected_string, output_str, logger):
                # logger.debug('Expected str: {}'.format(expected_string))
                output_list.append(output_str)
                is_correct_exit = True

            action_matched = action_map.process(session=self,
                                                logger=logger,
                                                output=output_str,
                                                check_action_loop_detector=check_action_loop_detector,
                                                action_loop_detector=action_loop_detector)

            if action_matched:
                output_list.append(output_str)
                output_str = ''

            if is_correct_exit:
                break

        if not is_correct_exit:
            raise SessionLoopLimitException(f'Session Loop limit exceeded, {retries_count} loops')

        result_output = ''.join(output_list)
        error_map.process(output=result_output, logger=logger)

        # Read buffer to the end. Useful when expected_string isn't last in buffer
        result_output += self._clear_buffer(self._clear_buffer_timeout, logger)
        return result_output

    def reconnect(self, prompt, logger, timeout=None):
        """Reconnect implementation

        :param str prompt:
        :param logging.Logger logger:
        :param int timeout:
        :return:
        """
        logger.debug('Reconnect')
        timeout = timeout or self._reconnect_timeout
        call_time = time.time()

        while time.time() - call_time < timeout:
            try:
                self.disconnect()
                return self.connect(prompt, logger)
            except Exception:
                logger.debug('Failed to reconnect:', exc_info=True)

        raise ExpectedSessionException('Reconnect unsuccessful, timeout exceeded, see logs for more details')
