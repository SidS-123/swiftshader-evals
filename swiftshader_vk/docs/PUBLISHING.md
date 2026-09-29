# Pre-publication checklist

**Internal; not part of the public export.** Every step below rewrites or
publishes history, creates remotes, or decides what becomes permanently
public. Preparation can be done by anyone; the irreversible commands are the
maintainer's.

## 0. The hidden split must stay hidden

If the hidden generators were ever committed to this repository, its history
leaks them whatever the working tree says. Measure before publishing:

```sh
git rev-list --all | wc -l
git log --all --oneline -S '<a hidden function name>' -- corpus/gen
```

Publication is then **a new repository from a cleaned history**, never a
visibility flip on this one.

## 1. Producing a clean public export

Route A (one commit, no history):

```sh
SRC=<this repository>; OUT=<export; must not exist>
git -C "$SRC" status --short          # must be empty
mkdir -p "$OUT" && cd "$OUT" && git init -q -b main
git -C "$SRC" archive --format=tar main | tar -x
rm -rf docs/internal docs/PUBLISHING.md
git add -A && git commit -q -m "ssvk: public release"
```

Route B (`git filter-repo` removing the generator paths and the internal
directories, then re-adding today's public generators) only if the history is
itself part of what is published.

## 2. Verification, on the export

- [ ] No hidden case writer in any commit of any ref (grep every commit for
      each private function name and seed).
- [ ] `corpus/hidden`, `runs`, `corpus/assets`, credentials never committed.
- [ ] No credential-shaped path in any commit.
- [ ] The export works as a public checkout: public generation succeeds,
      hidden generation stops with one message, the suite passes.
- [ ] The regenerated public split is byte-identical to the one the solver sees.

## 3. What may and may not appear in the published text

- Hidden case names beside their scores: acceptable.
- Hidden parameters, seeds, compositions, code fragments: never.
- `docs/internal/` and this file: excluded from the export; nothing public
  links to them.

## 4. The rest

- [ ] Reference as candidate 1.0, controls green, tests passing, on the
      release commit.
- [ ] Every report carries numbers of record under the current metric; no
      projection anywhere; generated sections regenerated.
- [ ] Private generator tree pushed to a **private** remote, today.
- [ ] Placeholder links and contact addresses filled or removed.
- [ ] The submission route for outsiders is documented.
- [ ] Licenses stated: the project's, the oracle's.
- [ ] Record the date and the exported commit in the internal notes.
