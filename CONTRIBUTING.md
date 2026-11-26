# Contributing to Riffle

Riffle is a personal project by Brandon M. Keltz. Anyone may report a problem or suggest an idea;
code changes come only from people he invites.

## Issues

Open an issue on [GitHub](https://github.com/keltzbm/riffle/issues/new/choose) for:

- **a bug:** a command that fails, crashes, or gives a wrong result;
- **wrong data:** a card, price, decklist, or other fact that doesn't match its source;
- **an idea:** something Riffle could do, or do better.

Each kind has a short form. Search the [open issues](https://github.com/keltzbm/riffle/issues)
first, and put one problem in each issue. For a bug, the most useful things are the exact command, what
you expected, what happened instead, and the full output, pasted as text rather than a screenshot. For
wrong data, name the card or list, the value Riffle shows, the value its source shows, and a link to
the source.

## Security problems

Don't open a public issue for a security problem. Report it privately: on GitHub, open the repository's
**Security** tab and choose **Report a vulnerability**.

## Pull requests

Pull requests are by invitation only: GitHub accepts them only from collaborators on the repository.
If you'd like to work on something, open an issue about it first.

You're welcome to fork Riffle and change your copy. It is licensed under the
[GNU Affero General Public License, version 3](LICENSE): if you run a changed version for other people
over a network, the license requires you to offer them its source code.

## For invited contributors

Set up as the [README](README.md#install) says. Its install writes the database's password into
`.env` and `~/.pgpass` and starts Postgres, which the tests use; from a clone made another way, the
same steps:

```sh
pw=$(openssl rand -hex 24)
printf '%s\n' "POSTGRES_PASSWORD=$pw" > .env
printf '%s\n' "localhost:5432:*:tcg:$pw" >> ~/.pgpass
chmod 600 ~/.pgpass
unset pw
riffle db up && riffle db upgrade
```

Every test runs with its own config and data folders and an unreachable database URL, so none can touch
real data. Tests marked `postgres` use a separate `tcg_test` database, recreated on every run; without
Postgres they skip, and the 90% coverage floor below then fails. The devcontainer
([GitHub Codespaces](README.md#github-codespaces)) has both.

Every change goes on its own branch and needs:

- tests for every new or changed line;
- an entry in [CHANGELOG.md](CHANGELOG.md) under **Unreleased**;
- these five checks passing, with test coverage of at least 90%:

```bash
uv sync --locked && uv run pytest --cov-fail-under=90 && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src
```

Every `uv run pytest` also measures line and branch coverage and lists the files with untested code.
CI's Linux jobs, which run every test against Postgres, fail below 90%; `--no-cov` skips the
measurement.

Git hooks run the same checks before your commits and pushes. Turn them on once per clone:

```bash
git config core.hooksPath .githooks
```

`pre-commit` runs ruff's lint and format checks; `pre-push` runs everything CI does, with the 90%
floor, and needs Postgres up (`riffle db up`).

A database change is a hand-written, numbered migration in `src/riffle/db/migrations/versions/`, with
the tables in `src/riffle/db/models.py` kept in step. A commit message has a title in the imperative
("Add …", "Fix …"), then plain prose wrapped at 72 columns: what was wrong, and what changes.

The code is in `src/riffle/`, and the paths below are inside it unless they begin with `tests/`. Start
with a command in `cli/`, then follow its calls into operations (`ops/`), analysis, ingestion, or the
catalog (`store/`). `ops/sync.py` coordinates a full sync; `sync.py`, at the package's root, handles
deck analysis and generated notes against the catalog protocol. `tests/repo/test_layout.py` keeps three
rules about what imports what:

- `ops/sync.py` reports results through a small output protocol and progress steps, without importing
  the CLI or Typer.
- Metagame analysis imports `Event` and `MtgoDeck` from `models/metagame.py`, so it loads no ingestion
  or database code.
- Scheduler dispatch selects a backend; both backends depend on `ops/schedule/jobs.py`, and import
  neither the dispatch nor each other.

Two more hold, though no test checks them: `models/` has no database imports, and
`ops/schedule/jobs.py` never imports the backends.

A test goes under `tests/` in the folder named for the code it tests (`tests/cli/`, `tests/ingest/`,
`tests/ops/`); `tests/repo/` checks docs and repository policy.
