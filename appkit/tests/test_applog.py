"""The app's log file (station_appkit/applog.py)."""
import logging

from station_appkit.applog import log_to_file


def test_logs_to_a_file_beside_the_data(tmp_path):
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    try:
        path = log_to_file(tmp_path / "home", "station-test", max_bytes=200, backups=1)
        assert path == tmp_path / "home" / "station-test.log"
        for i in range(20):
            logging.getLogger("station_test.thing").info("line %d", i)
        text = path.read_text()
        assert "INFO station_test.thing: line 19" in text
        assert (tmp_path / "home" / "station-test.log.1").exists()  # kept small
        assert not (tmp_path / "home" / "station-test.log.2").exists()
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers[:] = handlers
        root.setLevel(level)


def test_a_folder_it_cant_write_leaves_logging_alone(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    root = logging.getLogger()
    before = root.handlers[:]
    assert log_to_file(blocker / "home", "x") is None
    assert root.handlers == before
