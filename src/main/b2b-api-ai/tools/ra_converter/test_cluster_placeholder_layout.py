"""Two cases share a @Test only when their bodies take properties at the
same places.

    case 1   "emailDomains": [ "peaLCenteR.org" ]
    case 2   "emailDomains": [ "${Properties#Domain}" ]

Same keys and leaf types, so they clustered. The template merge keeps
bodies with different placeholder layouts in separate files, and a @Test
holds one template -- cluster[0]'s. Row 2 went out through row 1's template
with cells that belong to its own:

    "country": null, "emailDomains": [null]     -> 400

20 methods in 7 suites had a row like that. Three sent a literal null; the
rest sent the lead case's property where their own literal belonged, or the
other way round, with nothing in the log to say so.

    python tools/ra_converter/test_cluster_placeholder_layout.py
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ra_converter as rc  # noqa: E402


class Step:
    def __init__(self, body):
        self.request_body = body


BODY = ('{"name": "%s", "contactInfo": {"address": {"city": "%s", "country": "US"}},'
        ' "emailDomains": ["%s"]}')


def key(name, city, domain):
    return rc._body_shape_key(Step(BODY % (name, city, domain)))


def test_a_literal_against_a_property_does_not_share_a_test():
    literal = key("${Properties#name}", "ChaRLOTTe", "peaLCenteR.org")
    prop = key("${Properties#name}", "PittsbuRgh", "${Properties#Domain}")
    assert literal != prop


def test_different_literals_still_share_a_test():
    """NEGATIVE CONTROL: this is what clustering is for -- a CSV cell
    carries the difference."""
    a = key("${Properties#name}", "ChaRLOTTe", "peaLCenteR.org")
    b = key("${Properties#name}", "PittsbuRgh", "apollomessenger.com")
    assert a == b


def test_two_different_properties_at_one_leaf_do_not_share_a_test():
    owner = key("${Properties#name}", "x", "${Properties#Domain}")
    member = key("${Properties#name}", "x", "${Properties#DomainMember}")
    assert owner != member


def test_a_body_with_no_property_keeps_its_old_key():
    """Bodies with no placeholder are the bulk of the tree; their key must
    not move, or every such method would be re-clustered for nothing."""
    k = key("Acme", "x", "acme.com")
    assert k.startswith("j:") and "|ph:" not in k, k


def test_the_key_is_stable_between_runs():
    """A hash of the layout, not Python's per-process hash()."""
    src = inspect.getsource(rc._body_shape_key)
    assert "sha1" in src and "hash(" not in src.replace("hashlib", "")


def test_every_cluster_decision_goes_through_this_key():
    """Clustering, repeat-endpoint flattening and the prefix merge must
    agree on what 'same body' means."""
    assert "_body_shape_key(s)" in inspect.getsource(rc._rest_shape_sig)


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  " + name)
            n += 1
    print("%d passed" % n)
