"""The summarising model: the local oh-my-pi CLI (`omp`), which already holds the user's GPT login, so
this code never sees a credential. Each call is one-shot and tool-less: the prompt goes in a temporary
file, and with every tool disabled, instructions hidden in a chat cannot touch the disk."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time


class EngineError(RuntimeError):
    pass


def parse_json(text: str):
    """Models wrap JSON in prose or fences; take the first balanced object."""
    source = text.replace("```json", "").replace("```", "")
    start = source.find("{")
    if start < 0:
        raise EngineError("the reply contained no JSON")
    depth, in_string, escaped = 0, False, False
    for index in range(start, len(source)):
        char = source[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(source[start:index + 1])
                except ValueError as error:
                    raise EngineError("the reply was not valid JSON: %s" % error)
    raise EngineError("the reply was cut off")


class Engine:
    def __init__(self, command="omp", model="", timeout=420, concurrent=3, retries=2):
        self.command, self.model, self.timeout, self.retries = command, model, timeout, retries
        self.slots = threading.Semaphore(concurrent)

    def available(self):
        try:
            done = subprocess.run([self.command, "--version"], capture_output=True, text=True, timeout=30,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return done.returncode == 0, (done.stdout or done.stderr).strip()
        except (OSError, subprocess.SubprocessError) as error:
            return False, str(error)

    def complete(self, prompt: str, thinking: str = "low") -> str:
        last = None
        for attempt in range(self.retries + 1):
            try:
                with self.slots:
                    return self._once(prompt, thinking)
            except EngineError as error:
                last = error
                time.sleep(2 + 4 * attempt)
        raise last

    def complete_json(self, prompt: str, thinking: str = "low"):
        last = None
        for _ in range(2):
            try:
                return parse_json(self.complete(prompt, thinking))
            except EngineError as error:
                last = error
        raise last

    def _once(self, prompt, thinking):
        directory = tempfile.mkdtemp(prefix="memo-ai-")
        try:
            with open(os.path.join(directory, "prompt.md"), "w", encoding="utf-8") as handle:
                handle.write(prompt)
            args = [self.command, "-p", "--no-session", "--no-tools", "--no-extensions", "--no-skills", "--no-rules",
                    "--no-title", "--no-lsp", "--thinking", thinking, "--cwd", directory]
            if self.model:
                args += ["--model", self.model]
            args += ["@prompt.md", "Do the task described in prompt.md. Reply with only the JSON object it asks for."]
            try:
                done = subprocess.run(args, capture_output=True, cwd=directory, timeout=self.timeout, encoding="utf-8",
                                      errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except subprocess.TimeoutExpired:
                raise EngineError("the model timed out")
            except OSError as error:
                raise EngineError("cannot run %s: %s" % (self.command, error))
            if done.returncode != 0:
                # the prompt is never echoed: only the engine's own stderr tail explains a failure
                tail = done.stderr.replace("Working...", "").strip()[-300:]
                raise EngineError("the model failed (exit %d): %s" % (done.returncode, tail))
            return done.stdout
        finally:
            shutil.rmtree(directory, ignore_errors=True)
