from __future__ import annotations

from unittest.mock import MagicMock

import src.logging_config as logging_config


def test_file_log_sink_has_size_rotation_retention_and_compression(tmp_path, monkeypatch):
    add = MagicMock()
    monkeypatch.setattr(logging_config.logger, "remove", MagicMock())
    monkeypatch.setattr(logging_config.logger, "add", add)
    monkeypatch.setattr(logging_config.logger, "info", MagicMock())
    monkeypatch.setattr(logging_config, "get_log_dir", lambda: tmp_path)

    logging_config.setup_logging(intercept_stdlib=False, console=False)

    file_call = next(call for call in add.call_args_list if call.args[0] == str(tmp_path / "bot.log"))
    assert file_call.kwargs["rotation"] == "100 MB"
    assert file_call.kwargs["retention"] == "14 days"
    assert file_call.kwargs["compression"] == "gz"
