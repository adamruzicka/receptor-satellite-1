import asyncio

from . import playbook_verifier_adapter

from .config import Config
from .host import Host
from .run_monitor import run_monitor
from .response.response_queue import constants


class Run:
    def __init__(
        self,
        queue,
        remediation_id,
        playbook_run_id,
        account,
        hosts,
        playbook,
        config,
        satellite_api,
        logger,
    ):
        self.queue = queue
        self.remedation_id = remediation_id
        self.playbook_run_id = playbook_run_id
        self.account = account
        self.playbook = playbook
        self.config = Config.from_raw(Config.validate_input(config, logger))

        unsafe_hostnames = [name for name in hosts if "," in name]
        for name in unsafe_hostnames:
            logger.warning(f"Hostname '{name}' contains a comma, skipping")
            Host(self, None, name).mark_as_failed("Hostname contains a comma, skipping")

        self.hosts = [
            Host(self, None, name) for name in hosts if name not in unsafe_hostnames
        ]
        self.satellite_api = satellite_api
        self.logger = logger
        self.job_invocation_id = None
        self.cancelled = False
        self.running = {}
        self.since = None if self.config.text_update_full else 0.0

    @classmethod
    def from_raw(cls, queue, raw, satellite_api, logger):
        return cls(
            queue,
            raw["remediation_id"],
            raw["playbook_run_id"],
            raw["account"],
            raw["hosts"],
            raw["playbook"],
            raw["config"],
            satellite_api,
            logger,
        )

    async def run(self):
        if not await run_monitor.register(self):
            self.logger.error(
                f"Playbook run {self.playbook_run_id} already known, skipping."
            )
            return

        await self.satellite_api.init_session()
        try:
            self.queue.ack(self.playbook_run_id)
            self.playbook = playbook_verifier_adapter.verify(self.playbook)
            response = await self.satellite_api.trigger(
                {"playbook": self.playbook}, [host.name for host in self.hosts]
            )
            if response["error"]:
                self.abort(response["error"])
            else:
                self.job_invocation_id = response["body"]["id"]
                self.logger.info(
                    f"Playbook run {self.playbook_run_id} running as job invocation {self.job_invocation_id}"
                )
                self.update_hosts(response["body"]["targeting"]["hosts"])
                if await self.polling_loop():
                    await self.finish()
                self.logger.info(f"Playbook run {self.playbook_run_id} done")

        except playbook_verifier_adapter.PlaybookValidationError as err:
            self.abort(
                f"Playbook failed signature validation: {err}",
                error_key="validation_error",
            )
        finally:
            await run_monitor.done(self)
            await self.satellite_api.close_session()

    async def polling_loop(self):
        while any(self.running):
            response = await self.poll_with_retries()
            if response.get("status") == 404:
                await asyncio.gather(*[host.polling_loop() for host in self.hosts])
                break
            if response["error"]:
                return
            else:
                for host_output in response["body"]["outputs"]:
                    host = self.running[host_output["host_id"]]
                    host.process_outputs(host_output)
                    if self.since is not None and host.since > self.since:
                        self.since = host.since
                    if host_output["complete"]:
                        host.done()
                        self.running.pop(host.id)
        return True

    async def poll_with_retries(self):
        retry = 0
        while retry < 5:
            await asyncio.sleep(self.config.text_update_interval)
            names = [host.name for host in self.running.values()]
            response = await self.satellite_api.outputs(
                self.job_invocation_id, names, self.since
            )
            if response["error"] is None or response.get("status") == 404:
                return response
            retry += 1
        self.abort(response["error"], running=True)
        return dict(error=True)

    async def finish(self):
        result = constants.RESULT_FAILURE
        infrastructure_error = None
        if any(host.unreachable for host in self.hosts):
            infrastructure_error = "Infrastructure error"

        if self.cancelled:
            result = constants.RESULT_CANCEL
        elif all(host.result == constants.HOST_RESULT_SUCCESS for host in self.hosts):
            result = constants.RESULT_SUCCESS

        self.queue.playbook_run_completed(
            self.playbook_run_id,
            result,
            infrastructure_error=infrastructure_error,
        )

    def update_hosts(self, hosts):
        host_map = {host.name: host for host in self.hosts}
        for host in hosts:
            host_map[host["name"]].id = host["id"]

        for host in self.hosts:
            if host.id is None:
                host.mark_as_failed("This host is not known by Satellite", None)
            else:
                self.running[host.id] = host

    def abort(self, error, running=False, error_key="connection_error"):
        error = str(error)
        self.logger.error(
            f"Playbook run {self.playbook_run_id} encountered error '{error}', aborting."
        )
        hosts = [host for id, host in self.running.items()] if running else self.hosts
        for host in hosts:
            host.mark_as_failed(error, None)
        result = {}
        result[error_key] = error
        self.queue.playbook_run_completed(
            self.playbook_run_id, constants.RESULT_FAILURE, **result
        )
