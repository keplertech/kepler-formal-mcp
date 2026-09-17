"""Keep MCP transport responsive while synchronous workers are busy."""

import asyncio
import subprocess
import threading
import unittest
from unittest.mock import patch

from kepler_formal_mcp import runner, server


class ToolDispatchTest(unittest.TestCase):
    def test_info_worker_does_not_inherit_the_mcp_input_pipe(self):
        with patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="test worker stopped"
        )) as launch:
            runner.get_info()
        self.assertEqual(launch.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_mcp_event_loop_keeps_running_while_a_tool_waits(self):
        started, release = threading.Event(), threading.Event()

        def blocking_info():
            started.set()
            if not release.wait(3):
                raise RuntimeError("MCP event loop did not unblock the worker")
            return {"status": "success"}

        async def exercise():
            task = asyncio.create_task(server.app.call_tool("get_kepler_formal_info", {}))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                self.assertFalse(task.done())
                release.set()
                await task
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)

        with patch.object(server.session_tools.manager, "_active", None), \
             patch.object(runner, "get_info", side_effect=blocking_info):
            asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
