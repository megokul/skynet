import json
from io import StringIO

import gateway_config as cfg
from ssh_tunnel_executor import SSHTunnelExecutor


class _MemoryFile:
    def __init__(self, store, path, mode):
        self._store = store
        self._path = path
        self._mode = mode
        self._buffer = StringIO(store.get(path, "") if "r" in mode else "")

    def write(self, value):
        return self._buffer.write(value)

    def read(self):
        return self._buffer.getvalue()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if "w" in self._mode:
            self._store[self._path] = self._buffer.getvalue()
        return False


class _MemorySFTP:
    def __init__(self):
        self.files = {}
        self.dirs = {".", "E:\\", "E:\\work", "E:\\work\\demo"}

    def stat(self, path):
        if path in self.files:
            text = self.files[path]
            return type("Stat", (), {"st_size": len(text), "st_mtime": 123456})()
        if path in self.dirs:
            return type("Stat", (), {"st_size": 0, "st_mtime": 123456})()
        raise OSError(path)

    def mkdir(self, path):
        self.dirs.add(path)

    def open(self, path, mode):
        parent = str(path).rsplit("\\", 1)[0] if "\\" in str(path) else "."
        self.dirs.add(parent)
        return _MemoryFile(self.files, path, mode)

    def close(self):
        return None


class _FakeChannel:
    def __init__(self):
        self.stdout_chunks = [b"created main.py\n"]
        self.stderr_chunks = []
        self._rc = 0

    def recv_ready(self):
        return bool(self.stdout_chunks)

    def recv(self, _size):
        return self.stdout_chunks.pop(0) if self.stdout_chunks else b""

    def recv_stderr_ready(self):
        return bool(self.stderr_chunks)

    def recv_stderr(self, _size):
        return self.stderr_chunks.pop(0) if self.stderr_chunks else b""

    def exit_status_ready(self):
        return not self.stdout_chunks and not self.stderr_chunks

    def recv_exit_status(self):
        return self._rc


class _FakeStdout:
    def __init__(self):
        self.channel = _FakeChannel()


class _FakeClient:
    def __init__(self, sftp):
        self._sftp = sftp

    def open_sftp(self):
        return self._sftp

    def exec_command(self, _command, timeout=None, get_pty=False):
        del timeout, get_pty
        for path in list(self._sftp.files):
            if path.endswith(".txt") or path.endswith(".txt.pid"):
                self._sftp.files.pop(path, None)
        return None, _FakeStdout(), None


def _read_events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_windows_prompt_wrapper_emits_forensic_events(tmp_path, monkeypatch):
    trace_file = tmp_path / "ssh.trace.log"
    monkeypatch.setattr(cfg, "RUNTIME_TRACE_ENABLED", True, raising=False)
    monkeypatch.setattr(cfg, "RUNTIME_TRACE_LIVE_FILE", str(trace_file), raising=False)
    monkeypatch.setattr(cfg, "RUNTIME_TRACE_REDACTION_MODE", "forensic_redacted", raising=False)
    monkeypatch.setattr(cfg, "RUNTIME_TRACE_PROMPT_FILE_EVENTS", True, raising=False)
    monkeypatch.setattr(cfg, "RUNTIME_TRACE_ACTIVE_SESSION_REGISTRY", True, raising=False)

    executor = SSHTunnelExecutor()
    executor.remote_os = "windows"
    executor._trace_local.ctx = {
        "trace_id": "trace-1",
        "root_trace_id": "trace-1",
        "span_id": "span-1",
        "phase": "coding_generation",
        "stage": "codex",
        "project_id": "proj-1",
        "task_id": "task-1",
        "graph_id": "graph-1",
        "node_key": "W-1",
        "node_type": "work",
        "worker_id": "worker-primary",
        "action_name": "run_coding_agent",
        "session_key": "sess-1",
    }

    sftp = _MemorySFTP()
    client = _FakeClient(sftp)
    result = executor._run_windows_command_with_prompt_file(
        client=client,
        args_without_prompt=["codex", "exec", "-"],
        prompt="print('hi')",
        cwd=r"E:\work\demo",
        timeout=60,
        prompt_via_stdin=True,
        session_key="sess-1",
        before_snapshot={},
    )

    assert result["returncode"] == 0
    events = _read_events(trace_file)
    names = [event["event"] for event in events]
    assert "ssh.session.registered" in names
    assert "ssh.prompt_file.write" in names
    assert "ssh.command.launch" in names
    assert "ssh.command.stdout.chunk" in names
    assert "ssh.prompt_file.cleanup" in names
    assert "ssh.session.completed" in names

