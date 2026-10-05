Executes shell commands in a sandboxed environment.

Tools:
- run_command(command, timeout=30) — execute a shell command and return output
- run_python(code, timeout=30) — execute Python code and return result

Security: commands run in AGENT_HOME directory, with a timeout. No interactive commands.
