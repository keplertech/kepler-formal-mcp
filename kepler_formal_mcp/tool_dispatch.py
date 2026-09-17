"""Keep blocking verification and process I/O off the MCP event loop."""

from functools import partial, wraps
from inspect import signature

import anyio


def threaded_tool(app):
    def register(function):
        @wraps(function)
        async def dispatch(**arguments):
            return await anyio.to_thread.run_sync(partial(function, **arguments))

        # Resolve annotations in the original module before handing this wrapper
        # to FastMCP, so Literal choices and defaults remain in the tool schema.
        dispatch.__signature__ = signature(function, eval_str=True)
        app.tool()(dispatch)
        return function

    return register
