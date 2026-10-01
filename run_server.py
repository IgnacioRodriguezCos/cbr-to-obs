import subprocess

import uvicorn


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


if __name__ == "__main__":
    print(f"[run_server] Codigo activo: commit {_git_commit()}")
    print("[run_server] Auto-reload ACTIVADO: los cambios a *.py se aplican solos.")
    print("[run_server] Veras 'WatchFiles detected changes...' en esta consola en cada reload.")
    print("[run_server] OJO: si cambia un archivo mientras corre un pipeline, la corrida se aborta.")
    uvicorn.run("app:app", host="127.0.0.1", port=8086, log_level="info", access_log=False, reload=True, reload_dirs=["."])
