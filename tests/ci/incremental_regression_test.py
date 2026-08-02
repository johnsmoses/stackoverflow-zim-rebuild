"""Incremental-render regression gate (Task 10 / H8).

Pure-logic gate against the PATCHED sotoki source built by the CI
patch-apply-gate job. It proves the core incremental contract of the
snapshot-aware update mode (patch 0007):

  * a question whose *content* changed must produce a different
    fingerprint (-> re-render),
  * a question with identical content must produce the same fingerprint
    (-> skip re-render),
  * should_skip_render() must agree with the fingerprint comparison and
    must never skip for legacy (schema_version 1) manifests or a
    mismatched render contract.

The patched dependency is NOT committed here: the test imports
``fingerprint_post`` and ``should_skip_render`` from a temporary patched
clone of openzim/sotoki located via ``SOTOKI_PATCHED_SRC`` (the clone's
``src/`` directory) or the ``--patched-sotoki-src=<dir>`` pytest option.
When neither is available the whole module is skipped with a clear message
so local runs without a clone are graceful.

Grep markers such as ``source_sha256``/``should_skip_render`` in the
patched source are checked by the CI job as *diagnostic only*; the semantic
gate is this file.
"""

import os
import sys

import pytest

RENDER_CONTRACT = "1"


def _resolve_patched_src() -> str | None:
    src = os.environ.get("SOTOKI_PATCHED_SRC")
    if src:
        return src
    for arg in sys.argv[1:]:
        if arg.startswith("--patched-sotoki-src="):
            return arg.split("=", 1)[1]
    return None


patched_src = _resolve_patched_src()
if patched_src is None:
    pytest.skip(
        "SOTOKI_PATCHED_SRC not set and no --patched-sotoki-src given: no "
        "patched sotoki clone available. Build one (clone openzim/sotoki at "
        "157ca9a, `git am patches/sotoki/*.patch`) and point "
        "SOTOKI_PATCHED_SRC at its src/ directory; the CI patch-apply-gate "
        "job does exactly this.",
        allow_module_level=True,
    )

sys.path.insert(0, patched_src)

from sotoki.context import Context  # noqa: E402

# sotoki.utils.shared calls Context.get() at import time; provide a minimal
# singleton before importing the patched posts module.
Context.setup(
    domain="stackoverflow.com",
    mirror="file:///dev/null",
    title="Incremental regression fixture",
    description="Incremental regression fixture",
)

from sotoki.posts import (  # noqa: E402
    RENDER_CONTRACT_VERSION,
    fingerprint_post,
    should_skip_render,
)


def _post(overrides: dict | None = None) -> dict:
    """Canonical StackExchange-format question with two answers and comments."""
    post = {
        "Id": 1001,
        "Title": "How to bisect a git history",
        "Body": "<p>Step 1: pick a midpoint commit.</p>",
        "Score": 42,
        "Tags": ["git", "bisect"],
        "OwnerUserId": 7,
        "OwnerDisplayName": "alice",
        "CreationDate": "2020-01-01T00:00:00Z",
        "LastActivityDate": "2020-06-01T00:00:00Z",
        "ViewCount": 500,
        "LastEditDate": None,
        "LastEditorUserId": None,
        "AcceptedAnswerId": 2002,
        "comments": [
            {
                "Id": 3001,
                "PostId": 1001,
                "UserId": 9,
                "Score": 2,
                "Text": "nice question",
                "CreationDate": "2020-01-02T00:00:00Z",
                "ContentLicense": "CC BY-SA",
            },
            {
                "Id": 3002,
                "PostId": 1001,
                "UserId": 11,
                "Score": 1,
                "Text": "thanks",
                "CreationDate": "2020-01-03T00:00:00Z",
                "ContentLicense": "CC BY-SA",
            },
        ],
        "answers": [
            {
                "Id": 2001,
                "Score": 10,
                "Body": "<p>Answer A</p>",
                "OwnerUserId": 8,
                "OwnerDisplayName": "bob",
                "CreationDate": "2020-01-02T00:00:00Z",
                "LastEditDate": None,
                "LastEditorUserId": None,
                "comments": [
                    {
                        "Id": 3101,
                        "PostId": 2001,
                        "UserId": 9,
                        "Score": 0,
                        "Text": "a-comment",
                        "CreationDate": "2020-01-02T00:00:00Z",
                        "ContentLicense": "CC BY-SA",
                    }
                ],
            },
            {
                "Id": 2002,
                "Score": 5,
                "Body": "<p>Answer B</p>",
                "OwnerUserId": 12,
                "OwnerDisplayName": "carol",
                "CreationDate": "2020-01-03T00:00:00Z",
                "LastEditDate": None,
                "LastEditorUserId": None,
                "comments": [],
            },
        ],
        "links": {
            "linked": [{"Id": 1002, "PostTypeId": 1}],
            "duplicate": [],
        },
    }
    if overrides:
        post.update(overrides)
    return post


def _manifest(source_sha256: str | None = None, schema_version: int = 2) -> dict:
    manifest = {
        "schema_version": schema_version,
        "render_contract_version": RENDER_CONTRACT,
        "source_sha256": source_sha256,
    }
    if source_sha256 is None:
        del manifest["source_sha256"]
    return manifest


def test_render_contract_version_is_pinned():
    # The test's manifest fixtures are written against the exact contract
    # version the patched source exports; a contract bump must update both.
    assert RENDER_CONTRACT_VERSION == RENDER_CONTRACT


def test_same_post_permutation_invariant():
    """Reordering answers/comments must NOT change the fingerprint."""
    base = _post()
    shuffled = _post()
    shuffled["answers"] = list(reversed(shuffled["answers"]))
    shuffled["answers"][0]["comments"] = list(
        reversed(shuffled["answers"][0]["comments"])
    )
    shuffled["comments"] = list(reversed(shuffled["comments"]))
    assert fingerprint_post(base) == fingerprint_post(shuffled)


def test_same_post_deterministic():
    assert fingerprint_post(_post()) == fingerprint_post(_post())


@pytest.mark.parametrize(
    "field, edited",
    [
        ("Body", "<p>Step 1: pick a LATER midpoint commit.</p>"),
        ("Score", 43),
        ("Title", "How to bisect a git history with reflogs"),
    ],
)
def test_changed_content_changes_fingerprint(field, edited):
    """Same post ID, changed renderer-consumed content -> new fingerprint."""
    base = _post()
    changed = _post({field: edited})
    assert changed["Id"] == base["Id"]
    assert fingerprint_post(base) != fingerprint_post(changed)


def test_skip_render_true_when_source_matches():
    assert should_skip_render(_manifest(source_sha256=fingerprint_post(_post())),
                              fingerprint_post(_post())) is True


def test_skip_render_false_when_source_differs():
    assert should_skip_render(_manifest(source_sha256="0" * 64),
                              fingerprint_post(_post())) is False


def test_skip_render_false_for_legacy_schema_version():
    assert should_skip_render(_manifest(schema_version=1), fingerprint_post(_post())) is False


def test_skip_render_false_when_contract_version_mismatches():
    manifest = _manifest(source_sha256=fingerprint_post(_post()))
    manifest["render_contract_version"] = "0"
    assert should_skip_render(manifest, fingerprint_post(_post())) is False


def test_skip_render_false_for_missing_manifest():
    assert should_skip_render(None, fingerprint_post(_post())) is False