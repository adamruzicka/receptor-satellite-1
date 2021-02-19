import pytest
import os

from test_helper import base_scenario  # noqa: F401
from receptor_satellite.run_monitor import run_monitor  # noqa: E402
from receptor_satellite.run import Run  # noqa: E402
from receptor_satellite.response.response_queue import ResponseQueue  # noqa: E402
import receptor_satellite.response.constants as constants  # noqa: E402
import receptor_satellite.response.messages as messages  # noqa: E402
from fake_logger import FakeLogger  # noqa: E402
from fake_queue import FakeQueue  # noqa: E402


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


RUN_TEST_CASES = [
    # (api_responses, expected_api_requests, expected_queue_messages, expected_logger_messages, playbook_valid)
    (
        [{"error": "Something broke"}],
        [("trigger", ({"playbook": "playbook"}, ["host1"]))],
        [
            messages.ack("play_id"),
            messages.playbook_run_update("host1", "play_id", "Something broke", 0),
            messages.playbook_run_finished(
                "host1", "play_id", constants.RESULT_FAILURE, None
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
        .error("Playbook run play_id encountered error 'Something broke', aborting.")
        .messages,
        True,
    ),
    (
        [
            dict(
                body={"id": 123, "targeting": {"hosts": [{"name": "host1", "id": 5}]}},
                error=None,
            ),
            dict(
                body={
                    "outputs": [
                        {
                            "host_id": 5,
                            "output": [{"output": "Exit status: 0"}],
                            "complete": True,
                        }
                    ]
                },
                error=None,
            ),
        ],
        [
            ("trigger", ({"playbook": "playbook"}, ["host1"])),
            ("outputs", (123, ["host1"], None)),
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
        True,
    ),
    (
        [
            dict(
                body={"id": 123, "targeting": {"hosts": [{"name": "host1", "id": 5}]}},
                error=None,
            ),
            dict(
                body={
                    "outputs": [
                        {
                            "host_id": 5,
                            "output": [
                                {
                                    "output": "The only applicable capsule something.somewhere.com is down"
                                }
                            ],
                            "complete": True,
                        }
                    ]
                },
                error=None,
            ),
        ],
        [
            ("trigger", ({"playbook": "playbook"}, ["host1"])),
            ("outputs", (123, ["host1"], None)),
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
                "host1", "play_id", constants.HOST_RESULT_FAILURE, None
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
        True,
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
                    "outputs": [
                        {
                            "host_id": 5,
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
                        }
                    ],
                },
            ),
        ],
        [
            ("trigger", ({"playbook": "playbook"}, ["host1"])),
            ("outputs", (123, ["host1"], None)),
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
                "host1", "play_id", constants.HOST_RESULT_FAILURE, False
            ),
            messages.playbook_run_completed("play_id", constants.RESULT_FAILURE),
        ],
        FakeLogger()
        .info("Playbook run play_id running as job invocation 123")
        .info("Playbook run play_id done")
        .messages,
        True,
    ),
    # TODO: Uncomment once playbook signature validation is functional again
    # (
    #     [],
    #     [],
    #     [
    #         messages.ack("play_id"),
    #         messages.playbook_run_update(
    #             "host1",
    #             "play_id",
    #             "Playbook failed signature validation: PLAYBOOK VALIDATION FAILED",
    #             0,
    #         ),
    #         messages.playbook_run_finished(
    #             "host1", "play_id", constants.RESULT_FAILURE, connection_result=None
    #         ),
    #         messages.playbook_run_completed(
    #             "play_id",
    #             constants.RESULT_FAILURE,
    #             validation_code=1,
    #             validation_error="Playbook failed signature validation: PLAYBOOK VALIDATION FAILED",
    #             connection_code=None,
    #             infrastructure_code=None,
    #         ),
    #     ],
    #     FakeLogger()
    #     .error(
    #         "Playbook run play_id encountered error 'Playbook failed signature validation: PLAYBOOK VALIDATION FAILED', aborting."
    #     )
    #     .messages,
    #     False,
    # ),
    (
        [
            dict(
                body={"id": 123, "targeting": {"hosts": [{"name": "host1", "id": 5}]}},
                error=None,
            ),
            dict(error="Something happened."),
            dict(error="Something happened."),
            dict(error="Something happened."),
            dict(error="Something happened."),
            dict(error="Something happened."),
        ],
        [
            ("trigger", ({"playbook": "playbook"}, ["host1"])),
            ("outputs", (123, ["host1"], None)),
            ("outputs", (123, ["host1"], None)),
            ("outputs", (123, ["host1"], None)),
            ("outputs", (123, ["host1"], None)),
            ("outputs", (123, ["host1"], None)),
        ],
        [
            messages.ack("play_id"),
            messages.playbook_run_update("host1", "play_id", "Something happened.", 0),
            messages.playbook_run_finished(
                "host1", "play_id", constants.RESULT_FAILURE, None
            ),
            messages.playbook_run_completed(
                "play_id",
                constants.RESULT_FAILURE,
                connection_error="Something happened.",
                connection_code=1,
                infrastructure_error=None,
                infrastructure_code=None,
            ),
        ],
        FakeLogger()
        .info("Playbook run play_id running as job invocation 123")
        .error(
            "Playbook run play_id encountered error 'Something happened.', aborting."
        )
        .info("Playbook run play_id done")
        .messages,
        True,
    ),
    (
        [
            dict(
                body={"id": 123, "targeting": {"hosts": [{"name": "host1", "id": 5}]}},
                error=None,
            ),
            dict(status=404, error="Not found"),
            dict(
                body={
                    "output": [{"output": "Exit status: 0"}],
                    "complete": True,
                },
                error=None,
            ),
        ],
        [
            ("trigger", ({"playbook": "playbook"}, ["host1"])),
            ("outputs", (123, ["host1"], None)),
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
        True,
    ),
]


@pytest.fixture(params=RUN_TEST_CASES)
def run_scenario(request, base_scenario):  # noqa: F811
    # host_id, output_value, result, api_requests, queue_messages = request.param

    yield (base_scenario, request.param)


@pytest.mark.asyncio
async def test_run(run_scenario):
    run_monitor._RunMonitor__runs = {}
    base, case = run_scenario
    queue, logger, satellite_api, run = base
    (
        api_responses,
        expected_api_requests,
        expected_queue_messages,
        expected_logger_messages,
        playbook_signature_valid,
    ) = case
    satellite_api.responses = api_responses
    ansible_env_key = "ANSIBLE_PLAYBOOK_VERIFIER_THROW_ERROR"
    old = os.getenv(ansible_env_key)
    if not playbook_signature_valid:
        os.environ[ansible_env_key] = "1"
    await run.run()
    if old:
        os.environ[ansible_env_key] = old
    else:
        os.environ[ansible_env_key] = ""
    print(satellite_api.requests)
    print(logger.messages)
    print(queue.messages)
    assert satellite_api.requests == expected_api_requests
    assert logger.messages == expected_logger_messages
    assert queue.messages == expected_queue_messages
