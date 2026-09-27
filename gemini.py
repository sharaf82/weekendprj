import argparse
import os
import sys

from dotenv import load_dotenv
from google import genai


def main():
	parser = argparse.ArgumentParser(description="Chat with Gemini.")
	parser.add_argument("prompt", nargs="*", help="Optional first message")
	args = parser.parse_args()

	load_dotenv()
	api_key = os.getenv("GEMINI_API_KEY")
	if not api_key:
		parser.error("GEMINI_API_KEY was not found in the environment or .env file")

	try:
		client = genai.Client(api_key=api_key)
		chat = client.chats.create(model="gemini-3.7-flash")
	except Exception as error:
		print(f"Could not start Gemini chat: {error}", file=sys.stderr)
		return 1

	first_message = " ".join(args.prompt) if args.prompt else None
	print("Chat with Gemini. Type 'exit' or 'quit' to end.")
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

		try:
			response = chat.send_message(message)
		except Exception as error:
			print(f"Gemini request failed: {error}", file=sys.stderr)
			continue

		if response.text:
			print(f"Gemini: {response.text}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
