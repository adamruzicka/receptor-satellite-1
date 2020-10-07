import asyncio
import pytest


async def _sleep_override(interval):
    pass


asyncio.sleep = _sleep_override
from receptor_satellite.run_monitor import run_monitor  # noqa: E402
from receptor_satellite.host import Host  # noqa: E402
from receptor_satellite.run import Run  # noqa: E402
from receptor_satellite.response.response_queue import ResponseQueue  # noqa: E402
import receptor_satellite.response.constants as constants  # noqa: E402
import receptor_satellite.response.messages as messages  # noqa: E402
from fake_logger import FakeLogger  # noqa: E402
from fake_queue import FakeQueue  # noqa: E402


class FakeSatelliteAPI:
    def __init__(self, responses=[]):
        self.requests = []
        self.responses = []

    def record_request(self, request_type, data):
        self.requests.append((request_type, data))

    async def output(self, job_id, host_id, since):
        print(f"{(job_id, host_id, since)}")
        self.record_request("output", (job_id, host_id, since))
        return self.__pop_responses()

    async def trigger(self, inputs, hosts):
        self.record_request("trigger", (inputs, hosts))
        return self.__pop_responses()

    async def init_session(self):
        pass

    async def close_session(self):
        pass

    def __pop_responses(self):
        [response, *rest] = self.responses
        self.responses = rest
        return response


@pytest.fixture
def base_scenario(request):
    queue = FakeQueue()
    logger = FakeLogger()
    satellite_api = FakeSatelliteAPI()
    run = Run(
        ResponseQueue(queue),
        "rem_id",
        "play_id",
        "account_no",
        ["host1"],
        "playbook",
        {},
        satellite_api,
        logger,
    )
    yield (queue, logger, satellite_api, run)


def test_mark_as_failed(base_scenario):
    queue, logger, satellite_api, run = base_scenario
    host = Host(run, None, "host1")
    host.mark_as_failed("controlled failure")

    assert queue.messages == [
        messages.playbook_run_update(host.name, "play_id", "controlled failure", 0),
        messages.playbook_run_finished(host.name, "play_id", constants.RESULT_FAILURE),
    ]


class PollWithRetriesTestCase:
    def __init__(
        self,
        host_id=1,
        api_output=None,
        result=None,
        api_requests=[],
        queue_messages=[],
    ):
        self.host_id = host_id
        self.api_output = api_output
        self.result = result
        self.api_requests = api_requests
        self.queue_messages = queue_messages


POLL_WITH_RETRIES_TEST_CASES = [
    # Polling loop does not loop if there is no error when talking to
    # the API
    PollWithRetriesTestCase(
        result={"error": None, "key": "value"},
        api_output={"error": None, "key": "value"},
        api_requests=[("output", (None, 1, None))],
    ),
    PollWithRetriesTestCase(
        result={"error": True},
        api_output={"error": "controlled failure"},
        api_requests=[("output", (None, 1, None)) for _x in range(5)],
        queue_messages=[
            messages.playbook_run_update("host1", "play_id", "controlled failure", 0),
            messages.playbook_run_finished(
                "host1", "play_id", constants.RESULT_FAILURE
            ),
        ],
    ),
]


@pytest.fixture(params=POLL_WITH_RETRIES_TEST_CASES)
def poll_with_retries_scenario(request, base_scenario):
    # host_id, output_value, result, api_requests, queue_messages = request.param
    param = request.param
    queue, logger, satellite_api, run = base_scenario
    host = Host(run, param.host_id, "host1")

    yield (queue, host, param)


@pytest.mark.asyncio
async def test_poll_with_retries(poll_with_retries_scenario):
    (
        queue,
        host,
        param,
    ) = poll_with_retries_scenario
    satellite_api = host.run.satellite_api
    satellite_api.responses = [
        param.api_output for _x in range(len(param.api_requests))
    ]

    result = await host.poll_with_retries()

    assert result == param.result
    assert satellite_api.requests == param.api_requests
    assert queue.messages == param.queue_messages


