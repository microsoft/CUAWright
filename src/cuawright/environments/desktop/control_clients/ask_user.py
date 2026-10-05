import argparse
import json


def question(value):
    if not value.strip() or len(value) > 2000:
        raise argparse.ArgumentTypeError("question must contain 1..2000 characters")
    return value


parser = argparse.ArgumentParser()
parser.add_argument("--question", required=True, type=question)
args = parser.parse_args()
print(
    json.dumps(
        {
            "cuawright_control": 1,
            "operation": "ask_user",
            "question": args.question,
        }
    )
)
