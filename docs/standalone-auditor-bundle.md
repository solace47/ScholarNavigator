# Independent standalone auditor bundle

`standalone_auditor_bundle_v1` packages the shareable part of the tracked
validation-readiness evidence as deterministic data. It is an engineering
integrity check, not evidence of retrieval quality, human Precision, or an
official score.

The ZIP contains canonical JSON summaries for claims, blockers, freshness,
default-policy state, evidence references, and protocol dependencies, plus one
`verify.py`. Bottom-level internal artifacts are not copied into the archive;
their stable hashes are marked `externally_unverifiable_reference`. This lets
an external reviewer check the publication's closed hash chain without
claiming that a hash authenticates the publisher or independently proves the
unpublished artifact's contents.

The verifier uses only the Python standard library. A reviewer may copy
`verify.py` out of the archive and run it from an untrusted checkout-free
directory:

```bash
python -I -S verify.py verify standalone-auditor.zip
```

It reads only the named archive, does not import SPAR modules, execute archive
members, use the network, or launch subprocesses. It rejects extra, missing,
duplicate, linked, absolute, traversing, Unicode-colliding, oversized, highly
compressed, non-UTF-8, non-canonical, duplicate-key, and non-finite JSON
members. The embedded verifier is checked as data against its manifest hash;
the trusted copy used to start verification is never executed from inside the
archive.

Repository operators build and compare temporary archives with:

```bash
python scripts/check_standalone_auditor_bundle.py build \
  --output /tmp/standalone-auditor.zip
python scripts/check_standalone_auditor_bundle.py verify \
  /tmp/standalone-auditor.zip
python scripts/check_standalone_auditor_bundle.py compare \
  /tmp/standalone-auditor-a.zip /tmp/standalone-auditor-b.zip
python scripts/check_standalone_auditor_bundle.py audit-readiness
```

Exit codes are `0=verified_with_declared_blockers`,
`2=integrity_or_claim_violation`,
`3=not_ready_missing_shareable_evidence`, and `4=usage_error`. Generated ZIPs
are temporary release products and are intentionally not tracked.

The current archive preserves `formal_validation_complete=false` and all three
formal blockers: Full1000 completion, real independent human Precision, and an
exact official scorer/schema.
