"""A request header whose value is a project property is kept.

    content-language: ${#Project#content-language}

24 steps carry it; they post names in a non-Latin script. The header was
dropped at convert time on the belief that "the project never defines the
property" -- but a suite export holds no project properties at all, so
their absence from the XML says nothing. Without the header the API
answers 400 "Transliteration failed for locale": 21 calls in one run, and
every case that started with such an enroll failed from there on.

The value comes from config at run time. When the key is unset the runtime
omits the header instead of sending a placeholder
(RestStep.resolveHeaders), so keeping it cannot put `#content-language#`
on the wire.

    python tools/ra_converter/test_request_level_headers.py
"""
import inspect
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ra_converter as rc  # noqa: E402

NS = "http://eviware.com/soapui/config"


def _request(entries: str):
    frag = "".join('&lt;con:entry key="%s" value="%s" xmlns:con="%s"/&gt;' % (k, v, NS)
                   for k, v in entries)
    xml = ('<con:request xmlns:con="%s"><con:settings>'
           '<con:setting id="com.eviware.soapui.impl.wsdl.WsdlRequest@request-headers">%s'
           '</con:setting></con:settings></con:request>' % (NS, frag))
    return ET.fromstring(xml)


def test_a_project_property_header_is_kept():
    h = rc._request_level_headers(_request([("content-language", "${#Project#content-language}")]))
    assert h == {"content-language": "${#Project#content-language}"}, h


def test_a_literal_header_is_kept_as_before():
    h = rc._request_level_headers(_request([("content-language", "zh-CN")]))
    assert h == {"content-language": "zh-CN"}


def test_content_type_is_still_left_to_the_client():
    h = rc._request_level_headers(_request([("Content-Type", "application/json"),
                                            ("content-language", "zh-CN")]))
    assert h == {"content-language": "zh-CN"}


def test_no_settings_no_headers():
    assert rc._request_level_headers(None) == {}
    assert rc._request_level_headers(ET.fromstring('<con:request xmlns:con="%s"/>' % NS)) == {}


def test_the_client_finds_its_flags_under_the_name_it_renders():
    """A generic op ("Method 1") is rendered under its step name. The
    header flag was stored under the op name only, so the client method was
    emitted without the header slot while the dispatch class -- which reads
    the op key -- called the overload with it. Kept headers on such a step
    are what made that a compile error instead of a silent drop."""
    src = inspect.getsource(rc.Emitter.emit_service_client) if hasattr(rc, "Emitter") else \
        inspect.getsource(rc)
    assert "(effective_op, path), _flags[(op_name, path)]" in src


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  " + name)
            n += 1
    print("%d passed" % n)