class PollingLoopTestCase(PollWithRetriesTestCase):
    def __init__(self, cancelled=False, **kwargs):
        super().__init__(**kwargs)
        self.cancelled = cancelled


POLLING_LOOP_TEST_CASES = [
    # If the host doesn't have an ID, it is assumed to be not known by
    # Satellite and is marked as failed
    PollingLoopTestCase(
        host_id=None,
        queue_messages=[
            messages.playbook_run_update(
                "host1", "play_id", "This host is not known by Satellite", 0
            ),
            messages.playbook_run_finished(
                "host1", "play_id", constants.RESULT_FAILURE
            ),
        ],
    ),
    # If the polling loop receives an error from the API, it marks the
    # host as failed
    PollingLoopTestCase(
        api_output={"error": "controlled failure"},
        api_requests=[("output", (None, 1, None)) for _x in range(5)],
        queue_messages=[
            messages.playbook_run_update("host1", "play_id", "controlled failure", 0),
            messages.playbook_run_finished(
                "host1", "play_id", constants.RESULT_FAILURE
            ),
        ],
    ),
    # If the last output from the API ends with Exit status: 0, mark
    # the run on the host as success
    PollingLoopTestCase(
        api_output={
            "error": None,
            "body": {"complete": True, "output": [{"output": "Exit status: 0"}]},
        },
        api_requests=[("output", (None, 1, None))],
        queue_messages=[
            messages.playbook_run_update("host1", "play_id", "Exit status: 0", 0),
            messages.playbook_run_finished(
                "host1", "play_id", constants.RESULT_SUCCESS
            ),
        ],
    ),
    # If the run was cancelled, but the host managed to finish
    # successfully, mark it as success
    PollingLoopTestCase(
        cancelled=True,
        api_output={
            "error": None,
            "body": {"complete": True, "output": [{"output": "Exit status: 0"}]},
        },
        api_requests=[("output", (None, 1, None))],
        queue_messages=[
            messages.playbook_run_update("host1", "play_id", "Exit status: 0", 0),
            messages.playbook_run_finished(
                "host1", "play_id", constants.RESULT_SUCCESS
            ),
        ],
    ),
    # If the host failed, mark it as failed
    PollingLoopTestCase(
        api_output={
            "error": None,
            "body": {"complete": True, "output": [{"output": "Exit status: 123"}]},
        },
        api_requests=[("output", (None, 1, None))],
        queue_messages=[
            messages.playbook_run_update("host1", "play_id", "Exit status: 123", 0),
            messages.playbook_run_finished(
                "host1", "play_id", constants.RESULT_FAILURE, False, 123
            ),
        ],
    ),
    # If the run was cancelled and the run on the host failed, mark it
    # as cancelled
    PollingLoopTestCase(
        cancelled=True,
        api_output={
            "error": None,
            "body": {"complete": True, "output": [{"output": "Exit status: 123"}]},
        },
        api_requests=[("output", (None, 1, None))],
        queue_messages=[
            messages.playbook_run_update("host1", "play_id", "Exit status: 123", 0),
            messages.playbook_run_finished(
                "host1", "play_id", constants.RESULT_CANCEL, False, 123
            ),
        ],
    ),
]


@pytest.fixture(params=POLLING_LOOP_TEST_CASES)
def polling_loop_scenario(request, base_scenario):
    queue, logger, satellite_api, run = base_scenario
    run.cancelled = request.param.cancelled
    host = Host(run, request.param.host_id, "host1")

    yield (queue, host, request.param)


@pytest.mark.asyncio
async def test_polling_loop(polling_loop_scenario):
    (
        queue,
        host,
        param,
    ) = polling_loop_scenario
    satellite_api = host.run.satellite_api
    satellite_api.responses = [
        param.api_output for _x in range(len(param.api_requests))
    ]

    result = await host.polling_loop()
    assert result == param.result
    assert satellite_api.requests == param.api_requests
    assert queue.messages == param.queue_messages


