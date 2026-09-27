import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv


API_URL = "https://openrouter.ai/api/v1/chat/completions"
WORKSPACE = Path(__file__).resolve().parent
MAX_FILE_BYTES = 100_000
MAX_COMMAND_OUTPUT = 20_000
COMMAND_TIMEOUT_SECONDS = 30
APPROVE_ALL_COMMANDS = False

TOOLS = [
	{
		"type": "function",
		"function": {
			"name": "list_files",
			"description": "List files and folders in a project directory. Use '.' for the project root.",
			"parameters": {
				"type": "object",
				"properties": {"path": {"type": "string"}},
				"required": ["path"],
				"additionalProperties": False,
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "read_file",
			"description": "Read a UTF-8 text file in the project. Secret and environment files are unavailable.",
			"parameters": {
				"type": "object",
				"properties": {"path": {"type": "string"}},
				"required": ["path"],
				"additionalProperties": False,
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "write_file",
			"description": "Create or replace a UTF-8 text file in the project.",
			"parameters": {
				"type": "object",
				"properties": {
					"path": {"type": "string"},
					"content": {"type": "string"},
				},
				"required": ["path", "content"],
				"additionalProperties": False,
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "move_file",
			"description": "Move or rename a file within the project without overwriting an existing destination.",
			"parameters": {
				"type": "object",
				"properties": {
					"source": {"type": "string"},
					"destination": {"type": "string"},
				},
				"required": ["source", "destination"],
				"additionalProperties": False,
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "delete_file",
			"description": "Delete a file in the project. Directories cannot be deleted.",
			"parameters": {
				"type": "object",
				"properties": {"path": {"type": "string"}},
				"required": ["path"],
				"additionalProperties": False,
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "execute_bash",
			"description": "Run a Bash command from the project directory after user confirmation. Commands are not sandboxed and may access other files or the network.",
			"parameters": {
				"type": "object",
				"properties": {"command": {"type": "string"}},
				"required": ["command"],
				"additionalProperties": False,
			},
		},
	},
]


def is_protected_name(name):
	return name in {".git", ".venv"} or name.lower().startswith(".env")


def resolve_workspace_path(path, allow_root=False):
	if not isinstance(path, str) or Path(path).is_absolute():
		raise ValueError("Use a relative path inside the project.")

	candidate = WORKSPACE / path
	current = WORKSPACE
	for part in Path(path).parts:
		if part in {"", "."}:
			continue
		current = current / part
		if current.is_symlink():
			raise ValueError("Symbolic links are not available to file tools.")

	resolved = candidate.resolve()
	if not resolved.is_relative_to(WORKSPACE):
		raise ValueError("The path must stay inside the project.")
	if any(is_protected_name(part) for part in resolved.relative_to(WORKSPACE).parts):
		raise ValueError("Environment, Git, and virtual environment files are protected.")
	if not allow_root and resolved == WORKSPACE:
		raise ValueError("The project root is not a file.")
	return resolved


def list_files(path):
	directory = resolve_workspace_path(path, allow_root=True)
	if not directory.is_dir():
		raise ValueError("The requested path is not a directory.")
	return json.dumps([
		entry.name + ("/" if entry.is_dir() else "")
		for entry in sorted(directory.iterdir(), key=lambda item: item.name.lower())
		if not is_protected_name(entry.name) and not entry.is_symlink()
	])


def read_file(path):
	file_path = resolve_workspace_path(path)
	if not file_path.is_file():
		raise ValueError("The requested path is not a file.")
	if file_path.stat().st_size > MAX_FILE_BYTES:
		raise ValueError(f"Files larger than {MAX_FILE_BYTES} bytes cannot be read.")
	return file_path.read_text(encoding="utf-8")


def write_file(path, content):
	file_path = resolve_workspace_path(path)
	if not isinstance(content, str):
		raise ValueError("File content must be text.")
	if len(content.encode("utf-8")) > MAX_FILE_BYTES:
		raise ValueError(f"Files larger than {MAX_FILE_BYTES} bytes cannot be written.")
	file_path.parent.mkdir(parents=True, exist_ok=True)
	file_path.write_text(content, encoding="utf-8")
	return f"Wrote {path}."


def move_file(source, destination):
	source_path = resolve_workspace_path(source)
	destination_path = resolve_workspace_path(destination)
	if not source_path.is_file():
		raise ValueError("The source path is not a file.")
	if destination_path.exists():
		raise ValueError("The destination already exists.")
	destination_path.parent.mkdir(parents=True, exist_ok=True)
	source_path.rename(destination_path)
	return f"Moved {source} to {destination}."


def delete_file(path):
	file_path = resolve_workspace_path(path)
	if not file_path.is_file():
		raise ValueError("Only files can be deleted; directories are protected.")
	file_path.unlink()
	return f"Deleted {path}."


def execute_bash(command):
	global APPROVE_ALL_COMMANDS
	if not isinstance(command, str) or not command.strip():
		raise ValueError("The command must be a non-empty string.")

	print(f"\nRequested Bash command (working directory: {WORKSPACE}):\n{command}")
	if not APPROVE_ALL_COMMANDS:
		try:
			approval = input("Run this command? [y/N/all for this task] ").strip().lower()
		except (EOFError, KeyboardInterrupt):
			return json.dumps({"approved": False, "message": "Command cancelled."})
		if approval == "all":
			APPROVE_ALL_COMMANDS = True
		elif approval != "y":
			return json.dumps({"approved": False, "message": "Command not run."})
	else:
		print("Automatically approved for this task.")

	try:
		result = subprocess.run(
			["/bin/bash", "-c", command],
			cwd=WORKSPACE,
			capture_output=True,
			text=True,
			timeout=COMMAND_TIMEOUT_SECONDS,
			check=False,
		)
		return json.dumps({
			"approved": True,
			"exit_code": result.returncode,
			"stdout": result.stdout[:MAX_COMMAND_OUTPUT],
			"stderr": result.stderr[:MAX_COMMAND_OUTPUT],
		})
	except subprocess.TimeoutExpired as error:
		stdout = error.stdout or ""
		stderr = error.stderr or ""
		if isinstance(stdout, bytes):
			stdout = stdout.decode("utf-8", errors="replace")
		if isinstance(stderr, bytes):
			stderr = stderr.decode("utf-8", errors="replace")
		return json.dumps({
			"approved": True,
			"timed_out": True,
			"timeout_seconds": COMMAND_TIMEOUT_SECONDS,
			"stdout": stdout[:MAX_COMMAND_OUTPUT],
			"stderr": stderr[:MAX_COMMAND_OUTPUT],
		})


TOOL_HANDLERS = {
	"list_files": list_files,
	"read_file": read_file,
	"write_file": write_file,
	"move_file": move_file,
	"delete_file": delete_file,
	"execute_bash": execute_bash,
}


def run_tool(tool_call):
	function = tool_call.get("function", {})
	name = function.get("name")
	if name not in TOOL_HANDLERS:
		raise ValueError("This file operation is not allowed.")
	arguments = json.loads(function.get("arguments", "{}"))
	if not isinstance(arguments, dict):
		raise ValueError("Tool arguments must be an object.")

	path = arguments.get("path") or arguments.get("source") or ""
	if name == "execute_bash":
		return TOOL_HANDLERS[name](**arguments)
	print(f"[file tool] {name}: {path}", file=sys.stderr)
	return TOOL_HANDLERS[name](**arguments)


def send_message(api_key, model, messages):
	content = []
	tool_calls = {}
	stream_started = False
	try:
		with requests.post(
			API_URL,
			headers={
				"Authorization": f"Bearer {api_key}",
				"Content-Type": "application/json",
			},
			json={
				"model": model,
				"messages": messages,
				"tools": TOOLS,
				"tool_choice": "auto",
				"stream": True,
			},
			timeout=60,
			stream=True,
		) as response:
			if not response.ok:
				try:
					error = response.json().get("error", {})
					detail = error.get("message") if isinstance(error, dict) else str(error)
				except ValueError:
					detail = response.reason
				print(f"OpenRouter request failed (HTTP {response.status_code}): {detail}", file=sys.stderr)
				return None

			for line in response.iter_lines(chunk_size=1, decode_unicode=True):
				if not line:
					continue
				if isinstance(line, bytes):
					line = line.decode("utf-8")
				if not line.startswith("data:"):
					continue
				data = line[5:].strip()
				if data == "[DONE]":
					break
				try:
					chunk = json.loads(data)
				except ValueError:
					continue
				choices = chunk.get("choices", [])
				if not choices:
					continue
				delta = choices[0].get("delta", {})
				text = delta.get("content")
				if isinstance(text, str):
					if not stream_started:
						print("OpenRouter: ", end="", flush=True)
						stream_started = True
					sys.stdout.write(text)
					sys.stdout.flush()
					content.append(text)
				for call in delta.get("tool_calls", []):
					index = call.get("index", 0)
					tool_call = tool_calls.setdefault(index, {
						"id": "",
						"type": "function",
						"function": {"name": "", "arguments": ""},
					})
					if call.get("id"):
						tool_call["id"] = call["id"]
					if call.get("type"):
						tool_call["type"] = call["type"]
					function = call.get("function", {})
					if function.get("name"):
						tool_call["function"]["name"] += function["name"]
					if function.get("arguments"):
						tool_call["function"]["arguments"] += function["arguments"]
		if stream_started:
			print()
	except requests.RequestException as error:
		if stream_started:
			print()
		print(f"OpenRouter request failed: {error}", file=sys.stderr)
		return None

	assistant_message = {
		"role": "assistant",
		"content": "".join(content) or None,
	}
	if tool_calls:
		assistant_message["tool_calls"] = [tool_calls[index] for index in sorted(tool_calls)]
	return assistant_message


def load_agent_memory():
	memory_path = WORKSPACE / "AGENTS.md"
	try:
		content = memory_path.read_text(encoding="utf-8")
	except OSError as error:
		print(f"Could not load {memory_path.name}: {error}", file=sys.stderr)
		return None
	if not content.strip():
		return None
	return {
		"role": "system",
		"content": f"Repository memory from {memory_path.name}:\n\n{content}",
	}


def main():
	global APPROVE_ALL_COMMANDS
	parser = argparse.ArgumentParser(description="Chat with a model through OpenRouter.")
	parser.add_argument("prompt", nargs="*", help="Optional first message")
	args = parser.parse_args()

	load_dotenv()
	api_key = os.getenv("OPENROUTER_API_KEY")
	model = os.getenv("OPENROUTER_MODEL")
	if not api_key:
		parser.error("OPENROUTER_API_KEY was not found in the environment or .env file")
	if not model:
		parser.error("OPENROUTER_MODEL was not found in the environment or .env file")

	agent_memory = load_agent_memory()
	messages = [agent_memory] if agent_memory else []
	first_message = " ".join(args.prompt) if args.prompt else None
	print("Chat with OpenRouter. Type 'exit' or 'quit' to end.")
	while True:
		if first_message is not None:
			message = first_message
			first_message = None
			print(f"You: {message}")
		else:
			try:
				message = input("You: ").strip()
			except (EOFError, KeyboardInterrupt):
				print()
				break

		if not message:
			continue
		if message.lower() in {"exit", "quit"}:
			break

		APPROVE_ALL_COMMANDS = False
		request_messages = messages + [{"role": "user", "content": message}]
		for _ in range(8):
			assistant_message = send_message(api_key, model, request_messages)
			if assistant_message is None:
				break
			request_messages.append(assistant_message)
			tool_calls = assistant_message.get("tool_calls") or []
			if not tool_calls:
				if assistant_message.get("content"):
					messages = request_messages
				else:
					print("OpenRouter returned an empty response.", file=sys.stderr)
				break

			for tool_call in tool_calls:
				try:
					result = run_tool(tool_call)
				except Exception as error:
					result = json.dumps({"error": str(error)})
				request_messages.append({
					"role": "tool",
					"tool_call_id": tool_call.get("id", ""),
					"content": str(result),
				})
		else:
			print("OpenRouter reached the file-tool call limit for this message.", file=sys.stderr)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())