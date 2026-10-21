# How to contribute

We'd love to accept your patches and contributions to this project.

## Before you begin

### Sign our Contributor License Agreement

Contributions to this project must be accompanied by a
[Contributor License Agreement](https://cla.developers.google.com/about) (CLA).
You (or your employer) retain the copyright to your contribution; this simply
gives us permission to use and redistribute your contributions as part of the
project.

If you or your current employer have already signed the Google CLA (even if it
was for a different project), you probably don't need to do it again.

Visit <https://cla.developers.google.com/> to see your current agreements or to
sign a new one.

### Review our community guidelines

This project follows
[Google's Open Source Community Guidelines](https://opensource.google/conduct/).

## Contribution process

### Code reviews

All submissions, including submissions by project members, require review. We
use GitHub pull requests for this purpose. Consult
[GitHub Help](https://help.github.com/articles/about-pull-requests/) for more
information on using pull requests.

### Checks before you send a patch

Every check below is offline: no credentials, no benchmark checkout and no model
call. `.github/workflows/ci.yml` runs exactly these on Python 3.12 and 3.13.

```shell
python3 -m venv .venv && . .venv/bin/activate   # Python 3.12 or 3.13
pip install -e . ruff

ruff check .                      # the lint rules in pyproject.toml
python3 -m unittest discover -s tests -v   # the workflow, recursion, library tests
python3 scripts/check_provenance.py   # release runner and patch checksums
harness2-demo --output /tmp/harness2-demo --k 2   # real recursion, local editors
```

`harness2-demo` runs the method end to end with deterministic local editors, so
it catches a change that breaks the recursion or the experience library without
spending anything.

If your change touches a benchmark, say in the pull request which benchmark you
exercised and how, because the benchmark paths need a dataset and credentials
that the checks above deliberately do not use.

### Before you open a pull request

Search the open issues and pull requests first. Several patches have arrived
twice, and a duplicate costs review time that could go to your change instead.
If an issue already describes the bug, link it from the pull request.
