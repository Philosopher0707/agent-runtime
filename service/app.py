"""The HTTP service. Single port, two routes, config from the environment.

``POST /run`` returns the output schema. ``GET /healthz`` reports whether the model is
reachable and how many tools loaded.

Errors are typed on purpose:

* a bad configuration name or an unknown tool is the *caller's* mistake → 400
* anything else is *our* bug → 500, and it is not disguised as a run status

The loop deliberately does not swallow unexpected exceptions. A runtime whose whole
value is a disciplined failure taxonomy should not report a bug as one more row of the
taxonomy.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from runtime.config import (
    DEFAULT_CONFIG_DIR,
    ConfigError,
    available_configs,
    load_config_by_name,
    load_env_file,
)
from runtime.factory import (
    DEFAULT_NOTES_ROOT,
    DEFAULT_TRACE_DIR,
    build_provider,
    build_tools,
    run_task,
)
from runtime.schemas import HealthResponse, RunOutput, RunRequest
from tools.catalogue import UnknownToolError

VERSION = "0.1.0"


def create_app(
    *,
    config_root: str | Path = DEFAULT_CONFIG_DIR,
    trace_dir: str | Path = DEFAULT_TRACE_DIR,
    notes_root: str | Path = DEFAULT_NOTES_ROOT,
    clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    app = FastAPI(title="agent-runtime", version=VERSION)
    started = clock()

    @app.exception_handler(ConfigError)
    async def _config_error(_request: object, exc: ConfigError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(UnknownToolError)
    async def _unknown_tool(_request: object, exc: UnknownToolError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/healthz", response_model=HealthResponse)
    def healthz() -> HealthResponse:
        reachable = False
        tools_loaded = 0
        try:
            config = load_config_by_name(
                os.environ.get("AGENT_CONFIG", "default"), root=config_root
            )
            reachable = build_provider(config.provider).health()
            registry = build_tools(config, overrides={"write_note": {"root": notes_root}})
            try:
                tools_loaded = len(registry)
            finally:
                registry.close()
        except Exception:
            reachable = False
        return HealthResponse(
            status="ok" if reachable else "degraded",
            version=VERSION,
            uptime_s=round(clock() - started, 6),
            model_reachable=reachable,
            tools_loaded=tools_loaded,
        )

    @app.post("/run", response_model=RunOutput)
    def post_run(request: RunRequest) -> RunOutput:
        return run_task(
            request,
            config_root=config_root,
            trace_dir=trace_dir,
            tool_overrides={"write_note": {"root": notes_root}},
            clock=clock,
        )

    @app.get("/configs")
    def list_configs() -> dict[str, list[str]]:
        return {"configs": available_configs(config_root)}

    return app


def main() -> int:
    load_env_file()
    import uvicorn

    host = os.environ.get("AGENT_HOST", "127.0.0.1")
    port = int(os.environ.get("AGENT_PORT", "8080"))
    uvicorn.run(create_app(), host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["VERSION", "create_app", "main"]