def test_hostname_sanity():
    hosts = ["good", "fine", "not,really,good", "ok"]
    logger = FakeLogger()
    fake_queue = FakeQueue()
    playbook_id = "play_id"

    run = Run(
        ResponseQueue(fake_queue),
        "rem_id",
        playbook_id,
        "acc_num",
        hosts,
        "playbook",
        {},
        None,  # No need for SatelliteAPI in this test
        logger,
    )
    assert logger.warnings() == [
        "Hostname 'not,really,good' contains a comma, skipping"
    ]
    assert fake_queue.messages == [
        messages.playbook_run_update(
            "not,really,good", "play_id", "Hostname contains a comma, skipping", 0
        ),
        messages.playbook_run_finished(
            "not,really,good", "play_id", constants.RESULT_FAILURE
        ),
    ]
    assert list(map(lambda h: h.name, run.hosts)) == ["good", "fine", "ok"]


START_TEST_CASES = [
    # (api_responses, expected_api_requests, expected_queue_messages, expected_logger_messages)
    (
        [{"error": "Something broke"}],
        [("trigger", ({"playbook": "playbook"}, ["host1"]))],
        [
            messages.ack("play_id"),
            messages.playbook_run_update("host1", "play_id", "Something broke", 0),
            messages.playbook_run_finished(
                "host1", "play_id", constants.RESULT_FAILURE
            ),
            messages.playbook_run_completed(
                "play_id",
                constants.RESULT_FAILURE,
                connection_code=1,
                connection_error="Something broke",
                infrastructure_code=None,
            ),
        ],
        FakeLogger()
        .error("Playbook run play_id encountered error `Something broke`, aborting.")
        .info("Playbook run play_id done")
        .messages,
    ),
    (
        [
            dict(
                body={"id": 123, "targeting": {"hosts": [{"name": "host1", "id": 5}]}},
                error=None,
            ),
            dict(
                body={"output": [{"output": "Exit status: 0"}], "complete": True},
                error=None,
            ),
        ],
        [
            ("trigger", ({"playbook": "playbook"}, ["host1"])),
            ("output", (123, 5, None)),
        ],
        [
            messages.ack("play_id"),
            messages.playbook_run_update("host1", "play_id", "Exit status: 0", 0),
            messages.playbook_run_finished(
                "host1", "play_id", constants.RESULT_SUCCESS
            ),
            messages.playbook_run_completed(
                "play_id",
                constants.RESULT_SUCCESS,
            ),
        ],
        FakeLogger()
        .info("Playbook run play_id running as job invocation 123")
        .info("Playbook run play_id done")
        .messages,
    ),
    (
        [
            dict(
                body={"id": 123, "targeting": {"hosts": [{"name": "host1", "id": 5}]}},
                error=None,
            ),
            dict(
                body={
                    "output": [
                        {
                            "output": "The only applicable capsule something.somewhere.com is down"
                        }
                    ],
                    "complete": True,
                },
                error=None,
            ),
        ],
        [
            ("trigger", ({"playbook": "playbook"}, ["host1"])),
            ("output", (123, 5, None)),
        ],
        [
            messages.ack("play_id"),
            messages.playbook_run_update(
                "host1",
                "play_id",
                "The only applicable capsule something.somewhere.com is down",
                0,
            ),
            messages.playbook_run_finished(
                "host1", "play_id", constants.HOST_RESULT_FAILURE, True
            ),
            messages.playbook_run_completed(
                "play_id",
                constants.RESULT_FAILURE,
                infrastructure_error="Infrastructure error",
                infrastructure_code=1,
            ),
        ],
        FakeLogger()
        .info("Playbook run play_id running as job invocation 123")
        .info("Playbook run play_id done")
        .messages,
    ),
    (
        [
            dict(
                body={"id": 123, "targeting": {"hosts": [{"name": "host1", "id": 5}]}},
                error=None,
            ),
            dict(
                error=None,
                body={
                    "complete": True,
                    "output": [
                        {
                            "output": "\u001b[0;34mUsing /etc/ansible/ansible.cfg as config file\u001b[0m\n",
                            "output_type": "stdout",
                            "timestamp": 1600350676.69755,
                        },
                        {
                            "output": "\n",
                            "output_type": "stdout",
                            "timestamp": 1600350677.70155,
                        },
                        {
                            "output": "\r\nPLAY [all] *********************************************************************\n",
                            "output_type": "stdout",
                            "timestamp": 1600350677.70175,
                        },
                        {
                            "output": "\r\nTASK [Gathering Facts] *********************************************************\n",
                            "output_type": "stdout",
                            "timestamp": 1600350677.70195,
                        },
                        {
                            "output": "\n",
                            "output_type": "stdout",
                            "timestamp": 1600350677.70212,
                        },
                        {
                            "output": '\u001b[1;31mfatal: [host1]: UNREACHABLE! => {"changed": false, "msg": "Invalid/incorrect password: Permission denied, please try again.\\r\\nPermission denied, please try again.\\r\\nReceived disconnect from 10.110.156.47 port 22:2: Too many authentication failures\\r\\nDisconnected from 10.110.156.47 port 22", "unreachable": true}\u001b[0m\n',
                            "output_type": "stdout",
                            "timestamp": 1600350684.0395,
                        },
                        {
                            "output": "PLAY RECAP *********************************************************************\n\u001b[0;31mhost1\u001b[0m                   : ok=0    changed=0    \u001b[1;31munreachable=1   \u001b[0m failed=0    skipped=0    rescued=0    ignored=0   ",
                            "output_type": "stdout",
                            "timestamp": 1600350687.1491,
                        },
                        {
                            "output": "Exit status: 1",
                            "output_type": "stdout",
                            "timestamp": 1600350688.1491,
                        },
                    ],
                    "refresh": False,
                },
            ),
        ],
        [
            ("trigger", ({"playbook": "playbook"}, ["host1"])),
            ("output", (123, 5, None)),
        ],
        [
            messages.ack("play_id"),
            messages.playbook_run_update(
                "host1",
                "play_id",
                '\x1b[0;34mUsing /etc/ansible/ansible.cfg as config file\x1b[0m\n\n\r\nPLAY [all] *********************************************************************\n\r\nTASK [Gathering Facts] *********************************************************\n\n\x1b[1;31mfatal: [host1]: UNREACHABLE! => {"changed": false, "msg": "Invalid/incorrect password: Permission denied, please try again.\\r\\nPermission denied, please try again.\\r\\nReceived disconnect from 10.110.156.47 port 22:2: Too many authentication failures\\r\\nDisconnected from 10.110.156.47 port 22", "unreachable": true}\x1b[0m\nPLAY RECAP *********************************************************************\n\x1b[0;31mhost1\x1b[0m                   : ok=0    changed=0    \x1b[1;31munreachable=1   \x1b[0m failed=0    skipped=0    rescued=0    ignored=0   Exit status: 1',
                0,
            ),
            messages.playbook_run_finished(
                "host1", "play_id", constants.HOST_RESULT_FAILURE, True
            ),
            messages.playbook_run_completed(
                "play_id",
                constants.RESULT_FAILURE,
            ),
        ],
        FakeLogger()
        .info("Playbook run play_id running as job invocation 123")
        .info("Playbook run play_id done")
        .messages,
    ),
]


@pytest.fixture(params=START_TEST_CASES)
def start_scenario(request, base_scenario):
    # host_id, output_value, result, api_requests, queue_messages = request.param

    yield (base_scenario, request.param)


@pytest.mark.asyncio
async def test_start(start_scenario):
    run_monitor._RunMonitor__runs = {}
    base, case = start_scenario
    queue, logger, satellite_api, run = base
    (
        api_responses,
        expected_api_requests,
        expected_queue_messages,
        expected_logger_messages,
    ) = case
    satellite_api.responses = api_responses
    await run.start()
    assert satellite_api.requests == expected_api_requests
    assert logger.messages == expected_logger_messages
    assert queue.messages == expected_queue_messages
