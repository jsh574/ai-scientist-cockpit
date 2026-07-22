import json
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.test_question import write_json_output


def test_write_json_output_writes_complete_utf8_json():
    response = {"status": "成功", "items": [1, 2]}

    with TemporaryDirectory() as temporary_directory:
        output_path = Path(temporary_directory) / "output.json"
        write_json_output(response, output_path)
        output_text = output_path.read_text(encoding="utf-8")

    assert json.loads(output_text) == response
    assert "成功" in output_text


if __name__ == "__main__":
    test_write_json_output_writes_complete_utf8_json()
