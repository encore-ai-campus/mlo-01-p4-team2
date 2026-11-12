"""Command-line entry point. Paths are resolved relative to the caller's working directory."""
import argparse
import json
import sys
from pathlib import Path

from classifier import LayaClassifier


def main():
    parser = argparse.ArgumentParser(description="Classify text with the final 1024-token Laya model")
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--input-json", type=Path, help="UTF-8 JSON containing title and body")
    inputs.add_argument("--body-file", type=Path, help="UTF-8 document file")
    parser.add_argument("--title", help="Document title, required with --body-file")
    parser.add_argument("--markdown", action="store_true", help="Apply the original training Markdown cleaner")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--output", type=Path, help="Also save the result as UTF-8 JSON")
    args = parser.parse_args()
    if args.body_file and not args.title:
        parser.error("--title is required with --body-file")
    try:
        if args.input_json:
            document = json.loads(args.input_json.read_text(encoding="utf-8-sig"))
            title, body = document["title"], document["body"]
        else:
            title = args.title
            body = args.body_file.read_text(encoding="utf-8-sig")
        classifier = LayaClassifier(device=args.device)
        result = classifier.predict(title, body, markdown=args.markdown)
        text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        if args.output:
            args.output.write_text(text + "\n", encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
