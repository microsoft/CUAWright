import argparse
import json
from pathlib import PurePosixPath


def absolute_path(value):
    path = PurePosixPath(value)
    if (
        not path.is_absolute()
        or path.as_posix() != value
        or "\\" in value
        or "\x00" in value
        or value.startswith("//")
        or ".." in path.parts
    ):
        raise argparse.ArgumentTypeError("must be a canonical absolute POSIX path")
    return value


parser = argparse.ArgumentParser()
parser.add_argument("--path", required=True, type=absolute_path)
args = parser.parse_args()
print(
    json.dumps({"cuawright_control": 1, "operation": "image_show", "path": args.path})
)
