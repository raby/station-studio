import pytest

from station_server.senders import (
    ADMIN_USER_AGENT,
    FileSender,
    IcecastSender,
    SenderError,
    ShoutcastSender,
    StreamInfo,
    sender_from_url,
)

INFO = StreamInfo(name="Rebirth Classics", genre="Club Classics", url="https://www.rebirthclassics.uk", bitrate=128)


def test_shoutcast_handshake_audio_and_title(shoutcast):
    s = ShoutcastSender("127.0.0.1", shoutcast.port, "hackme", INFO)
    s.connect()
    s.send(b"\xff\xfbaudio")
    assert shoutcast.got_audio.wait(2)
    assert bytes(shoutcast.audio) == b"\xff\xfbaudio"
    assert "icy-name:Rebirth Classics" in shoutcast.headers
    assert "icy-br:128" in shoutcast.headers
    assert "icy-pub:0" in shoutcast.headers
    assert "content-type:audio/mpeg" in shoutcast.headers
    s.set_title("Inner City - Good Life")
    assert shoutcast.titles == ["Inner City - Good Life"]
    # SHOUTcast 1.x ignores admin requests that don't look like a browser.
    assert shoutcast.admin_agents == [ADMIN_USER_AGENT]
    assert ADMIN_USER_AGENT.startswith("Mozilla/")
    s.close()


def test_shoutcast_second_stream_sends_its_stream_number(shoutcast):
    s = ShoutcastSender("127.0.0.1", shoutcast.port, "hackme", INFO, sid=2)
    s.connect()
    assert shoutcast.secrets == ["hackme:#2"]
    s.close()


def test_shoutcast_wrong_password(shoutcast):
    s = ShoutcastSender("127.0.0.1", shoutcast.port, "wrong", INFO)
    with pytest.raises(SenderError, match="invalid password"):
        s.connect()


def test_reconnects_after_the_server_goes_away(shoutcast):
    now = [100.0]
    s = ShoutcastSender("127.0.0.1", shoutcast.port, "hackme", INFO, clock=lambda: now[0])
    s.connect()
    s._sock.close()  # the connection drops
    s.send(b"lost")  # noticed here; audio is dropped, not queued
    assert not s.connected
    s.send(b"too soon")  # waits before trying again
    assert not s.connected
    now[0] += 6
    shoutcast.got_audio.clear()
    s.send(b"back")
    assert s.connected
    assert shoutcast.got_audio.wait(2)
    assert shoutcast.audio.endswith(b"back")
    assert len(shoutcast.secrets) == 2
    s.close()


def test_server_down_at_runtime_never_raises():
    s = ShoutcastSender("127.0.0.1", 1, "pw", INFO)
    s.send(b"x")  # nothing listening: logged, not raised
    s.set_title("y")
    assert not s.connected


def test_icecast_put_audio_and_title(icecast):
    s = IcecastSender("127.0.0.1", icecast.port, "radio", "hackme", INFO)
    s.connect()
    s.send(b"\xff\xfbaudio")
    assert icecast.got_audio.wait(2)
    method, target, headers = icecast.requests[0]
    assert (method, target) == ("PUT", "/radio")
    assert headers["content-type"] == "audio/mpeg"
    assert headers["ice-name"] == "Rebirth Classics"
    assert headers["ice-audio-info"] == "bitrate=128"
    s.set_title("Inner City - Good Life")
    assert icecast.titles == ["Inner City - Good Life"]
    s.close()


def test_icecast_wrong_password(icecast):
    s = IcecastSender("127.0.0.1", icecast.port, "/radio", "wrong", INFO)
    with pytest.raises(SenderError, match="401"):
        s.connect()


def test_file_sender(tmp_path):
    s = FileSender(tmp_path / "out" / "test.mp3")
    s.connect()
    s.send(b"abc")
    s.set_title("One - Two")
    s.close()
    assert (tmp_path / "out" / "test.mp3").read_bytes() == b"abc"
    assert (tmp_path / "out" / "test.txt").read_text().endswith("One - Two\n")


def test_sender_from_url():
    s = sender_from_url("shoutcast://:pw@example.com:8002?sid=2", INFO)
    assert isinstance(s, ShoutcastSender)
    assert (s.host, s.port, s.password, s.sid) == ("example.com", 8002, "pw", 2)

    s = sender_from_url("shoutcast://example.com:8000", INFO, password="from-env")
    assert s.password == "from-env"

    s = sender_from_url("icecast://dj:p%40ss@example.com:8000/classics", INFO)
    assert isinstance(s, IcecastSender)
    assert (s.user, s.password, s.mount) == ("dj", "p@ss", "/classics")

    assert isinstance(sender_from_url("recordings/test.mp3", INFO), FileSender)

    with pytest.raises(ValueError, match="password"):
        sender_from_url("shoutcast://example.com:8000", INFO)
    with pytest.raises(ValueError, match="host:port"):
        sender_from_url("shoutcast://:pw@example.com", INFO)
    with pytest.raises(ValueError, match="mount"):
        sender_from_url("icecast://:pw@example.com:8000", INFO)
